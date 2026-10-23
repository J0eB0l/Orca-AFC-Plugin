#include "HMS.hpp"
#include "HMSPanel.hpp"
#include "slic3r/GUI/DeviceCore/DevHMS.h"
#include "slic3r/GUI/DeviceCore/DevManager.h"
#include <boost/log/trivial.hpp>
#include <slic3r/GUI/Widgets/SideTools.hpp>
#include <wx/event.h>
#include <string>
#include <wx/panel.h>
#include <wx/string.h>
#include <wx/gdicmn.h>
#include <wx/sizer.h>
#include <wx/stattext.h>
#include <wx/utils.h>
#include "slic3r/GUI/wxExtensions.hpp"
#include <wx/scrolwin.h>
#include "GUI.hpp"
#include "GUI_App.hpp"
#include "I18N.hpp"
#include "MainFrame.hpp"
#include "Monitor.hpp"
#include "format.hpp"
#include "slic3r/Utils/Http.hpp"
#include <algorithm>
#include <boost/nowide/fstream.hpp>
#include <wx/filedlg.h>
#include <wx/statbmp.h>

class wxWindow;

namespace Slic3r {
namespace GUI {

#define HMS_NOTIFY_ITEM_TEXT_SIZE wxSize(FromDIP(730), -1)
#define HMS_NOTIFY_ITEM_SIZE wxSize(-1, FromDIP(80))

wxDEFINE_EVENT(EVT_ALREADY_READ_HMS, wxCommandEvent);

HMSNotifyItem::HMSNotifyItem(const std::string& dev_id, wxWindow *parent, DevHMSItem& item)
    : wxPanel(parent, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxTAB_TRAVERSAL)
    , m_hms_item(item)
    , dev_id(dev_id)
    , long_error_code(item.get_long_error_code())
    , m_url(get_hms_wiki_url(item.get_long_error_code()))
{
    init_bitmaps();

    this->SetBackgroundColour(*wxWHITE);

    auto main_sizer = new wxBoxSizer(wxVERTICAL);

    m_panel_hms = new wxPanel(this, wxID_ANY, wxDefaultPosition, HMS_NOTIFY_ITEM_SIZE, wxTAB_TRAVERSAL);
    m_panel_hms->SetBackgroundColour(*wxWHITE);
    auto m_panel_sizer = new wxBoxSizer(wxVERTICAL);

    auto m_panel_sizer_inner = new wxBoxSizer(wxHORIZONTAL);

    m_bitmap_notify = new wxStaticBitmap(m_panel_hms, wxID_ANY, wxNullBitmap, wxDefaultPosition, wxDefaultSize, 0);
    m_bitmap_notify->SetBitmap(get_notify_bitmap());

    m_hms_content = new wxStaticText(m_panel_hms, wxID_ANY, "", wxDefaultPosition, wxDefaultSize, wxST_ELLIPSIZE_END);
    m_hms_content->SetForegroundColour(*wxBLACK);
    m_hms_content->SetSize(HMS_NOTIFY_ITEM_TEXT_SIZE);
    m_hms_content->SetMinSize(HMS_NOTIFY_ITEM_TEXT_SIZE);
    m_hms_content->SetLabelText(wxGetApp().get_hms_query()->query_hms_msg(dev_id, m_hms_item.get_long_error_code()));
    m_hms_content->Wrap(HMS_NOTIFY_ITEM_TEXT_SIZE.GetX());

    m_bitmap_arrow = new wxStaticBitmap(m_panel_hms, wxID_ANY, wxNullBitmap, wxDefaultPosition, wxDefaultSize, 0);
    if (!m_url.empty())
        m_bitmap_arrow->SetBitmap(m_img_arrow);

    m_panel_sizer_inner->Add(m_bitmap_notify, 0, wxALIGN_CENTER_VERTICAL, 0);
    m_panel_sizer_inner->AddSpacer(FromDIP(8));
    m_panel_sizer_inner->Add(m_hms_content, 0, wxALIGN_CENTER_VERTICAL, 0);
    m_panel_sizer_inner->AddStretchSpacer();
    m_panel_sizer_inner->Add(m_bitmap_arrow, 0, wxALIGN_CENTER_VERTICAL, 0);

    m_panel_sizer->Add(m_panel_sizer_inner, 1, wxEXPAND | wxALL, FromDIP(20));

    m_staticline = new wxPanel(m_panel_hms, wxID_DELETE, wxDefaultPosition, wxSize(-1, FromDIP(1)));
    m_staticline->SetBackgroundColour(wxColour(238, 238, 238));

    m_panel_sizer->Add(m_staticline, 0, wxLEFT | wxRIGHT | wxEXPAND, FromDIP(20));

    m_panel_hms->SetSizer(m_panel_sizer);
    m_panel_hms->Layout();
    m_panel_sizer->Fit(m_panel_hms);

    main_sizer->Add(m_panel_hms, 0, wxEXPAND, 0);

    this->SetSizer(main_sizer);
    this->Layout();

#ifdef __linux__
    m_panel_hms->Bind(wxEVT_ENTER_WINDOW, [this](wxMouseEvent& e) {
        e.Skip();
        if (!m_url.empty()) {
            auto font = m_hms_content->GetFont();
            font.SetUnderlined(true);
            m_hms_content->SetFont(font);
            Layout();
            SetCursor(wxCURSOR_HAND);
        }
        });
    m_panel_hms->Bind(wxEVT_LEAVE_WINDOW, [this](wxMouseEvent& e) {
        e.Skip();
        if (!m_url.empty()) {
            auto font = m_hms_content->GetFont();
            font.SetUnderlined(false);
            m_hms_content->SetFont(font);
            Layout();
            SetCursor(wxCURSOR_ARROW);
        }
        });
    m_panel_hms->Bind(wxEVT_LEFT_UP, [this](wxMouseEvent& e) {
        if (!m_url.empty()) wxLaunchDefaultBrowser(m_url);
        });
    m_hms_content->Bind(wxEVT_LEFT_UP, [this](wxMouseEvent& e) {
        if (!m_url.empty()) wxLaunchDefaultBrowser(m_url);
        });
#else
    m_hms_content->Bind(wxEVT_ENTER_WINDOW, [this](wxMouseEvent& e) {
        e.Skip();
        if (!m_url.empty()) {
            auto font = m_hms_content->GetFont();
            font.SetUnderlined(true);
            m_hms_content->SetFont(font);
            Layout();
            SetCursor(wxCURSOR_HAND);
        }
        });
    m_hms_content->Bind(wxEVT_LEAVE_WINDOW, [this](wxMouseEvent& e) {
        e.Skip();
        if (!m_url.empty()) {
            auto font = m_hms_content->GetFont();
            font.SetUnderlined(false);
            m_hms_content->SetFont(font);
            Layout();
            SetCursor(wxCURSOR_ARROW);
        }
        });
    m_hms_content->Bind(wxEVT_LEFT_UP, [this](wxMouseEvent& e) {
            wxCommandEvent evt(EVT_ALREADY_READ_HMS);
            evt.SetString(long_error_code);
            if (MonitorPanel* monitor = MonitorPanel::if_built())
                wxPostEvent(monitor, evt);

            if (!m_url.empty()) wxLaunchDefaultBrowser(m_url);
        });
#endif

    wxGetApp().UpdateDarkUIWin(this);
}
HMSNotifyItem ::~HMSNotifyItem() {
    ;
}

void HMSNotifyItem::init_bitmaps() {
    m_img_notify_lv1 = create_scaled_bitmap("hms_notify_lv1", nullptr, 18);
    m_img_notify_lv2 = create_scaled_bitmap("hms_notify_lv2", nullptr, 18);
    m_img_notify_lv3 = create_scaled_bitmap("hms_notify_lv3", nullptr, 18);
    m_img_arrow      = create_scaled_bitmap("hms_arrow", nullptr, 14);
}

wxBitmap & HMSNotifyItem::get_notify_bitmap()
{
    switch (m_hms_item.get_level()) {
        case (HMS_FATAL):
            return m_img_notify_lv1;
            break;
        case (HMS_SERIOUS):
            return m_img_notify_lv2;
            break;
        case (HMS_COMMON):
            return m_img_notify_lv3;
            break;
        case (HMS_INFO):
            //return m_img_notify_lv4;
            break;
        default: break;
    }
    return wxNullBitmap;
}

AgentLogPanel::AgentLogPanel(wxWindow* parent)
    : wxPanel(parent, wxID_ANY), m_alive(std::make_shared<std::atomic<bool>>(true))
{
    SetBackgroundColour(*wxWHITE);
    auto sizer = new wxBoxSizer(wxVERTICAL);

    auto problems_bar = new wxBoxSizer(wxHORIZONTAL);
    m_problems_title  = new Label(this, Label::Head_14);
    m_clear           = new Button(this, _L("Clear"));
    m_clear->SetStyle(ButtonStyle::Regular, ButtonType::Window);
    m_download = new Button(this, wxEmptyString);
    m_download->SetStyle(ButtonStyle::Regular, ButtonType::Window);
    problems_bar->Add(m_problems_title, 0, wxALIGN_CENTER_VERTICAL);
    problems_bar->AddStretchSpacer();
    problems_bar->Add(m_clear, 0, wxALIGN_CENTER_VERTICAL | wxLEFT, FromDIP(8));
    problems_bar->Add(m_download, 0, wxALIGN_CENTER_VERTICAL | wxLEFT, FromDIP(8));
    sizer->Add(problems_bar, 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(20));

    m_problems_list = new wxScrolledWindow(this, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxVSCROLL);
    m_problems_list->SetBackgroundColour(*wxWHITE);
    m_problems_list->SetScrollRate(0, FromDIP(10));
    m_problems_sizer = new wxBoxSizer(wxVERTICAL);
    m_problems_list->SetSizer(m_problems_sizer);
    sizer->Add(m_problems_list, 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(10));

    auto log_bar = new wxBoxSizer(wxHORIZONTAL);
    m_log_title  = new Label(this, Label::Head_14);
    m_debug      = new ::CheckBox(this);
    log_bar->Add(m_log_title, 0, wxALIGN_CENTER_VERTICAL);
    log_bar->AddStretchSpacer();
    log_bar->Add(m_debug, 0, wxALIGN_CENTER_VERTICAL);
    log_bar->Add(new Label(this, Label::Body_13, _L("Debug lines")), 0, wxALIGN_CENTER_VERTICAL | wxLEFT, FromDIP(4));
    sizer->Add(log_bar, 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(20));

    m_log = new wxTextCtrl(this, wxID_ANY, wxEmptyString, wxDefaultPosition, wxDefaultSize,
                           wxTE_MULTILINE | wxTE_READONLY | wxTE_RICH2 | wxTE_DONTWRAP);
    m_log->SetFont(wxGetApp().code_font());
    sizer->Add(m_log, 1, wxEXPAND | wxALL, FromDIP(20));
    SetSizer(sizer);

    m_clear->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) {
        DeviceManager* dev = wxGetApp().getDeviceManager();
        MachineObject* obj = dev ? dev->get_selected_machine() : nullptr;
        if (obj && obj->get_dev_id() == m_dev_id)
            obj->command_clear_agent_log();
    });
    m_download->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { download(); });
    m_debug->Bind(wxEVT_TOGGLEBUTTON, [this](wxCommandEvent& e) {
        e.Skip(); // the check box redraws itself
        show_lines();
    });
    show_problems({});
}

AgentLogPanel::~AgentLogPanel() { *m_alive = false; }

void AgentLogPanel::update(MachineObject* obj)
{
    const MachineObject::AgentLog& log = obj->agent_log;
    if (m_name != log.name) {
        m_name = log.name;
        // TRN %1% is a log file's name, such as AFC.log
        m_problems_title->SetLabel(format_wxstr(_L("Errors and warnings in %1%"), m_name));
        // TRN %1% is a log file's name, such as AFC.log
        m_download->SetLabel(format_wxstr(_L("Download %1%"), m_name));
        m_log_title->SetLabel(from_u8(m_name));
        Layout();
    }
    m_dev_id = obj->get_dev_id();
    std::string base = obj->file_url;
    while (!base.empty() && base.back() == '/')
        base.pop_back();
    m_url = base.empty() || log.file.empty() ? std::string() : base + "/server/files/" + log.file;
    // The access code is the printer preset's API key, 88888888 for none (GUI_App::select_machine).
    const std::string code = obj->get_access_code();
    m_api_key              = code == "88888888" ? std::string() : code;
    m_download->Enable(!m_url.empty());

    const auto same = [](const MachineObject::AgentLogProblem& a, const MachineObject::AgentLogProblem& b) {
        return a.error == b.error && a.time == b.time && a.text == b.text && a.count == b.count;
    };
    if (!std::equal(m_problems.begin(), m_problems.end(), log.problems.begin(), log.problems.end(), same))
        show_problems(log.problems);
    // Kept as they are while the log has the focus, so it can be read and copied.
    if (m_lines != log.lines && !m_log->HasFocus()) {
        m_lines = log.lines;
        show_lines();
    }
    if (IsShownOnScreen())
        m_read = m_newest;
}

bool AgentLogPanel::has_unread() const { return !m_newest.empty() && m_newest != m_read; }

void AgentLogPanel::show_problems(const std::vector<MachineObject::AgentLogProblem>& problems)
{
    m_problems = problems;
    m_newest   = problems.empty() ? std::string() : problems.front().time + " " + problems.front().text;
    const wxColour grey(144, 144, 144);
    m_problems_list->Freeze();
    m_problems_sizer->Clear(true);
    if (problems.empty()) {
        auto none = new Label(m_problems_list, Label::Body_13, _L("No errors or warnings since the last Clear."));
        none->SetForegroundColour(grey);
        m_problems_sizer->Add(none, 0, wxALL, FromDIP(8));
    }
    for (const MachineObject::AgentLogProblem& problem : problems) {
        auto row  = new wxBoxSizer(wxHORIZONTAL);
        auto icon = new wxStaticBitmap(m_problems_list, wxID_ANY,
                                       create_scaled_bitmap(problem.error ? "hms_notify_lv1" : "hms_notify_lv3", this, 18));
        auto time = new Label(m_problems_list, Label::Body_13, from_u8(problem.time));
        time->SetForegroundColour(grey);
        auto text = new Label(m_problems_list, Label::Body_13, from_u8(problem.text));
        text->Wrap(HMS_NOTIFY_ITEM_TEXT_SIZE.GetX() - FromDIP(140));
        row->Add(icon, 0, wxALIGN_TOP);
        row->Add(time, 0, wxALIGN_TOP | wxLEFT, FromDIP(8));
        row->Add(text, 1, wxALIGN_TOP | wxLEFT, FromDIP(12));
        if (problem.count > 1) {
            // How many times it was logged.
            auto count = new Label(m_problems_list, Label::Body_13, wxString::Format(L"\u00D7%d", problem.count));
            count->SetForegroundColour(grey);
            row->Add(count, 0, wxALIGN_TOP | wxLEFT, FromDIP(12));
        }
        m_problems_sizer->Add(row, 0, wxEXPAND | wxLEFT | wxRIGHT | wxBOTTOM, FromDIP(8));
    }
    m_problems_list->SetMinSize(wxSize(-1, std::min(m_problems_sizer->GetMinSize().y, FromDIP(220))));
    m_problems_list->FitInside();
    wxGetApp().UpdateDarkUIWin(m_problems_list);
    m_problems_list->Thaw();
    Layout();
}

void AgentLogPanel::show_lines()
{
    const bool     debug  = m_debug->GetValue();
    const wxColour normal = m_log->GetForegroundColour();
    m_log->Freeze();
    m_log->Clear();
    for (const auto& [level, text] : m_lines) {
        if (level == 'd' && !debug)
            continue;
        const wxColour colour = level == 'e' ? wxColour("#E14747") :
                                level == 'w' ? wxColour("#FF6F00") :
                                level == 'd' ? wxColour(144, 144, 144) : normal;
        m_log->SetDefaultStyle(wxTextAttr(colour));
        m_log->AppendText(from_u8(text) + "\n");
    }
    m_log->ShowPosition(m_log->GetLastPosition());
    m_log->Thaw();
}

void AgentLogPanel::download()
{
    if (m_url.empty())
        return;
    // TRN %1% is a log file's name, such as AFC.log
    wxFileDialog dialog(this, format_wxstr(_L("Save %1%"), m_name), wxEmptyString, from_u8(m_name), "*.log",
                        wxFD_SAVE | wxFD_OVERWRITE_PROMPT);
    if (dialog.ShowModal() != wxID_OK)
        return;
    const std::string path = into_u8(dialog.GetPath());
    const std::string name = m_name;
    auto              alive = m_alive;
    // A failure is shown on the UI thread, where the panel may be gone by then.
    auto done = [this, alive, name](bool saved, std::string error) {
        if (saved)
            return;
        wxGetApp().CallAfter([this, alive, name, error] {
            if (*alive)
                // TRN %1% is a log file's name, such as AFC.log; %2% says why it failed
                show_error(this, format_wxstr(_L("Could not download %1%: %2%"), name, error));
        });
    };
    Http http = Http::get(m_url);
    if (!m_api_key.empty())
        http.header("X-Api-Key", m_api_key);
    http.timeout_connect(5)
        .timeout_max(120)
        .on_complete([path, done](std::string body, unsigned) {
            boost::nowide::ofstream file(path, std::ios::binary | std::ios::trunc);
            file.write(body.data(), std::streamsize(body.size()));
            file.close();
            done(!file.fail(), file.fail() ? "the file could not be written" : "");
        })
        .on_error([done](std::string, std::string error, unsigned status) {
            done(false, error.empty() ? "HTTP " + std::to_string(status) : error);
        })
        .perform();
}

HMSPanel::HMSPanel(wxWindow *parent, wxWindowID id, const wxPoint &pos, const wxSize &size, long style)
    :wxPanel(parent, id, pos, size, style)
{
    this->SetBackgroundColour(wxColour(238, 238, 238));

    auto m_main_sizer = new wxBoxSizer(wxVERTICAL);

    m_scrolledWindow = new wxScrolledWindow(this, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxVSCROLL);
    m_scrolledWindow->SetBackgroundColour(*wxWHITE);
    m_scrolledWindow->SetScrollRate(5, FromDIP(20));

    m_top_sizer = new wxBoxSizer(wxVERTICAL);

    m_top_sizer->AddSpacer(FromDIP(30));

    m_scrolledWindow->SetSizerAndFit(m_top_sizer);

    // Orca: a printer agent's log, shown while the agent sends one
    m_agent_log = new AgentLogPanel(this);
    m_agent_log->Hide();
    m_main_sizer->Add(m_agent_log, 1, wxEXPAND, 0);
    m_main_sizer->Add(m_scrolledWindow, 1, wxEXPAND, 0);

    this->SetSizerAndFit(m_main_sizer);

    Layout();

    wxGetApp().UpdateDarkUIWin(this);
}

HMSPanel::~HMSPanel() {
    ;
}

void HMSPanel::append_hms_panel(const std::string& dev_id, DevHMSItem& item) {
    wxString msg = wxGetApp().get_hms_query()->query_hms_msg(dev_id, item.get_long_error_code());
    if (!msg.empty()) {
        HMSNotifyItem *notify_item = new HMSNotifyItem(dev_id, m_scrolledWindow, item);
        m_top_sizer->Add(notify_item, 0, wxALIGN_CENTER_HORIZONTAL);
    } else {
        // debug for hms display error info
        // m_top_sizer->Add(m_notify_item, 0, wxALIGN_CENTER_HORIZONTAL);
        BOOST_LOG_TRIVIAL(info) << "hms: do not display empty_item";
    }
}

void HMSPanel::delete_hms_panels() {
    m_scrolledWindow->DestroyChildren();
}

void HMSPanel::clear_hms_tag()
{
    temp_hms_list.clear();
}

void HMSPanel::update(MachineObject *obj)
{
    if (obj) {
        this->Freeze();
        delete_hms_panels();
        wxString hms_text;
        for (auto item : obj->GetHMS()->GetHMSItems()) {
            if (wxGetApp().get_hms_query()) {

                auto key = item.get_long_error_code();
                auto iter = temp_hms_list.find(key);
                if (iter == temp_hms_list.end()) {
                    temp_hms_list[key] = item;
                }

                append_hms_panel(obj->get_dev_id(), item);
            }
        }

        for (auto it = temp_hms_list.begin(); it != temp_hms_list.end(); ) {
            auto key = it->second.get_long_error_code();
            bool inr = false;
            for (auto hms : obj->GetHMS()->GetHMSItems()) {
                if (hms.get_long_error_code() == key) {
                    inr = true;
                    break;
                }
            }

            if (!inr) {
                it = temp_hms_list.erase(it);
            }
            else {
                ++it;
            }
        }

        // Orca: a printer agent's log, with Bambu's list only when that has items
        const bool agent_log = !obj->agent_log.name.empty();
        if (agent_log)
            m_agent_log->update(obj);
        m_agent_log->Show(agent_log);
        m_scrolledWindow->Show(!agent_log || !obj->GetHMS()->GetHMSItems().empty());

        Layout();
        this->Thaw();
    } else {
        delete_hms_panels();
        m_agent_log->Hide();
        m_scrolledWindow->Show();
        Layout();
    }
}

void HMSPanel::show_status(int status)
{
    if (last_status == status) return;
    last_status = status;

    if (((status & (int)MonitorStatus::MONITOR_DISCONNECTED) != 0)
        || ((status & (int)MonitorStatus::MONITOR_DISCONNECTED_SERVER) != 0)
        || ((status & (int)MonitorStatus::MONITOR_CONNECTING) != 0)
        || ((status & (int)MonitorStatus::MONITOR_NO_PRINTER) != 0)
        ) {
        delete_hms_panels();
        m_agent_log->Hide();
        m_scrolledWindow->Show();
        Layout();
    }
}

bool HMSPanel::Show(bool show)
{
    return wxPanel::Show(show);
}

}}
