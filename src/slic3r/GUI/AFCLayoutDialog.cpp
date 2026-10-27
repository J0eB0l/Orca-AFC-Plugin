#include "AFCLayoutDialog.hpp"

#include "GUI.hpp"
#include "GUI_App.hpp"
#include "I18N.hpp"
#include "MainFrame.hpp"
#include "MsgDialog.hpp"
#include "PartPlate.hpp"
#include "Plater.hpp"
#include "format.hpp"
#include "wxExtensions.hpp"
#include "DeviceCore/DevFilaSystem.h"
#include "DeviceCore/DevManager.h"
#include "Widgets/Button.hpp"
#include "Widgets/DialogButtons.hpp"
#include "Widgets/Label.hpp"
#include "libslic3r/GCode/ToolOrdering.hpp"
#include "libslic3r/Preset.hpp"
#include "libslic3r/PresetBundle.hpp"
#include "libslic3r/Print.hpp"
#include "libslic3r/PrintConfig.hpp"

#include <algorithm>
#include <boost/algorithm/string/predicate.hpp>
#include <cstddef>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>
#include <wx/bitmap.h>
#include <wx/colour.h>
#include <wx/defs.h>
#include <wx/event.h>
#include <wx/listbox.h>
#include <wx/utils.h>
#include <wx/sizer.h>
#include <wx/statbmp.h>
#include <wx/window.h>

namespace Slic3r { namespace GUI {

namespace afc_layout {

namespace {

// A spool move by hand, in seconds of lane changes it is worth.
constexpr double MOVE_SECONDS = 60.;
// The lane change time of a unit the printer gave none for.
constexpr double UNKNOWN_SECONDS = 75.;

// "#RRGGBB" in upper case from "RRGGBBAA" or "#rrggbb", "" when it is neither.
std::string rgb(const std::string& color)
{
    std::string hex = boost::starts_with(color, "#") ? color.substr(1) : color;
    if (hex.size() != 6 && hex.size() != 8)
        return std::string();
    hex.resize(6);
    std::transform(hex.begin(), hex.end(), hex.begin(), ::toupper);
    return "#" + hex;
}

bool same_filament(const MachineObject::AgentLane& lane, const PrintConfig& config, int filament)
{
    return rgb(lane.color) == rgb(config.filament_colour.get_at(filament)) &&
           boost::iequals(lane.type, config.filament_type.get_at(filament));
}

} // namespace

bool suggest(const Print& print, const MachineObject& obj, const std::vector<std::string>& preferred, Suggestion& out, wxString& why)
{
    const PrintConfig& config    = print.config();
    const size_t       heads     = config.nozzle_diameter.values.size();
    const size_t       filaments = config.filament_colour.values.size();
    if (heads < 2 || !config.multi_extruder_multi_material.value) {
        why = _L("A suggested layout needs a printer with several toolheads and Multi-material per toolhead turned on.");
        return false;
    }
    // The print's own tool ordering, or with a wipe tower the tower's.
    const ToolOrdering& ordering = print.tool_ordering().empty() ? print.get_tool_ordering() : print.tool_ordering();
    std::vector<std::vector<unsigned int>> layer_filaments;
    for (const LayerTools& layer : ordering)
        if (!layer.extruders.empty())
            layer_filaments.push_back(layer.extruders);
    if (layer_filaments.empty()) {
        why = _L("Slice the plate first. A plate printed by object has no suggested layout.");
        return false;
    }
    if (obj.agent_lanes.empty()) {
        why = _L("Connect to the AFC printer on the Device tab first, so its lanes are known.");
        return false;
    }

    out.agent_lanes = obj.agent_lanes;
    std::map<std::string, double> unit_seconds;
    {
        std::vector<std::string> names;
        std::vector<double>      seconds;
        for (const MachineObject::AgentUnit& unit : obj.agent_units) {
            names.push_back(unit.name);
            seconds.push_back(unit.seconds > 0. ? unit.seconds : UNKNOWN_SECONDS);
        }
        const std::vector<double> ranked = AFCLayout::rank_units(names, seconds, preferred);
        for (size_t i = 0; i < names.size(); ++i)
            unit_seconds[names[i]] = ranked[i];
    }
    // A lane holds filament n when it is mapped to T(n), as a filament sync leaves it, and holds the
    // same filament; else the first loaded lane with its color and material holds it.
    std::set<int> placed;
    out.lanes.clear();
    for (const MachineObject::AgentLane& agent : out.agent_lanes) {
        AFCLayout::Lane lane;
        lane.name     = agent.name;
        lane.unit     = agent.unit;
        lane.toolhead = agent.toolhead >= 0 && size_t(agent.toolhead) < heads ? agent.toolhead : -1;
        lane.loaded   = agent.loaded;
        auto seconds  = unit_seconds.find(agent.unit);
        lane.seconds  = seconds != unit_seconds.end() ? seconds->second : UNKNOWN_SECONDS;
        for (int tool : agent.tools)
            if (agent.loaded && tool >= 0 && size_t(tool) < filaments && !placed.count(tool) && same_filament(agent, config, tool)) {
                lane.filament = tool;
                placed.insert(tool);
                break;
            }
        out.lanes.push_back(lane);
    }
    for (size_t f = 0; f < filaments; ++f)
        for (size_t l = 0; l < out.lanes.size() && !placed.count(int(f)); ++l)
            if (out.lanes[l].loaded && out.lanes[l].filament < 0 && same_filament(out.agent_lanes[l], config, int(f))) {
                out.lanes[l].filament = int(f);
                placed.insert(int(f));
            }

    std::set<unsigned int> used;
    for (const auto& layer : layer_filaments)
        used.insert(layer.begin(), layer.end());
    const std::vector<int> maps = print.get_filament_maps();
    out.current.assign(filaments, -1);
    for (unsigned int f : used)
        if (f < filaments)
            out.current[f] = f < maps.size() ? maps[f] - 1 : 0;

    std::vector<int> lanes_per_head(heads, 0);
    for (const AFCLayout::Lane& lane : out.lanes)
        if (lane.toolhead >= 0)
            ++lanes_per_head[lane.toolhead];
    out.suggested = ToolOrdering::get_afc_toolhead_groups(&print, layer_filaments, lanes_per_head);
    if (out.suggested.size() != filaments)
        out.suggested = out.current;
    out.now   = AFCLayout::count_changes(layer_filaments, out.current);
    out.after = AFCLayout::count_changes(layer_filaments, out.suggested);
    out.suggested = AFCLayout::fit_toolheads(out.lanes, out.suggested, out.after.loads, MOVE_SECONDS, int(heads));
    // The grouping counts flushes; a toolhead's lane changes decide, and a tie keeps the spools where they are.
    if (out.after.lane_changes >= out.now.lane_changes) {
        out.suggested = out.current;
        out.after     = out.now;
    }
    out.plan = AFCLayout::place(out.lanes, out.suggested, out.after.loads, MOVE_SECONDS);
    return true;
}

nlohmann::json moves_json(const Suggestion& suggestion, const Print& print)
{
    const PrintConfig& config = print.config();
    nlohmann::json     moves  = nlohmann::json::array();
    for (const AFCLayout::Move& move : suggestion.plan.moves) {
        if (move.to < 0)
            continue;
        const MachineObject::AgentLane& to   = suggestion.agent_lanes[move.to];
        const MachineObject::AgentLane* from = move.from >= 0 ? &suggestion.agent_lanes[move.from] : nullptr;
        // The T number the spool prints with: its filament's, else the one its old lane had.
        const int tool = move.filament >= 0 ? move.filament : from && !from->tools.empty() ? from->tools.front() : -1;
        if (tool < 0)
            continue;
        nlohmann::json item = {{"lane", to.name}, {"tool", tool},
                               {"was", {{"spool_id", to.spool_id}, {"material", to.type}, {"color", rgb(to.color)}}}};
        if (from) {
            item["spool_id"]    = from->spool_id;
            item["material"]    = from->type;
            item["color"]       = rgb(from->color);
            item["weight"]      = from->remain_g;
            item["nozzle_temp"] = from->nozzle_temp;
            item["bed_temp"]    = from->bed_temp;
        } else {
            item["material"] = config.filament_type.get_at(move.filament);
            item["color"]    = rgb(config.filament_colour.get_at(move.filament));
        }
        moves.push_back(std::move(item));
    }
    return moves;
}

} // namespace afc_layout

namespace {

const char* const TEXT_COLOUR = "#323A3D";
const char* const HINT_COLOUR = "#6B6B6B";

Label* add_label(wxWindow* parent, const wxFont& font, const wxString& text, const wxColour& colour)
{
    auto* label = new Label(parent, font, text);
    label->SetForegroundColour(colour);
    label->SetBackgroundColour(parent->GetBackgroundColour());
    return label;
}

wxString lane_name(const MachineObject::AgentLane& lane)
{
    return from_u8(lane.name + " \xC2\xB7 " + lane.unit);
}

MachineObject* selected_machine()
{
    DeviceManager* dev = wxGetApp().getDeviceManager();
    return dev ? dev->get_selected_machine() : nullptr;
}

std::vector<std::string> preferred_units()
{
    const auto* order = wxGetApp().preset_bundle->printers.get_edited_preset().config.option<ConfigOptionStrings>("afc_unit_order");
    return order ? order->values : std::vector<std::string>();
}

} // namespace

void AFCLayoutDialog::show(wxWindow* parent)
{
    Plater* plater = wxGetApp().plater();
    if (plater == nullptr)
        return;
    PartPlate*             plate = plater->get_partplate_list().get_curr_plate();
    Print*                 print = plate ? plate->fff_print() : nullptr;
    MachineObject*         obj   = selected_machine();
    afc_layout::Suggestion suggestion;
    wxString               why;
    if (print == nullptr || !plate->is_slice_result_valid())
        why = _L("Slice the plate first. A plate printed by object has no suggested layout.");
    else if (obj == nullptr)
        why = _L("Connect to the AFC printer on the Device tab first, so its lanes are known.");
    else if ([&] { wxBusyCursor busy; return afc_layout::suggest(*print, *obj, preferred_units(), suggestion, why); }()) {
        AFCLayoutDialog dlg(parent, *print, *obj, std::move(suggestion));
        dlg.ShowModal();
        return;
    }
    MessageDialog(parent, why, _L("Suggested spool layout"), wxOK | wxICON_INFORMATION).ShowModal();
}

AFCLayoutDialog::AFCLayoutDialog(wxWindow* parent, const Print& print, const MachineObject& obj, afc_layout::Suggestion suggestion)
    : DPIDialog(parent ? parent : static_cast<wxWindow*>(wxGetApp().mainframe), wxID_ANY, _L("Suggested spool layout"), wxDefaultPosition,
                wxDefaultSize, wxCAPTION | wxCLOSE_BOX)
    , m_print(print)
    , m_dev_id(obj.get_dev_id())
    , m_suggestion(std::move(suggestion))
{
    SetBackgroundColour(*wxWHITE);
    SetFont(Label::Body_14);
    const int width = FromDIP(520);
    const PrintConfig& config = m_print.config();
    auto*     sizer = new wxBoxSizer(wxVERTICAL);

    const bool moves = !m_suggestion.plan.moves.empty();
    // TRN %1%: lane changes the print makes with its spools where they are, %2%: with the suggested layout.
    auto* summary = add_label(this, Label::Head_14,
                              format_wxstr(_L("Lane changes: %1% now, %2% with this layout."), m_suggestion.now.lane_changes,
                                           m_suggestion.after.lane_changes),
                              TEXT_COLOUR);
    sizer->Add(summary, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(20));
    auto* intro = add_label(this, Label::Body_14,
                            moves ? _L("Move the spools as listed and let AFC see them, then click Sync layout to AFC. Each lane "
                                       "takes the spool its old lane had, unless it reads the spool's tag, and the plate is "
                                       "sliced again with each filament on its new tool.") :
                                    _L("The spools are already in the best lanes for this print."),
                            HINT_COLOUR);
    intro->Wrap(width);
    sizer->Add(intro, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(10));

    auto*     rows = new wxFlexGridSizer(2, FromDIP(8), FromDIP(10));
    const int icon = FromDIP(16);
    for (const AFCLayout::Move& move : m_suggestion.plan.moves) {
        const auto& lanes  = m_suggestion.agent_lanes;
        std::string colour = move.filament >= 0 && size_t(move.filament) < config.filament_colour.values.size() ?
                                 config.filament_colour.get_at(move.filament) :
                                 move.from >= 0 ? afc_layout::rgb(lanes[move.from].color) : std::string();
        const wxBitmap* swatch = get_extruder_color_icon(colour.empty() ? "#F0F0F0" : colour, "", icon, icon);
        rows->Add(new wxStaticBitmap(this, wxID_ANY, swatch ? *swatch : wxNullBitmap), 0, wxALIGN_CENTER_VERTICAL);
        const wxString type = move.filament >= 0 ? from_u8(config.filament_type.get_at(move.filament)) : wxString();
        wxString       text;
        if (move.to < 0)
            text = format_wxstr(_L("Take the spool in %1% off the printer."), lane_name(lanes[move.from]));
        else if (move.from < 0)
            // TRN %1%: a filament's number, %2%: its material, %3%: an AFC lane and its unit.
            text = format_wxstr(_L("Load a spool of filament %1% (%2%) into %3%."), move.filament + 1, type, lane_name(lanes[move.to]));
        else if (move.filament < 0)
            text = format_wxstr(_L("Move the spool in %1% to %2%."), lane_name(lanes[move.from]), lane_name(lanes[move.to]));
        else
            // TRN %1%: a filament's number, %2%: its material, %3% and %4%: AFC lanes and their units.
            text = format_wxstr(_L("Move filament %1% (%2%) from %3% to %4%."), move.filament + 1, type, lane_name(lanes[move.from]),
                                lane_name(lanes[move.to]));
        rows->Add(add_label(this, Label::Body_14, text, TEXT_COLOUR), 0, wxALIGN_CENTER_VERTICAL);
    }
    sizer->Add(rows, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(20));

    // TRN Buttons of the suggested spool layout of an AFC printer
    auto* buttons = moves ? new DialogButtons(this, {L("Sync layout to AFC"), "Cancel"}, _L("Sync layout to AFC")) :
                            new DialogButtons(this, {L("Close")}, _L("Close"));
    if (moves) {
        buttons->GetButtonFromIndex(0)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { sync(); });
        buttons->GetCANCEL()->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { EndModal(wxID_CANCEL); });
    } else
        buttons->GetButtonFromIndex(0)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { EndModal(wxID_CLOSE); });
    sizer->Add(buttons, 0, wxEXPAND | wxTOP, FromDIP(20));

    SetSizerAndFit(sizer);
    CenterOnParent();
    wxGetApp().UpdateDlgDarkUI(this);
}

void AFCLayoutDialog::sync()
{
    MachineObject* obj = selected_machine();
    if (obj == nullptr || obj->get_dev_id() != m_dev_id) {
        MessageDialog(this, _L("The printer this layout is for is no longer connected."), _L("Suggested spool layout"), wxOK | wxICON_WARNING)
            .ShowModal();
        return;
    }
    // A lane with no spool yet stays as it is on the printer.
    wxString empty;
    for (const AFCLayout::Move& move : m_suggestion.plan.moves)
        if (move.to >= 0)
            if (const MachineObject::AgentLane* lane = obj->agent_lane(m_suggestion.agent_lanes[move.to].name); lane && !lane->loaded)
                empty += (empty.empty() ? "" : ", ") + from_u8(lane->name);
    if (!empty.empty() &&
        MessageDialog(this, format_wxstr(_L("AFC sees no spool in %1% yet and leaves it as it is. Sync the other lanes now?"), empty),
                      _L("Suggested spool layout"), wxYES_NO | wxICON_QUESTION)
                .ShowModal() != wxID_YES)
        return;

    obj->command_afc_layout(afc_layout::moves_json(m_suggestion, m_print));
    std::vector<int> toolheads = m_suggestion.suggested;
    EndModal(wxID_OK);
    wxGetApp().CallAfter([toolheads] {
        if (Plater* plater = wxGetApp().plater())
            plater->apply_filament_toolheads(toolheads);
    });
}

void AFCLayoutDialog::on_dpi_changed(const wxRect&)
{
    GetSizer()->SetSizeHints(this);
    Refresh();
}

AFCUnitOrderDialog::AFCUnitOrderDialog(wxWindow* parent, const std::vector<std::string>& order)
    : DPIDialog(parent ? parent : static_cast<wxWindow*>(wxGetApp().mainframe), wxID_ANY, _L("Preferred units"), wxDefaultPosition,
                wxDefaultSize, wxCAPTION | wxCLOSE_BOX)
    , m_order(order)
{
    if (MachineObject* obj = selected_machine())
        m_units = obj->agent_units;
    // The printer's units not in the order yet follow it, quickest first.
    std::vector<MachineObject::AgentUnit> others;
    for (const MachineObject::AgentUnit& unit : m_units)
        if (std::find(m_order.begin(), m_order.end(), unit.name) == m_order.end())
            others.push_back(unit);
    std::stable_sort(others.begin(), others.end(), [](const auto& a, const auto& b) { return a.seconds < b.seconds; });
    for (const MachineObject::AgentUnit& unit : others)
        m_order.push_back(unit.name);

    SetBackgroundColour(*wxWHITE);
    SetFont(Label::Body_14);
    auto* sizer = new wxBoxSizer(wxVERTICAL);
    auto* intro = add_label(this, Label::Body_14,
                            m_units.empty() ? _L("Connect to the AFC printer on the Device tab to list its units here.") :
                                              _L("The suggested spool layout fills the units at the top first. Units below the ones you "
                                                 "move follow AFC's measured change times, or the speed of their kind."),
                            HINT_COLOUR);
    intro->Wrap(FromDIP(420));
    sizer->Add(intro, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(20));

    auto* row = new wxBoxSizer(wxHORIZONTAL);
    m_list    = new wxListBox(this, wxID_ANY, wxDefaultPosition, wxSize(FromDIP(320), FromDIP(200)));
    row->Add(m_list, 1, wxEXPAND);
    auto* side = new wxBoxSizer(wxVERTICAL);
    auto* up   = new Button(this, _L("Move up"));
    auto* down = new Button(this, _L("Move down"));
    for (Button* button : {up, down}) {
        button->SetStyle(ButtonStyle::Regular, ButtonType::Choice);
        side->Add(button, 0, wxEXPAND | wxBOTTOM, FromDIP(8));
    }
    up->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { move(-1); });
    down->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { move(1); });
    row->Add(side, 0, wxLEFT, FromDIP(10));
    sizer->Add(row, 1, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(20));

    auto* buttons = new DialogButtons(this, {"OK", "Cancel"});
    buttons->GetOK()->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { EndModal(wxID_OK); });
    buttons->GetCANCEL()->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { EndModal(wxID_CANCEL); });
    sizer->Add(buttons, 0, wxEXPAND | wxTOP, FromDIP(10));

    refill(m_order.empty() ? -1 : 0);
    SetSizerAndFit(sizer);
    CenterOnParent();
    wxGetApp().UpdateDlgDarkUI(this);
}

void AFCUnitOrderDialog::refill(int selection)
{
    m_list->Clear();
    for (const std::string& name : m_order) {
        auto unit = std::find_if(m_units.begin(), m_units.end(), [&name](const auto& u) { return u.name == name; });
        wxString text;
        if (unit == m_units.end())
            // TRN An AFC unit in the Preferred units list that the connected printer does not have.
            text = format_wxstr(_L("%1% (not on this printer)"), from_u8(name));
        else if (unit->measured)
            // TRN %1%: an AFC unit, %2%: its kind, %3%: seconds a lane change takes there, from AFC's log.
            text = format_wxstr(_L("%1% (%2%, %3% s measured)"), from_u8(name), from_u8(unit->type), int(unit->seconds + 0.5));
        else
            // TRN %1%: an AFC unit, %2%: its kind.
            text = format_wxstr(_L("%1% (%2%)"), from_u8(name), from_u8(unit->type));
        m_list->Append(text);
    }
    if (selection >= 0 && selection < int(m_order.size()))
        m_list->SetSelection(selection);
}

void AFCUnitOrderDialog::move(int by)
{
    const int from = m_list->GetSelection();
    const int to   = from + by;
    if (from == wxNOT_FOUND || to < 0 || to >= int(m_order.size()))
        return;
    std::swap(m_order[from], m_order[to]);
    refill(to);
}

void AFCUnitOrderDialog::on_dpi_changed(const wxRect&)
{
    GetSizer()->SetSizeHints(this);
    Refresh();
}

}} // namespace Slic3r::GUI
