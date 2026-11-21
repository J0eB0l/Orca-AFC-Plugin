#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include <string>
#include <vector>

#include "libslic3r/AFCLayout.hpp"

using namespace Slic3r::AFCLayout;

TEST_CASE("count_changes counts each toolhead's loads", "[AFCLayout]")
{
    // Filaments 0 and 1 on toolhead 0, 2 on toolhead 1.
    const std::vector<int> heads{0, 0, 1};
    const Changes          changes = count_changes({{0, 2}, {0, 1, 2}, {1, 2}, {0}}, heads);
    // Toolhead 0: 0, then 0 kept and 1 loaded (ending on 1, which the next layer uses), then 1, then 0.
    // The first loads (0 on toolhead 0, 2 on toolhead 1) change no lane.
    CHECK(changes.loads == std::vector<int>{1, 1, 0});
    CHECK(changes.lane_changes == 2);
    // On one toolhead every change of filament is a lane change.
    CHECK(count_changes({{0, 2}, {0, 1, 2}, {1, 2}, {0}}, {0, 0, 0}).lane_changes == 5);
}

TEST_CASE("rank_units puts the preferred units first", "[AFCLayout]")
{
    const std::vector<std::string> units{"Turtle_1", "AMS_1", "Ace_1"};
    const std::vector<double>      seconds{77., 56., 95.};
    CHECK(rank_units(units, seconds, {}) == std::vector<double>{77., 56., 95.});
    CHECK(rank_units(units, seconds, {"Ace_1"}) == std::vector<double>{95., 77., 56.});
    CHECK(rank_units(units, seconds, {"Ace_1", "Turtle_1"}) == std::vector<double>{77., 95., 56.});
    CHECK(rank_units(units, seconds, {"Turtle_1", "Ace_1", "AMS_1"}) == std::vector<double>{56., 95., 77.});
}

TEST_CASE("place keeps spools where they are when that is best", "[AFCLayout]")
{
    std::vector<Lane> lanes{{"lane0", "A", 0, 0, true, 60.}, {"lane1", "A", 0, 1, true, 60.},
                            {"lane2", "B", 1, 2, true, 60.}, {"lane3", "B", 1, -1, false, 60.}};
    const Plan plan = place(lanes, {0, 0, 1}, {3, 2, 1}, 60.);
    CHECK(plan.lane_of == std::vector<int>{0, 1, 2});
    CHECK(plan.moves.empty());
}

TEST_CASE("place moves a spool to its toolhead and the busiest one to the quickest unit", "[AFCLayout]")
{
    // Filament 1 now prints on toolhead 1, whose lanes are a quick unit and a slow one.
    std::vector<Lane> lanes{{"lane0", "A", 0, 0, true, 60.}, {"lane1", "A", 0, 1, true, 60.},
                            {"lane2", "Quick", 1, -1, false, 50.}, {"lane3", "Slow", 1, -1, false, 100.}};
    Plan plan = place(lanes, {0, 1}, {4, 10}, 60.);
    CHECK(plan.lane_of == std::vector<int>{0, 2});
    REQUIRE(plan.moves.size() == 1);
    CHECK((plan.moves[0].filament == 1 && plan.moves[0].from == 1 && plan.moves[0].to == 2));

    // A spool the print does not use makes way, into the lane the moved spool left.
    lanes[2].loaded   = true;
    lanes[2].filament = 5;
    lanes[3].loaded   = true;
    lanes[3].filament = 6;
    plan              = place(lanes, {0, 1}, {4, 10}, 60.);
    CHECK(plan.lane_of == std::vector<int>{0, 2});
    REQUIRE(plan.moves.size() == 2);
    CHECK((plan.moves[1].filament == 5 && plan.moves[1].from == 2 && plan.moves[1].to == 1));
}

TEST_CASE("place takes a spool that has nowhere to go off the printer", "[AFCLayout]")
{
    std::vector<Lane> lanes{{"lane0", "A", 0, 0, true, 60.}, {"lane1", "B", 1, 7, true, 60.}};
    // Filament 1 has no spool on the printer yet.
    const Plan plan = place(lanes, {0, 1}, {1, 1}, 60.);
    CHECK(plan.lane_of == std::vector<int>{0, 1});
    REQUIRE(plan.moves.size() == 2);
    CHECK((plan.moves[0].filament == 1 && plan.moves[0].from == -1 && plan.moves[0].to == 1));
    CHECK((plan.moves[1].filament == 7 && plan.moves[1].from == 1 && plan.moves[1].to == -1));
}

TEST_CASE("fit_toolheads keeps a group on the toolhead that holds its spools", "[AFCLayout]")
{
    // All three spools sit on toolhead 0, the quickest unit; the engine put them on toolheads 1 to 3.
    std::vector<Lane> lanes{{"lane0", "Turtle", 0, 0, true, 50.},  {"lane1", "Turtle", 0, 1, true, 50.},
                            {"lane2", "Turtle", 0, 2, true, 50.},  {"lane3", "A", 1, 9, true, 100.},
                            {"lane4", "B", 2, -1, false, 100.}, {"lane5", "C", 3, -1, false, 100.}};
    std::vector<int>  groups(10, -1);
    groups[0] = 1;
    groups[1] = 2;
    groups[2] = 3;
    const std::vector<int> loads{1, 5, 1, 0, 0, 0, 0, 0, 0, 0};
    const std::vector<int> heads = fit_toolheads(lanes, groups, loads, 60., 4);
    // The most loaded filament stays in the quickest unit; the others take a toolhead each.
    CHECK(heads[1] == 0);
    CHECK((heads[0] != heads[2] && heads[0] > 0 && heads[2] > 0));
    CHECK(heads[9] == -1);
    const Plan plan = place(lanes, heads, loads, 60.);
    CHECK(plan.lane_of[1] == 1);
    // The empty toolheads take a spool each before the one whose lane must be cleared.
    CHECK(plan.moves.size() == 2);
}

TEST_CASE("improve moves a spool only when the time it saves outweighs the move", "[AFCLayout]")
{
    // Both spools on toolhead 0; toolhead 1 has an empty lane. Printing filament 1 on toolhead 1 saves
    // two lane changes of a minute.
    const std::vector<Lane> lanes{{"lane0", "A", 0, 0, true, 60.}, {"lane1", "A", 0, 1, true, 60.},
                                  {"lane2", "B", 1, -1, false, 60.}};
    const std::vector<std::vector<unsigned int>> layers{{0}, {1}, {0}};
    CHECK(improve(lanes, layers, {0, 0}, 200., 2) == std::vector<int>{0, 0});
    const std::vector<int> heads = improve(lanes, layers, {0, 0}, 0.01, 2);
    CHECK(heads[0] != heads[1]);
}

TEST_CASE("improve leaves a spool a toolhead never swaps out in a slow unit", "[AFCLayout]")
{
    // Filament 1 is the only one on toolhead 1, in a slow unit, beside an empty lane of a quick one. It
    // loads once whichever lane feeds it, so moving it gains nothing.
    const std::vector<Lane> lanes{{"lane0", "A", 0, 0, true, 60.}, {"lane1", "Slow", 1, 1, true, 120.},
                                  {"lane2", "Quick", 1, -1, false, 40.}};
    const std::vector<std::vector<unsigned int>> layers{{0, 1}, {0, 1}};
    const std::vector<int> heads = improve(lanes, layers, {0, 1}, 0.01, 2);
    CHECK(heads == std::vector<int>{0, 1});
    CHECK(place(lanes, heads, count_changes(layers, heads).loads, 0.01).moves.empty());
}

TEST_CASE("improve undoes a swap of filaments that changes nothing", "[AFCLayout]")
{
    // Every layer prints all four filaments, so any two per toolhead count the same lane changes; the
    // grouping engine swapped 1 and 2, which would move two spools for nothing.
    const std::vector<Lane> lanes{{"lane0", "A", 0, 0, true, 60.}, {"lane1", "A", 0, 1, true, 60.},
                                  {"lane2", "B", 1, 2, true, 60.}, {"lane3", "B", 1, 3, true, 60.}};
    const std::vector<std::vector<unsigned int>> layers{{0, 1, 2, 3}, {0, 1, 2, 3}, {0, 1, 2, 3}};
    const std::vector<int> heads = improve(lanes, layers, {0, 1, 0, 1}, 0.01, 2);
    const Plan plan = place(lanes, heads, count_changes(layers, heads).loads, 0.01);
    CHECK(plan.moves.empty());
    CHECK(plan.cost == place(lanes, {0, 0, 1, 1}, count_changes(layers, {0, 0, 1, 1}).loads, 0.01).cost);
}

TEST_CASE("run counts the loads of count_changes when no lane is behind a switch", "[AFCLayout]")
{
    const std::vector<std::vector<unsigned int>> layers{{0, 2}, {0, 1, 2}, {1, 2}, {0}};
    const std::vector<int>                       heads{0, 0, 1};
    Times                                        times;
    times.tool_change = 10.;
    times.flush.assign(2, std::vector<std::vector<double>>(3, std::vector<double>(3, 0.)));
    times.flush[0][0][1] = 30.;
    times.flush[0][1][0] = 40.;
    const Run walked = run(layers, heads, {}, times);
    CHECK(walked.loads == count_changes(layers, heads).loads);
    CHECK(walked.lane_changes == 2);
    // Toolhead 0 prints 0, then 1, then 0 again: two flushes. Each layer starts on the toolhead the one
    // before ended on, so the four layers change toolheads four times.
    CHECK(walked.flushes == 2);
    CHECK(walked.tool_changes == 4);
    CHECK_THAT(walked.seconds, Catch::Matchers::WithinAbs(4 * 10. + 30. + 40., 1e-9));
}

TEST_CASE("run ends a layer on a toolhead the next layer uses", "[AFCLayout]")
{
    // Layer 0 prints on toolheads 0 and 1, layer 1 only on toolhead 0: ending layer 0 there saves a change.
    const Run walked = run({{0, 1}, {0}}, {0, 1}, {}, Times());
    CHECK(walked.tool_changes == 1);
    CHECK(walked.lane_changes == 0);
}

TEST_CASE("run takes a filament out of its toolhead when another loads through its inlet", "[AFCLayout]")
{
    const std::vector<std::vector<unsigned int>> layers{{0}, {1}, {0}, {1}};
    Times                                        times;
    times.flush.assign(2, std::vector<std::vector<double>>(2, std::vector<double>(2, 50.)));
    // Both lanes behind one inlet, a toolhead each: every change takes the other filament out, and each
    // nozzle only ever prints its own, so nothing is flushed.
    const Run split = run(layers, {0, 1}, {0, 0}, times);
    CHECK(split.loads == std::vector<int>{1, 2});
    CHECK(split.lane_changes == 3);
    CHECK(split.tool_changes == 3);
    CHECK(split.flushes == 0);
    // On one toolhead the same changes flush each time.
    const Run shared = run(layers, {0, 0}, {0, 0}, times);
    CHECK(shared.lane_changes == 3);
    CHECK(shared.tool_changes == 0);
    CHECK(shared.flushes == 3);
    CHECK_THAT(shared.seconds, Catch::Matchers::WithinAbs(150., 1e-9));
    // Behind an inlet each, both stay loaded.
    CHECK(run(layers, {0, 1}, {0, 1}, times).lane_changes == 0);
}

TEST_CASE("run counts both filaments a load takes out", "[AFCLayout]")
{
    // Filaments 0 and 2 share an inlet, 1 has its own; 1 and 2 print on toolhead 1.
    const Run walked = run({{0}, {1}, {2}, {0}}, {0, 1, 1}, {0, 1, 0}, Times());
    // Loading 2 takes 1 out of its toolhead and 0 out through the inlet; 0 then goes back into an empty
    // toolhead and takes 2 out.
    CHECK(walked.loads == std::vector<int>{1, 0, 2});
    CHECK(walked.lane_changes == 3);
}

TEST_CASE("Times has no flush where its table has none", "[AFCLayout]")
{
    Times times;
    times.flush.assign(1, std::vector<std::vector<double>>(2, std::vector<double>(2, 7.)));
    CHECK_THAT(times.flush_seconds(0, 1, 0), Catch::Matchers::WithinAbs(7., 1e-9));
    CHECK_THAT(times.flush_seconds(1, 1, 0), Catch::Matchers::WithinAbs(0., 1e-9));
    CHECK_THAT(times.flush_seconds(0, 2, 0), Catch::Matchers::WithinAbs(0., 1e-9));
    CHECK_THAT(times.flush_seconds(0, 0, 2), Catch::Matchers::WithinAbs(0., 1e-9));
    CHECK_THAT(times.flush_seconds(-1, 0, 0), Catch::Matchers::WithinAbs(0., 1e-9));
}

namespace {

// Two spools in one unit behind a filament switch, which sends its lanes to toolhead 0 or 1.
std::vector<Lane> switch_lanes()
{
    Lane first{"lane0", "A", 0, 0, true, 60.};
    first.inlet          = 0;
    first.other_toolhead = 1;
    Lane second          = first;
    second.name          = "lane1";
    second.filament      = 1;
    return {first, second};
}

Times switch_times(double flush, double tool_change)
{
    Times times;
    times.tool_change = tool_change;
    times.flush.assign(2, std::vector<std::vector<double>>(2, std::vector<double>(2, flush)));
    return times;
}

} // namespace

TEST_CASE("route prints a spool through the switch without moving it", "[AFCLayout]")
{
    const std::vector<std::vector<unsigned int>> layers{{0}, {1}, {0}, {1}, {0}};
    const Routed routed = route(switch_lanes(), layers, {0, 1}, 0.01, switch_times(100., 10.));
    CHECK(routed.plan.lane_of == std::vector<int>{0, 1});
    CHECK(routed.plan.moves.empty());
    CHECK(routed.run.lane_changes == 4);
    CHECK(routed.run.flushes == 0);
    // Four lane changes of a minute and four tool changes.
    CHECK_THAT(routed.plan.cost, Catch::Matchers::WithinAbs(4 * 60. + 4 * 10., 1e-6));
    // On one toolhead the same lane changes flush.
    CHECK_THAT(route(switch_lanes(), layers, {0, 0}, 0.01, switch_times(100., 10.)).plan.cost,
               Catch::Matchers::WithinAbs(4 * 60. + 4 * 100., 1e-6));
}

TEST_CASE("route moves a spool to a toolhead its switch does not reach", "[AFCLayout]")
{
    std::vector<Lane> lanes = switch_lanes();
    lanes.push_back({"lane2", "B", 2, -1, false, 60.});
    const Routed routed = route(lanes, {{0}, {1}, {0}}, {0, 2}, 0.01, switch_times(100., 10.));
    CHECK(routed.plan.lane_of == std::vector<int>{0, 2});
    REQUIRE(routed.plan.moves.size() == 1);
    CHECK(routed.plan.moves[0].to == 2);
    // Off the switch nothing takes the other filament out.
    CHECK(routed.run.lane_changes == 0);
}

TEST_CASE("improve sends a filament through the switch only when the print is shorter for it", "[AFCLayout]")
{
    const std::vector<std::vector<unsigned int>> layers{{0}, {1}, {0}, {1}, {0}};
    // A flush takes longer than a tool change: a nozzle each.
    const std::vector<int> split = improve(switch_lanes(), layers, {0, 0}, 0.01, 2, switch_times(100., 10.));
    CHECK(split[0] != split[1]);
    // A tool change takes longer than a flush, or as long: both stay on the toolhead their unit feeds.
    CHECK(improve(switch_lanes(), layers, {0, 0}, 0.01, 2, switch_times(5., 100.)) == std::vector<int>{0, 0});
    CHECK(improve(switch_lanes(), layers, {0, 0}, 0.01, 2, switch_times(10., 10.)) == std::vector<int>{0, 0});
}

TEST_CASE("improve leaves a lane its switch cannot move on its toolhead", "[AFCLayout]")
{
    // The switch is not calibrated: neither lane has another toolhead, and toolhead 1 has no lane.
    std::vector<Lane> lanes = switch_lanes();
    lanes[0].other_toolhead = -1;
    lanes[1].other_toolhead = -1;
    CHECK(improve(lanes, {{0}, {1}, {0}, {1}, {0}}, {0, 0}, 0.01, 2, switch_times(100., 10.)) == std::vector<int>{0, 0});
}

TEST_CASE("switch_in_place gives two filaments of one unit a nozzle each when a flush takes longer than a tool change", "[AFCLayout]")
{
    const std::vector<std::vector<unsigned int>> layers{{0}, {1}, {0}, {1}, {0}};
    const std::vector<int> split = switch_in_place(layers, {0, 0}, {1, 1}, {0, 0}, switch_times(100., 10.), 60.);
    REQUIRE(split.size() == 2);
    CHECK(split[0] != split[1]);
}

TEST_CASE("switch_in_place keeps a filament on its own toolhead when the print is no shorter on the other", "[AFCLayout]")
{
    const std::vector<std::vector<unsigned int>> layers{{0}, {1}, {0}, {1}, {0}};
    CHECK(switch_in_place(layers, {0, 0}, {1, 1}, {0, 0}, switch_times(5., 100.), 60.) == std::vector<int>{0, 0});
    CHECK(switch_in_place(layers, {0, 0}, {1, 1}, {0, 0}, switch_times(10., 10.), 60.) == std::vector<int>{0, 0});
}

TEST_CASE("switch_in_place leaves a filament its switch cannot send elsewhere", "[AFCLayout]")
{
    const std::vector<std::vector<unsigned int>> layers{{0}, {1}, {0}, {1}, {0}};
    CHECK(switch_in_place(layers, {0, 0}, {-1, -1}, {0, 0}, switch_times(100., 10.), 60.) == std::vector<int>{0, 0});
    // A filament the print does not use has no toolhead to leave.
    CHECK(switch_in_place(layers, {0, 0, -1}, {-1, -1, 1}, {0, 0, 0}, switch_times(100., 10.), 60.) == std::vector<int>{0, 0, -1});
}

TEST_CASE("switch_in_place counts the lane changes a filament saves on a nozzle of its own", "[AFCLayout]")
{
    // Filament 0 is behind the switch, filament 1 is in a unit of toolhead 0 the switch does not serve. On one
    // toolhead they change lanes four times; with filament 0 through the switch neither is ever unloaded.
    const std::vector<std::vector<unsigned int>> layers{{0}, {1}, {0}, {1}, {0}};
    const Times                                  times = switch_times(5., 20.);
    // Four flushes of 5 s against four tool changes of 20 s: not worth it by those alone.
    CHECK(switch_in_place(layers, {0, 0}, {1, -1}, {0, -1}, times, 0.) == std::vector<int>{0, 0});
    // Each lane change saved is a minute.
    CHECK(switch_in_place(layers, {0, 0}, {1, -1}, {0, -1}, times, 60.) == std::vector<int>{1, 0});
}
