#include "sr_input_contract.hpp"
#include <cassert>
#include <limits>
#include <vector>

namespace {
template<class Function> bool rejects(Function action) {
    try { action(); } catch (const std::invalid_argument&) { return true; }
    return false;
}
}
int main() {
    using namespace comfy_dlss::sr;
    settings config{64, 64, 128, 128};
    validate(config);
    assert(input_bytes(config, 8) == 32768);
    assert(output_bytes(config) == 131072);
    validate_runtime_range(config, 64, 64, 32, 32, 128, 128);
    assert(rejects([&] { validate_runtime_range(config, 64, 64, 0, 32, 128, 128); }));
    assert(rejects([&] { validate_runtime_range(config, 63, 64, 32, 32, 63, 128); }));
    assert(rejects([&] { validate_runtime_range(config, 80, 80, 70, 70, 128, 128); }));
    auto wrong = config;
    wrong.output_height = 127;
    assert(rejects([&] { validate(wrong); }));
    wrong = config; wrong.input_width = 0xffffffffU;
    assert(rejects([&] { validate(wrong); }));
    wrong = config; wrong.output_width = wrong.output_height = 64;
    assert(rejects([&] { validate(wrong); }));
    wrong.mode = quality::dlaa; validate(wrong);
    wrong = config; wrong.mode = quality::dlaa;
    assert(rejects([&] { validate(wrong); }));
    wrong = config; wrong.preset = 1;
    assert(rejects([&] { validate(wrong); }));
    for (std::uint32_t preset = 10; preset <= 13; ++preset) {
        wrong = config; wrong.preset = preset; validate(wrong);
    }
    assert(rejects([&] { input_bytes(config, std::numeric_limits<std::size_t>::max()); }));
    frame_info frame;
    validate(frame);
    frame.jitter_x = 0.51F;
    assert(rejects([&] { validate(frame); }));
    frame = {}; frame.motion_scale_y = 0;
    assert(rejects([&] { validate(frame); }));
    frame = {}; frame.exposure = std::numeric_limits<float>::quiet_NaN();
    assert(rejects([&] { validate(frame); }));
    frame = {}; frame.pre_exposure = 0;
    assert(rejects([&] { validate(frame); }));
    frame = {}; frame.frame_time_ms = std::numeric_limits<float>::infinity();
    assert(rejects([&] { validate(frame); }));
    std::vector<unsigned char> color(input_bytes(config, 8));
    std::vector<unsigned char> motion(input_bytes(config, 4));
    std::vector<unsigned char> depth(input_bytes(config, 4));
    std::vector<unsigned char> result(output_bytes(config));
    validate_planes(config, color, motion, depth, result);
    color[1] = 0x7c;
    assert(rejects([&] { validate_planes(config, color, motion, depth, result); }));
    color[1] = 0;
    motion[1] = 0xfe;
    assert(rejects([&] { validate_planes(config, color, motion, depth, result); }));
    motion[1] = 0;
    // Device depth 1.0 is accepted; relative/metric values > 1 are not silently
    // normalized or substituted with flat depth by this engine.
    depth[2] = 0x80; depth[3] = 0x3f;
    validate_planes(config, color, motion, depth, result);
    depth[2] = 0; depth[3] = 0x40;
    assert(rejects([&] { validate_planes(config, color, motion, depth, result); }));
    depth[3] = 0;
    color.pop_back();
    assert(rejects([&] { validate_planes(config, color, motion, depth, result); }));
    assert(valid_project_id("8a4787af-bdf9-4591-b7e2-df4226aaad13"));
    assert(!valid_project_id(""));
    assert(!valid_project_id("8a4787af_bdf9-4591-b7e2-df4226aaad13"));
    assert(!valid_project_id("8a4787af-bdf9-4591-b7e2-df4226aaad1z"));
}
