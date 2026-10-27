#pragma once

#include "GUI_Utils.hpp"
#include "DeviceManager.hpp"
#include "libslic3r/AFCLayout.hpp"

#include <nlohmann/json.hpp>
#include <string>
#include <vector>
#include <wx/gdicmn.h>
#include <wx/string.h>

class wxListBox;
class wxWindow;

namespace Slic3r {

class Print;

namespace GUI {

// Orca: the suggested spool layout of an AFC printer with several toolheads (docs/HLSD/afc-spool-layout.md).
namespace afc_layout {

struct Suggestion
{
    std::vector<MachineObject::AgentLane> agent_lanes; // the printer's lanes when it was suggested
    std::vector<AFCLayout::Lane>          lanes;       // the same lanes, for AFCLayout
    std::vector<int>                      current;     // per filament, the toolhead it prints on now (-1 not printed)
    std::vector<int>                      suggested;   // ... and with the layout
    AFCLayout::Changes                    now, after;
    AFCLayout::Plan                       plan;
};

// The layout for the plate's print on the printer's lanes, with its units ranked after `preferred`
// (afc_unit_order); false with the reason in `why` when there is none to suggest.
bool suggest(const Print& print, const MachineObject& obj, const std::vector<std::string>& preferred, Suggestion& out, wxString& why);

// The moves Sync layout to AFC sends (MachineObject::command_afc_layout): each lane a spool goes to, the
// T number it keeps, what its old lane knew of the spool, and what the lane itself knew.
nlohmann::json moves_json(const Suggestion& suggestion, const Print& print);

} // namespace afc_layout

// The spools to move for the suggested layout, and Sync layout to AFC, which writes the moved spools' lanes
// back to AFC and slices the plate again with each filament on its new toolhead.
class AFCLayoutDialog : public DPIDialog
{
public:
    // For the current plate and the selected printer; tells why when there is nothing to suggest.
    static void show(wxWindow* parent);

private:
    AFCLayoutDialog(wxWindow* parent, const Print& print, const MachineObject& obj, afc_layout::Suggestion suggestion);
    void sync();
    void on_dpi_changed(const wxRect& suggested_rect) override;

    const Print&           m_print;
    std::string            m_dev_id;
    afc_layout::Suggestion m_suggestion;
};

// Orca: the Preferred units printer setting (afc_unit_order): the saved order, then the selected printer's
// other AFC units quickest first, moved up and down.
class AFCUnitOrderDialog : public DPIDialog
{
public:
    AFCUnitOrderDialog(wxWindow* parent, const std::vector<std::string>& order);
    std::vector<std::string> order() const { return m_order; }

private:
    void refill(int selection);
    void move(int by);
    void on_dpi_changed(const wxRect& suggested_rect) override;

    std::vector<std::string>              m_order;
    std::vector<MachineObject::AgentUnit> m_units; // the printer's, for their types and times
    wxListBox*                            m_list{nullptr};
};

} // namespace GUI
} // namespace Slic3r
