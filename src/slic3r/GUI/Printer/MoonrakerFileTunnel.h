#pragma once

#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <string>

#include "nlohmann/json_fwd.hpp"

#define BAMBU_DYNAMIC
#include "BambuTunnel.h"

namespace Slic3r {

// The Device tab's Storage page for a printer agent's Moonraker printer (FileLocal::FL_Moonraker).
// PrinterFileSystem speaks a Bambu printer's file protocol over a Bambu_Tunnel; this tunnel answers
// the same requests from Moonraker's HTTP file API, so the page lists the printer's G-code files as
// models and moonraker-timelapse's videos as timelapses, and keeps one implementation for both kinds
// of printer. It also answers FILE_PRINT, which starts a G-code file as it is, and FILE_INFO, which
// reads a G-code file's filaments for the print dialog, so the file can print on other trays.
// Its URL is "moonraker:<Moonraker's base URL>", with "?api_key=<key>" when Moonraker wants a key.
class MoonrakerFileTunnel
{
public:
    // The requests it answers, numbered as PrinterFileSystem numbers them.
    enum Command {
        LIST_INFO             = 0x0001,
        SUB_FILE              = 0x0002,
        FILE_DEL              = 0x0003,
        FILE_DOWNLOAD         = 0x0004,
        REQUEST_MEDIA_ABILITY = 0x0007,
        FILE_PRINT            = 0x0010,
        FILE_INFO             = 0x0011,
        TASK_CANCEL           = 0x1000,
    };

    struct Reply
    {
        unsigned status = 0; // the HTTP status, 0 when Moonraker could not be reached
        std::string body;
        std::string headers;
    };
    // One HTTP request to Moonraker: method "GET", "POST" or "DELETE", target the path and query that
    // follow the base URL, range "<first byte>-<last byte>" for part of a file or empty.
    using Fetch = std::function<Reply(std::string const& method, std::string const& target, std::string const& range)>;

    explicit MoonrakerFileTunnel(Fetch fetch);
    ~MoonrakerFileTunnel();

    // A tunnel that talks to Moonraker over HTTP for a "moonraker:" URL, nullptr for any other URL.
    static std::unique_ptr<MoonrakerFileTunnel> create(std::string const& url);

    // The Bambu_Tunnel functions that drive a tunnel create() makes, for PrinterFileSystem.
    static BambuLib const& lib();

    // Whether Moonraker answers at all.
    bool open();

    // A request: its JSON, then "\n\n" and a parameter when it has one.
    void send(std::string const& message);

    // The next answer, its JSON then "\n\n" and its data; false when none is due. The HTTP requests
    // behind it run here, on the caller's thread, one request or one part of a download per call.
    bool read(std::string& sample);

private:
    struct Download;

    void handle(std::string const& message);
    void list(int sequence, std::string const& type);
    void sub_files(int sequence, nlohmann::json const& req);
    nlohmann::json metadata(std::string const& path);
    void delete_files(int sequence, nlohmann::json const& req);
    void start_download(int sequence, nlohmann::json const& req);
    void continue_download();
    void print(int sequence, nlohmann::json const& req);
    void file_info(int sequence, nlohmann::json const& req);
    void cancel(int sequence, nlohmann::json const& req);
    void answer(int sequence, int result, nlohmann::json const& reply, std::string const& data = {});

    Fetch m_fetch;
    std::deque<std::string> m_requests;
    std::deque<std::string> m_answers;
    std::unique_ptr<Download> m_download;
    std::map<std::string, uint64_t> m_sizes; // listed files' sizes by path
    std::set<std::string> m_previews;        // listed timelapse previews
};

} // namespace Slic3r
