#include "sl_sr_input_contract.hpp"
#include <cassert>
#include <limits>

namespace {
template<class F> void rejects(F action) {
    bool rejected = false;
    try { action(); } catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected);
}
}

int main() {
    using namespace comfy_dlss;
    sr::settings settings{640, 360, 960, 540};
    sl_sr::camera_frame camera{};
    for (const auto i : {0U, 5U, 10U, 15U})
        camera.view_to_clip[i] = camera.clip_to_view[i] = camera.clip_to_previous[i] = camera.previous_to_clip[i] = 1;
    camera.up = {0, 1, 0}; camera.right = {1, 0, 0}; camera.forward = {0, 0, 1};
    camera.near_plane = .1F; camera.far_plane = 100; camera.vertical_fov = 1; camera.aspect = 640.0F / 360.0F;
    sl_sr::validate(camera, settings);
    const sr::settings portrait{736, 1280, 1472, 2560};
    sr::validate(portrait);
    auto portrait_camera = camera; portrait_camera.aspect = 736.0F / 1280.0F;
    sl_sr::validate(portrait_camera, portrait);
    rejects([&] { sr::validate(sr::settings{1920, 1920, 3840, 3840}); });
    auto bad = camera; bad.view_to_clip[0] = std::numeric_limits<float>::infinity();
    rejects([&] { sl_sr::validate(bad, settings); });
    bad = camera; bad.clip_to_view[5] = 2;
    rejects([&] { sl_sr::validate(bad, settings); });
    bad = camera; bad.up = bad.right;
    rejects([&] { sl_sr::validate(bad, settings); });
    bad = camera; bad.aspect = 1;
    rejects([&] { sl_sr::validate(bad, settings); });
    bad = camera; bad.near_plane = 100;
    rejects([&] { sl_sr::validate(bad, settings); });
    rejects([&] { sl_sr::validate(sl_sr::camera_frame{}, settings); });
    sl_sr::frame_info frame{};
    sl_sr::validate(frame);
    frame.jitter_x = std::numeric_limits<float>::quiet_NaN();
    rejects([&] { sl_sr::validate(frame); });
    frame = {}; frame.pre_exposure = 0;
    rejects([&] { sl_sr::validate(frame); });
    frame = {}; frame.motion_scale_y = std::numeric_limits<float>::infinity();
    rejects([&] { sl_sr::validate(frame); });
}
