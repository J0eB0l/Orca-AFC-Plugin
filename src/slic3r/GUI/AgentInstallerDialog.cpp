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
#include <boost/locale/encoding_utf.hpp>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <memory>
#include <regex>
#include <string>
#include <utility>
#include <vector>
#include <wx/brush.h>
#include <wx/clipbrd.h>
#include <wx/colour.h>
#include <wx/dataobj.h>
#include <wx/dc.h>
#include <wx/dcbuffer.h>
#include <wx/defs.h>
#include <wx/display.h>
#include <wx/event.h>
#include <wx/font.h>
#include <wx/gdicmn.h>
#include <wx/graphics.h>
#include <wx/menu.h>
#include <wx/menuitem.h>
#include <wx/pen.h>
#include <wx/scrolwin.h>
#include <wx/settings.h>
#include <wx/sizer.h>
#include <wx/stattext.h>
#include <wx/string.h>
#include <wx/textctrl.h>
#include <wx/timer.h>
#include <wx/window.h>

namespace Slic3r { namespace GUI {

static constexpr int    REFRESH_MS  = 250;
static constexpr auto   RENEW       = std::chrono::seconds(5); // the agent's watch lasts 15 s
static constexpr int    COLUMNS     = 100;                     // the script's terminal, as AFC opens it
static constexpr int    ROWS        = 28;                      // lines the screen shows at its smallest
static constexpr int    TALL_ROWS   = 64;                      // and on opening, where the display has room
static constexpr size_t LABEL_CHARS = 48;                      // a choice's button shows this much of its label
// A terminal's colors, in either theme: the script's own colors are picked for a dark terminal.
static const wxColour TERMINAL_BG(30, 30, 30);
static const wxColour TERMINAL_TEXT(212, 212, 212);
static const wxColour SELECTION_BG(38, 79, 120);
static const wxColour FAILED_CLR(255, 111, 0);

// The arms of the box-drawing lines U+2500 to U+254F, four digits each: up, right, down and left, 0 for none,
// 1 for a light line and 2 for a heavy one. Dashed lines are drawn whole.
static const char BOX_ARMS[] = "0101" "0202" "1010" "2020" "0101" "0202" "1010" "2020" "0101" "0202" "1010" "2020"
                               "0110" "0210" "0120" "0220" "0011" "0012" "0021" "0022" "1100" "1200" "2100" "2200"
                               "1001" "1002" "2001" "2002" "1110" "1210" "2110" "1120" "2120" "2210" "1220" "2220"
                               "1011" "1012" "2011" "1021" "2021" "2012" "1022" "2022" "0111" "0112" "0211" "0212"
                               "0121" "0122" "0221" "0222" "1101" "1102" "1201" "1202" "2101" "2102" "2201" "2202"
                               "1111" "1112" "1211" "1212" "2111" "1121" "2121" "2112" "2211" "1122" "1221" "2212"
                               "1222" "2122" "2221" "2222" "0101" "0202" "1010" "2020";
// And of the half lines U+2574 to U+257F.
static const char HALF_ARMS[] = "0001" "1000" "0100" "0010" "0002" "2000" "0200" "0020" "0201" "1020" "0102" "2010";
// The quadrants U+2596 to U+259F fill: 1 upper left, 2 upper right, 4 lower left, 8 lower right.
static const unsigned char QUADRANTS[] = {4, 8, 1, 13, 9, 7, 11, 2, 6, 14};

// The script's screen as a terminal draws it, in the terminal's colors in either theme: each character in a
// cell of a grid as wide as the code font's digits, in its color over its background. Block elements, which
// logos are drawn with, and box lines are shapes that fill their cells, and other characters outside ASCII are
// drawn one per cell: a font without them falls back to one whose glyphs are another width, which would break
// the grid. The view follows the end of the screen while it is at the end. Dragging selects text, which Ctrl+C
// or the context menu copies.
class InstallerScreen : public wxScrolledCanvas
{
public:
    using Lines = std::vector<std::vector<MachineObject::AgentInstaller::Run>>;

    explicit InstallerScreen(wxWindow* parent);
    ~InstallerScreen() override;

    // Shows `lines` when they changed.
    void set_lines(const Lines& lines);
    // Measures the cells again, as after a DPI change.
    void rescale();
    // The height that shows `rows` lines.
    int  height_for(int rows) const { return m_cell_h * rows + 2 * m_pad; }
    // Clicked for a selection, but left out of the TAB order.
    bool AcceptsFocusFromKeyboard() const override { return false; }

private:
    struct Span
    {
        wxColour       color;
        wxColour       background; // not IsOk() for the terminal's own
        std::u32string text;
    };
    // A rounded corner or a diagonal, drawn anti-aliased once the rest is.
    struct Curve
    {
        char32_t c;
        wxRect   cell;
        wxColour color;
    };
    using Spot = std::pair<int, int>; // a row, and the column before which a selection starts or ends

    void     update_size();
    int      length(int row) const;
    // Columns [col, col + count) of a row in client coordinates. Neighbors share their edges.
    wxRect   cells(int row, int col, int count) const;
    Spot     spot_at(const wxPoint& point) const;
    wxString selected_text() const;
    void     copy() const;
    void     select_all();
    // One character outside ASCII in its cell.
    void     draw_cell(wxDC& dc, char32_t c, const wxRect& cell, const Span& span, std::vector<Curve>& curves) const;
    void     draw_arms(wxDC& dc, const char* arms, const wxRect& cell) const;
    void     draw_curve(wxGraphicsContext& gc, const Curve& curve) const;
    void     render(wxDC& dc) const;

    void on_paint(wxPaintEvent& event);
    void on_left_down(wxMouseEvent& event);
    void on_motion(wxMouseEvent& event);
    void on_left_up(wxMouseEvent& event);
    void on_capture_lost(wxMouseCaptureLostEvent& event);
    void on_key(wxKeyEvent& event);
    void on_menu(wxContextMenuEvent& event);

    Lines                          m_lines;
    std::vector<std::vector<Span>> m_rows;
    double                         m_cell_w {8.0};
    int                            m_cell_h {16};
    int                            m_pad {6};
    int                            m_line {1};      // a light line's thickness
    int                            m_columns {0};   // of the longest row
    Spot                           m_anchor {0, 0}; // where the selection started
    Spot                           m_caret {0, 0};  // and where it ends: the same when nothing is selected
    bool                           m_dragging {false};
};

InstallerScreen::InstallerScreen(wxWindow* parent)
    : wxScrolledCanvas(parent, wxID_ANY, wxDefaultPosition, wxDefaultSize,
                       wxHSCROLL | wxVSCROLL | wxBORDER_NONE | wxFULL_REPAINT_ON_RESIZE)
{
    SetBackgroundStyle(wxBG_STYLE_PAINT);
    SetBackgroundColour(TERMINAL_BG);
    SetFont(wxGetApp().code_font());
    rescale();
    Bind(wxEVT_PAINT, &InstallerScreen::on_paint, this);
    Bind(wxEVT_LEFT_DOWN, &InstallerScreen::on_left_down, this);
    Bind(wxEVT_LEFT_DCLICK, &InstallerScreen::on_left_down, this); // the second press of a double click
    Bind(wxEVT_MOTION, &InstallerScreen::on_motion, this);
    Bind(wxEVT_LEFT_UP, &InstallerScreen::on_left_up, this);
    Bind(wxEVT_MOUSE_CAPTURE_LOST, &InstallerScreen::on_capture_lost, this);
    Bind(wxEVT_KEY_DOWN, &InstallerScreen::on_key, this);
    Bind(wxEVT_CONTEXT_MENU, &InstallerScreen::on_menu, this);
}

InstallerScreen::~InstallerScreen()
{
    if (HasCapture())
        ReleaseMouse();
}

void InstallerScreen::set_lines(const Lines& lines)
{
    if (lines == m_lines)
        return;
    int unit_x = 0, unit_y = 0;
    GetScrollPixelsPerUnit(&unit_x, &unit_y);
    const bool at_end = GetViewStart().y * unit_y + GetClientSize().y >= GetVirtualSize().y - m_cell_h;
    m_lines = lines;
    m_rows.clear();
    m_columns = 0;
    for (const std::vector<MachineObject::AgentInstaller::Run>& line : m_lines) {
        std::vector<Span>& row = m_rows.emplace_back();
        for (const MachineObject::AgentInstaller::Run& run : line) {
            const wxColour color      = run.color.empty() ? TERMINAL_TEXT : wxColour(from_u8(run.color));
            const wxColour background = run.background.empty() ? wxColour() : wxColour(from_u8(run.background));
            row.push_back({color.IsOk() ? color : TERMINAL_TEXT, background,
                           boost::locale::conv::utf_to_utf<char32_t>(run.text)});
        }
        m_columns = std::max(m_columns, length(int(m_rows.size()) - 1));
    }
    // A selection keeps to the rows that are left.
    const Spot last(int(m_rows.size()) - 1, m_rows.empty() ? 0 : length(int(m_rows.size()) - 1));
    m_anchor = std::min(m_anchor, std::max(last, Spot(0, 0)));
    m_caret  = std::min(m_caret, std::max(last, Spot(0, 0)));
    update_size();
    if (at_end)
        Scroll(wxDefaultCoord, int(m_rows.size()) + 1); // a line a step, and wx stops at the last page
    Refresh();
}

void InstallerScreen::rescale()
{
    const wxSize digits = GetTextExtent(wxString('0', COLUMNS));
    m_cell_w            = std::max(1.0, digits.x / double(COLUMNS));
    m_cell_h            = std::max(1, digits.y);
    m_pad               = FromDIP(6);
    m_line              = std::max(1, int(std::lround(m_cell_w / 8)));
    // The script's terminal, and room for a scroll bar.
    SetMinSize(wxSize(int(std::ceil(m_cell_w * COLUMNS)) + 2 * m_pad + wxSystemSettings::GetMetric(wxSYS_VSCROLL_X, this),
                      height_for(ROWS)));
    update_size();
    Refresh();
}

void InstallerScreen::update_size()
{
    SetScrollRate(std::max(1, int(std::lround(m_cell_w))), m_cell_h);
    SetVirtualSize(int(std::ceil(m_columns * m_cell_w)) + 2 * m_pad, int(m_rows.size()) * m_cell_h + 2 * m_pad);
}

int InstallerScreen::length(int row) const
{
    int out = 0;
    for (const Span& span : m_rows[row])
        out += int(span.text.size());
    return out;
}

wxRect InstallerScreen::cells(int row, int col, int count) const
{
    const wxPoint origin = CalcScrolledPosition(wxPoint(m_pad, m_pad + row * m_cell_h));
    const int     left   = origin.x + int(std::lround(col * m_cell_w));
    const int     right  = origin.x + int(std::lround((col + count) * m_cell_w));
    return wxRect(left, origin.y, right - left, m_cell_h);
}

InstallerScreen::Spot InstallerScreen::spot_at(const wxPoint& point) const
{
    const wxPoint at = CalcUnscrolledPosition(point) - wxPoint(m_pad, m_pad);
    if (m_rows.empty() || at.y < 0)
        return Spot(0, 0);
    const int row = at.y / m_cell_h;
    if (row >= int(m_rows.size()))
        return Spot(int(m_rows.size()) - 1, length(int(m_rows.size()) - 1));
    return Spot(row, std::clamp(int(std::lround(at.x / m_cell_w)), 0, length(row)));
}

wxString InstallerScreen::selected_text() const
{
    const auto [from, to] = std::minmax(m_anchor, m_caret);
    std::u32string text;
    for (int row = from.first; row <= to.first && row < int(m_rows.size()); ++row) {
        std::u32string line;
        for (const Span& span : m_rows[row])
            line += span.text;
        const size_t end   = row == to.first ? std::min(size_t(to.second), line.size()) : line.size();
        const size_t start = row == from.first ? std::min(size_t(from.second), end) : 0;
        line               = line.substr(start, end - start);
        while (!line.empty() && line.back() == U' ')
            line.pop_back();
        if (row != from.first)
            text += U'\n';
        text += line;
    }
    return from_u8(boost::locale::conv::utf_to_utf<char>(text));
}

void InstallerScreen::copy() const
{
    const wxString text = selected_text();
    if (text.empty())
        return;
    wxClipboardLocker lock;
    if (!lock)
        return;
    wxTheClipboard->SetData(new wxTextDataObject(text));
}

void InstallerScreen::select_all()
{
    if (m_rows.empty())
        return;
    m_anchor = Spot(0, 0);
    m_caret  = Spot(int(m_rows.size()) - 1, length(int(m_rows.size()) - 1));
    Refresh();
}

void InstallerScreen::on_paint(wxPaintEvent&)
{
    wxAutoBufferedPaintDC dc(this);
    render(dc);
}

void InstallerScreen::render(wxDC& dc) const
{
    dc.SetBackground(wxBrush(TERMINAL_BG));
    dc.Clear();
    dc.SetFont(GetFont());
    dc.SetBackgroundMode(wxBRUSHSTYLE_TRANSPARENT);
    dc.SetPen(*wxTRANSPARENT_PEN);
    const auto [from, to] = std::minmax(m_anchor, m_caret);
    const int          height = GetClientSize().y;
    std::vector<Curve> curves;
    for (int row = 0; row < int(m_rows.size()); ++row) {
        const wxRect line = cells(row, 0, 0);
        if (line.GetBottom() < 0)
            continue;
        if (line.y >= height)
            break;
        // Backgrounds, the selection over them, then the characters.
        int col = 0;
        for (const Span& span : m_rows[row]) {
            if (span.background.IsOk()) {
                dc.SetBrush(wxBrush(span.background));
                dc.DrawRectangle(cells(row, col, int(span.text.size())));
            }
            col += int(span.text.size());
        }
        if (from != to && row >= from.first && row <= to.first) {
            const int start = row == from.first ? from.second : 0;
            const int end   = row == to.first ? to.second : col;
            dc.SetBrush(wxBrush(SELECTION_BG));
            dc.DrawRectangle(cells(row, start, std::max(0, end - start)));
        }
        col = 0;
        for (const Span& span : m_rows[row]) {
            dc.SetTextForeground(span.color);
            for (size_t i = 0; i < span.text.size();) {
                // ASCII in one call: the code font has all of it, as wide as the cells.
                std::string ascii;
                for (size_t end = i; end < span.text.size() && span.text[end] >= U' ' && span.text[end] < 0x7F; ++end)
                    ascii += char(span.text[end]);
                if (!ascii.empty()) {
                    dc.DrawText(from_u8(ascii), cells(row, col + int(i), 0).x, line.y);
                    i += ascii.size();
                } else {
                    draw_cell(dc, span.text[i], cells(row, col + int(i), 1), span, curves);
                    ++i;
                }
            }
            col += int(span.text.size());
        }
    }
    if (!curves.empty()) {
        std::unique_ptr<wxGraphicsContext> gc(wxGraphicsContext::CreateFromUnknownDC(dc));
        if (gc)
            for (const Curve& curve : curves)
                draw_curve(*gc, curve);
    }
}

void InstallerScreen::draw_cell(wxDC& dc, char32_t c, const wxRect& cell, const Span& span, std::vector<Curve>& curves) const
{
    const int x = cell.x, y = cell.y, w = cell.width, h = cell.height;
    auto      fill = [&dc, x, y, w, h](double x0, double y0, double x1, double y1) {
        const int left = x + int(std::lround(x0 * w)), top = y + int(std::lround(y0 * h));
        dc.DrawRectangle(left, top, x + int(std::lround(x1 * w)) - left, y + int(std::lround(y1 * h)) - top);
    };
    if (c >= 0x2580 && c <= 0x259F) { // block elements, as the parts of the cell they fill
        dc.SetBrush(wxBrush(span.color));
        if (c == 0x2580)
            fill(0, 0, 1, 0.5);
        else if (c <= 0x2588) // eighths from the bottom
            fill(0, 1 - (c - 0x2580) / 8.0, 1, 1);
        else if (c <= 0x258F) // eighths from the left
            fill(0, 0, (0x2590 - c) / 8.0, 1);
        else if (c == 0x2590)
            fill(0.5, 0, 1, 1);
        else if (c <= 0x2593) { // shades: the color over the background by a quarter, a half and three quarters
            const wxColour back = span.background.IsOk() ? span.background : TERMINAL_BG;
            const double   by   = (c - 0x2590) / 4.0;
            auto mix = [by](unsigned char a, unsigned char b) { return (unsigned char) std::lround(a + (b - a) * by); };
            dc.SetBrush(wxBrush(wxColour(mix(back.Red(), span.color.Red()), mix(back.Green(), span.color.Green()),
                                         mix(back.Blue(), span.color.Blue()))));
            fill(0, 0, 1, 1);
        } else if (c == 0x2594)
            fill(0, 0, 1, 1 / 8.0);
        else if (c == 0x2595)
            fill(7 / 8.0, 0, 1, 1);
        else {
            const unsigned char parts = QUADRANTS[c - 0x2596];
            for (int part = 0; part < 4; ++part)
                if (parts & (1 << part))
                    fill(part % 2 * 0.5, part / 2 * 0.5, part % 2 * 0.5 + 0.5, part / 2 * 0.5 + 0.5);
        }
    } else if (c >= 0x2500 && c <= 0x254F) {
        dc.SetBrush(wxBrush(span.color));
        draw_arms(dc, BOX_ARMS + (c - 0x2500) * 4, cell);
    } else if (c >= 0x2574 && c <= 0x257F) {
        dc.SetBrush(wxBrush(span.color));
        draw_arms(dc, HALF_ARMS + (c - 0x2574) * 4, cell);
    } else if (c >= 0x256D && c <= 0x2573)
        curves.push_back({c, cell, span.color});
    else if (c > U' ' && c != 0x7F) { // a glyph from whichever font has it, centered in its cell
        const wxString glyph = from_u8(boost::locale::conv::utf_to_utf<char>(std::u32string(1, c)));
        dc.DrawText(glyph, x + (w - dc.GetTextExtent(glyph).x) / 2, y);
    }
}

void InstallerScreen::draw_arms(wxDC& dc, const char* arms, const wxRect& cell) const
{
    // Each arm runs from the edge to the middle of the cell, and into the line across it, so corners close.
    auto      thick = [this](char arm) { return arm == '0' ? 0 : arm == '2' ? 2 * m_line : m_line; };
    const int up = thick(arms[0]), right = thick(arms[1]), down = thick(arms[2]), left = thick(arms[3]);
    const int mx = cell.x + cell.width / 2, my = cell.y + cell.height / 2;
    const int across_v = std::max(up, down), across_h = std::max(left, right);
    const int v0 = across_v ? mx - across_v / 2 : mx, v1 = across_v ? v0 + across_v : mx;
    const int h0 = across_h ? my - across_h / 2 : my, h1 = across_h ? h0 + across_h : my;
    if (up)
        dc.DrawRectangle(mx - up / 2, cell.y, up, h1 - cell.y);
    if (down)
        dc.DrawRectangle(mx - down / 2, h0, down, cell.GetBottom() + 1 - h0);
    if (left)
        dc.DrawRectangle(cell.x, my - left / 2, v1 - cell.x, left);
    if (right)
        dc.DrawRectangle(v0, my - right / 2, cell.GetRight() + 1 - v0, right);
}

void InstallerScreen::draw_curve(wxGraphicsContext& gc, const Curve& curve) const
{
    const wxRect& cell = curve.cell;
    gc.SetPen(gc.CreatePen(wxGraphicsPenInfo(curve.color, m_line).Cap(wxCAP_BUTT)));
    // The middle of a light line, where draw_arms() puts it.
    const double   mx = cell.x + cell.width / 2 - m_line / 2 + m_line / 2.0;
    const double   my = cell.y + cell.height / 2 - m_line / 2 + m_line / 2.0;
    const double   left = cell.x, top = cell.y, right = cell.x + cell.width, bottom = cell.y + cell.height;
    wxGraphicsPath path = gc.CreatePath();
    const char32_t c    = curve.c;
    if (c <= 0x2570) { // a rounded corner, from the edge its line across leaves through to the edge of its line down or up
        const double x_edge = c == 0x256D || c == 0x2570 ? right : left;
        const double y_edge = c == 0x256D || c == 0x256E ? bottom : top;
        path.MoveToPoint(x_edge, my);
        path.AddArcToPoint(mx, my, mx, y_edge, std::min(std::abs(x_edge - mx), std::abs(y_edge - my)));
        path.AddLineToPoint(mx, y_edge);
    } else {
        if (c != 0x2572) {
            path.MoveToPoint(left, bottom);
            path.AddLineToPoint(right, top);
        }
        if (c != 0x2571) {
            path.MoveToPoint(left, top);
            path.AddLineToPoint(right, bottom);
        }
    }
    gc.StrokePath(path);
}

void InstallerScreen::on_left_down(wxMouseEvent& event)
{
    event.Skip();
    SetFocus(); // for Ctrl+C
    m_anchor = m_caret = spot_at(event.GetPosition());
    m_dragging         = true;
    if (!HasCapture())
        CaptureMouse();
    Refresh();
}

void InstallerScreen::on_motion(wxMouseEvent& event)
{
    event.Skip();
    if (!m_dragging)
        return;
    const Spot spot = spot_at(event.GetPosition());
    if (spot != m_caret) {
        m_caret = spot;
        Refresh();
    }
}

void InstallerScreen::on_left_up(wxMouseEvent& event)
{
    event.Skip();
    if (HasCapture())
        ReleaseMouse();
    m_dragging = false;
}

void InstallerScreen::on_capture_lost(wxMouseCaptureLostEvent&)
{
    m_dragging = false; // what it selected so far stays selected
    Refresh();
}

void InstallerScreen::on_key(wxKeyEvent& event)
{
    // wxMOD_CONTROL is Cmd on macOS.
    if (event.GetModifiers() == wxMOD_CONTROL && event.GetKeyCode() == 'C')
        copy();
    else if (event.GetModifiers() == wxMOD_CONTROL && event.GetKeyCode() == 'A')
        select_all();
    else
        event.Skip();
}

void InstallerScreen::on_menu(wxContextMenuEvent&)
{
    if (HasCapture())
        ReleaseMouse();
    m_dragging = false;
    CallAfter([this] {
        wxMenu      menu;
        wxMenuItem* copy_item = menu.Append(wxID_ANY, _L("Copy"));
        wxMenuItem* all_item  = menu.Append(wxID_ANY, _L("Select All"));
        copy_item->Enable(m_anchor != m_caret);
        menu.Bind(wxEVT_MENU, [this](wxCommandEvent&) { copy(); }, copy_item->GetId());
        menu.Bind(wxEVT_MENU, [this](wxCommandEvent&) { select_all(); }, all_item->GetId());
        PopupMenu(&menu);
    });
}

// A branch the agent and AFC_INSTALLER take: letters, digits, '.', '_', '-' and '/', starting with a letter or digit
// and ending with neither '.' nor '/', with no ".." or "//", at most 100 characters.
static bool branch_ok(const std::string& branch)
{
    static const std::regex name(R"((?!.*\.\.)(?!.*//)[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,98}[A-Za-z0-9_-])?)");
    return std::regex_match(branch, name);
}

// Asks for the branch AFC's updater or installer runs on, and which of the two: the script switches AFC's checkout
// to the branch first where AFC's folder is a git checkout (`git`), and elsewhere only shows it. Ends with wxID_OK
// once one is picked.
class InstallerBranchDialog : public DPIDialog
{
public:
    InstallerBranchDialog(wxWindow* parent, const std::string& branch, bool git);

    std::string        branch() const { return into_u8(m_branch->GetTextCtrl()->GetValue().Strip(wxString::both)); }
    const std::string& mode() const { return m_mode; }

protected:
    void on_dpi_changed(const wxRect& suggested_rect) override;

private:
    // Lets the scripts run while the box holds a branch.
    void check();
    void run(const std::string& mode);

    ::TextInput*   m_branch {nullptr};
    DialogButtons* m_buttons {nullptr};
    std::string    m_mode;
};

InstallerBranchDialog::InstallerBranchDialog(wxWindow* parent, const std::string& branch, bool git)
    // TRN Title of the dialog where the branch AFC's updater or installer runs on is typed
    : DPIDialog(parent, wxID_ANY, _L("Choose branch"), wxDefaultPosition, wxDefaultSize, wxCAPTION | wxCLOSE_BOX)
{
    SetBackgroundColour(*wxWHITE);
    SetFont(Label::Body_14);

    auto* sizer = new wxBoxSizer(wxVERTICAL);
    auto* text  = new wxStaticText(this, wxID_ANY,
                                   git ? _L("AFC's updater or installer switches AFC's checkout to this branch first, then its "
                                             "menu shows in a window here. Klipper restarts when it exits after changing "
                                             "anything.") :
                                         // TRN In the Choose branch dialog when AFC's folder on the printer is no git checkout
                                         _L("AFC's folder on this printer is not a git checkout, so the updater or installer "
                                            "runs on the files it holds and only shows this branch. Its menu shows in a window "
                                            "here, and Klipper restarts when it exits after changing anything."));
    text->Wrap(FromDIP(440));
    sizer->Add(text, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(20));
    m_branch = new ::TextInput(this, from_u8(branch), wxEmptyString, wxEmptyString, wxDefaultPosition,
                               wxSize(FromDIP(440), FromDIP(32)));
    m_branch->GetTextCtrl()->Bind(wxEVT_TEXT, [this](wxCommandEvent& event) {
        event.Skip();
        check();
    });
    sizer->Add(m_branch, 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(10));
    // TRN Under the box where the branch for AFC's updater or installer is typed: the characters a branch may hold
    auto* rule = new wxStaticText(this, wxID_ANY, _L("Letters, digits, '.', '_', '-' and '/'"));
    rule->SetFont(Label::Body_12);
    sizer->Add(rule, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(6));

    m_buttons = new DialogButtons(this, {L("Run updater"), L("Run installer"), "Cancel"}, _L("Run updater"));
    m_buttons->GetButtonFromIndex(0)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { run("update"); });
    m_buttons->GetButtonFromIndex(1)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { run("install"); });
    m_buttons->GetCANCEL()->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { EndModal(wxID_CANCEL); });
    sizer->Add(m_buttons, 0, wxEXPAND | wxTOP, FromDIP(10));

    check();
    SetSizerAndFit(sizer);
    CenterOnParent();
    wxGetApp().UpdateDlgDarkUI(this);
}

void InstallerBranchDialog::check()
{
    const bool ok = branch_ok(branch());
    m_buttons->GetButtonFromIndex(0)->Enable(ok);
    m_buttons->GetButtonFromIndex(1)->Enable(ok);
}

void InstallerBranchDialog::run(const std::string& mode)
{
    if (!branch_ok(branch()))
        return;
    m_mode = mode;
    EndModal(wxID_OK);
}

void InstallerBranchDialog::on_dpi_changed(const wxRect&)
{
    m_branch->Rescale();
    GetSizer()->SetSizeHints(this);
    Refresh();
}

// Asks for the password the script's password prompt waits on, such as su's for root on a Snapmaker U1. The box
// hides what is typed. Ends with wxID_OK on Send or Enter.
class InstallerPasswordDialog : public DPIDialog
{
public:
    explicit InstallerPasswordDialog(wxWindow* parent);

    std::string password() const { return into_u8(m_password->GetTextCtrl()->GetValue()); }

protected:
    void on_dpi_changed(const wxRect& suggested_rect) override;

private:
    ::TextInput* m_password {nullptr};
};

InstallerPasswordDialog::InstallerPasswordDialog(wxWindow* parent)
    // TRN Title of the dialog where the password AFC's updater or installer asks for is typed
    : DPIDialog(parent, wxID_ANY, _L("Password"), wxDefaultPosition, wxDefaultSize, wxCAPTION | wxCLOSE_BOX)
{
    SetBackgroundColour(*wxWHITE);
    SetFont(Label::Body_14);

    auto* sizer = new wxBoxSizer(wxVERTICAL);
    auto* text  = new wxStaticText(this, wxID_ANY,
                                   _L("The script on the printer is asking for a password. It goes to the script "
                                      "directly, not through G-code, so it stays out of the printer's console history."));
    text->Wrap(FromDIP(440));
    sizer->Add(text, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(20));
    m_password = new ::TextInput(this, wxEmptyString, wxEmptyString, wxEmptyString, wxDefaultPosition,
                                 wxSize(FromDIP(440), FromDIP(32)), wxTE_PASSWORD);
    m_password->Bind(wxEVT_TEXT_ENTER, [this](wxCommandEvent&) { CallAfter([this] { EndModal(wxID_OK); }); });
    sizer->Add(m_password, 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(10));
    // TRN Under the box where a password for AFC's updater or installer is typed. snapmaker is the password itself:
    // keep it as it is
    auto* note = new wxStaticText(this, wxID_ANY, _L("A Snapmaker U1's root password is snapmaker unless it was changed."));
    note->SetFont(Label::Body_12);
    sizer->Add(note, 0, wxLEFT | wxRIGHT | wxTOP, FromDIP(6));

    auto* buttons = new DialogButtons(this, {L("Send"), "Cancel"}, _L("Send"));
    buttons->GetButtonFromIndex(0)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { EndModal(wxID_OK); });
    buttons->GetCANCEL()->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { EndModal(wxID_CANCEL); });
    sizer->Add(buttons, 0, wxEXPAND | wxTOP, FromDIP(10));

    SetSizerAndFit(sizer);
    CenterOnParent();
    wxGetApp().UpdateDlgDarkUI(this);
    m_password->GetTextCtrl()->SetFocus();
}

void InstallerPasswordDialog::on_dpi_changed(const wxRect&)
{
    m_password->Rescale();
    GetSizer()->SetSizeHints(this);
    Refresh();
}

void AgentInstallerDialog::choose_branch(const std::string& dev_id)
{
    DeviceManager* manager = wxGetApp().getDeviceManager();
    MachineObject* obj     = manager ? manager->get_selected_machine() : nullptr;
    if (!obj || obj->get_dev_id() != dev_id)
        return;
    InstallerBranchDialog dlg(wxGetApp().mainframe, obj->agent_installer.branch, obj->agent_installer.git);
    if (dlg.ShowModal() == wxID_OK)
        show(dev_id, dlg.mode(), true, dlg.branch());
}

void AgentInstallerDialog::show(const std::string& dev_id, const std::string& mode, bool start, const std::string& branch)
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
        installer.password  = false;
        installer.exit_code = -1;
        installer.starting  = true;
        installer.mode      = mode;
    }
    AgentInstallerDialog dlg(wxGetApp().mainframe, dev_id, mode);
    // The start goes before the watch, so the first screen the window gets is the new session's.
    if (start)
        obj->command_agent_installer_start(mode, branch);
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

    m_screen = new InstallerScreen(this);
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
    // TRN Opens the box where the password AFC's updater or installer asks for is typed
    m_password_button = new Button(this, _L("Enter password"));
    m_password_button->SetStyle(ButtonStyle::Confirm, ButtonType::Choice);
    m_password_button->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { CallAfter([this] { ask_password(); }); });
    m_password_button->Hide();
    answer_row->Add(answer_label, 0, wxALIGN_CENTER_VERTICAL | wxRIGHT, FromDIP(10));
    answer_row->Add(m_answer, 1, wxALIGN_CENTER_VERTICAL);
    answer_row->Add(m_send, 0, wxALIGN_CENTER_VERTICAL | wxLEFT, FromDIP(10));
    answer_row->Add(m_password_button, 0, wxALIGN_CENTER_VERTICAL | wxLEFT, FromDIP(10));
    sizer->Add(answer_row, 0, wxEXPAND | wxLEFT | wxRIGHT | wxTOP, FromDIP(10));

    m_buttons = new DialogButtons(this, {L("Stop"), L("Close")}, "", 1);
    m_buttons->GetButtonFromIndex(0)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { CallAfter([this] { stop(); }); });
    m_buttons->GetButtonFromIndex(1)->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { EndModal(wxID_CLOSE); });
    sizer->Add(m_buttons, 0, wxEXPAND | wxTOP, FromDIP(10));

    enable(false, false);
    SetSizerAndFit(sizer);
    make_tall();
    CenterOnParent();
    wxGetApp().UpdateDlgDarkUI(this);
    m_text_color = m_status->GetForegroundColour();

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

    // The agent takes a password while AFC says the script's terminal waits on one.
    const bool live     = m_connected && installer.running && !installer.away;
    const bool password = live && installer.password;
    wxString   status;
    bool       failed = false;
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
    else if (password)
        status = _L("The script is asking for a password.");
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
    wxString hint;
    if (installer.hint == "password" && !password)
        // TRN Shown while AFC's updater or installer waits for a password, such as su asking for root's password
        hint = _L("The script is asking for a password, which can't be typed here: answers go through G-code, "
                  "which Klipper writes to its log. Stop the script and run that step over SSH.");
    else if (installer.hint == "sudo")
        hint = _L("A step needed sudo, which asked for a password. No password can be typed here: "
                  "run that step over SSH, or let this user run sudo without a password.");

    // No answer goes to a password prompt: answers travel as G-code.
    const bool answers = live && installer.hint != "password" && !installer.password;
    m_screen->set_lines(installer.lines);
    draw_choices(answers ? installer.choices : Choices());
    m_password_button->Show(password);
    draw_status(status, failed, hint);
    enable(answers, m_connected && installer.running);
    // The password box opens by itself once for each prompt; Enter password opens it again.
    if (!password)
        m_asked = false;
    else if (!m_asked) {
        m_asked = true;
        CallAfter([this] { ask_password(); });
    }
}

void AgentInstallerDialog::ask_password()
{
    MachineObject* obj = machine();
    if (m_asking || !obj || !obj->agent_installer.password)
        return;
    m_asking = true;
    InstallerPasswordDialog dlg(this);
    const bool send = dlg.ShowModal() == wxID_OK;
    m_asking = false;
    // The prompt may have ended, or another printer been picked, while the box was open.
    MachineObject* still = machine();
    if (send && still && still->agent_installer.password)
        still->command_agent_installer_password(dlg.password());
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

void AgentInstallerDialog::make_tall()
{
    // AFC's menus run to about 58 lines, and their choices' buttons take some of the screen's height. Until the window
    // is shown it has no display of its own, so it takes its parent's, which CenterOnParent() centers it on.
    const wxRect area = wxDisplay(IsShown() ? static_cast<wxWindow*>(this) : GetParent()).GetClientArea();
    const int    tall = GetSize().y - m_screen->GetMinSize().y + m_screen->height_for(TALL_ROWS);
    SetSize(wxSize(GetSize().x, std::max(GetSize().y, std::min(tall, area.height))));
}

void AgentInstallerDialog::on_dpi_changed(const wxRect&)
{
    m_answer->Rescale();
    m_send->Rescale();
    m_password_button->Rescale();
    m_screen->rescale();
    // The choices' rows were laid out for the old size.
    const Choices choices = m_choices;
    m_choices.clear();
    draw_choices(choices);
    GetSizer()->SetSizeHints(this);
    make_tall();
    Refresh();
}

}} // namespace Slic3r::GUI
