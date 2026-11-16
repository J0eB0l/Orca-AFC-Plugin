#include "AgentInstallerDialog.hpp"

#include "GUI.hpp"
#include "GUI_App.hpp"
#include "GUI_Utils.hpp"
#include "I18N.hpp"
#include "MainFrame.hpp"
#include "MsgDialog.hpp"
#include "DeviceManager.hpp"
#include "format.hpp"
#include "DeviceCore/DevManager.h"
#include "Widgets/Button.hpp"
#include "Widgets/DialogButtons.hpp"
#include "Widgets/Label.hpp"
#include "Widgets/TextInput.hpp"

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <string>
#include <utility>
#include <vector>
#include <wx/colour.h>
#include <wx/defs.h>
#include <wx/event.h>
#include <wx/gdicmn.h>
#include <wx/sizer.h>
#include <wx/stattext.h>
#include <wx/string.h>
#include <wx/textctrl.h>
#include <wx/timer.h>

namespace Slic3r { namespace GUI {

static constexpr int    REFRESH_MS  = 250;
static constexpr auto   RENEW       = std::chrono::seconds(5); // the agent's watch lasts 15 s
static constexpr int    COLUMNS     = 100;                     // the script's terminal, as AFC opens it
static constexpr int    ROWS        = 28;
static constexpr size_t LABEL_CHARS = 48;                      // a choice's button shows this much of its label
// A terminal's colors, in either theme: the script's own colors are picked for a dark terminal.
static const wxColour TERMINAL_BG(30, 30, 30);
static const wxColour TERMINAL_TEXT(212, 212, 212);
static const wxColour FAILED_CLR(255, 111, 0);

void AgentInstallerDialog::show(const std::string& dev_id, const std::string& mode, bool start)
{
    DeviceManager* manager = wxGetApp().getDeviceManager();
    MachineObject* obj     = manager ? manager->get_selected_machine() : nullptr;
    if (!obj || obj->get_dev_id() != dev_id)
        return;
    if (start) {
        // The last session's screen goes until the agent sends the new one.
        MachineObject::AgentInstaller& installer = obj->agent_installer;
        installer.lines.clear();
        installer.choices.clear();
        installer.result.clear();
        installer.ended.clear();
        installer.error.clear();
        installer.hint.clear();
        installer.exit_code = -1;
        installer.starting  = true;
        installer.mode      = mode;
    }
    AgentInstallerDialog dlg(wxGetApp().mainframe, dev_id, mode);
    // The start goes before the watch, so the first screen the window gets is the new session's.
    if (start)
        obj->command_agent_installer_start(mode);
    obj->command_agent_installer_watch(true);
    dlg.ShowModal();
    if (MachineObject* still = dlg.machine())
        still->command_agent_installer_watch(false);
}

AgentInstallerDialog::AgentInstallerDialog(wxWindow* parent, const std::string& dev_id, const std::string& mode)
    : DPIDialog(parent, wxID_ANY, mode == "install" ? _L("AFC installer") : _L("AFC updater"), wxDefaultPosition,
                wxDefaultSize, wxCAPTION | wxCLOSE_BOX | wxRESIZE_BORDER)
    , m_dev_id(dev_id)
    , m_renew_at(Clock::now() + RENEW)
    , m_timer(this)
{
    SetBackgroundColour(*wxWHITE);
    SetFont(Label::Body_14);

    auto* sizer = new wxBoxSizer(wxVERTICAL);
    m_status = new wxStaticText(this, wxID_ANY, _L("Waiting for the printer..."));
    m_status->SetFont(Label::Head_14);
    sizer->Add(m_status, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(20));
    m_hint = new wxStaticText(this, wxID_ANY, wxEmptyString);
    m_hint->SetFont(Label::Body_13);
    m_hint->Hide();
    sizer->Add(m_hint, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(6));

    m_screen = new wxTextCtrl(this, wxID_ANY, wxEmptyString, wxDefaultPosition, wxDefaultSize,
                              wxTE_MULTILINE | wxTE_READONLY | wxTE_RICH2 | wxTE_DONTWRAP);
    m_screen->SetFont(wxGetApp().code_font());
    const wxSize cell = m_screen->GetTextExtent("0");
    m_screen->SetMinSize(wxSize(cell.x * COLUMNS + FromDIP(30), cell.y * ROWS + FromDIP(10)));
    sizer->Add(m_screen, 1, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(10));

    m_choice_sizer = new wxBoxSizer(wxVERTICAL);
    sizer->Add(m_choice_sizer, 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(10));

    auto* answer_row = new wxBoxSizer(wxHORIZONTAL);
    // TRN Before the box where an answer to AFC's updater or installer is typed
    auto* answer_label = new wxStaticText(this, wxID_ANY, _L("Answer:"));
    m_answer = new ::TextInput(this, wxEmptyString, wxEmptyString, wxEmptyString, wxDefaultPosition,
                               wxSize(-1, FromDIP(32)), wxTE_PROCESS_ENTER);
    m_answer->Bind(wxEVT_TEXT_ENTER, [this](wxCommandEvent&) { CallAfter([this] { send_typed(); }); });
    // TRN Sends the typed answer, and Enter, to AFC's updater or installer
    m_send = new Button(this, _L("Send"));
    m_send->SetStyle(ButtonStyle::Confirm, ButtonType::Choice);
    m_send->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { CallAfter([this] { send_typed(); }); });
    answer_row->Add(answer_label, 0, wxALIGN_CENTER_VERTICAL | wxRIGHT, FromDIP(10));
    answer_row->Add(m_answer, 1, wxALIGN_CENTER_VERTICAL);
    answer_row->Add(m_send, 0, wxALIGN_CENTER_VERTICAL | wxLEFT, FromDIP(10));
    sizer->Add(answer_row, 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(10));

    m_buttons = new DialogButtons(this, {L("Stop"), L("Close")}, "", 1);
    m_buttons->GetButtonFromIndex(0)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { CallAfter([this] { stop(); }); });
    m_buttons->GetButtonFromIndex(1)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { EndModal(wxID_CLOSE); });
    sizer->Add(m_buttons, 0, wxEXPAND | wxTOP, FromDIP(10));

    enable(false, false);
    SetSizerAndFit(sizer);
    CenterOnParent();
    wxGetApp().UpdateDlgDarkUI(this);
    m_text_color = m_status->GetForegroundColour();
    style_screen();

    Bind(wxEVT_TIMER, &AgentInstallerDialog::on_timer, this);
    m_timer.Start(REFRESH_MS);
}

AgentInstallerDialog::~AgentInstallerDialog() { m_timer.Stop(); }

MachineObject* AgentInstallerDialog::machine() const
{
    DeviceManager* manager = wxGetApp().getDeviceManager();
    MachineObject* obj     = manager ? manager->get_selected_machine() : nullptr;
    return obj && obj->get_dev_id() == m_dev_id ? obj : nullptr;
}

void AgentInstallerDialog::on_timer(wxTimerEvent&)
{
    MachineObject* obj = machine();
    if (!obj) { // another printer was picked, or this one went away
        if (!m_gone) {
            m_gone = true;
            enable(false, false);
            draw_status(_L("The printer is no longer selected."), true, wxEmptyString);
        }
        return;
    }
    if (Clock::now() >= m_renew_at) {
        obj->command_agent_installer_watch(true);
        m_renew_at = Clock::now() + RENEW;
    }
    const bool connected = obj->is_connected();
    if (!m_drawn || m_gone || obj->agent_installer.screens != m_screens || connected != m_connected) {
        m_drawn     = true;
        m_gone      = false;
        m_screens   = obj->agent_installer.screens;
        m_connected = connected;
        draw(*obj);
    }
}

void AgentInstallerDialog::draw(const MachineObject& obj)
{
    const MachineObject::AgentInstaller& installer = obj.agent_installer;
    if (!installer.mode.empty())
        SetTitle(installer.mode == "install" ? _L("AFC installer") : _L("AFC updater"));

    wxString status;
    bool     failed = false;
    if (!m_connected) {
        status = _L("The printer is not connected.");
        failed = true;
    } else if (!installer.error.empty()) {
        status = from_u8(installer.error);
        failed = true;
    } else if (installer.starting)
        status = _L("Starting on the printer...");
    else if (installer.away)
        status = _L("Klipper is not answering. This is the last of what the script showed.");
    else if (installer.ended == "restarted")
        status = _L("Klipper restarted, which ended the script. This is the last of what it showed.");
    else if (installer.running)
        status = _L("Running on the printer.");
    else if (installer.result == "done")
        status = _L("The script finished.");
    else if (installer.result == "failed") {
        // TRN %1% is the exit status of AFC's updater or installer, such as 1
        status = format_wxstr(_L("The script ended with exit status %1%."), installer.exit_code);
        failed = true;
    } else if (installer.result == "stopped")
        status = _L("The script was stopped.");
    else if (!installer.available && !installer.why.empty()) {
        status = from_u8(installer.why);
        failed = true;
    } else
        status = _L("Waiting for the printer...");
    const wxString hint = installer.hint == "sudo" ?
                              _L("A step needed sudo, which asked for a password. No password can be typed here: "
                                 "run that step over SSH, or let this user run sudo without a password.") :
                              wxString();

    const bool live = m_connected && installer.running && !installer.away;
    draw_screen(installer);
    draw_choices(live ? installer.choices : Choices());
    draw_status(status, failed, hint);
    enable(live, m_connected && installer.running);
}

void AgentInstallerDialog::draw_screen(const MachineObject::AgentInstaller& installer)
{
    if (installer.lines == m_lines)
        return;
    m_lines = installer.lines;
    m_screen->Freeze();
    m_screen->Clear();
    for (size_t i = 0; i < m_lines.size(); ++i) {
        for (const MachineObject::AgentInstaller::Run& run : m_lines[i]) {
            const wxColour color = run.first.empty() ? TERMINAL_TEXT : wxColour(from_u8(run.first));
            m_screen->SetDefaultStyle(wxTextAttr(color.IsOk() ? color : TERMINAL_TEXT, TERMINAL_BG));
            m_screen->AppendText(from_u8(run.second));
        }
        if (i + 1 < m_lines.size())
            m_screen->AppendText("\n");
    }
    m_screen->ShowPosition(m_screen->GetLastPosition());
    m_screen->Thaw();
}

void AgentInstallerDialog::draw_choices(const Choices& choices)
{
    if (choices == m_choices)
        return;
    m_choices = choices;
    m_choice_sizer->Clear(true);
    m_choice_buttons.clear();
    // Rows of buttons as wide as the screen.
    const int   width = m_screen->GetSize().x;
    const int   gap   = FromDIP(8);
    wxBoxSizer* row   = nullptr;
    int         used  = 0;
    for (const std::pair<std::string, std::string>& choice : m_choices) {
        const wxString label = from_u8(choice.second);
        const wxString shown = label.length() > LABEL_CHARS ? label.Left(LABEL_CHARS - 3) + "..." : label;
        auto*          button = new Button(this, shown);
        button->SetStyle(ButtonStyle::Regular, ButtonType::Choice);
        if (shown != label)
            button->SetToolTip(label);
        button->Bind(wxEVT_BUTTON, [this, answer = choice.first](wxCommandEvent&) {
            CallAfter([this, answer] { send_answer(answer); });
        });
        wxGetApp().UpdateDarkUIWin(button);
        const int needs = button->GetBestSize().x + gap;
        if (!row || used + needs > width) {
            row = new wxBoxSizer(wxHORIZONTAL);
            m_choice_sizer->Add(row, 0, wxBOTTOM, gap);
            used = 0;
        }
        row->Add(button, 0, wxRIGHT, gap);
        used += needs;
        m_choice_buttons.push_back(button);
    }
    grow_to_fit();
}

void AgentInstallerDialog::draw_status(const wxString& status, bool failed, const wxString& hint)
{
    m_status->SetLabelText(status);
    m_status->SetForegroundColour(failed ? FAILED_CLR : m_text_color);
    m_status->Wrap(m_screen->GetSize().x);
    m_hint->SetLabelText(hint);
    m_hint->SetForegroundColour(FAILED_CLR);
    m_hint->Wrap(m_screen->GetSize().x);
    m_hint->Show(!hint.empty());
    grow_to_fit();
}

void AgentInstallerDialog::enable(bool answers, bool stoppable)
{
    m_answer->Enable(answers);
    m_send->Enable(answers);
    for (Button* button : m_choice_buttons)
        button->Enable(answers);
    m_buttons->GetButtonFromIndex(0)->Enable(stoppable);
}

void AgentInstallerDialog::send_answer(const std::string& text)
{
    MachineObject* obj = machine();
    if (!obj || !m_answer->IsEnabled())
        return;
    obj->command_agent_installer_answer(text);
    // One answer per prompt: the controls come back with the next screen.
    enable(false, m_buttons->GetButtonFromIndex(0)->IsEnabled());
}

void AgentInstallerDialog::send_typed()
{
    const std::string text = into_u8(m_answer->GetTextCtrl()->GetValue());
    send_answer(text);
    m_answer->GetTextCtrl()->Clear();
}

void AgentInstallerDialog::stop()
{
    MessageDialog dlg(this, _L("Stop the script on the printer? It is interrupted as Ctrl+C would interrupt it."),
                      _L("Stop"), wxYES_NO | wxICON_QUESTION);
    if (dlg.ShowModal() != wxID_YES)
        return;
    if (MachineObject* obj = machine())
        obj->command_agent_installer_stop();
}

void AgentInstallerDialog::style_screen()
{
    m_screen->SetBackgroundColour(TERMINAL_BG);
    m_screen->SetForegroundColour(TERMINAL_TEXT);
    m_screen->SetDefaultStyle(wxTextAttr(TERMINAL_TEXT, TERMINAL_BG));
    m_screen->Refresh();
}

void AgentInstallerDialog::grow_to_fit()
{
    Layout();
    const wxSize needs = ClientToWindowSize(GetSizer()->GetMinSize());
    const wxSize has   = GetSize();
    SetMinSize(needs);
    if (needs.x > has.x || needs.y > has.y)
        SetSize(wxSize(std::max(needs.x, has.x), std::max(needs.y, has.y)));
    Layout();
}

void AgentInstallerDialog::on_dpi_changed(const wxRect&)
{
    m_answer->Rescale();
    m_send->Rescale();
    const wxSize cell = m_screen->GetTextExtent("0");
    m_screen->SetMinSize(wxSize(cell.x * COLUMNS + FromDIP(30), cell.y * ROWS + FromDIP(10)));
    // The choices' rows were laid out for the old size.
    const Choices choices = m_choices;
    m_choices.clear();
    draw_choices(choices);
    GetSizer()->SetSizeHints(this);
    Refresh();
}

}} // namespace Slic3r::GUI
