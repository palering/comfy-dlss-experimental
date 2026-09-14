#include "sl_rr_input_contract.hpp"
#include <cassert>
#include <vector>

namespace {
template<class F> void rejects(F action) {
    bool rejected = false;
    try { action(); } catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected);
}
}

int main() {
    using namespace comfy_dlss;
    sr::settings settings{64, 64, 96, 96}; settings.auto_exposure = false;
    std::vector<unsigned char> albedo(sr::input_bytes(settings, 8)), normals(albedo.size()), motion(sr::input_bytes(settings, 4));
    for (std::size_t at = 0; at < albedo.size(); at += 8) {
        const std::uint16_t material[]{0x3800, 0x3800, 0x3800, 0x3c00};
        const std::uint16_t normal[]{0, 0, 0xbc00, 0x3800};
        std::memcpy(albedo.data() + at, material, 8); std::memcpy(normals.data() + at, normal, 8);
    }
    sl_rr::guides guides{albedo, albedo, normals, motion};
    for (const auto i : {0U, 5U, 10U, 15U}) guides.world_to_view[i] = guides.view_to_world[i] = 1;
    sl_rr::validate(guides, settings);
    auto bad = guides; bad.specular_motion = {};
    rejects([&] { sl_rr::validate(bad, settings); });
    bad = guides; bad.exposure = 0;
    rejects([&] { sl_rr::validate(bad, settings); });
    bad = guides; bad.world_to_view[0] = 0;
    rejects([&] { sl_rr::validate(bad, settings); });
    auto bad_settings = settings; bad_settings.auto_exposure = true;
    rejects([&] { sl_rr::validate(guides, bad_settings); });
    bad_settings = settings; bad_settings.preset = 10;
    rejects([&] { sl_rr::validate(guides, bad_settings); });
    normals[5] = 0;
    rejects([&] { sl_rr::validate(guides, settings); });
    normals[5] = 0xbc; normals[7] = 0x40; // roughness=2
    rejects([&] { sl_rr::validate(guides, settings); });
    normals[7] = 0x38; albedo[1] = 0x40; // reflectance=2
    rejects([&] { sl_rr::validate(guides, settings); });
    albedo[1] = 0x7c; // infinity
    rejects([&] { sl_rr::validate(guides, settings); });
}
