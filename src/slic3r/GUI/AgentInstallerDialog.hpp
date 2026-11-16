#pragma once

#include "GUI_Utils.hpp"
#include "DeviceManager.hpp"

#include <chrono>
#include <string>
#include <utility>
#include <vector>
#include <wx/colour.h>
#include <wx/gdicmn.h>
#include <wx/string.h>
#include <wx/timer.h>

class Button;
class TextInput;
class wxBoxSizer;
class wxStaticText;
class wxTimerEvent;

namespace Slic3r { namespace GUI {

class DialogButtons;
class InstallerScreen;

// Orca: AFC's updater or installer, which a printer agent's AFC runs on the printer for this window
// (MachineObject::AgentInstaller): the script's screen as a terminal shows it (InstallerScreen), the answers
// its prompt takes as buttons, a box for any other answer, and Stop. The window watches the session while it
// is open and renews the watch, which the agent lets lapse otherwise. Closing it leaves the script running,
// and the Update page's AFC row opens it again.
class AgentInstallerDialog : public DPIDialog
{
public:
    // Opens the window for the selected printer `dev_id`: a new session in `mode` ("update" or "install")
    // when `start`, else the one that runs.
    static void show(const std::string& dev_id, const std::string& mode, bool start);

protected:
    void on_dpi_changed(const wxRect& suggested_rect) override;

private:
    using Clock   = std::chrono::steady_clock;
    using Choices = std::vector<std::pair<std::string, std::string>>;

    AgentInstallerDialog(wxWindow* parent, const std::string& dev_id, const std::string& mode);
    ~AgentInstallerDialog() override;

    // The printer the window was opened for, nullptr once it is no longer the selected one.
    MachineObject* machine() const;
    void           on_timer(wxTimerEvent& event);
    void           draw(const MachineObject& obj);
    void           draw_choices(const Choices& choices);
    void           draw_status(const wxString& status, bool failed, const wxString& hint);
    // The answer box and the choices take input while `answers`, Stop while `stoppable`.
    void           enable(bool answers, bool stoppable);
    void           send_answer(const std::string& text);
    void           send_typed();
    void           stop();
    // Grows the window to fit what it shows, never shrinking it, so it does not jump between screens.
    void           grow_to_fit();

    std::string          m_dev_id;
    unsigned             m_screens {0};
    bool                 m_drawn {false};
    bool                 m_gone {false};
    bool                 m_connected {false};
    Clock::time_point    m_renew_at;
    Choices              m_choices;
    wxColour             m_text_color;
    wxStaticText*        m_status {nullptr};
    wxStaticText*        m_hint {nullptr};
    InstallerScreen*     m_screen {nullptr};
    wxBoxSizer*          m_choice_sizer {nullptr};
    std::vector<Button*> m_choice_buttons;
    ::TextInput*         m_answer {nullptr};
    Button*              m_send {nullptr};
    DialogButtons*       m_buttons {nullptr};
    wxTimer              m_timer;
};

}} // namespace Slic3r::GUI
