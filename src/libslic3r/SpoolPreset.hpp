#pragma once

#include <string>
#include <vector>

namespace Slic3r {

class DynamicPrintConfig;
class PresetBundle;

// What a printer agent reports of the spool on a tray, so a filament sync can give the tray the
// spool's own filament preset instead of matching a filament id to a stock one.
struct TraySpool
{
    std::vector<std::string> names; // preset names without the " @<printer>" part to look for, best first
    std::string              base;  // the preset to copy when none of them exists
    std::string              vendor;
    int                      nozzle_temp      = 0;   // 0: not known, likewise below
    int                      bed_temp         = 0;
    double                   density          = 0.;
    double                   diameter         = 0.;
    double                   pressure_advance = -1.; // < 0: none saved on the spool
};

struct SpoolPreset
{
    std::string name; // empty: no preset for this spool
    bool        created = false;
    bool        updated = false;
};

// First line of the notes of a filament preset a sync made for a spool. A later sync refreshes only
// presets that still carry it, so a user who removes it keeps their own values.
extern const std::string SPOOL_PRESET_NOTE;

// Puts the spool's temperatures, density, diameter and pressure advance into a filament config,
// widening the nozzle temperature range to take the spool's. A bed plate the preset rules out (0)
// stays ruled out. Returns whether anything changed.
bool apply_spool_values(const TraySpool &spool, DynamicPrintConfig &config);

// The compatible preset named like the spool that spool_filament_preset() would pick, without
// making or changing one; "" when there is none.
std::string existing_spool_preset(PresetBundle &bundle, const TraySpool &spool);

// The filament preset for a spool on the selected printer: a compatible user preset, then a system
// one, named like the spool, else a user preset "<name> @<printer>" copied from spool.base with the
// spool's values. A spool with a saved pressure advance that matches a system preset gets its own
// copy of it, so the preset shows what the slot prints with. A preset made this way earlier takes
// the spool's values again.
SpoolPreset spool_filament_preset(PresetBundle &bundle, const TraySpool &spool);

// Puts each filament slot's spool_pressure_advance (-1 = none) over that slot's pressure_advance
// in every one of its extruder variants, as filament_self_index maps them to slots, and turns
// pressure advance on there. A slot keeps its spool's K even when slots share a preset. Returns
// whether anything changed.
bool apply_spool_pressure_advance(DynamicPrintConfig &config);

} // namespace Slic3r
