#include "AFCLayout.hpp"

#include <algorithm>
#include <limits>
#include <map>
#include <numeric>
#include <set>

namespace Slic3r::AFCLayout {

Changes count_changes(const std::vector<std::vector<unsigned int>>& layer_filaments, const std::vector<int>& toolhead_of)
{
    Changes out;
    out.loads.assign(toolhead_of.size(), 0);
    std::map<int, int> last; // per toolhead, the filament it holds
    for (size_t layer = 0; layer < layer_filaments.size(); ++layer) {
        std::map<int, std::set<int>> on_head;
        for (unsigned int filament : layer_filaments[layer])
            if (filament < toolhead_of.size() && toolhead_of[filament] >= 0)
                on_head[toolhead_of[filament]].insert(int(filament));
        std::set<int> next;
        if (layer + 1 < layer_filaments.size())
            next.insert(layer_filaments[layer + 1].begin(), layer_filaments[layer + 1].end());
        for (const auto& [head, filaments] : on_head) {
            const auto held  = last.find(head);
            const int  start = held != last.end() && filaments.count(held->second) ? held->second : -1;
            int        end   = -1;
            bool       holding = held != last.end();
            for (int filament : filaments) {
                if (filament == start)
                    continue;
                ++out.loads[filament];
                if (holding)
                    ++out.lane_changes;
                holding = true;
                if (end < 0 || next.count(filament))
                    end = filament;
            }
            last[head] = end >= 0 ? end : start;
        }
    }
    return out;
}

std::vector<double> rank_units(const std::vector<std::string>& units, const std::vector<double>& seconds,
                               const std::vector<std::string>& preferred)
{
    auto rank_of = [&preferred](const std::string& unit) {
        return size_t(std::find(preferred.begin(), preferred.end(), unit) - preferred.begin());
    };
    std::vector<size_t> order(units.size());
    std::iota(order.begin(), order.end(), 0);
    std::stable_sort(order.begin(), order.end(), [&](size_t a, size_t b) {
        const size_t ra = rank_of(units[a]), rb = rank_of(units[b]);
        return ra != rb ? ra < rb : seconds[a] < seconds[b];
    });
    std::vector<double> sorted = seconds;
    std::sort(sorted.begin(), sorted.end());
    std::vector<double> out(units.size());
    for (size_t i = 0; i < order.size(); ++i)
        out[order[i]] = sorted[i];
    return out;
}

// The cheapest assignment of each row to its own column (rows <= columns), by the Hungarian method.
static std::vector<int> assign(const std::vector<std::vector<double>>& cost)
{
    const size_t n = cost.size(), m = n ? cost.front().size() : 0;
    const double inf = std::numeric_limits<double>::infinity();
    // 1-based, column 0 the free start: p[j] the row column j holds, way[] the path to augment.
    std::vector<double> u(n + 1, 0.), v(m + 1, 0.);
    std::vector<size_t> p(m + 1, 0), way(m + 1, 0);
    for (size_t i = 1; i <= n; ++i) {
        p[0]                = i;
        size_t              j0 = 0;
        std::vector<double> minv(m + 1, inf);
        std::vector<bool>   used(m + 1, false);
        do {
            used[j0]      = true;
            const size_t i0    = p[j0];
            double       delta = inf;
            size_t       j1    = 0;
            for (size_t j = 1; j <= m; ++j)
                if (!used[j]) {
                    const double cur = cost[i0 - 1][j - 1] - u[i0] - v[j];
                    if (cur < minv[j]) {
                        minv[j] = cur;
                        way[j]  = j0;
                    }
                    if (minv[j] < delta) {
                        delta = minv[j];
                        j1    = j;
                    }
                }
            for (size_t j = 0; j <= m; ++j)
                if (used[j]) {
                    u[p[j]] += delta;
                    v[j] -= delta;
                } else
                    minv[j] -= delta;
            j0 = j1;
        } while (p[j0] != 0);
        do {
            const size_t j1 = way[j0];
            p[j0]           = p[j1];
            j0              = j1;
        } while (j0 != 0);
    }
    std::vector<int> out(n, -1);
    for (size_t j = 1; j <= m; ++j)
        if (p[j] != 0)
            out[p[j] - 1] = int(j - 1);
    return out;
}

Plan place(const std::vector<Lane>& lanes, const std::vector<int>& toolhead_of, const std::vector<int>& loads, double move_seconds)
{
    Plan plan;
    plan.lane_of.assign(toolhead_of.size(), -1);
    auto printed = [&toolhead_of](int filament) {
        return filament >= 0 && size_t(filament) < toolhead_of.size() && toolhead_of[filament] >= 0;
    };
    std::vector<int> current(toolhead_of.size(), -1); // the lane each filament's spool is in now
    for (size_t l = 0; l < lanes.size(); ++l)
        if (lanes[l].loaded && lanes[l].filament >= 0 && size_t(lanes[l].filament) < current.size() && current[lanes[l].filament] < 0)
            current[lanes[l].filament] = int(l);

    std::set<int> heads(toolhead_of.begin(), toolhead_of.end());
    heads.erase(-1);
    for (int head : heads) {
        std::vector<int> rows, columns;
        for (size_t f = 0; f < toolhead_of.size(); ++f)
            if (toolhead_of[f] == head)
                rows.push_back(int(f));
        for (size_t l = 0; l < lanes.size(); ++l)
            if (lanes[l].toolhead == head)
                columns.push_back(int(l));
        // More filaments than lanes: the ones loaded least get none.
        std::stable_sort(rows.begin(), rows.end(), [&loads](int a, int b) { return loads[a] > loads[b]; });
        rows.resize(std::min(rows.size(), columns.size()));
        std::vector<std::vector<double>> cost(rows.size(), std::vector<double>(columns.size()));
        for (size_t r = 0; r < rows.size(); ++r)
            for (size_t c = 0; c < columns.size(); ++c) {
                const Lane& lane     = lanes[columns[c]];
                const bool  in_place = current[rows[r]] == columns[c];
                // A spool the print does not use has to make way.
                const bool clears = lane.loaded && !in_place && !printed(lane.filament);
                cost[r][c]        = (in_place ? 0. : move_seconds) + (clears ? move_seconds : 0.) +
                             (rows[r] < int(loads.size()) ? loads[rows[r]] : 0) * lane.seconds;
            }
        const std::vector<int> column_of = assign(cost);
        for (size_t r = 0; r < rows.size(); ++r)
            if (column_of[r] >= 0)
                plan.lane_of[rows[r]] = columns[column_of[r]];
    }

    std::vector<bool> taken(lanes.size(), false);
    for (int lane : plan.lane_of)
        if (lane >= 0)
            taken[lane] = true;
    // The lanes a cleared spool can go to: the ones the moved spools leave, then the empty ones.
    std::vector<int> free_lanes;
    for (size_t f = 0; f < plan.lane_of.size(); ++f)
        if (plan.lane_of[f] >= 0 && current[f] >= 0 && current[f] != plan.lane_of[f] && !taken[current[f]])
            free_lanes.push_back(current[f]);
    for (size_t l = 0; l < lanes.size(); ++l)
        if (!lanes[l].loaded && !taken[l])
            free_lanes.push_back(int(l));
    for (size_t f = 0; f < plan.lane_of.size(); ++f) {
        const int to = plan.lane_of[f];
        if (to < 0 || current[f] == to)
            continue;
        plan.moves.push_back({int(f), current[f], to});
        if (!lanes[to].loaded || printed(lanes[to].filament))
            continue;
        // The spool there swaps into the lane this one left when it can.
        auto spot = std::find(free_lanes.begin(), free_lanes.end(), current[f]);
        if (spot == free_lanes.end())
            spot = free_lanes.begin();
        const int place_to = spot == free_lanes.end() ? -1 : *spot;
        if (spot != free_lanes.end())
            free_lanes.erase(spot);
        plan.moves.push_back({lanes[to].filament, to, place_to});
    }
    return plan;
}

} // namespace Slic3r::AFCLayout
