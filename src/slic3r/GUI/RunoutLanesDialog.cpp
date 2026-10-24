#include "RunoutLanesDialog.hpp"

#include "GUI.hpp"
#include "GUI_App.hpp"
#include "GUI_Utils.hpp"
#include "I18N.hpp"
#include "MainFrame.hpp"
#include "DeviceManager.hpp"
#include "format.hpp"
#include "wxExtensions.hpp"
#include "DeviceCore/DevFilaSystem.h"
#include "DeviceCore/DevManager.h"
#include "Widgets/Button.hpp"
#include "Widgets/CheckBox.hpp"
#include "Widgets/ComboBox.hpp"
#include "Widgets/DialogButtons.hpp"
#include "Widgets/DropDown.hpp"
#include "Widgets/Label.hpp"

#include <algorithm>
#include <chrono>
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
#include <wx/font.h>
#include <wx/gdicmn.h>
#include <wx/panel.h>
#include <wx/scrolwin.h>
#include <wx/sizer.h>
#include <wx/statbmp.h>
#include <wx/string.h>
#include <wx/timer.h>
#include <wx/tglbtn.h>
#include <wx/window.h>

namespace Slic3r { namespace GUI {

namespace runout_lanes {

namespace {

const wxString& dot()
{
    static const wxString dot = wxString::FromUTF8(" \xC2\xB7 ");
    return dot;
}

bool holds_same(const Lane& lane, const std::string& name)
{
    return std::find(lane.same.begin(), lane.same.end(), name) != lane.same.end();
}

// "PLA · 977 g", or "Empty" for a lane with no spool.
wxString details(const Lane& lane)
{
    if (!lane.loaded)
        return _L("Empty");
    wxString text = from_u8(lane.type);
    if (lane.remain_g >= 0)
        // TRN %1%: grams of filament left on a spool
        text += (text.empty() ? wxString() : dot()) + format_wxstr(_L("%1% g"), lane.remain_g);
    return text;
}

wxBitmap swatch_bitmap(const Lane& lane, int size)
{
    const wxBitmap* bitmap = get_extruder_color_icon(swatch(lane), "", size, size);
    return bitmap ? *bitmap : wxNullBitmap;
}

} // namespace

const Lane* find(const Lanes& lanes, const std::string& name)
{
    for (const Lane& lane : lanes)
        if (lane.name == name)
            return &lane;
    return nullptr;
}

std::string swatch(const Lane& lane)
{
    if (!lane.loaded || lane.color.size() != 8)
        return "#F0F0F0";
    return DevAmsTray::decode_color(lane.color).GetAsString(wxC2S_HTML_SYNTAX).ToStdString();
}

wxString describe(const Lane& lane, const Lane* from)
{
    wxString text = from_u8(lane.name);
    if (const wxString about = details(lane); lane.loaded && !about.empty())
        text += dot() + about;
    if (from && !lane.extruder.empty() && lane.extruder != from->extruder)
        text += dot() + from_u8(lane.extruder);
    return text;
}

wxString outcome(const Lanes& lanes, const Lane& lane, const std::string& to)
{
    if (to.empty())
        return _L("The print pauses when this lane runs out.");
    const Lane* next = find(lanes, to);
    if (next && !next->extruder.empty() && next->extruder != lane.extruder)
        // TRN %1%: the lane the printer loads when this one runs out; %2%: the extruder it feeds, "extruder1"
        return format_wxstr(_L("AFC loads %1% into %2% and keeps printing."), to, next->extruder);
    // TRN %1%: the lane the printer loads when this one runs out
    return format_wxstr(_L("AFC loads %1% and keeps printing."), to);
}

wxString warning(const Lanes& lanes, const Lane& lane, const std::string& to)
{
    if (to.empty())
        return {};
    const Lane* next = find(lanes, to);
    if (!next)
        // TRN %1%: a runout lane the printer has no lane of that name for
        return format_wxstr(_L("AFC has no lane named %1%."), to);
    if (!next->loaded)
        // TRN %1%: a lane with no spool
        return format_wxstr(_L("%1% is empty."), to);
    // Only lanes whose materials are both known are compared, so the agent's `same` settles the color.
    if (!lane.loaded || holds_same(lane, to) || lane.family.empty() || next->family.empty())
        return {};
    if (lane.family != next->family)
        // TRN %1%: a lane; %2%: the material on its spool, "PETG"
        return format_wxstr(_L("%1% holds %2%."), to, next->type);
    if (!lane.color.empty() && !next->color.empty())
        // TRN %1%: a lane whose spool is another color than this lane's
        return format_wxstr(_L("%1% is another color."), to);
    return {};
}

bool loops(const Lanes& lanes, const Lane& lane, const std::string& to)
{
    std::string name = to;
    for (size_t step = 0; !name.empty() && step <= lanes.size(); ++step) {
        if (name == lane.name)
            return true;
        const Lane* next = find(lanes, name);
        name = next ? next->runout : std::string();
    }
    return false;
}

void fill(::ComboBox* combo, const Lanes& lanes, const Lane& lane, const std::string& selected, int icon_size)
{
    combo->Clear();
    int select = combo->Append(_L("Pause the print"));
    combo->SetItemAlias(select, wxEmptyString);
    if (!selected.empty())
        select = -1;

    auto add_group = [&](const wxString& title, auto wanted) {
        bool titled = false;
        for (const Lane& other : lanes) {
            if (other.name == lane.name || !wanted(other))
                continue;
            if (!titled) {
                combo->Append(title, wxNullBitmap, DD_ITEM_STYLE_SPLIT_ITEM | DD_ITEM_STYLE_DISABLED);
                titled = true;
            }
            const int item = combo->Append(describe(other, &lane), swatch_bitmap(other, icon_size),
                                           loops(lanes, lane, other.name) ? DD_ITEM_STYLE_DISABLED : 0);
            combo->SetItemAlias(item, from_u8(other.name));
            if (other.name == selected)
                select = item;
        }
    };
    add_group(_L("Same filament"), [&](const Lane& other) { return holds_same(lane, other.name); });
    add_group(_L("Other lanes"), [&](const Lane& other) { return other.loaded && !holds_same(lane, other.name); });
    add_group(_L("Empty lanes"), [&](const Lane& other) { return !other.loaded; });
    if (select < 0) { // a runout lane the printer has no lane of that name for
        select = combo->Append(from_u8(selected), wxNullBitmap, DD_ITEM_STYLE_DIMMED);
        combo->SetItemAlias(select, from_u8(selected));
    }
    combo->SetSelection(select);
}

std::string selected(::ComboBox* combo)
{
    const int n = combo->GetSelection();
    return n < 0 ? std::string() : into_u8(combo->GetItemAlias(n));
}

} // namespace runout_lanes

namespace {

// Light palette colors, which UpdateDlgDarkUI() maps for dark mode.
const char* const TEXT_COLOUR    = "#323A3D";
const char* const HINT_COLOUR    = "#6B6B6B";
const char* const WARNING_COLOUR = "#FF6F00";
const char* const ACCENT_COLOUR  = "#009688";
const char* const CARD_COLOUR    = "#F8F8F8";
const char* const LINE_COLOUR    = "#EEEEEE";

constexpr int CHAIN_LANES_PER_LINE = 3;
constexpr int REFRESH_MS           = 500;
constexpr int HOLD_S               = 5; // seconds a runout lane picked here wins over what the printer reports

Label* add_label(wxWindow* parent, const wxFont& font, const wxString& text, const wxColour& colour)
{
    auto* label = new Label(parent, font, text);
    label->SetForegroundColour(colour);
    label->SetBackgroundColour(parent->GetBackgroundColour());
    return label;
}

} // namespace

RunoutLanesDialog::RunoutLanesDialog(wxWindow* parent, MachineObject* obj)
    : DPIDialog(parent ? parent : static_cast<wxWindow*>(wxGetApp().mainframe), wxID_ANY, _L("Auto Refill"), wxDefaultPosition,
                wxDefaultSize, wxCAPTION | wxCLOSE_BOX)
    , m_dev_id(obj->get_dev_id())
    , m_timer(this)
{
    SetBackgroundColour(*wxWHITE);
    SetFont(Label::Body_14);
    const int width = FromDIP(520);

    auto* sizer = new wxBoxSizer(wxVERTICAL);

    auto* intro = add_label(this, Label::Body_14,
                            _L("When a lane runs out mid-print, AFC loads its runout lane and keeps printing. Changes go to the printer at once."),
                            TEXT_COLOUR);
    intro->Wrap(width);
    sizer->Add(intro, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(20));

    // AMS Settings' AMS filament backup: the agent links matching spools for each print sent from here.
    auto* backup_sizer = new wxBoxSizer(wxHORIZONTAL);
    m_backup = new ::CheckBox(this);
    m_backup->SetValue(obj->GetFilaSystem()->IsAutoRefillEnabled());
    m_backup->Bind(wxEVT_TOGGLEBUTTON, [this](wxCommandEvent& e) {
        if (MachineObject* printer = machine())
            printer->command_ams_switch_filament(m_backup->GetValue());
        e.Skip();
    });
    backup_sizer->Add(m_backup, 0, wxRIGHT, FromDIP(8));
    auto* backup_text  = new wxBoxSizer(wxVERTICAL);
    auto* backup_title = add_label(this, Label::Head_14, _L("Back up each print with matching spools"), TEXT_COLOUR);
    auto* backup_hint  = add_label(this, Label::Body_13,
                                   _L("When OrcaSlicer sends a print, each lane it uses runs out onto the loaded spools with the same material and color. Runout lanes picked by hand stay."),
                                   HINT_COLOUR);
    backup_hint->Wrap(width - FromDIP(26));
    backup_text->Add(backup_title, 0);
    backup_text->Add(backup_hint, 0, wxTOP, FromDIP(4));
    backup_sizer->Add(backup_text, 1);
    sizer->Add(backup_sizer, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(20));

    m_scroll = new wxScrolledWindow(this, wxID_ANY, wxDefaultPosition, wxDefaultSize, wxVSCROLL);
    m_scroll->SetBackgroundColour(*wxWHITE);
    m_scroll->SetScrollRate(0, FromDIP(10));
    m_body = new wxBoxSizer(wxVERTICAL);
    m_scroll->SetSizer(m_body);
    sizer->Add(m_scroll, 1, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(20));

    // TRN Buttons of the Auto Refill dialog of an AFC printer
    m_buttons = new DialogButtons(this, {L("Clear all"), L("Link matching spools"), L("Close")}, _L("Link matching spools"), 1);
    m_buttons->GetButtonFromIndex(0)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { send_all("clear"); });
    m_buttons->GetButtonFromIndex(1)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { send_all("match"); });
    m_buttons->GetButtonFromIndex(2)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { EndModal(wxID_CLOSE); });
    sizer->Add(m_buttons, 0, wxEXPAND | wxTOP, FromDIP(10));

    rebuild(*obj);
    SetSizerAndFit(sizer);
    CenterOnParent();
    wxGetApp().UpdateDlgDarkUI(this);

    Bind(wxEVT_TIMER, &RunoutLanesDialog::on_timer, this);
    m_timer.Start(REFRESH_MS);
}

RunoutLanesDialog::~RunoutLanesDialog() { m_timer.Stop(); }

bool RunoutLanesDialog::show_for(wxWindow* parent, MachineObject* obj)
{
    if (!obj || !obj->is_support_runout_lane)
        return false;
    RunoutLanesDialog dlg(parent, obj);
    dlg.ShowModal();
    return true;
}

MachineObject* RunoutLanesDialog::machine() const
{
    DeviceManager* dev = wxGetApp().getDeviceManager();
    MachineObject* obj = dev ? dev->get_selected_machine() : nullptr;
    return obj && obj->get_dev_id() == m_dev_id ? obj : nullptr;
}

runout_lanes::Lanes RunoutLanesDialog::lanes(const MachineObject& obj) const
{
    runout_lanes::Lanes out = obj.agent_lanes;
    for (runout_lanes::Lane& lane : out)
        if (auto held = m_held.find(lane.name); held != m_held.end())
            lane.runout = held->second.first;
    return out;
}

void RunoutLanesDialog::on_timer(wxTimerEvent&)
{
    MachineObject* obj = machine();
    if (!obj || !obj->is_support_runout_lane) { // another printer was picked, or this one went away
        m_timer.Stop();
        EndModal(wxID_CLOSE);
        return;
    }
    const Clock::time_point now = Clock::now();
    for (auto held = m_held.begin(); held != m_held.end();) {
        const runout_lanes::Lane* lane = obj->agent_lane(held->first);
        if (!lane || lane->runout == held->second.first || now - held->second.second > std::chrono::seconds(HOLD_S))
            held = m_held.erase(held);
        else
            ++held;
    }
    const bool backup = obj->GetFilaSystem()->IsAutoRefillEnabled();
    if (m_backup->GetValue() != backup)
        m_backup->SetValue(backup);
    // Never pull a list out from under the pointer.
    if (std::none_of(m_combos.begin(), m_combos.end(), [](::ComboBox* combo) { return combo->is_drop_down(); }))
        rebuild(*obj);
}

void RunoutLanesDialog::rebuild(const MachineObject& obj)
{
    const runout_lanes::Lanes all = lanes(obj);
    std::string shown;
    for (const runout_lanes::Lane& lane : all) {
        shown += lane.name + '\t' + lane.unit + '\t' + lane.color + '\t' + lane.type + '\t' + lane.family + '\t' + lane.extruder + '\t' +
                 lane.runout + '\t' + std::to_string(lane.remain_g) + (lane.loaded ? "\t1\t" : "\t0\t");
        for (const std::string& name : lane.same)
            shown += name + ',';
        shown += '\n';
    }
    if (shown == m_shown)
        return;
    m_shown = shown;

    m_scroll->Freeze();
    const wxPoint view = m_scroll->GetViewStart();
    m_combos.clear();
    m_body->Clear(true);
    add_chains(all);
    add_lanes(all);
    wxGetApp().UpdateDarkUIWin(m_scroll);
    m_scroll->SetMinSize(wxSize(FromDIP(520), std::min(m_body->GetMinSize().y, FromDIP(440))));
    m_scroll->FitInside();
    m_scroll->Scroll(view);
    m_scroll->Thaw();

    const bool linked   = std::any_of(all.begin(), all.end(), [](const runout_lanes::Lane& lane) { return !lane.runout.empty(); });
    const bool matching = std::any_of(all.begin(), all.end(), [](const runout_lanes::Lane& lane) { return !lane.same.empty(); });
    m_buttons->GetButtonFromIndex(0)->Enable(linked);
    m_buttons->GetButtonFromIndex(1)->Enable(matching);
    if (GetSizer()) { // the constructor fits the dialog itself
        Layout();
        Fit();
    }
}

void RunoutLanesDialog::add_chains(const runout_lanes::Lanes& all)
{
    m_body->Add(add_label(m_scroll, Label::Body_13, _L("Chains"), HINT_COLOUR), 0, wxBOTTOM, FromDIP(6));

    // Each chain from a lane no other lane runs out onto, as the printer goes through it.
    std::set<std::string> into;
    for (const runout_lanes::Lane& lane : all)
        into.insert(lane.runout);
    int count = 0;
    for (const runout_lanes::Lane& first : all) {
        if (first.runout.empty() || into.count(first.name))
            continue;
        std::vector<const runout_lanes::Lane*> chain{&first};
        for (const runout_lanes::Lane* next = runout_lanes::find(all, first.runout);
             next && std::find(chain.begin(), chain.end(), next) == chain.end(); next = runout_lanes::find(all, next->runout))
            chain.push_back(next);
        if (chain.size() < 2)
            continue; // it runs out onto a lane the printer does not have, which its row below names
        ++count;

        auto* card = new wxPanel(m_scroll);
        card->SetBackgroundColour(CARD_COLOUR);
        auto* card_sizer = new wxBoxSizer(wxVERTICAL);
        wxBoxSizer* line = nullptr;
        for (size_t i = 0; i < chain.size(); ++i) {
            const runout_lanes::Lane& lane = *chain[i];
            if (i % CHAIN_LANES_PER_LINE == 0) {
                line = new wxBoxSizer(wxHORIZONTAL);
                card_sizer->Add(line, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(10));
            }
            if (i > 0)
                line->Add(add_label(card, Label::Body_14, wxString::FromUTF8("\xE2\x86\x92"), HINT_COLOUR), 0,
                          wxALIGN_CENTER_VERTICAL | wxRIGHT | (i % CHAIN_LANES_PER_LINE ? wxLEFT : 0), FromDIP(8));
            line->Add(new wxStaticBitmap(card, wxID_ANY, runout_lanes::swatch_bitmap(lane, FromDIP(14))), 0, wxALIGN_CENTER_VERTICAL | wxRIGHT,
                      FromDIP(4));
            line->Add(add_label(card, Label::Head_13, from_u8(lane.name), TEXT_COLOUR), 0, wxALIGN_CENTER_VERTICAL | wxRIGHT, FromDIP(4));
            line->Add(add_label(card, Label::Body_12, runout_lanes::details(lane), HINT_COLOUR), 0, wxALIGN_CENTER_VERTICAL);
        }

        // What the chain holds in all, and the toolheads it goes through.
        int                       grams    = 0;
        const runout_lanes::Lane* unweighed = nullptr;
        std::vector<std::string>  extruders;
        for (const runout_lanes::Lane* lane : chain) {
            if (lane->loaded && lane->remain_g < 0 && !unweighed)
                unweighed = lane;
            grams += std::max(lane->remain_g, 0);
            if (!lane->extruder.empty() && std::find(extruders.begin(), extruders.end(), lane->extruder) == extruders.end())
                extruders.push_back(lane->extruder);
        }
        auto* summary = new wxBoxSizer(wxHORIZONTAL);
        summary->Add(add_label(card, Label::Body_12,
                               // TRN %1%: a lane whose spool the printer has no weight for
                               unweighed ? format_wxstr(_L("AFC has no weight for %1%"), unweighed->name) :
                                           // TRN %1%: grams of filament left on the spools of a chain of runout lanes
                                           format_wxstr(_L("%1% g in all"), grams),
                               HINT_COLOUR));
        if (extruders.size() > 1) {
            wxString path;
            for (const std::string& extruder : extruders)
                path += (path.empty() ? wxString() : wxString::FromUTF8(" \xE2\x86\x92 ")) + from_u8(extruder);
            summary->Add(add_label(card, Label::Body_12, path, ACCENT_COLOUR), 0, wxLEFT, FromDIP(12));
        }
        card_sizer->Add(summary, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(10));
        for (size_t i = 0; i + 1 < chain.size(); ++i) {
            const wxString warning = runout_lanes::warning(all, *chain[i], chain[i + 1]->name);
            if (!warning.empty())
                card_sizer->Add(add_label(card, Label::Body_12, warning, WARNING_COLOUR), 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(10));
        }
        card_sizer->AddSpacer(FromDIP(10));
        card->SetSizer(card_sizer);
        m_body->Add(card, 0, wxEXPAND | wxBOTTOM, FromDIP(8));
    }
    if (count == 0)
        m_body->Add(add_label(m_scroll, Label::Body_13, _L("No lane runs out onto another lane yet."), TEXT_COLOUR), 0, wxBOTTOM, FromDIP(8));
}

void RunoutLanesDialog::add_lanes(const runout_lanes::Lanes& all)
{
    m_body->Add(add_label(m_scroll, Label::Body_13, _L("Lanes"), HINT_COLOUR), 0, wxTOP | wxBOTTOM, FromDIP(6));

    bool any = false;
    for (const runout_lanes::Lane& lane : all) {
        if (!lane.loaded && lane.runout.empty())
            continue;
        if (any) {
            auto* separator = new wxPanel(m_scroll, wxID_ANY, wxDefaultPosition, wxSize(-1, FromDIP(1)));
            separator->SetBackgroundColour(LINE_COLOUR);
            m_body->Add(separator, 0, wxEXPAND);
        }
        any = true;

        auto* row = new wxBoxSizer(wxHORIZONTAL);
        row->Add(new wxStaticBitmap(m_scroll, wxID_ANY, runout_lanes::swatch_bitmap(lane, FromDIP(20))), 0, wxALIGN_CENTER_VERTICAL | wxRIGHT,
                 FromDIP(10));
        auto* text = new wxBoxSizer(wxVERTICAL);
        text->Add(add_label(m_scroll, Label::Head_14, from_u8(lane.name), TEXT_COLOUR));
        wxString about = from_u8(lane.unit);
        about += (about.empty() ? wxString() : runout_lanes::dot()) + runout_lanes::details(lane);
        text->Add(add_label(m_scroll, Label::Body_12, about, HINT_COLOUR));
        row->Add(text, 1, wxALIGN_CENTER_VERTICAL);
        row->Add(add_label(m_scroll, Label::Body_14, wxString::FromUTF8("\xE2\x86\x92"), HINT_COLOUR), 0, wxALIGN_CENTER_VERTICAL | wxLEFT | wxRIGHT,
                 FromDIP(10));

        auto* combo = new ::ComboBox(m_scroll, wxID_ANY, wxEmptyString, wxDefaultPosition, wxSize(FromDIP(230), FromDIP(30)), 0, nullptr,
                                     wxCB_READONLY);
        combo->SetKeepDropArrow(true);
        combo->GetDropDown().SetUseContentWidth(true);
        runout_lanes::fill(combo, all, lane, lane.runout, FromDIP(16));
        combo->Bind(wxEVT_COMBOBOX, [this, combo, name = lane.name](wxCommandEvent&) { set_runout(name, runout_lanes::selected(combo)); });
        m_combos.push_back(combo);
        row->Add(combo, 0, wxALIGN_CENTER_VERTICAL);
        m_body->Add(row, 0, wxEXPAND | wxTOP | wxBOTTOM, FromDIP(8));
    }
    if (!any)
        m_body->Add(add_label(m_scroll, Label::Body_13, _L("AFC reports no loaded lanes."), TEXT_COLOUR));
    m_body->AddSpacer(FromDIP(4));
}

void RunoutLanesDialog::set_runout(const std::string& lane, const std::string& runout)
{
    MachineObject* obj = machine();
    if (!obj)
        return;
    obj->command_set_runout_lane(lane, runout);
    // The chains show it at the next refresh, and keep it until the printer reports it.
    m_held[lane] = {runout, Clock::now()};
}

void RunoutLanesDialog::send_all(const std::string& action)
{
    if (MachineObject* obj = machine()) {
        obj->command_runout_lanes(action);
        m_held.clear();
    }
}

void RunoutLanesDialog::on_dpi_changed(const wxRect&)
{
    m_backup->Rescale();
    for (::ComboBox* combo : m_combos)
        combo->CloseDropdown();
    // The swatches are bitmaps of the old size.
    m_shown.clear();
    if (MachineObject* obj = machine())
        rebuild(*obj);
    GetSizer()->SetSizeHints(this);
    Refresh();
}

}} // namespace Slic3r::GUI
