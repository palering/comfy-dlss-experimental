#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <limits>
#include <span>
#include <stdexcept>
#include <string_view>
#include "sr_limits.hpp"

namespace comfy_dlss::sr {

static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559,
              "SR wire planes require IEEE-754 binary32");

// These values intentionally match the documented NGX quality enum. The engine
// translation unit statically checks the mapping against the supplied SDK.
enum class quality : std::uint32_t {
    performance = 0, balanced = 1, quality = 2, ultra_performance = 3,
    ultra_quality = 4, dlaa = 5,
};

struct settings {
    std::uint32_t input_width = 0, input_height = 0;
    std::uint32_t output_width = 0, output_height = 0;
    quality mode = quality::quality;
    std::uint32_t preset = 0; // Default, J, K, L, M only; not NR preset values.
    bool hdr = false, depth_inverted = false, motion_jittered = false;
    bool auto_exposure = true;
};

struct frame_info {
    float jitter_x = 0, jitter_y = 0; // Actual input sample jitter, in input pixels.
    float motion_scale_x = 1, motion_scale_y = 1;
    float exposure = 1, pre_exposure = 1, exposure_scale = 1;
    float frame_time_ms = 1000.0F / 30.0F;
    bool reset = false;
};

inline void validate(const settings& s) {
    if (s.input_width < limits::min_side || s.input_width > limits::input_max_side ||
        s.input_height < limits::min_side || s.input_height > limits::input_max_side ||
        static_cast<std::uint64_t>(s.input_width)*s.input_height > limits::input_max_pixels ||
        s.output_width < limits::min_side || s.output_width > limits::output_max_side ||
        s.output_height < limits::min_side || s.output_height > limits::output_max_side ||
        static_cast<std::uint64_t>(s.output_width)*s.output_height > limits::output_max_pixels)
        throw std::invalid_argument("SR dimensions exceed experimental input/output bounds");
    if (s.output_width < s.input_width || s.output_height < s.input_height ||
        static_cast<std::uint64_t>(s.input_width) * s.output_height !=
        static_cast<std::uint64_t>(s.input_height) * s.output_width)
        throw std::invalid_argument("SR must preserve aspect ratio without downscaling");
    if (static_cast<std::uint32_t>(s.mode) > 5)
        throw std::invalid_argument("Unknown SR quality mode");
    const bool equal = s.input_width == s.output_width && s.input_height == s.output_height;
    if ((s.mode == quality::dlaa) != equal)
        throw std::invalid_argument("DLAA requires equal input/output; SR requires larger output");
    if (s.preset != 0 && (s.preset < 10 || s.preset > 13))
        throw std::invalid_argument("SR preset must be Default, J, K, L or M");
}

inline void validate(const frame_info& f) {
    for (const float v : {f.jitter_x, f.jitter_y, f.motion_scale_x, f.motion_scale_y,
                          f.exposure, f.pre_exposure, f.exposure_scale, f.frame_time_ms})
        if (!std::isfinite(v)) throw std::invalid_argument("SR metadata must be finite");
    if (std::abs(f.jitter_x) > 0.5F || std::abs(f.jitter_y) > 0.5F)
        throw std::invalid_argument("SR jitter must be actual subpixel offsets in [-0.5, 0.5]");
    if (f.motion_scale_x == 0 || f.motion_scale_y == 0 || std::abs(f.motion_scale_x) > 16384 ||
        std::abs(f.motion_scale_y) > 16384 || f.exposure <= 0 || f.exposure > 65504 ||
        f.pre_exposure <= 0 || f.pre_exposure > 65504 || f.exposure_scale <= 0 ||
        f.exposure_scale > 65504 || f.frame_time_ms <= 0 || f.frame_time_ms > 60000)
        throw std::invalid_argument("SR scale/exposure/frame time outside contract bounds");
}

inline std::size_t input_bytes(const settings& s, std::size_t pixel_bytes) {
    validate(s);
    if (pixel_bytes != 4 && pixel_bytes != 8) throw std::invalid_argument("Unknown SR input pixel format");
    return static_cast<std::size_t>(s.input_width) * s.input_height * pixel_bytes;
}
inline std::size_t output_bytes(const settings& s) {
    validate(s);
    return static_cast<std::size_t>(s.output_width) * s.output_height * 8;
}

inline void validate_runtime_range(const settings& s, std::uint32_t optimal_width, std::uint32_t optimal_height,
                                   std::uint32_t minimum_width, std::uint32_t minimum_height,
                                   std::uint32_t maximum_width, std::uint32_t maximum_height) {
    validate(s);
    if (!minimum_width || !minimum_height || minimum_width > optimal_width || minimum_height > optimal_height ||
        optimal_width > maximum_width || optimal_height > maximum_height ||
        s.input_width < minimum_width || s.input_height < minimum_height ||
        s.input_width > maximum_width || s.input_height > maximum_height)
        throw std::invalid_argument("Explicit SR input dimensions are outside the runtime-reported quality-mode range");
}

inline void validate_half_plane(std::span<const unsigned char> bytes) {
    if (bytes.size() % 2) throw std::invalid_argument("SR FP16 plane is not aligned to samples");
    for (std::size_t i = 0; i < bytes.size(); i += 2) {
        const std::uint32_t sample = static_cast<std::uint32_t>(bytes[i]) |
            (static_cast<std::uint32_t>(bytes[i + 1]) << 8);
        if ((sample & 0x7c00U) == 0x7c00U) throw std::invalid_argument("SR FP16 input contains NaN/Inf");
    }
}

inline void validate_planes(const settings& s, std::span<const unsigned char> color,
                            std::span<const unsigned char> motion, std::span<const unsigned char> depth,
                            std::span<unsigned char> output) {
    if (color.size() != input_bytes(s, 8) || motion.size() != input_bytes(s, 4) ||
        depth.size() != input_bytes(s, 4) || output.size() != output_bytes(s))
        throw std::invalid_argument("SR expects tightly packed RGBA16F, RG16F, R32F and RGBA16F planes");
    validate_half_plane(color);
    validate_half_plane(motion);
    // Wire depth is little-endian IEEE-754 device depth, not relative/metric
    // monocular depth. The adapter must explicitly supply the conversion.
    for (std::size_t i = 0; i < depth.size(); i += 4) {
        const std::uint32_t bits = static_cast<std::uint32_t>(depth[i]) |
            (static_cast<std::uint32_t>(depth[i + 1]) << 8) |
            (static_cast<std::uint32_t>(depth[i + 2]) << 16) |
            (static_cast<std::uint32_t>(depth[i + 3]) << 24);
        float value = 0;
        static_assert(sizeof(value) == sizeof(bits));
        std::memcpy(&value, &bits, sizeof(value));
        if (!std::isfinite(value) || value < 0 || value > 1)
            throw std::invalid_argument("SR depth must be finite device depth in [0, 1]");
    }
}

inline bool valid_project_id(std::string_view value) noexcept {
    if (value.size() != 36) return false;
    for (std::size_t i = 0; i < value.size(); ++i) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (value[i] != '-') return false;
        } else if (!((value[i] >= '0' && value[i] <= '9') || (value[i] >= 'a' && value[i] <= 'f') ||
                     (value[i] >= 'A' && value[i] <= 'F'))) return false;
    }
    return true;
}
}
