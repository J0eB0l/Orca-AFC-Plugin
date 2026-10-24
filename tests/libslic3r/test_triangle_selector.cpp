#include <catch2/catch_all.hpp>

#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_message.hpp>
#include <catch2/generators/catch_generators.hpp>
#include "libslic3r/Model.hpp"
#include "libslic3r/TriangleSelector.hpp"
#include "libslic3r/TriangleMesh.hpp"

#include <algorithm>
#include "libslic3r/libslic3r.h"
#include <vector>
#include <cstddef>
#include <string>

using namespace Slic3r;

// A sphere gives well over ExtruderMax original facets, so every extruder state can be assigned
// to a facet of its own without any splitting getting in the way.
static TriangleMesh test_mesh() { return make_sphere(5., 2 * PI / 24); }

// Read the nibble_idx-th 4-bit group of a serialized bitstream, least significant bit first.
static int nibble_at(const std::vector<bool> &bitstream, size_t nibble_idx)
{
    int n = 0;
    for (size_t bit = 0; bit < 4; ++bit)
        n |= int(bitstream[nibble_idx * 4 + bit]) << bit;
    return n;
}

// The nibbles of a leaf in a state past 2: the "11"-prefixed leaf code, `escapes` times 0b1111, then `last`.
static std::vector<int> leaf_nibbles(int escapes, int last)
{
    std::vector<int> nibbles(size_t(escapes) + 2, 0b1111);
    nibbles.front() = 0b1100;
    nibbles.back()  = last;
    return nibbles;
}

TEST_CASE("Every extruder state survives a serialize/deserialize round trip", "[TriangleSelector]")
{
    const TriangleMesh mesh      = test_mesh();
    const int          max_state = int(EnforcerBlockerType::ExtruderMax);
    REQUIRE(int(mesh.its.indices.size()) >= max_state);

    TriangleSelector selector(mesh);
    for (int state = 1; state <= max_state; ++state)
        selector.set_facet(state - 1, EnforcerBlockerType(state));

    TriangleSelector restored(mesh);
    restored.deserialize(selector.serialize());

    for (int state = 1; state <= max_state; ++state) {
        INFO("Extruder " << state);
        REQUIRE(restored.has_facets(EnforcerBlockerType(state)));
        REQUIRE(restored.num_facets(EnforcerBlockerType(state)) == 1);
    }
}

TEST_CASE("Serialized data reports the extruder states it uses", "[TriangleSelector]")
{
    const TriangleMesh mesh = test_mesh();
    TriangleSelector   selector(mesh);
    selector.set_facet(0, EnforcerBlockerType::Extruder16);
    selector.set_facet(1, EnforcerBlockerType::Extruder32);
    selector.set_facet(2, EnforcerBlockerType::Extruder64);
    selector.set_facet(3, EnforcerBlockerType::ExtruderMax);

    const TriangleSelector::TriangleSplittingData data = selector.serialize();

    REQUIRE(data.used_states.size() == size_t(EnforcerBlockerType::ExtruderMax) + 1);
    REQUIRE(data.used_states[size_t(EnforcerBlockerType::Extruder16)]);
    REQUIRE(data.used_states[size_t(EnforcerBlockerType::Extruder32)]);
    REQUIRE(data.used_states[size_t(EnforcerBlockerType::Extruder64)]);
    REQUIRE(data.used_states[size_t(EnforcerBlockerType::ExtruderMax)]);
    REQUIRE_FALSE(data.used_states[size_t(EnforcerBlockerType::Extruder17)]);

    SECTION("used_states recomputed from the bitstream agrees") {
        TriangleSelector::TriangleSplittingData recomputed = data;
        recomputed.reset_used_states();
        recomputed.update_used_states(0);
        REQUIRE(recomputed.used_states == data.used_states);
    }

    SECTION("has_facets on the raw data agrees") {
        REQUIRE(TriangleSelector::has_facets(data, EnforcerBlockerType::Extruder32));
        REQUIRE(TriangleSelector::has_facets(data, EnforcerBlockerType::Extruder64));
        REQUIRE(TriangleSelector::has_facets(data, EnforcerBlockerType::ExtruderMax));
        REQUIRE_FALSE(TriangleSelector::has_facets(data, EnforcerBlockerType::Extruder17));
    }
}

// States 3..17 must keep the pre-existing encoding ("11" prefix plus one nibble of state-3) so
// projects written by older builds stay readable and newly written ones stay readable by them.
TEST_CASE("Extruder states up to 17 keep the single-nibble encoding", "[TriangleSelector]")
{
    const int state = GENERATE(3, 8, 16, 17);

    TriangleSelector selector(test_mesh());
    selector.set_facet(0, EnforcerBlockerType(state));
    const std::vector<bool> bitstream = selector.serialize().bitstream;

    INFO("Extruder " << state);
    // Two nibbles: the "11"-prefixed leaf code, then the state itself.
    REQUIRE(bitstream.size() == 8);
    REQUIRE(nibble_at(bitstream, 0) == 0b1100);
    REQUIRE(nibble_at(bitstream, 1) == state - 3);
}

// States 18 and above set the state nibble to 0b1111 and carry (state-18) in one more nibble.
TEST_CASE("Extruder states above 17 are encoded in a second nibble", "[TriangleSelector]")
{
    const int state = GENERATE(18, 25, 32);

    TriangleSelector selector(test_mesh());
    selector.set_facet(0, EnforcerBlockerType(state));
    const std::vector<bool> bitstream = selector.serialize().bitstream;

    INFO("Extruder " << state);
    REQUIRE(bitstream.size() == 12);
    REQUIRE(nibble_at(bitstream, 0) == 0b1100);
    REQUIRE(nibble_at(bitstream, 1) == 0b1111);
    REQUIRE(nibble_at(bitstream, 2) == state - 18);
}

// Past 32 the chain goes on: every further 0b1111 nibble adds 15 and calls for one more, so states
// up to 32 read the same as in builds that stop there.
TEST_CASE("Extruder states above 32 add one 0b1111 nibble per 15 states", "[TriangleSelector]")
{
    struct Case { int state; std::vector<int> nibbles; };
    const auto c = GENERATE(values<Case>({
        {33, {0b1100, 0b1111, 0b1111, 0}},
        {47, {0b1100, 0b1111, 0b1111, 14}},
        {48, {0b1100, 0b1111, 0b1111, 0b1111, 0}},
        {62, {0b1100, 0b1111, 0b1111, 0b1111, 14}},
        {63, {0b1100, 0b1111, 0b1111, 0b1111, 0b1111, 0}},
        {64, {0b1100, 0b1111, 0b1111, 0b1111, 0b1111, 1}},
        {128, leaf_nibbles(8, 5)},
        {243, leaf_nibbles(16, 0)},
        {254, leaf_nibbles(16, 11)},
    }));

    TriangleSelector selector(test_mesh());
    selector.set_facet(0, EnforcerBlockerType(c.state));
    const std::vector<bool> bitstream = selector.serialize().bitstream;

    INFO("Extruder " << c.state);
    REQUIRE(bitstream.size() == 4 * c.nibbles.size());
    for (size_t i = 0; i < c.nibbles.size(); ++i)
        REQUIRE(nibble_at(bitstream, i) == c.nibbles[i]);
}

// Model.cpp writes these hex strings into the 3MF for colored mesh imports; the selector must
// decode exactly the states CONST_FILAMENTS assigns to them.
TEST_CASE("Extruder states match the CONST_FILAMENTS hex encoding", "[TriangleSelector]")
{
    struct Case { const char *hex; int state; };
    const auto c = GENERATE(values<Case>({
        {"8", 2}, {"0C", 3}, {"DC", 16}, {"EC", 17}, {"0FC", 18}, {"EFC", 32},
        {"0FFC", 33}, {"EFFC", 47}, {"0FFFC", 48}, {"EFFFC", 62}, {"0FFFFC", 63}, {"1FFFFC", 64},
        {"5FFFFFFFFC", 128}, {"0FFFFFFFFFFFFFFFFC", 243}, {"BFFFFFFFFFFFFFFFFC", 254},
    }));

    // get_triangle_as_string emits the nibbles most significant first, so read the hex backwards.
    const std::string hex = c.hex;
    std::vector<bool> bitstream;
    for (auto it = hex.rbegin(); it != hex.rend(); ++it) {
        const int nibble = *it >= 'A' ? (*it - 'A' + 10) : (*it - '0');
        for (int bit = 0; bit < 4; ++bit)
            bitstream.push_back((nibble >> bit) & 1);
    }

    TriangleSelector::TriangleSplittingData data;
    data.triangles_to_split.emplace_back(0, 0);
    data.bitstream = bitstream;

    INFO("Hex " << c.hex << " -> extruder " << c.state);
    REQUIRE(TriangleSelector::has_facets(data, EnforcerBlockerType(c.state)));
}

// Colored mesh imports paint each face with the CONST_FILAMENTS code of its filament (Model.cpp),
// so every filament up to ExtruderMax has to come back as its own state.
TEST_CASE("A colored mesh import paints every filament up to ExtruderMax", "[TriangleSelector]")
{
    const int max_state = int(EnforcerBlockerType::ExtruderMax);
    Model        model;
    ModelObject *object = model.add_object();
    object->add_volume(test_mesh());
    const TriangleMesh &mesh = object->volumes.front()->mesh();
    REQUIRE(int(mesh.its.indices.size()) >= max_state);

    std::vector<unsigned char> face_filament_ids(mesh.its.indices.size(), 0);
    for (int state = 1; state <= max_state; ++state)
        face_filament_ids[state - 1] = (unsigned char) state;
    REQUIRE(Model::obj_import_face_color_deal(face_filament_ids, 1, &model));

    TriangleSelector restored(mesh);
    restored.deserialize(object->volumes.front()->mmu_segmentation_facets.get_data());
    for (int state = 1; state <= max_state; ++state) {
        INFO("Extruder " << state);
        REQUIRE(restored.num_facets(EnforcerBlockerType(state)) == 1);
    }
}

// Pack 4-bit codes into a bitstream, least significant bit first, in the order the decoder reads them.
static std::vector<bool> pack_nibbles(const std::vector<int> &nibbles)
{
    std::vector<bool> bitstream;
    for (const int nibble : nibbles)
        for (int bit = 0; bit < 4; ++bit)
            bitstream.push_back((nibble >> bit) & 1);
    return bitstream;
}

TEST_CASE("A valid paint stream with nested splits round-trips bit for bit", "[TriangleSelector]")
{
    const TriangleMesh mesh = test_mesh();

    TriangleSelector::TriangleSplittingData data;
    data.triangles_to_split.emplace_back(0, 0);
    // A three-side split whose children, in stream order, are: a one-side split (side 2) into two
    // leaves, a two-side split (side 1) into leaves of states 20, 0 and 8, then two plain leaves.
    const std::vector<int> triangle_0 = {0b0011,
                                         0b1001, 0b1000, 0b0100,
                                         0b0110, 0b1100, 0b1111, 20 - 18, 0b0000, 0b1100, 8 - 3,
                                         0b1000,
                                         0b0100};
    data.bitstream = pack_nibbles(triangle_0);
    data.triangles_to_split.emplace_back(5, int(data.bitstream.size()));
    const std::vector<bool> triangle_5 = pack_nibbles({0b1100, 3 - 3});
    data.bitstream.insert(data.bitstream.end(), triangle_5.begin(), triangle_5.end());
    data.reset_used_states();
    REQUIRE(data.update_used_states(0));

    TriangleSelector restored(mesh);
    restored.deserialize(data);

    REQUIRE(restored.num_facets(EnforcerBlockerType::Extruder20) == 1);
    REQUIRE(restored.num_facets(EnforcerBlockerType::Extruder3) == 1);
    REQUIRE(restored.serialize() == data);
}

// get_facets() for all states walks the triangles once; it has to give each state the mesh the
// single-state get_facets() gives, vertex numbering included, also where states share vertices.
TEST_CASE("Facets collected for all states at once match each state's own", "[TriangleSelector]")
{
    const TriangleMesh mesh = test_mesh();

    TriangleSelector::TriangleSplittingData data;
    auto add_triangle = [&data](int triangle_idx, const std::vector<int> &nibbles) {
        data.triangles_to_split.emplace_back(triangle_idx, int(data.bitstream.size()));
        const std::vector<bool> bits = pack_nibbles(nibbles);
        data.bitstream.insert(data.bitstream.end(), bits.begin(), bits.end());
    };
    // The nested split of the round trip test above, then leaves around the same pole vertex.
    add_triangle(0, {0b0011,
                     0b1001, 0b1000, 0b0100,
                     0b0110, 0b1100, 0b1111, 20 - 18, 0b0000, 0b1100, 8 - 3,
                     0b1000,
                     0b0100});
    add_triangle(1, leaf_nibbles(16, 11));      // 254
    add_triangle(2, leaf_nibbles(2, 7));        // 40
    add_triangle(3, {0b1100, 0b1111, 20 - 18}); // 20
    add_triangle(5, {0b1100, 3 - 3});           // 3
    data.reset_used_states();
    REQUIRE(data.update_used_states(0));

    TriangleSelector selector(mesh);
    selector.deserialize(data);
    REQUIRE(selector.num_facets(EnforcerBlockerType::ExtruderMax) == 1);
    REQUIRE(selector.num_facets(EnforcerBlockerType::Extruder20) == 2);

    std::vector<indexed_triangle_set> facets_per_type;
    selector.get_facets(facets_per_type);
    REQUIRE(facets_per_type.size() == size_t(EnforcerBlockerType::ExtruderMax) + 1);
    for (size_t type = 0; type < facets_per_type.size(); ++type) {
        INFO("State " << type);
        const indexed_triangle_set single = selector.get_facets(EnforcerBlockerType(type));
        REQUIRE(facets_per_type[type].indices == single.indices);
        REQUIRE(facets_per_type[type].vertices == single.vertices);
    }
}

TEST_CASE("A truncated or malformed paint stream drops only the damaged triangle", "[TriangleSelector][Regression]")
{
    struct Case { const char *name; std::vector<int> nibbles; };
    const auto c = GENERATE(values<Case>({
        {"three-side split missing two children", {0b0011, 0b1000, 0b1000}},
        {"leaf missing its state nibble",         {0b1100}},
        {"leaf missing its second state nibble",  {0b1100, 0b1111}},
        {"leaf missing its third state nibble",   {0b1100, 0b1111, 0b1111}},
        {"leaf state one past ExtruderMax",       leaf_nibbles(16, 12)},
        {"leaf chain running past ExtruderMax",   leaf_nibbles(17, 0)},
        {"splits nested past the end",            {0b0011, 0xF, 0xF, 0xF, 0xF, 0xF, 0xF, 0xF, 0xF,
                                                    0xF, 0xF, 0xF, 0xF, 0xF, 0xF, 0xF, 0xF}},
        {"one-side split of the nonexistent side 3", {0b1101, 0b1000, 0b1000}},
    }));
    INFO(c.name);

    const TriangleMesh mesh = test_mesh();
    TriangleSelector   intact(mesh);
    intact.set_facet(0, EnforcerBlockerType::Extruder2);

    // Triangle 0 stays intact, triangle 1 carries the damaged stream.
    TriangleSelector::TriangleSplittingData data = intact.serialize();
    data.triangles_to_split.emplace_back(1, int(data.bitstream.size()));
    const std::vector<bool> damaged = pack_nibbles(c.nibbles);
    data.bitstream.insert(data.bitstream.end(), damaged.begin(), damaged.end());

    TriangleSelector restored(mesh);
    REQUIRE_NOTHROW(restored.deserialize(data));
    // Triangle 1 unwinds completely, so the selector holds exactly the intact paint.
    REQUIRE(restored.serialize() == intact.serialize());

    REQUIRE_NOTHROW(TriangleSelector::has_facets(data, EnforcerBlockerType::Extruder3));

    TriangleSelector::TriangleSplittingData recomputed = data;
    recomputed.reset_used_states();
    REQUIRE_FALSE(recomputed.update_used_states(0));
    REQUIRE(std::none_of(recomputed.used_states.begin(), recomputed.used_states.end(), [](bool used) { return used; }));
}
