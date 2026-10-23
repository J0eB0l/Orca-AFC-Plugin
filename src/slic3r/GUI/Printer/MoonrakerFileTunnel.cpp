#include "MoonrakerFileTunnel.h"

#include "slic3r/Utils/Http.hpp"

#include <algorithm>
#include <cstdlib>
#include <initializer_list>
#include <locale>
#include <sstream>
#include <utility>
#include <vector>

#include <boost/algorithm/string/case_conv.hpp>
#include <boost/algorithm/string/predicate.hpp>
#include <boost/algorithm/string/trim.hpp>
#include <openssl/evp.h>

#include "nlohmann/json.hpp"

using json = nlohmann::json;

namespace Slic3r {

namespace {

// Answers, numbered as PrinterFileSystem numbers them. A failed HTTP request answers its status.
enum Result {
    SUCCESS        = 0,
    CONTINUE       = 1,
    ERROR_JSON     = 2,
    ERROR_PIPE     = 3,
    ERROR_RES_BUSY = 5,
    FILE_NO_EXIST  = 10,
    FILE_TYPE_ERR  = 16,
};

// Moonraker's file roots: the G-code files, and moonraker-timelapse's videos with their previews.
char const* const GCODES    = "gcodes";
char const* const TIMELAPSE = "timelapse";

// A download's part per answer.
constexpr uint64_t CHUNK = 4 << 20;

// How much of a G-code file's end FILE_INFO reads: OrcaSlicer writes a Klipper file's settings
// block there, then how much of each filament it uses.
constexpr uint64_t TAIL = 1 << 20;

bool has_extension(std::string const& path, std::initializer_list<char const*> extensions)
{
    return std::any_of(extensions.begin(), extensions.end(), [&path](char const* e) { return boost::iends_with(path, e); });
}

// Moonraker's own G-code extensions.
bool is_gcode(std::string const& path) { return has_extension(path, {".gcode", ".g", ".gco", ".ufp", ".nc"}); }

bool is_video(std::string const& path) { return has_extension(path, {".mp4", ".mkv", ".webm", ".avi", ".mov"}); }

// In a hidden folder, as the .thumbs folders that hold G-code thumbnails.
bool is_hidden(std::string const& path) { return path.front() == '.' || path.find("/.") != std::string::npos; }

// A timelapse's preview: its .jpg beside it.
std::string preview_of(std::string const& video) { return video.substr(0, video.find_last_of('.')) + ".jpg"; }

std::string file_target(char const* root, std::string const& path)
{
    std::string target = std::string("/server/files/") + root;
    for (size_t start = 0; start != std::string::npos;) {
        size_t end = path.find('/', start);
        target += '/' + Http::url_encode(path.substr(start, end == std::string::npos ? end : end - start));
        start = end == std::string::npos ? end : end + 1;
    }
    return target;
}

int failure(Slic3r::MoonrakerFileTunnel::Reply const& reply)
{
    return reply.status == 0 ? ERROR_PIPE : reply.status == 404 ? FILE_NO_EXIST : int(reply.status);
}

json result_of(Slic3r::MoonrakerFileTunnel::Reply const& reply)
{
    json body = json::parse(reply.body, nullptr, false);
    return reply.status == 200 && body.is_object() && body.contains("result") ? body["result"] : json();
}

// The whole size of a file from the "Content-Range: bytes 0-99/1234" of an answer with part of it.
uint64_t range_total(std::string const& headers)
{
    std::string lower = boost::algorithm::to_lower_copy(headers);
    size_t n          = lower.rfind("content-range:");
    n                 = n == std::string::npos ? n : lower.find('/', n);
    return n == std::string::npos ? 0 : std::strtoull(lower.c_str() + n + 1, nullptr, 10);
}

// The values of an "a;b" or "1.2, 0" setting, without the quotes OrcaSlicer puts around a value
// that has a space or a separator in it.
std::vector<std::string> split_values(std::string const& text, char separator)
{
    std::vector<std::string> values(1);
    bool quoted = false;
    for (size_t i = 0; i < text.size(); ++i) {
        char const c = text[i];
        if (c == '\\' && quoted && i + 1 < text.size())
            values.back() += text[++i];
        else if (c == '"')
            quoted = !quoted;
        else if (c == separator && !quoted)
            values.emplace_back();
        else
            values.back() += c;
    }
    for (auto& value : values)
        boost::algorithm::trim(value);
    return text.empty() ? std::vector<std::string>() : values;
}

// A number as OrcaSlicer writes it, whatever the user's locale.
double to_number(std::string const& text)
{
    std::istringstream in(text);
    in.imbue(std::locale::classic());
    double value = 0;
    return in >> value ? value : 0.;
}

// A G-code file's filaments from its end: {"filament_count", "nozzle_diameter", "filaments": [{"id",
// "type", "color", "vendor", "filament_id", "used_g", "used_m"}]} with the filaments it prints. An
// end without OrcaSlicer's settings block gives an empty object.
json gcode_filaments(std::string const& tail)
{
    std::map<std::string, std::string> values;
    bool in_config  = false;
    bool has_config = false;
    std::istringstream lines(tail);
    for (std::string line; std::getline(lines, line);) {
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (boost::starts_with(line, "; CONFIG_BLOCK_START")) {
            in_config = has_config = true;
            continue;
        }
        if (boost::starts_with(line, "; CONFIG_BLOCK_END")) {
            in_config = false;
            continue;
        }
        size_t const n = line.find(" = ");
        if (!boost::starts_with(line, "; ") || n == std::string::npos)
            continue;
        std::string key = line.substr(2, n - 2);
        if (in_config || boost::starts_with(key, "filament used ["))
            values[key] = line.substr(n + 3);
    }
    if (!has_config)
        return json::object();

    auto list = [&values](char const* key, char separator) {
        auto value = values.find(key);
        return value == values.end() ? std::vector<std::string>() : split_values(value->second, separator);
    };
    auto text   = [](std::vector<std::string> const& items, size_t i) { return i < items.size() ? items[i] : std::string(); };
    auto number = [](std::vector<std::string> const& items, size_t i) { return i < items.size() ? to_number(items[i]) : 0.; };
    std::vector<std::string> const colors  = list("filament_colour", ';');
    std::vector<std::string> const types   = list("filament_type", ';');
    std::vector<std::string> const vendors = list("filament_vendor", ';');
    std::vector<std::string> const ids     = list("filament_ids", ';');
    std::vector<std::string> const used_g  = list("filament used [g]", ',');
    std::vector<std::string> const used_mm = list("filament used [mm]", ',');
    size_t const count                     = std::max(colors.size(), types.size());
    json filaments                         = json::array();
    for (size_t i = 0; i < count; ++i) {
        if (number(used_mm, i) <= 0 && number(used_g, i) <= 0)
            continue;
        filaments.push_back({{"id", i},
                             {"type", text(types, i)},
                             {"color", text(colors, i)},
                             {"vendor", text(vendors, i)},
                             {"filament_id", text(ids, i)},
                             {"used_g", number(used_g, i)},
                             {"used_m", number(used_mm, i) / 1000}});
    }
    json nozzles = json::array();
    for (auto const& diameter : list("nozzle_diameter", ','))
        nozzles.push_back(to_number(diameter));
    return {{"filament_count", count}, {"nozzle_diameter", nozzles}, {"filaments", filaments}};
}

struct TunnelHandle
{
    std::unique_ptr<MoonrakerFileTunnel> tunnel;
    std::string sample;
};

} // namespace

struct MoonrakerFileTunnel::Download
{
    int sequence = 0;
    std::string target;
    uint64_t offset = 0;
    uint64_t total  = 0;
    std::string whole; // the file, when Moonraker sent all of it for a part
    EVP_MD_CTX* md5 = EVP_MD_CTX_new();

    Download() { EVP_DigestInit_ex(md5, EVP_md5(), nullptr); }
    ~Download() { EVP_MD_CTX_free(md5); }
};

MoonrakerFileTunnel::MoonrakerFileTunnel(Fetch fetch) : m_fetch(std::move(fetch)) {}

MoonrakerFileTunnel::~MoonrakerFileTunnel() = default;

std::unique_ptr<MoonrakerFileTunnel> MoonrakerFileTunnel::create(std::string const& url)
{
    std::string const scheme = "moonraker:";
    if (!boost::starts_with(url, scheme))
        return nullptr;
    std::string base = url.substr(scheme.size());
    std::string key;
    if (size_t n = base.find("?api_key="); n != std::string::npos) {
        key = Http::url_decode(base.substr(n + 9));
        base.erase(n);
    }
    while (!base.empty() && base.back() == '/')
        base.pop_back();
    return std::make_unique<MoonrakerFileTunnel>([base, key](std::string const& method, std::string const& target,
                                                             std::string const& range) {
        Reply reply;
        Http http = method == "POST" ? Http::post(base + target) : method == "DELETE" ? Http::del(base + target) : Http::get(base + target);
        if (!key.empty())
            http.header("X-Api-Key", key);
        if (method == "POST")
            http.header("Content-Type", "application/json").set_post_body(std::string("{}"));
        if (!range.empty())
            http.set_range(range);
        http.timeout_connect(5)
            .timeout_max(range.empty() ? 30 : 120)
            .on_header_callback([&reply](std::string headers) { reply.headers = std::move(headers); })
            .on_complete([&reply](std::string body, unsigned status) {
                reply.body   = std::move(body);
                reply.status = status;
            })
            .on_error([&reply](std::string body, std::string, unsigned status) {
                reply.body   = std::move(body);
                reply.status = status;
            })
            .perform_sync();
        return reply;
    });
}

BambuLib const& MoonrakerFileTunnel::lib()
{
    static BambuLib const functions = [] {
        BambuLib table{};
        table.Bambu_Create = [](Bambu_Tunnel* tunnel, char const* url) {
            auto made = create(url);
            *tunnel   = made ? new TunnelHandle{std::move(made), {}} : nullptr;
            return *tunnel ? 0 : -1;
        };
        table.Bambu_SetLogger     = [](Bambu_Tunnel, Logger, void*) {};
        table.Bambu_Open          = [](Bambu_Tunnel tunnel) { return static_cast<TunnelHandle*>(tunnel)->tunnel->open() ? 0 : -1; };
        table.Bambu_StartStream   = [](Bambu_Tunnel, bool) { return 0; };
        table.Bambu_StartStreamEx = [](Bambu_Tunnel, int) { return 0; };
        table.Bambu_SendMessage   = [](Bambu_Tunnel tunnel, int, char const* data, int len) {
            static_cast<TunnelHandle*>(tunnel)->tunnel->send(std::string(data, len));
            return 0;
        };
        table.Bambu_ReadSample = [](Bambu_Tunnel tunnel, Bambu_Sample* sample) {
            auto handle = static_cast<TunnelHandle*>(tunnel);
            if (!handle->tunnel->read(handle->sample))
                return int(Bambu_would_block);
            *sample        = {};
            sample->size   = int(handle->sample.size());
            sample->buffer = reinterpret_cast<unsigned char const*>(handle->sample.data());
            return int(Bambu_success);
        };
        table.Bambu_Close   = [](Bambu_Tunnel) {};
        table.Bambu_Destroy = [](Bambu_Tunnel tunnel) { delete static_cast<TunnelHandle*>(tunnel); };
        return table;
    }();
    return functions;
}

bool MoonrakerFileTunnel::open() { return m_fetch("GET", "/server/info", {}).status != 0; }

void MoonrakerFileTunnel::send(std::string const& message) { m_requests.push_back(message); }

bool MoonrakerFileTunnel::read(std::string& sample)
{
    if (m_answers.empty()) {
        if (!m_requests.empty()) {
            std::string message = std::move(m_requests.front());
            m_requests.pop_front();
            handle(message);
        } else if (m_download) {
            continue_download();
        }
    }
    if (m_answers.empty())
        return false;
    sample = std::move(m_answers.front());
    m_answers.pop_front();
    return true;
}

void MoonrakerFileTunnel::handle(std::string const& message)
{
    json root = json::parse(message.substr(0, message.find("\n\n")), nullptr, false);
    if (!root.is_object())
        return;
    int sequence = root.value("sequence", 0);
    try {
        json const& req = root.contains("req") && root["req"].is_object() ? root["req"] : json::object();
        switch (root.value("cmdtype", 0)) {
        case LIST_INFO: list(sequence, req.value("type", "")); break;
        case SUB_FILE: sub_files(sequence, req); break;
        case FILE_DEL: delete_files(sequence, req); break;
        case FILE_DOWNLOAD: start_download(sequence, req); break;
        case REQUEST_MEDIA_ABILITY: answer(sequence, SUCCESS, {{"storage", json::array()}}); break;
        case FILE_PRINT: print(sequence, req); break;
        case FILE_INFO: file_info(sequence, req); break;
        case TASK_CANCEL: cancel(sequence, req); break;
        default: answer(sequence, FILE_TYPE_ERR, json::object());
        }
    } catch (json::exception const&) {
        answer(sequence, ERROR_JSON, json::object());
    }
}

void MoonrakerFileTunnel::list(int sequence, std::string const& type)
{
    bool const videos = type == "timelapse";
    json files        = json::array();
    if (videos || type == "model") {
        Reply reply = m_fetch("GET", std::string("/server/files/list?root=") + (videos ? TIMELAPSE : GCODES), {});
        json result = result_of(reply);
        // Without moonraker-timelapse there is no timelapse root, and so no timelapses.
        bool no_root = videos && (reply.status == 400 || reply.status == 404);
        if (!result.is_array() && !no_root) {
            answer(sequence, failure(reply), json::object());
            return;
        }
        if (videos)
            m_previews.clear();
        for (auto const& f : result.is_array() ? result : json::array()) {
            std::string path = f.value("path", "");
            if (path.empty() || is_hidden(path))
                continue;
            if (videos && boost::iends_with(path, ".jpg"))
                m_previews.insert(path);
            if (!(videos ? is_video(path) : is_gcode(path)))
                continue;
            uint64_t size = f.value("size", uint64_t(0));
            m_sizes[path] = size;
            // Named without its folder, as a download is saved by its name.
            std::string name = path.substr(path.find_last_of('/') + 1);
            files.push_back({{"name", name}, {"path", path}, {"time", int64_t(f.value("modified", 0.0))}, {"size", size}});
        }
    }
    answer(sequence, SUCCESS, {{"file_lists", files}});
}

void MoonrakerFileTunnel::sub_files(int sequence, json const& req)
{
    std::vector<std::pair<json, std::string>> parts; // each answer and its data
    std::vector<std::string> paths;
    for (auto const& path : req.value("paths", json::array()))
        paths.push_back(path.get<std::string>());
    if (req.value("zip", false)) {
        // A 3MF's metadata, as "<file>#<entry>" paths: a G-code file has Moonraker's metadata instead.
        std::vector<std::string> files;
        for (auto const& path : paths)
            if (auto file = path.substr(0, path.find('#')); std::find(files.begin(), files.end(), file) == files.end())
                files.push_back(file);
        for (auto const& file : files)
            parts.emplace_back(json{{"path", file}, {"size", 0}, {"metadata", metadata(file)}}, std::string());
    } else {
        // "<file>#<image>": a G-code thumbnail by its path, or "<video>#thumbnail" for a timelapse's preview.
        for (auto const& path : paths) {
            size_t n = path.find_last_of('#');
            if (n == std::string::npos)
                continue;
            std::string image = path.substr(n + 1);
            bool preview      = image == "thumbnail";
            if (preview)
                image = preview_of(path.substr(0, n));
            if (preview && m_previews.count(image) == 0)
                continue;
            Reply reply = m_fetch("GET", file_target(preview ? TIMELAPSE : GCODES, image), {});
            if (reply.status != 200 || reply.body.empty())
                continue;
            std::string mimetype = boost::iends_with(image, ".png") ? "image/png" : "image/jpeg";
            json info{{"path", path}, {"thumbnail", image}, {"size", reply.body.size()}, {"mimetype", mimetype}};
            parts.emplace_back(std::move(info), std::move(reply.body));
        }
    }
    if (parts.empty())
        answer(sequence, FILE_NO_EXIST, json::object());
    for (size_t i = 0; i < parts.size(); ++i)
        answer(sequence, i + 1 < parts.size() ? CONTINUE : SUCCESS, parts[i].first, parts[i].second);
}

json MoonrakerFileTunnel::metadata(std::string const& path)
{
    json meta   = json::object();
    json result = result_of(m_fetch("GET", "/server/files/metadata?filename=" + Http::url_encode(path), {}));
    if (!result.is_object())
        return meta;
    if (result.contains("estimated_time") && result["estimated_time"].is_number())
        meta["time"] = result["estimated_time"];
    if (result.contains("filament_weight_total") && result["filament_weight_total"].is_number())
        meta["weight"] = result["filament_weight_total"];
    // The largest thumbnail, whose relative path is from the file's folder.
    int width = 0;
    std::string thumbnail;
    for (auto const& t : result.value("thumbnails", json::array())) {
        bool usable = t.is_object() && t.contains("width") && t["width"].is_number() && t.contains("relative_path") &&
                      t["relative_path"].is_string();
        if (usable && t["width"].get<int>() > width) {
            width     = t["width"].get<int>();
            thumbnail = t["relative_path"].get<std::string>();
        }
    }
    if (!thumbnail.empty())
        meta["thumbnail"] = path.substr(0, path.find_last_of('/') + 1) + thumbnail;
    return meta;
}

void MoonrakerFileTunnel::delete_files(int sequence, json const& req)
{
    int result = SUCCESS;
    for (auto const& item : req.contains("paths") ? req["paths"] : req.value("delete", json::array())) {
        std::string path = item.get<std::string>();
        bool video       = is_video(path);
        Reply reply      = m_fetch("DELETE", file_target(video ? TIMELAPSE : GCODES, path), {});
        if (reply.status != 200)
            result = failure(reply);
        else if (video && m_previews.erase(preview_of(path))) // its preview goes with it
            m_fetch("DELETE", file_target(TIMELAPSE, preview_of(path)), {});
    }
    answer(sequence, result, json::object());
}

void MoonrakerFileTunnel::start_download(int sequence, json const& req)
{
    std::string path     = req.contains("path") ? req.value("path", "") : req.value("file", "");
    m_download           = std::make_unique<Download>();
    m_download->sequence = sequence;
    m_download->target   = file_target(is_video(path) ? TIMELAPSE : GCODES, path);
    if (auto size = m_sizes.find(path); size != m_sizes.end())
        m_download->total = size->second;
    continue_download();
}

void MoonrakerFileTunnel::continue_download()
{
    Download& download = *m_download;
    std::string data;
    if (download.offset > 0 && !download.whole.empty()) {
        data = download.whole.substr(download.offset, CHUNK);
    } else {
        Reply reply = m_fetch("GET", download.target, std::to_string(download.offset) + "-" + std::to_string(download.offset + CHUNK - 1));
        if (reply.status == 206) {
            data = std::move(reply.body);
            if (uint64_t total = range_total(reply.headers))
                download.total = total;
        } else if (reply.status == 200 && download.offset == 0) {
            // All of it at once: it is still handed on a part at a time.
            download.whole = std::move(reply.body);
            download.total = download.whole.size();
            data           = download.whole.substr(0, CHUNK);
        } else if (reply.status == 416) {
            download.total = download.offset; // past the end: an empty file, or one whose size was unknown
        } else {
            answer(download.sequence, failure(reply), json::object());
            m_download.reset();
            return;
        }
    }
    if (data.empty() && download.offset < download.total) {
        answer(download.sequence, ERROR_PIPE, json::object());
        m_download.reset();
        return;
    }
    uint64_t const offset = download.offset;
    download.offset += data.size();
    if (data.size() < CHUNK)
        download.total = download.offset; // a short part is the last one
    else if (download.total < download.offset)
        download.total = download.offset + 1; // more to come of a file whose size is unknown
    EVP_DigestUpdate(download.md5, data.data(), data.size());
    json reply{{"size", data.size()}, {"offset", offset}, {"total", download.total}};
    bool done = download.offset >= download.total;
    if (done) {
        unsigned char digest[EVP_MAX_MD_SIZE];
        unsigned int length = 0;
        EVP_DigestFinal_ex(download.md5, digest, &length);
        std::string md5;
        for (unsigned int i = 0; i < length; ++i) {
            md5 += "0123456789abcdef"[digest[i] >> 4];
            md5 += "0123456789abcdef"[digest[i] & 15];
        }
        reply["file_md5"] = md5;
    }
    answer(download.sequence, done ? SUCCESS : CONTINUE, reply, data);
    if (done)
        m_download.reset();
}

void MoonrakerFileTunnel::print(int sequence, json const& req)
{
    Reply reply = m_fetch("POST", "/printer/print/start?filename=" + Http::url_encode(req.value("path", "")), {});
    if (reply.status == 200) {
        answer(sequence, SUCCESS, json::object());
        return;
    }
    // Moonraker's own reason, {"error": {"message": ...}}, as when the printer is busy.
    json body = json::parse(reply.body, nullptr, false);
    std::string message;
    if (body.is_object() && body.contains("error") && body["error"].is_object())
        message = body["error"].value("message", "");
    answer(sequence, reply.status == 0 ? ERROR_PIPE : reply.status == 404 ? FILE_NO_EXIST : ERROR_RES_BUSY, {{"message", message}});
}

void MoonrakerFileTunnel::file_info(int sequence, json const& req)
{
    std::string const path = req.value("path", "");
    auto const size        = m_sizes.find(path);
    if (size == m_sizes.end()) {
        answer(sequence, FILE_NO_EXIST, json::object());
        return;
    }
    uint64_t const first = size->second > TAIL ? size->second - TAIL : 0;
    std::string range    = size->second > 0 ? std::to_string(first) + "-" + std::to_string(size->second - 1) : std::string();
    Reply reply          = m_fetch("GET", file_target(GCODES, path), range);
    if (reply.status != 200 && reply.status != 206) {
        answer(sequence, failure(reply), json::object());
        return;
    }
    if (reply.body.size() > TAIL) // all of it, from a server that ignores the range
        reply.body.erase(0, reply.body.size() - TAIL);
    json info       = gcode_filaments(reply.body);
    json const meta = metadata(path);
    for (char const* key : {"time", "weight"})
        if (meta.contains(key))
            info[key] = meta[key];
    answer(sequence, SUCCESS, info);
}

void MoonrakerFileTunnel::cancel(int sequence, json const& req)
{
    json const tasks = req.value("tasks", json::array());
    for (auto const& task : tasks)
        if (m_download && task.is_number() && task.get<int>() == m_download->sequence)
            m_download.reset();
    answer(sequence, SUCCESS, {{"tasks", tasks}});
}

void MoonrakerFileTunnel::answer(int sequence, int result, json const& reply, std::string const& data)
{
    m_answers.push_back(json{{"sequence", sequence}, {"result", result}, {"reply", reply}}.dump() + "\n\n" + data);
}

} // namespace Slic3r
