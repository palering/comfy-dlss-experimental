#pragma once

#include "sr_input_contract.hpp"
#include <array>

namespace comfy_dlss::sl_sr {

// Only fields exposed by the public SL SR interface. Unlike direct NGX/CSR1,
// this API has no per-frame frame-time parameter or manual exposure texture in
// this bounded implementation; those capabilities must not be silently claimed.
struct frame_info {
    float jitter_x = 0, jitter_y = 0;
    float motion_scale_x = 1, motion_scale_y = 1;
    float pre_exposure = 1, exposure_scale = 1;
    bool reset = false;
};

inline void validate(const frame_info& frame) {
    sr::frame_info common{};
    common.jitter_x = frame.jitter_x; common.jitter_y = frame.jitter_y;
    common.motion_scale_x = frame.motion_scale_x; common.motion_scale_y = frame.motion_scale_y;
    common.pre_exposure = frame.pre_exposure; common.exposure_scale = frame.exposure_scale;
    sr::validate(common);
}

// Host-owned camera data, not an sl::Constants object crossing the public wire.
// All matrices are row-major, without projection jitter. No valid camera is
// invented by default: the caller must supply finite, mutually inverse pairs.
struct camera_frame {
    std::array<float, 16> view_to_clip{}, clip_to_view{};
    std::array<float, 16> clip_to_previous{}, previous_to_clip{};
    std::array<float, 3> position{}, up{}, right{}, forward{};
    float near_plane = 0, far_plane = 0, vertical_fov = 0, aspect = 0;
    bool orthographic = false;
};

inline void validate_inverse(const std::array<float, 16>& a, const std::array<float, 16>& b) {
    for (const auto value : a) if (!std::isfinite(value)) throw std::invalid_argument("SL SR camera matrix is not finite");
    for (const auto value : b) if (!std::isfinite(value)) throw std::invalid_argument("SL SR camera matrix is not finite");
    for (std::size_t row = 0; row < 4; ++row) for (std::size_t column = 0; column < 4; ++column) {
        double ab = 0, ba = 0;
        for (std::size_t k = 0; k < 4; ++k) {
            ab += static_cast<double>(a[row * 4 + k]) * b[k * 4 + column];
            ba += static_cast<double>(b[row * 4 + k]) * a[k * 4 + column];
        }
        const double expected = row == column ? 1 : 0;
        if (std::abs(ab - expected) > .01 || std::abs(ba - expected) > .01)
            throw std::invalid_argument("SL SR camera matrix pair is not inverse");
    }
}

inline void validate(const camera_frame& camera, const sr::settings& settings) {
    sr::validate(settings);
    validate_inverse(camera.view_to_clip, camera.clip_to_view);
    validate_inverse(camera.clip_to_previous, camera.previous_to_clip);
    for (const auto vector : {camera.position, camera.up, camera.right, camera.forward})
        for (const auto value : vector) if (!std::isfinite(value)) throw std::invalid_argument("SL SR camera vector is not finite");
    auto dot = [](const auto& a, const auto& b) {
        return static_cast<double>(a[0]) * b[0] + static_cast<double>(a[1]) * b[1] + static_cast<double>(a[2]) * b[2];
    };
    for (const auto vector : {camera.up, camera.right, camera.forward})
        if (std::abs(dot(vector, vector) - 1) > .001) throw std::invalid_argument("SL SR camera axes must be unit vectors");
    if (std::abs(dot(camera.up, camera.right)) > .001 || std::abs(dot(camera.up, camera.forward)) > .001 ||
        std::abs(dot(camera.right, camera.forward)) > .001)
        throw std::invalid_argument("SL SR camera axes must be orthogonal");
    for (const auto value : {camera.near_plane, camera.far_plane, camera.vertical_fov, camera.aspect})
        if (!std::isfinite(value)) throw std::invalid_argument("SL SR camera parameters must be finite");
    if (camera.near_plane <= 0 || camera.far_plane <= camera.near_plane || camera.vertical_fov <= 0 ||
        camera.vertical_fov >= 3.141593F || camera.aspect <= 0 ||
        std::abs(static_cast<double>(camera.aspect) - static_cast<double>(settings.input_width) / settings.input_height) > .001)
        throw std::invalid_argument("SL SR camera projection parameters do not match the frame");
}
}
