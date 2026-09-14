#pragma once

#include "sl_sr_input_contract.hpp"

namespace comfy_dlss::sl_rr {

// Renderer-provided G-buffers, not guides estimated from an ordinary video.
// RGB albedos are linear reflectance [0,1]; normals are normalized view/world
// vectors with linear roughness in alpha. This first path takes explicit
// reflection motion (RG16F, same convention/scale as the primary motion).
struct guides {
    std::span<const unsigned char> diffuse_albedo, specular_albedo;
    std::span<const unsigned char> normal_roughness, specular_motion;
    std::array<float, 16> world_to_view{}, view_to_world{};
    float exposure = 1; // Explicit 1x1 exposure texture; no inferred auto exposure.
};

inline float half_value(const unsigned char* bytes) {
    const unsigned int bits = static_cast<unsigned int>(bytes[0]) | (static_cast<unsigned int>(bytes[1]) << 8);
    const int exponent = static_cast<int>((bits >> 10) & 31);
    const float fraction = static_cast<float>(bits & 1023) / 1024;
    const float value = exponent ? std::ldexp(1 + fraction, exponent - 15) : std::ldexp(fraction, -14);
    return (bits & 0x8000U) ? -value : value;
}

inline void validate(const guides& value, const sr::settings& settings) {
    sr::validate(settings);
    if (settings.preset != 0 || settings.auto_exposure)
        throw std::invalid_argument("SL RR currently requires its default preset and explicit exposure");
    if (value.diffuse_albedo.size() != sr::input_bytes(settings, 8) ||
        value.specular_albedo.size() != sr::input_bytes(settings, 8) ||
        value.normal_roughness.size() != sr::input_bytes(settings, 8) ||
        value.specular_motion.size() != sr::input_bytes(settings, 4))
        throw std::invalid_argument("SL RR requires full-resolution linear albedos, packed normal/roughness and specular motion");
    for (const auto plane : {value.diffuse_albedo, value.specular_albedo, value.normal_roughness, value.specular_motion})
        sr::validate_half_plane(plane);
    for (const auto plane : {value.diffuse_albedo, value.specular_albedo})
        for (std::size_t at = 0; at < plane.size(); at += 8)
            for (std::size_t channel = 0; channel < 3; ++channel) {
                const auto reflectance = half_value(plane.data() + at + channel * 2);
                if (reflectance < 0 || reflectance > 1)
                    throw std::invalid_argument("SL RR albedo must be linear reflectance in [0,1]");
            }
    for (std::size_t at = 0; at < value.normal_roughness.size(); at += 8) {
        double length = 0;
        for (std::size_t channel = 0; channel < 3; ++channel) {
            const auto normal = static_cast<double>(half_value(value.normal_roughness.data() + at + channel * 2));
            length += normal * normal;
        }
        const auto roughness = half_value(value.normal_roughness.data() + at + 6);
        if (std::abs(length - 1) > .01 || roughness < 0 || roughness > 1)
            throw std::invalid_argument("SL RR normals must be normalized with linear roughness in [0,1]");
    }
    sl_sr::validate_inverse(value.world_to_view, value.view_to_world);
    if (!std::isfinite(value.exposure) || value.exposure <= 0 || value.exposure > 65504)
        throw std::invalid_argument("SL RR exposure must be finite and positive");
}
}
