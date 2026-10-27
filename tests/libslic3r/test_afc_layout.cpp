#include <catch2/catch_test_macros.hpp>

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
    CHECK(changes.loads == std::vector<int>{2, 1, 1});
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
