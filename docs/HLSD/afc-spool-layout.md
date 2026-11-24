# AFC suggested spool layout

On an AFC printer with several toolheads, each fed by its own lanes (Multi-material per toolhead), a
filament prints on the toolhead its lane feeds. Which lane holds which spool therefore decides how
often each toolhead changes lanes. The suggested spool layout regroups a sliced plate's filaments
onto the toolheads, picks a lane for each, lists the spools to move, and once they are moved writes
the lanes back to AFC so nothing has to be scanned or typed in again. A printer with one toolhead has
nothing to gain: every filament goes through the same nozzle whatever lane holds it.

The Filament Grouping box of the preview legend offers it as "Suggest spool layout" on such a printer
(`GCodeViewer::render_legend_toolhead_groups`), and `AFCLayoutDialog` shows it.

## Grouping

`ToolOrdering::get_afc_toolhead_groups` runs the grouping engine Bambu uses for its two-nozzle
printers (`FilamentGroup`, flush mode) on the print's layer filaments, with each toolhead holding at
most as many filaments as it has lanes. Every lane change adds a fixed cost to the flush matrix, so a
grouping that changes lanes less wins even when the matrix purges little.

The engine numbers its groups in no particular order, so `AFCLayout::fit_toolheads` then gives each
group the toolhead where its spools cost the least to place, by a minimum-cost assignment of groups to
toolheads over the lane costs below. A group whose spools already sit on a toolhead stays there.

The engine counts flushes and ignores where the spools are, and among groupings that change lanes
equally often it picks one anyhow. So the dialog does not take its grouping as it is.
`AFCLayout::improve` moves one printed filament to another toolhead, or swaps two on different
toolheads, while that lowers the cost of the whole plan: the moves and loads `AFCLayout::place` prices
below, each toolhead's loads counted by `AFCLayout::count_changes` (each toolhead starting a layer on
the filament it holds and ending on one the next layer uses). The dialog improves three groupings, the
slice's, the one the spools sit in (each filament on the toolhead its lane feeds) and the engine's, and
keeps the cheapest plan, the slice's on a tie. So no spool is moved unless the print gets faster for it,
and a tie never swaps spools. The lane changes it shows as "now" are those of the spools where they sit,
since AFC prints a filament on the toolhead its lane feeds whatever tool the slice gave it.

How much regrouping helps depends on the print. A plate with many colors on few toolheads changes
lanes about as often however they are grouped; one whose colors fall into layers or regions gains
the most.

## Lanes

`AFCLayout::place` gives each printed filament a lane on its toolhead by a minimum-cost assignment
(Hungarian method) per toolhead. Each lane change into a filament costs the lane change time of the
lane's unit, the time the print spends on it. A toolhead's first load is no lane change: it loads once
whatever lane feeds it, so a filament its toolhead never swaps out gains nothing from a quicker unit. Spools are moved before the print starts, so a move costs no print time; it costs
a hundredth of a second only to break ties, so of the layouts that print equally fast the one moving the
fewest spools wins. A lane holding a spool the print does not use costs one more move to clear it. A spool cleared goes to a lane a moved spool left, else to an empty lane, else
off the printer.

The unit times come from the printer agent (`afc_units`): its lanes' median load plus median unload in
AFC.log, or a figure for the kind of unit when the log has too few. The printer setting Preferred units
(`afc_unit_order`) puts units first in the order given; the measured seconds, sorted, are dealt out in
that order, so a preferred unit counts as quick without the times leaving the range the printer
measured (`AFCLayout::rank_units`).

A project filament is known to be in a lane when the lane is mapped to its T number, as a filament
sync leaves it (slot n prints as T(n-1)), and holds its color and material; otherwise the first loaded
lane with that color and material holds it. Materials compare by family, as the agent does: the
leading word in upper case without a trailing '+', so a "PLA+" spool holds a PLA filament.

A unit the printer agent reports as not connected (an ACE or BridgeBox unit whose link is down) has
no lanes in the layout, since no spool can be loaded into it or taken out of it. When its lanes hold
spools the print uses, by the same test on the spool AFC last knew in each, the layout asks for those
spools elsewhere, so the dialog names the unit and asks to connect it and suggest the layout again.

A printed filament that no loaded lane holds, or that the slice puts on another toolhead than its lane
feeds, means the project's slots were not synced from these lanes, so "now" and the moves would be
counted from slots that no longer match the spools. The dialog names those filaments and asks for a
filament sync first.

## Filament switch

A filament switch has two inlets, each with units plumbed into it, and two outlets, each feeding a
toolhead. It sends a lane to either toolhead, so two filaments of one unit can print through a nozzle
each. An inlet holds one lane at a time, though: loading the second filament takes the first out of its
toolhead, and it loads again when it next prints. The lane changes stay, but each nozzle only prints its
own filament, so the flush between the two is skipped, at the cost of a tool change.

The printer agent reports each lane's switch and inlet, and the two toolheads the switch reaches while
it can move the lane (`afc_lanes`: `switch`, `switch_inlet`, `switch_toolheads`). When any lane is behind
a switch, the dialog plans with `AFCLayout::route` in place of `place`, and `improve` searches by its
cost:

- `AFCLayout::run` walks the layers like `count_changes` and also follows each inlet and nozzle. A load
  counts the filaments it takes out, the one in its toolhead and the one another toolhead holds through
  the same inlet. A toolhead flushes only when its nozzle last printed another filament. A layer starts
  on the toolhead the print is on and ends on one the next layer uses.
- The cost adds the seconds of those flushes and tool changes to the lane changes (`AFCLayout::Times`):
  a flush is the lane change's flushing volume (`Print::lane_change_flush`) at the incoming filament's
  maximum volumetric speed, a tool change is the printer's tool change time
  (`machine_tool_change_time`), the figures the print's time estimate uses.
- A lane whose spool is grouped onto the other toolhead its switch reaches feeds that toolhead, so the
  spool stays where it is. A spool that moves prints on the toolhead its new lane feeds now.

So a filament goes through the switch to the other toolhead only when the print is shorter for it, and a
tie leaves it on the toolhead its lane feeds. The dialog shows the flushes between filaments beside the
lane changes and lists each filament that prints through the switch on the other toolhead. A lane whose
switch reaches the toolhead its filament was sliced for counts as synced, and the print dialog keeps that
toolhead (`picked_toolheads` in `SelectMachine.cpp`) instead of slicing again for the one the lane feeds
now. The print file then sends each lane to its toolhead (the agent's `AFC_BRIDGEBOX_SWITCH_ASSIGN`
lines).

The slice does the same with the spools where they are. A filament sync keeps each slot's inlet and the
other toolhead its switch reaches with the project (`filament_switch_inlet`, `filament_switch_toolhead`,
beside `filament_toolhead`), and `ToolOrdering::get_recommended_filament_maps` puts each such filament on
whichever of its two toolheads makes the print shorter (`AFCLayout::switch_in_place`): `run`'s seconds,
plus the printer's load and unload times for each lane change. A filament stays on its own toolhead when
the other has a nozzle of another size, as its paths are made for the nozzle of its own, when the other
toolhead cannot reach its parts, and on a plate printed by object. `Print::lane_toolheads` says where each lane
was sliced. The prime tower plans its lane change flushes from it, the file's `filament_toolhead` line
carries it to the agent, and the preview's Filament Grouping box names the filaments that print on the
other toolhead. When none moves and the printer already has filaments of one inlet on different toolheads,
with a lane still loaded where an earlier print put it or put there by hand, the box says that instead. The
printer puts a lane a print moved back on its own toolhead once that print is over and the lane is unloaded.
A layout synced from the dialog names the toolheads itself, and so do trays picked in the print dialog that
need another slice. Those filaments then stay on the toolhead named (`filament_switch_toolhead` cleared)
until the filaments are synced again.

A printer without a filament switch is planned as before: its layout counts lane changes only.

## Sync layout to AFC

The spools moved, Sync layout to AFC sends `afc_layout` with every lane a spool went to: the T number
the spool keeps, so the project's filament slots stay as they are, what the spool's old lane knew of
it (Spoolman spool, material, color, weight, temperatures) and what the lane itself knew when the
layout was suggested. The agent writes the spool to the lane and maps the T number to it. A lane that
knows a spool it did not know then read it from a tag, and keeps it. A lane AFC sees nothing in yet
is left alone, and the dialog says so before sending. The plate is then sliced again with each
filament on its new toolhead (`filament_toolhead`, `Plater::apply_filament_toolheads`).
