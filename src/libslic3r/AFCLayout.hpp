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
};

// Lane changes over a print's layers with its filaments on toolhead_of (-1 not printed): how many
// times each filament is loaded (its first load included) and how many changes the toolheads make
// after their first load. Each toolhead starts a layer on the filament it holds when the layer uses it
// and ends it on one the next layer uses, which no order of a layer's filaments beats.
struct Changes
{
    std::vector<int> loads;
    int              lane_changes{0};
};
Changes count_changes(const std::vector<std::vector<unsigned int>>& layer_filaments, const std::vector<int>& toolhead_of);

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

} // namespace Slic3r::AFCLayout
