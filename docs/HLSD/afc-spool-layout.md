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
grouping that changes lanes less wins even when the matrix purges little. The dialog keeps the
current grouping when the engine's would change lanes more often by its own count
(`AFCLayout::count_changes`, each toolhead starting a layer on the filament it holds and ending on
one the next layer uses).

How much regrouping helps depends on the print. A plate with many colors on few toolheads changes
lanes about as often however they are grouped; one whose colors fall into layers or regions gains
the most.

## Lanes

`AFCLayout::place` gives each printed filament a lane on its toolhead by a minimum-cost assignment
(Hungarian method) per toolhead. A spool moved by hand costs a minute of lane changes, each load costs
the lane change time of the lane's unit, and a lane holding a spool the print does not use costs one
more move to clear it. A spool cleared goes to a lane a moved spool left, else to an empty lane, else
off the printer.

The unit times come from the printer agent (`afc_units`): its lanes' median load plus median unload in
AFC.log, or a figure for the kind of unit when the log has too few. The printer setting Preferred units
(`afc_unit_order`) puts units first in the order given; the measured seconds, sorted, are dealt out in
that order, so a preferred unit counts as quick without the times leaving the range the printer
measured (`AFCLayout::rank_units`).

A project filament is known to be in a lane when the lane is mapped to its T number, as a filament
sync leaves it (slot n prints as T(n-1)), and holds its color and material; otherwise the first loaded
lane with that color and material holds it.

## Sync layout to AFC

The spools moved, Sync layout to AFC sends `afc_layout` with every lane a spool went to: the T number
the spool keeps, so the project's filament slots stay as they are, what the spool's old lane knew of
it (Spoolman spool, material, color, weight, temperatures) and what the lane itself knew when the
layout was suggested. The agent writes the spool to the lane and maps the T number to it. A lane that
knows a spool it did not know then read it from a tag, and keeps it. A lane AFC sees nothing in yet
is left alone, and the dialog says so before sending. The plate is then sliced again with each
filament on its new toolhead (`filament_toolhead`, `Plater::apply_filament_toolheads`).
