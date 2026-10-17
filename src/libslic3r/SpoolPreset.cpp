#include "SpoolPreset.hpp"

#include "Config.hpp"
#include "Preset.hpp"
#include "PresetBundle.hpp"
#include "PrintConfig.hpp"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <string>
#include <utility>
#include <vector>

#include <boost/algorithm/string/trim.hpp>
#include <boost/log/trivial.hpp>

namespace Slic3r {

const std::string SPOOL_PRESET_NOTE = "Made by filament sync from the spool's data. Each sync puts the spool's "
                                      "temperatures, density, diameter and pressure advance back in; delete this "
                                      "line to keep your own.";

namespace {

std::string lower_ascii(std::string s)
{
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return char(std::tolower(c)); });
    return s;
}

// "Polymaker PolyLite PLA @Voron 2.4" -> "polymaker polylite pla"
std::string preset_base_key(const std::string &name) { return lower_ascii(boost::trim_copy(name.substr(0, name.find('@')))); }

bool made_by_sync(const Preset &preset)
{
    const auto *notes = preset.config.option<ConfigOptionStrings>("filament_notes");
    return preset.is_user() && notes != nullptr && !notes->values.empty() &&
           notes->values.front().compare(0, SPOOL_PRESET_NOTE.size(), SPOOL_PRESET_NOTE) == 0;
}

// Every value of a vector option, all extruder variants alike; a 0 stays when keep_zero.
template<class Option, class Value> bool set_every_value(Option *option, const Value &value, bool keep_zero = false)
{
    bool changed = false;
    if (option != nullptr)
        for (auto &v : option->values)
            if (v != value && !(keep_zero && v == Value())) {
                v       = value;
                changed = true;
            }
    return changed;
}

// The filament id of the presets made for one spool: every printer's copy shares it, as with
// OrcaSlicer's own user filaments, and no other filament has it.
std::string spool_filament_id(const PresetCollection &filaments, const std::string &spool_name)
{
    const std::string key = lower_ascii(spool_name);
    for (const Preset &preset : filaments)
        if (!preset.filament_id.empty() && preset.filament_id != "null" && preset_base_key(preset.name) == key)
            return preset.filament_id;
    for (unsigned salt = 0; salt < 1000; ++salt) {
        uint32_t hash = 2166136261u; // FNV-1a
        for (unsigned char c : key + (salt > 0 ? "#" + std::to_string(salt) : std::string())) {
            hash ^= c;
            hash *= 16777619u;
        }
        char id[16];
        std::snprintf(id, sizeof(id), "P%07x", unsigned(hash >> 4));
        if (std::none_of(filaments.begin(), filaments.end(), [&id](const Preset &p) { return p.filament_id == id; }))
            return id;
    }
    return {};
}

} // namespace

bool apply_spool_values(const TraySpool &spool, DynamicPrintConfig &config)
{
    bool changed = false;
    if (spool.nozzle_temp > 0) {
        for (const char *key : {"nozzle_temperature", "nozzle_temperature_initial_layer"})
            changed |= set_every_value(config.option<ConfigOptionInts>(key), spool.nozzle_temp);
        if (auto *low = config.option<ConfigOptionInts>("nozzle_temperature_range_low"))
            for (int &v : low->values)
                if (v > spool.nozzle_temp) {
                    v       = spool.nozzle_temp;
                    changed = true;
                }
        if (auto *high = config.option<ConfigOptionInts>("nozzle_temperature_range_high"))
            for (int &v : high->values)
                if (v < spool.nozzle_temp) {
                    v       = spool.nozzle_temp;
                    changed = true;
                }
    }
    if (spool.bed_temp > 0)
        for (const std::string plate : {"supertack_plate_temp", "cool_plate_temp", "textured_cool_plate_temp", "eng_plate_temp",
                                        "hot_plate_temp", "textured_plate_temp"}) {
            changed |= set_every_value(config.option<ConfigOptionInts>(plate), spool.bed_temp, true);
            changed |= set_every_value(config.option<ConfigOptionInts>(plate + "_initial_layer"), spool.bed_temp, true);
        }
    if (spool.density > 0)
        changed |= set_every_value(config.option<ConfigOptionFloats>("filament_density"), spool.density);
    if (spool.diameter > 0)
        changed |= set_every_value(config.option<ConfigOptionFloats>("filament_diameter"), spool.diameter);
    if (spool.pressure_advance >= 0) {
        changed |= set_every_value(config.option<ConfigOptionBools>("enable_pressure_advance"), static_cast<unsigned char>(1));
        changed |= set_every_value(config.option<ConfigOptionFloats>("pressure_advance"), spool.pressure_advance);
    }
    return changed;
}

SpoolPreset spool_filament_preset(PresetBundle &bundle, const TraySpool &spool)
{
    SpoolPreset out;
    if (spool.names.empty())
        return out;
    PresetCollection &filaments = bundle.filaments;

    // The user's presets before the system ones, so a preset made for the spool keeps winning
    // over a system preset of that name arriving later.
    Preset *found = nullptr;
    for (size_t a = 0; a < spool.names.size() && found == nullptr; ++a) {
        const std::string key = lower_ascii(spool.names[a]);
        for (bool system : {false, true}) {
            auto it = std::find_if(filaments.begin(), filaments.end(), [&key, system](const Preset &preset) {
                return preset.is_compatible && !preset.is_default && preset.is_system == system && preset_base_key(preset.name) == key;
            });
            if (it != filaments.end()) {
                found = &*it;
                break;
            }
        }
    }
    // A system preset cannot hold the spool's pressure advance, so a spool with one gets its own copy.
    const bool copy_system = found != nullptr && found->is_system && spool.pressure_advance >= 0;
    if (found != nullptr && !copy_system) {
        out.name                  = found->name;
        DynamicPrintConfig config = found->config;
        if (made_by_sync(*found) && apply_spool_values(spool, config)) {
            // Reload it in the filament tab too, unless it has edits there that would be lost.
            const bool reload = filaments.get_selected_preset_name() == found->name && !filaments.current_is_dirty();
            found->config     = std::move(config);
            found->save(nullptr);
            if (reload)
                filaments.select_preset_by_name(found->name, true);
            out.updated = true;
        }
        return out;
    }

    const Preset     *from    = copy_system ? found : spool.base.empty() ? nullptr : filaments.find_preset(spool.base);
    const std::string printer = bundle.printers.get_selected_preset_name();
    const std::string name    = spool.names.front() + " @" + printer;
    out.name                  = found != nullptr ? found->name : std::string();
    // A preset of that name this printer cannot use is someone else's: leave it be.
    if (from == nullptr || printer.empty() || filaments.find_preset(name) != nullptr)
        return out;
    const std::string from_name   = from->name;
    const std::string filament_id = spool_filament_id(filaments, spool.names.front());
    const std::string vendor      = boost::trim_copy(spool.vendor);

    // Making a preset reselects the edited filament preset, which drops its unsaved changes; keep them.
    const bool         keep_edits = filaments.current_is_dirty();
    DynamicPrintConfig edits;
    if (keep_edits)
        edits = filaments.get_edited_preset().config;
    std::vector<std::string> failures;
    const bool made = filaments.clone_presets({from}, failures, [&](Preset &preset, Preset::Type &) {
        preset.name        = name;
        preset.filament_id = filament_id;
        preset.config.option<ConfigOptionStrings>("compatible_printers", true)->values = {printer};
        preset.config.option<ConfigOptionString>("compatible_printers_condition", true)->value.clear();
        auto set_strings = [&preset](const char *key, const std::string &value) {
            auto *option = preset.config.option<ConfigOptionStrings>(key, true);
            if (option->values.empty())
                option->values.push_back(value);
            for (std::string &v : option->values)
                v = value;
        };
        if (!vendor.empty())
            set_strings("filament_vendor", vendor);
        set_strings("filament_notes", SPOOL_PRESET_NOTE);
        apply_spool_values(spool, preset.config);
    });
    if (keep_edits) {
        filaments.get_edited_preset().config = std::move(edits);
        filaments.update_dirty();
    }
    if (!made || filaments.find_preset(name) == nullptr)
        return out;
    BOOST_LOG_TRIVIAL(info) << "Filament sync: made preset \"" << name << "\" for a spool from \"" << from_name << "\"";
    out.name    = name;
    out.created = true;
    return out;
}

bool apply_spool_pressure_advance(DynamicPrintConfig &config)
{
    const auto *spool      = config.option<ConfigOptionFloats>("spool_pressure_advance");
    const auto *self_index = config.option<ConfigOptionInts>("filament_self_index");
    auto       *pa         = config.option<ConfigOptionFloats>("pressure_advance");
    auto       *enable     = config.option<ConfigOptionBools>("enable_pressure_advance");
    if (spool == nullptr || pa == nullptr || enable == nullptr)
        return false;
    // One value per filament variant when filament_self_index lists them, else one per filament.
    const bool by_variant = self_index != nullptr && self_index->values.size() == pa->values.size();
    bool       changed    = false;
    for (size_t i = 0; i < pa->values.size(); ++i) {
        const int slot = by_variant ? self_index->values[i] - 1 : int(i);
        if (slot < 0 || size_t(slot) >= spool->values.size() || !(spool->values[slot] >= 0.))
            continue;
        changed |= pa->values[i] != spool->values[slot];
        pa->values[i] = spool->values[slot];
        if (i < enable->values.size()) {
            changed |= !enable->values[i];
            enable->values[i] = 1;
        }
    }
    return changed;
}

} // namespace Slic3r
