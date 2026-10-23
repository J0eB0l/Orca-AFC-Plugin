#include <catch2/catch_test_macros.hpp>
#include <catch2/generators/catch_generators.hpp>

#include <cmath>
#include <map>
#include <string>
#include <utility>
#include <vector>

#include "nlohmann/json.hpp"
#include "slic3r/GUI/Printer/MoonrakerFileTunnel.h"

using namespace Slic3r;
using json = nlohmann::json;

namespace {

// A Moonraker that answers from `files` (path from the server root -> content) and `api`
// ("METHOD target" -> reply), and keeps every request it was sent.
struct FakeMoonraker
{
    std::map<std::string, std::string> files;
    std::map<std::string, MoonrakerFileTunnel::Reply> api;
    std::vector<std::string> requests;
    bool ranges = true;

    MoonrakerFileTunnel tunnel()
    {
        return MoonrakerFileTunnel([this](std::string const& method, std::string const& target, std::string const& range) {
            requests.push_back(method + " " + target + (range.empty() ? "" : " " + range));
            if (auto reply = api.find(method + " " + target); reply != api.end())
                return reply->second;
            auto file = files.find(target);
            if (method != "GET" || file == files.end())
                return MoonrakerFileTunnel::Reply{404, "", ""};
            if (range.empty() || !ranges)
                return MoonrakerFileTunnel::Reply{200, file->second, ""};
            size_t first = std::stoull(range.substr(0, range.find('-')));
            size_t last  = std::stoull(range.substr(range.find('-') + 1));
            if (first >= file->second.size())
                return MoonrakerFileTunnel::Reply{416, "", ""};
            std::string part = file->second.substr(first, last + 1 - first);
            return MoonrakerFileTunnel::Reply{206, part,
                                              "HTTP/1.1 206 Partial Content\r\nContent-Range: bytes " + std::to_string(first) + "-" +
                                                  std::to_string(first + part.size() - 1) + "/" + std::to_string(file->second.size()) +
                                                  "\r\n"};
        });
    }
};

MoonrakerFileTunnel::Reply ok(json const& result) { return {200, json{{"result", result}}.dump(), ""}; }

std::string request(int cmdtype, int sequence, json const& req)
{
    return json{{"cmdtype", cmdtype}, {"sequence", sequence}, {"req", req}}.dump();
}

struct Answer
{
    json root;
    std::string data;
};

std::vector<Answer> answers(MoonrakerFileTunnel& tunnel)
{
    std::vector<Answer> all;
    std::string sample;
    while (tunnel.read(sample)) {
        size_t n = sample.find("\n\n");
        all.push_back({json::parse(sample.substr(0, n)), sample.substr(n + 2)});
    }
    return all;
}

json const gcode_list = json::array({
    {{"path", "benchy.gcode"}, {"modified", 1700000000.5}, {"size", 1234}},
    {{"path", "parts/clip.gcode"}, {"modified", 1700000100.0}, {"size", 99}},
    {{"path", ".thumbs/benchy-300x300.png"}, {"modified", 1700000000.0}, {"size", 10}},
    {{"path", "notes.txt"}, {"modified", 1700000000.0}, {"size", 5}},
});

json const timelapse_list = json::array({
    {{"path", "timelapse_benchy.mp4"}, {"modified", 1700000200.0}, {"size", 50}},
    {{"path", "timelapse_benchy.jpg"}, {"modified", 1700000200.0}, {"size", 7}},
    {{"path", "timelapse_clip.mp4"}, {"modified", 1700000300.0}, {"size", 60}},
});

} // namespace

TEST_CASE("Only a moonraker URL makes a Moonraker file tunnel", "[MoonrakerFiles]")
{
    CHECK(MoonrakerFileTunnel::create("moonraker:http://10.0.0.5:7125?api_key=abc") != nullptr);
    CHECK(MoonrakerFileTunnel::create("bambu:///local/10.0.0.5.?port=6000") == nullptr);
}

TEST_CASE("A Moonraker printer's G-code files are its models", "[MoonrakerFiles]")
{
    FakeMoonraker moonraker;
    moonraker.api["GET /server/files/list?root=gcodes"] = ok(gcode_list);
    auto tunnel                                         = moonraker.tunnel();

    tunnel.send(request(MoonrakerFileTunnel::LIST_INFO, 7, {{"type", "model"}, {"api_version", 2}}));
    auto all = answers(tunnel);

    REQUIRE(all.size() == 1);
    CHECK(all[0].root["sequence"] == 7);
    CHECK(all[0].root["result"] == 0);
    json const files = all[0].root["reply"]["file_lists"];
    REQUIRE(files.size() == 2);
    CHECK(files[0] == json{{"name", "benchy.gcode"}, {"path", "benchy.gcode"}, {"time", 1700000000}, {"size", 1234}});
    CHECK(files[1] == json{{"name", "clip.gcode"}, {"path", "parts/clip.gcode"}, {"time", 1700000100}, {"size", 99}});
}

TEST_CASE("A Moonraker printer's timelapse videos are its timelapses", "[MoonrakerFiles]")
{
    FakeMoonraker moonraker;
    moonraker.api["GET /server/files/list?root=timelapse"]          = ok(timelapse_list);
    moonraker.files["/server/files/timelapse/timelapse_benchy.jpg"] = "jpgdata";
    auto tunnel                                                     = moonraker.tunnel();

    tunnel.send(request(MoonrakerFileTunnel::LIST_INFO, 1, {{"type", "timelapse"}}));
    auto listed = answers(tunnel);
    REQUIRE(listed.size() == 1);
    json const files = listed[0].root["reply"]["file_lists"];
    REQUIRE(files.size() == 2);
    CHECK(files[0]["path"] == "timelapse_benchy.mp4");
    CHECK(files[1]["path"] == "timelapse_clip.mp4");

    SECTION("a video's thumbnail is its preview beside it")
    {
        tunnel.send(
            request(MoonrakerFileTunnel::SUB_FILE, 2, {{"paths", {"timelapse_benchy.mp4#thumbnail", "timelapse_clip.mp4#thumbnail"}}}));
        auto all = answers(tunnel);
        REQUIRE(all.size() == 1);
        CHECK(all[0].root["result"] == 0);
        CHECK(all[0].root["reply"]["path"] == "timelapse_benchy.mp4#thumbnail");
        CHECK(all[0].root["reply"]["size"] == 7);
        CHECK(all[0].data == "jpgdata");
    }

    SECTION("deleting a video deletes its preview")
    {
        moonraker.api["DELETE /server/files/timelapse/timelapse_benchy.mp4"] = ok(json::object());
        moonraker.api["DELETE /server/files/timelapse/timelapse_benchy.jpg"] = ok(json::object());
        tunnel.send(request(MoonrakerFileTunnel::FILE_DEL, 3, {{"paths", {"timelapse_benchy.mp4"}}}));
        auto all = answers(tunnel);
        REQUIRE(all.size() == 1);
        CHECK(all[0].root["result"] == 0);
        CHECK(moonraker.requests.back() == "DELETE /server/files/timelapse/timelapse_benchy.jpg");
    }
}

TEST_CASE("A printer without moonraker-timelapse has no timelapses", "[MoonrakerFiles]")
{
    FakeMoonraker moonraker;
    auto tunnel = moonraker.tunnel();

    tunnel.send(request(MoonrakerFileTunnel::LIST_INFO, 1, {{"type", "timelapse"}}));
    auto all = answers(tunnel);

    REQUIRE(all.size() == 1);
    CHECK(all[0].root["result"] == 0);
    CHECK(all[0].root["reply"]["file_lists"].empty());
}

TEST_CASE("A G-code file's Moonraker metadata answers its model metadata request", "[MoonrakerFiles]")
{
    FakeMoonraker moonraker;
    moonraker.api["GET /server/files/metadata?filename=parts%2Fclip.gcode"] = ok(
        {{"estimated_time", 3725.4},
         {"filament_weight_total", 12.6},
         {"thumbnails",
          {{{"width", 32}, {"relative_path", ".thumbs/clip-32x32.png"}}, {{"width", 300}, {"relative_path", ".thumbs/clip-300x300.png"}}}}});
    moonraker.files["/server/files/gcodes/parts/.thumbs/clip-300x300.png"] = "pngdata";
    auto tunnel                                                            = moonraker.tunnel();

    tunnel.send(request(MoonrakerFileTunnel::SUB_FILE, 4,
                        {{"paths", {"parts/clip.gcode#_rels/.rels", "parts/clip.gcode#3D/3dmodel.model"}}, {"zip", true}}));
    auto all = answers(tunnel);

    REQUIRE(all.size() == 1);
    CHECK(all[0].root["result"] == 0);
    CHECK(all[0].root["reply"]["path"] == "parts/clip.gcode");
    json const meta = all[0].root["reply"]["metadata"];
    CHECK(meta["time"] == 3725.4);
    CHECK(meta["weight"] == 12.6);
    CHECK(meta["thumbnail"] == "parts/.thumbs/clip-300x300.png");

    SECTION("and its thumbnail is fetched by that path")
    {
        tunnel.send(request(MoonrakerFileTunnel::SUB_FILE, 5, {{"paths", {"parts/clip.gcode#parts/.thumbs/clip-300x300.png"}}}));
        auto images = answers(tunnel);
        REQUIRE(images.size() == 1);
        CHECK(images[0].root["reply"]["mimetype"] == "image/png");
        CHECK(images[0].data == "pngdata");
    }
}

TEST_CASE("A download comes in parts, the last with the file's MD5", "[MoonrakerFiles]")
{
    FakeMoonraker moonraker;
    // 3 bytes more than a part, whose MD5 is aad348008328b40ee246bfcc3798e112.
    size_t const size                                    = (4 << 20) + 3;
    moonraker.files["/server/files/gcodes/benchy.gcode"] = std::string(size, 'a');
    moonraker.ranges                                     = GENERATE(true, false);
    auto tunnel                                          = moonraker.tunnel();

    tunnel.send(request(MoonrakerFileTunnel::FILE_DOWNLOAD, 9, {{"path", "benchy.gcode"}}));
    auto all = answers(tunnel);

    REQUIRE(all.size() == 2);
    CHECK(all[0].root["result"] == 1);
    CHECK(all[0].root["reply"]["offset"] == 0);
    CHECK(all[0].root["reply"]["total"] == size);
    CHECK(all[1].root["result"] == 0);
    CHECK(all[1].root["reply"]["offset"] == 4 << 20);
    CHECK(all[1].root["reply"]["size"] == 3);
    CHECK(all[1].root["reply"]["file_md5"] == "aad348008328b40ee246bfcc3798e112");
    CHECK(all[0].data.size() + all[1].data.size() == size);
}

TEST_CASE("A cancelled download sends no more parts", "[MoonrakerFiles]")
{
    FakeMoonraker moonraker;
    moonraker.files["/server/files/gcodes/benchy.gcode"] = std::string((4 << 20) + 3, 'a');
    auto tunnel                                          = moonraker.tunnel();
    std::string sample;

    tunnel.send(request(MoonrakerFileTunnel::FILE_DOWNLOAD, 9, {{"path", "benchy.gcode"}}));
    REQUIRE(tunnel.read(sample));
    tunnel.send(request(MoonrakerFileTunnel::TASK_CANCEL, 10, {{"tasks", {9}}}));
    auto all = answers(tunnel);

    REQUIRE(all.size() == 1);
    CHECK(all[0].root["sequence"] == 10);
    CHECK(all[0].root["reply"]["tasks"] == json::array({9}));
}

TEST_CASE("Print starts a G-code file, and a refusal carries Moonraker's reason", "[MoonrakerFiles]")
{
    FakeMoonraker moonraker;
    auto tunnel = moonraker.tunnel();

    moonraker.api["POST /printer/print/start?filename=parts%2Fclip.gcode"] = ok("ok");
    tunnel.send(request(MoonrakerFileTunnel::FILE_PRINT, 1, {{"path", "parts/clip.gcode"}}));
    auto started = answers(tunnel);
    REQUIRE(started.size() == 1);
    CHECK(started[0].root["result"] == 0);

    moonraker.api["POST /printer/print/start?filename=parts%2Fclip.gcode"] =
        {400, json{{"error", {{"code", 400}, {"message", "Printer is busy"}}}}.dump(), ""};
    tunnel.send(request(MoonrakerFileTunnel::FILE_PRINT, 2, {{"path", "parts/clip.gcode"}}));
    auto refused = answers(tunnel);
    REQUIRE(refused.size() == 1);
    CHECK(refused[0].root["result"] != 0);
    CHECK(refused[0].root["reply"]["message"] == "Printer is busy");
}

TEST_CASE("A G-code file's info has the filaments its end says it prints", "[MoonrakerFiles]")
{
    // The end of a Klipper file OrcaSlicer wrote: its settings block, then what each filament used.
    std::string const end = "; CONFIG_BLOCK_START\n"
                            "; filament_colour = #FF0000;\"#00FF00\";#0000FF\n"
                            "; filament_type = PLA;PETG;PLA\n"
                            "; filament_vendor = \"Generic\";\"Bambu Lab\";Generic\n"
                            "; filament_ids = GFL99;GFG99;GFL99\n"
                            "; nozzle_diameter = 0.4,0.6\n"
                            "; CONFIG_BLOCK_END\n\n"
                            "; filament used [mm] = 1520.25, 0.00, 300.50\r\n"
                            "; filament used [g] = 4.53, 0, 0.90\n"
                            "; total filament used [g] = 5.43\n";
    // A setting before the block is not one of the file's.
    std::string body = "; filament_type = ABS\n";
    while (body.size() < (2 << 20))
        body += "G1 X10 Y10 E0.5\n";
    std::string const file = body + end;

    FakeMoonraker moonraker;
    moonraker.files["/server/files/gcodes/parts/clip.gcode"]                = file;
    moonraker.api["GET /server/files/list?root=gcodes"]                     = ok({{{"path", "parts/clip.gcode"}, {"size", file.size()}}});
    moonraker.api["GET /server/files/metadata?filename=parts%2Fclip.gcode"] = ok({{"estimated_time", 3725.4}, {"filament_weight_total", 5.43}});
    moonraker.ranges                                                        = GENERATE(true, false);
    auto tunnel                                                             = moonraker.tunnel();
    tunnel.send(request(MoonrakerFileTunnel::LIST_INFO, 1, {{"type", "model"}}));
    answers(tunnel);

    tunnel.send(request(MoonrakerFileTunnel::FILE_INFO, 2, {{"path", "parts/clip.gcode"}}));
    auto all = answers(tunnel);

    REQUIRE(all.size() == 1);
    CHECK(all[0].root["result"] == 0);
    CHECK(moonraker.requests[1] == "GET /server/files/gcodes/parts/clip.gcode " + std::to_string(file.size() - (1 << 20)) + "-" +
                                       std::to_string(file.size() - 1));
    json const info = all[0].root["reply"];
    CHECK(info["filament_count"] == 3);
    CHECK(info["nozzle_diameter"] == json::array({0.4, 0.6}));
    CHECK(info["time"] == 3725.4);
    CHECK(info["weight"] == 5.43);
    json const filaments = info["filaments"];
    REQUIRE(filaments.size() == 2);
    CHECK(filaments[0]["id"] == 0);
    CHECK(filaments[0]["type"] == "PLA");
    CHECK(filaments[0]["color"] == "#FF0000");
    CHECK(filaments[0]["vendor"] == "Generic");
    CHECK(filaments[0]["filament_id"] == "GFL99");
    CHECK(filaments[0]["used_g"] == 4.53);
    CHECK(std::abs(filaments[0]["used_m"].get<double>() - 1.52025) < 1e-9);
    CHECK(filaments[1]["id"] == 2);
    CHECK(filaments[1]["color"] == "#0000FF");
    CHECK(filaments[1]["used_g"] == 0.9);
}

TEST_CASE("A G-code file without OrcaSlicer's settings block has no filaments to map", "[MoonrakerFiles]")
{
    std::string const file = "G28\nG1 X10\n; filament used [mm] = 152.0\n";
    FakeMoonraker moonraker;
    moonraker.files["/server/files/gcodes/benchy.gcode"] = file;
    moonraker.api["GET /server/files/list?root=gcodes"]  = ok({{{"path", "benchy.gcode"}, {"size", file.size()}}});
    auto tunnel                                          = moonraker.tunnel();
    tunnel.send(request(MoonrakerFileTunnel::LIST_INFO, 1, {{"type", "model"}}));
    answers(tunnel);

    tunnel.send(request(MoonrakerFileTunnel::FILE_INFO, 2, {{"path", "benchy.gcode"}}));
    tunnel.send(request(MoonrakerFileTunnel::FILE_INFO, 3, {{"path", "unlisted.gcode"}}));
    auto all = answers(tunnel);

    REQUIRE(all.size() == 2);
    CHECK(all[0].root["result"] == 0);
    CHECK_FALSE(all[0].root["reply"].contains("filaments"));
    CHECK(all[1].root["result"] == 10);
}
