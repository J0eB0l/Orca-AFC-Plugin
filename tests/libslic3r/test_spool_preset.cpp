#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "libslic3r/Config.hpp"
#include "libslic3r/PrintConfig.hpp"
#include "libslic3r/SpoolPreset.hpp"

using namespace Slic3r;
using Catch::Matchers::WithinAbs;

TEST_CASE("A slot's spool pressure advance replaces its preset value in every variant", "[SpoolPreset]")
{
    DynamicPrintConfig config;
    // Slot 1 prints through two extruder variants, slot 2 through one and has no spool K.
    config.set_key_value("filament_self_index", new ConfigOptionInts{1, 1, 2});
    config.set_key_value("pressure_advance", new ConfigOptionFloats{0.02, 0.03, 0.04});
    config.set_key_value("enable_pressure_advance", new ConfigOptionBools{false, false, false});
    config.set_key_value("spool_pressure_advance", new ConfigOptionFloats{0.05, -1});

    REQUIRE(apply_spool_pressure_advance(config));
    const auto &pa = config.option<ConfigOptionFloats>("pressure_advance")->values;
    CHECK_THAT(pa[0], WithinAbs(0.05, 1e-9));
    CHECK_THAT(pa[1], WithinAbs(0.05, 1e-9));
    CHECK_THAT(pa[2], WithinAbs(0.04, 1e-9));
    const auto &enable = config.option<ConfigOptionBools>("enable_pressure_advance")->values;
    CHECK(enable[0]);
    CHECK(enable[1]);
    CHECK_FALSE(enable[2]);
    CHECK_FALSE(apply_spool_pressure_advance(config));
}

TEST_CASE("Spool values go into a filament config and widen its temperature range", "[SpoolPreset]")
{
    DynamicPrintConfig config;
    config.set_key_value("nozzle_temperature", new ConfigOptionInts{200});
    config.set_key_value("nozzle_temperature_initial_layer", new ConfigOptionInts{205});
    config.set_key_value("nozzle_temperature_range_low", new ConfigOptionInts{190});
    config.set_key_value("nozzle_temperature_range_high", new ConfigOptionInts{215});
    config.set_key_value("hot_plate_temp", new ConfigOptionInts{60});
    config.set_key_value("cool_plate_temp", new ConfigOptionInts{0}); // the preset rules this plate out
    config.set_key_value("filament_density", new ConfigOptionFloats{1.24});
    config.set_key_value("enable_pressure_advance", new ConfigOptionBools{false});
    config.set_key_value("pressure_advance", new ConfigOptionFloats{0.02});

    TraySpool spool;
    spool.nozzle_temp      = 225;
    spool.bed_temp         = 65;
    spool.density          = 1.3;
    spool.pressure_advance = 0.04;

    REQUIRE(apply_spool_values(spool, config));
    CHECK(config.opt_int("nozzle_temperature", 0) == 225);
    CHECK(config.opt_int("nozzle_temperature_initial_layer", 0) == 225);
    CHECK(config.opt_int("nozzle_temperature_range_low", 0) == 190);
    CHECK(config.opt_int("nozzle_temperature_range_high", 0) == 225);
    CHECK(config.opt_int("hot_plate_temp", 0) == 65);
    CHECK(config.opt_int("cool_plate_temp", 0) == 0);
    CHECK_THAT(config.option<ConfigOptionFloats>("filament_density")->values[0], WithinAbs(1.3, 1e-9));
    CHECK(config.option<ConfigOptionBools>("enable_pressure_advance")->values[0]);
    CHECK_THAT(config.option<ConfigOptionFloats>("pressure_advance")->values[0], WithinAbs(0.04, 1e-9));
    CHECK_FALSE(apply_spool_values(spool, config));
}
