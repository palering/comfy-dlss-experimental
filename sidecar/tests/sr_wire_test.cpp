#include "sr_wire.hpp"
#include <array>
#include <bit>
#include <cassert>
#include <cstdio>
#include <limits>
#include <stdexcept>

namespace wire = comfy_dlss::sr_wire;
template<class F> void rejected(F&& function) {
    bool failed = false;
    try { function(); } catch (const std::exception&) { failed = true; }
    assert(failed);
}

int main() {
    const wire::header value{wire::type::frame, 1024, 17, 9};
    auto bytes = wire::encode_header(value);
    const auto decoded = wire::parse_header(bytes);
    assert(decoded.kind == value.kind && decoded.bytes == 1024 && decoded.request == 17 && decoded.session == 9);
    rejected([&] { comfy_dlss::owned_wire::parse_header(bytes); });
    auto invalid = bytes;
    wire::put32(invalid, 4, 2); rejected([&] { wire::parse_header(invalid); });
    wire::put32(invalid, 4, 1); wire::put32(invalid, 8, 127);
    rejected([&] { wire::parse_header(invalid); });
    wire::put32(invalid, 8, 2); wire::put32(invalid, 12, wire::max_payload + 1);
    rejected([&] { wire::parse_header(invalid); });
    rejected([&] { wire::parse_header(std::span(bytes).first(31)); });
    rejected([&] { wire::encode_header({wire::type::result, wire::max_payload + 1, 1, 1}); });

    const auto disabled = wire::capabilities(false), enabled = wire::capabilities(true);
    assert(wire::get32(disabled, 56) == 0 && wire::get32(enabled, 56) == 1);
    assert(wire::get32(enabled, 40) == 66355208 && wire::get32(enabled, 60) == 1000000);

    std::array<unsigned char, wire::settings_bytes> settings{};
    for (const auto pair : std::array<std::pair<std::size_t, std::uint32_t>, 8>{{
        {0, 640}, {4, 360}, {8, 1280}, {12, 720}, {16, 2}, {20, 13}, {24, 15}, {28, 0}}})
        wire::put32(settings, pair.first, pair.second);
    const auto shape = wire::parse_settings(settings);
    assert(shape.hdr && shape.depth_inverted && shape.motion_jittered && shape.auto_exposure);
    assert(shape.preset == 13 && wire::frame_bytes(shape) == 3686448);
    wire::put32(settings, 28, 1); rejected([&] { wire::parse_settings(settings); });
    wire::put32(settings, 28, 0); wire::put32(settings, 24, 16);
    rejected([&] { wire::parse_settings(settings); });
    wire::put32(settings, 24, 8); wire::put32(settings, 16, 5);
    rejected([&] { wire::parse_settings(settings); });

    std::array<unsigned char, wire::frame_header_bytes> frame{};
    wire::put64(frame, 0, 33333333); wire::put32(frame, 8, 1);
    for (const auto offset : {24U, 28U, 32U, 36U, 40U})
        wire::put32(frame, offset, std::bit_cast<std::uint32_t>(1.0F));
    wire::put32(frame, 44, std::bit_cast<std::uint32_t>(33.3333F));
    const auto meta = wire::parse_frame(frame, 0, 0);
    assert(meta.pts_ns == 33333333 && meta.value.reset);
    wire::put32(frame, 8, 0); rejected([&] { wire::parse_frame(frame, 0, 0); });
    wire::parse_frame(frame, 1, 33333332);
    rejected([&] { wire::parse_frame(frame, 1, 33333333); });
    wire::put32(frame, 8, 1); wire::put64(frame, 0, 0);
    wire::parse_frame(frame, 0, std::numeric_limits<std::uint64_t>::max()); // New END task epoch.
    wire::put64(frame, 0, std::numeric_limits<std::uint64_t>::max());
    rejected([&] { wire::parse_frame(frame, 0, 0); });
    wire::put64(frame, 0, 0);
    wire::put32(frame, 16, std::bit_cast<std::uint32_t>(0.51F));
    rejected([&] { wire::parse_frame(frame, 0, 0); });
    wire::put32(frame, 16, std::bit_cast<std::uint32_t>(std::numeric_limits<float>::quiet_NaN()));
    rejected([&] { wire::parse_frame(frame, 0, 0); });

    wire::validate_request({wire::type::create, 32, 1, 5}, 1, false, 0, 0, 0, 0);
    wire::validate_request({wire::type::frame, 1000, 2, 5}, 2, true, 5, 1000, 0, 0);
    wire::validate_request({wire::type::end, 0, 3, 5}, 3, true, 5, 1000, 1, 1);
    wire::validate_request({wire::type::release, 0, 4, 5}, 4, true, 5, 1000, 1, 0);
    wire::validate_request({wire::type::shutdown, 0, 5, 0}, 5, false, 0, 0, 0, 0);
    wire::validate_request({wire::type::ping, 0, 1, 0}, 1, true, 5, 0, 0, 0);
    rejected([] { wire::validate_request({wire::type::create, 32, 1, 0}, 1, false, 0, 0, 0, 0); });
    rejected([] { wire::validate_request({wire::type::create, 32, 1, 5}, 1, true, 5, 0, 0, 0); });
    rejected([] { wire::validate_request({wire::type::frame, 1000, 2, 5}, 2, true, 6, 1000, 0, 0); });
    rejected([] { wire::validate_request({wire::type::frame, 1001, 2, 5}, 2, true, 5, 1000, 0, 0); });
    rejected([] { wire::validate_request({wire::type::frame, 1000, 2, 5}, 2, true, 5, 1000, 1000000, 0); });
    rejected([] { wire::validate_request({wire::type::end, 0, 3, 5}, 3, true, 5, 1000, 0, 0); });
    rejected([] { wire::validate_request({wire::type::ping, 0, 1, 5}, 1, true, 5, 0, 0, 0); });
    rejected([] { wire::validate_request({wire::type::caps, 0, 1, 0}, 1, false, 0, 0, 0, 0); });
    rejected([] { wire::validate_request({wire::type::ping, 0, 2, 0}, 1, false, 0, 0, 0, 0); });
    std::puts("CSR1 bounds, flags, typed metadata and request state tests passed");
}
