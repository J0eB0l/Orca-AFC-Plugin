#pragma once

#include <string>
#include <vector>

// The suggested spool layout for an AFC printer with several toolheads: which lane each filament of
// a print goes in once the print's filaments are grouped onto toolheads, so that the fewest spools
// move and the filaments loaded most often sit in the quickest units. docs/HLSD/afc-spool-layout.md
namespace Slic3r::AFCLayout {

// An AFC lane as the printer agent reports it.
struct Lane
{
    std::string name;
    std::string unit;
    int         toolhead{-1};
    int         filament{-1}; // the project filament whose spool it holds, -1 none or not in the project
    bool        loaded{false};
    double      seconds{0.};  // a lane change on its unit (rank_units())
    // Behind a filament switch: its inlet, the same number for every lane plumbed into it (-1 none), which
    // holds one filament at a time, and the other toolhead the switch can send the lane to (-1 when it cannot).
    int         inlet{-1};
    int         other_toolhead{-1};
};

// Lane changes over a print's layers with its filaments on toolhead_of (-1 not printed): how many
// times each filament is loaded in place of another on its toolhead, and how many changes that makes
// in all. A toolhead's first load is no lane change: whatever lane feeds it, it loads once. Each toolhead starts a layer on the filament it holds when the layer uses it
// and ends it on one the next layer uses, which no order of a layer's filaments beats.
struct Changes
{
    std::vector<int> loads;
    int              lane_changes{0};
};
Changes count_changes(const std::vector<std::vector<unsigned int>>& layer_filaments, const std::vector<int>& toolhead_of);

// What a print spends beside its lane changes, which a filament switch trades them against: it sends a lane
// to either of two toolheads, so two filaments of one unit can print through a nozzle each and skip the flush
// between them, at the cost of a tool change.
struct Times
{
    std::vector<std::vector<std::vector<double>>> flush; // [toolhead][from][to]: the seconds to flush `from` out for `to`
    double                                        tool_change{0.}; // from one toolhead to another
    double flush_seconds(int toolhead, int from, int to) const;    // 0 where flush has none
};

// A print's layers walked with its filaments on toolhead_of and each one's lane behind the switch inlet
// inlet_of (-1 none). A load counts the filaments it takes out: the one in its toolhead, as count_changes()
// counts, and the one another toolhead holds through the same inlet, which holds one filament at a time. That
// one loads again when it next prints. A toolhead flushes only when its nozzle last printed another filament.
// A layer starts on the toolhead it is on and ends on one the next layer uses.
struct Run
{
    std::vector<int> loads;
    int              lane_changes{0};
    int              tool_changes{0};
    int              flushes{0};
    double           seconds{0.}; // of the flushes and tool changes
};
Run run(const std::vector<std::vector<unsigned int>>& layer_filaments, const std::vector<int>& toolhead_of,
        const std::vector<int>& inlet_of, const Times& times);

// Each unit's lane change seconds in the order it is preferred: the units named in `preferred` first,
// in that order, then the others quickest first. The seconds the units measured, sorted, go to them in
// that order, so a preferred unit counts as quick without the times leaving the range they span.
std::vector<double> rank_units(const std::vector<std::string>& units, const std::vector<double>& seconds,
                               const std::vector<std::string>& preferred);

// A spool to move from lane `from` to lane `to` (indices into the lanes; from -1: not loaded on the
// printer, to -1: take it off). filament is the project filament, -1 for a spool the project lacks.
struct Move
{
    int filament{-1};
    int from{-1};
    int to{-1};
};

struct Plan
{
    std::vector<int>  lane_of; // per filament, the lane it prints from, -1 not printed (or no lane left)
    std::vector<Move> moves;
    double            cost{0.}; // the seconds of moves and loads, a printed filament left without a lane costing 1e9
};

// The grouping engine numbers its groups in no particular order: toolhead_of with each group (0 to
// heads - 1) moved to the toolhead where place() would cost the least for all groups together, so a
// group stays on the toolhead whose lanes already hold its spools.
std::vector<int> fit_toolheads(const std::vector<Lane>& lanes, const std::vector<int>& toolhead_of, const std::vector<int>& loads,
                               double move_seconds, int heads);

// The lanes for the printed filaments on their toolheads (toolhead_of, -1 for a filament not printed):
// each spool moved costs move_seconds, each load the seconds of its lane, and a lane holding a spool
// the print does not use costs one move to clear. The spools cleared go to the lanes the moved spools
// left, else to empty lanes, else off the printer.
Plan place(const std::vector<Lane>& lanes, const std::vector<int>& toolhead_of, const std::vector<int>& loads, double move_seconds);

// toolhead_of improved one filament at a time: each printed filament tried on every other toolhead, and
// each pair of printed filaments on different toolheads swapped, while that lowers the cost of placing
// them (place()) with each filament's loads on its new toolhead (count_changes()). The grouping engine
// ignores where the spools are and breaks its ties anyhow, so this settles a tie on the spools' side.
std::vector<int> improve(const std::vector<Lane>& lanes, const std::vector<std::vector<unsigned int>>& layer_filaments,
                         const std::vector<int>& toolhead_of, double move_seconds, int heads);

// place() on a printer with a filament switch. A lane whose spool toolhead_of puts on the other toolhead its
// switch reaches feeds that one, so the spool stays where it is; a spool that moves prints on the toolhead
// its new lane feeds now. The loads are run()'s, counted from the lanes the spools are in to place them and
// again from the lanes they get, and the cost adds run()'s seconds, so a filament goes through the switch
// only when the print is shorter for it.
struct Routed
{
    Plan plan;
    Run  run;
};
Routed route(const std::vector<Lane>& lanes, const std::vector<std::vector<unsigned int>>& layer_filaments,
             const std::vector<int>& toolhead_of, double move_seconds, const Times& times);

// improve() by the cost of route().
std::vector<int> improve(const std::vector<Lane>& lanes, const std::vector<std::vector<unsigned int>>& layer_filaments,
                         const std::vector<int>& toolhead_of, double move_seconds, int heads, const Times& times);

} // namespace Slic3r::AFCLayout
