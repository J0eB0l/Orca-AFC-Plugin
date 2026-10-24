#pragma once

#include "GUI_Utils.hpp"
#include "DeviceManager.hpp"

#include <chrono>
#include <map>
#include <string>
#include <utility>
#include <vector>
#include <wx/gdicmn.h>
#include <wx/string.h>
#include <wx/timer.h>

class ComboBox;
class CheckBox;
class wxBoxSizer;
class wxScrolledWindow;
class wxTimerEvent;
class wxWindow;

namespace Slic3r { namespace GUI {

class DialogButtons;

// Orca: a printer agent's runout lanes (AFC's infinite spool), as the tray popup's "When it runs out" row and
// the AFC Auto Refill dialog show them. A lane that runs out mid-print hands over to its runout lane, which the
// printer loads and prints on; "" pauses the print. The agent works out which lanes hold the same filament.
namespace runout_lanes {

using Lane  = MachineObject::AgentLane;
using Lanes = std::vector<Lane>;

const Lane* find(const Lanes& lanes, const std::string& name);
// The lane's color as get_extruder_color_icon() takes it, a light gray for an empty or unknown one.
std::string swatch(const Lane& lane);
// "lane2 · PLA · 977 g", with the extruder the lane feeds when it is not the one `from` feeds.
wxString describe(const Lane& lane, const Lane* from = nullptr);
// What the printer does when `lane` runs out onto `to` ("" to pause).
wxString outcome(const Lanes& lanes, const Lane& lane, const std::string& to);
// Why `to` is a poor runout lane for `lane` (empty, another material or another color), "" when it is not.
wxString warning(const Lanes& lanes, const Lane& lane, const std::string& to);
// True when `lane` running out onto `to` would lead back round to `lane`.
bool loops(const Lanes& lanes, const Lane& lane, const std::string& to);
// The choices for `lane`: Pause the print, then the lanes with the same filament, the other loaded lanes and
// the empty lanes, each one's name in its alias. Lanes that would loop back are greyed out. Selects `selected`.
void fill(::ComboBox* combo, const Lanes& lanes, const Lane& lane, const std::string& selected, int icon_size);
// The lane a choice of fill() stands for, "" to pause.
std::string selected(::ComboBox* combo);

} // namespace runout_lanes

// Orca: the Auto Refill dialog of a printer agent that takes runout lanes (is_support_runout_lane), in place of
// Bambu's refill groups: the AMS filament backup switch, every chain of runout lanes with what it holds and its
// poor matches, and a runout lane choice for each lane. Every change goes to the printer at once.
class RunoutLanesDialog : public DPIDialog
{
public:
    RunoutLanesDialog(wxWindow* parent, MachineObject* obj);
    ~RunoutLanesDialog() override;

    // Shows the dialog for `obj` when it is such a printer agent; false for any other printer.
    static bool show_for(wxWindow* parent, MachineObject* obj);

protected:
    void on_dpi_changed(const wxRect& suggested_rect) override;

private:
    using Clock = std::chrono::steady_clock;

    // The printer the dialog was opened for, nullptr once it is no longer the selected one.
    MachineObject*     machine() const;
    // The printer's lanes, with the runout lanes picked here in place until the printer reports them.
    runout_lanes::Lanes lanes(const MachineObject& obj) const;
    void on_timer(wxTimerEvent& event);
    void rebuild(const MachineObject& obj);
    void add_chains(const runout_lanes::Lanes& lanes);
    void add_lanes(const runout_lanes::Lanes& lanes);
    void set_runout(const std::string& lane, const std::string& runout);
    void send_all(const std::string& action);

    std::string              m_dev_id;
    std::string              m_shown; // what the lists show, to rebuild them when the printer reports a change
    wxTimer                  m_timer;
    ::CheckBox*              m_backup{nullptr};
    wxScrolledWindow*        m_scroll{nullptr};
    wxBoxSizer*              m_body{nullptr};
    DialogButtons*           m_buttons{nullptr};
    std::vector<::ComboBox*> m_combos;
    // Runout lanes picked here, kept in place until the printer reports them or a few seconds pass.
    std::map<std::string, std::pair<std::string, Clock::time_point>> m_held;
};

}} // namespace Slic3r::GUI
