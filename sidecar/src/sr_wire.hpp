#pragma once

#include "owned_wire.hpp"
#include "sr_input_contract.hpp"
#include <array>
#include <bit>
#include <cstdint>
#include <limits>
#include <span>
#include <stdexcept>

namespace comfy_dlss::sr_wire {
// CSR1 is intentionally not CNR1. No old NR client can accidentally interpret
// changed output dimensions or the additional depth / exposure planes.
constexpr std::uint32_t magic = 0x31525343, version = 1;
constexpr std::size_t header_bytes = 32, settings_bytes = 32, frame_header_bytes = 48;
constexpr std::uint32_t max_payload = 3840U * 2160U * 8U + 8U;
constexpr std::uint32_t max_evaluations = 1000000;
using type = owned_wire::type;
using header = owned_wire::header;
using owned_wire::get32;
using owned_wire::get64;
using owned_wire::put32;
using owned_wire::put64;

inline header parse_header(std::span<const unsigned char> data) {
    if (data.size() != header_bytes || get32(data, 0) != magic || get32(data, 4) != version ||
        get32(data, 12) > max_payload) throw std::invalid_argument("Invalid SR protocol header");
    const auto kind = get32(data, 8);
    if (!((kind >= 1 && kind <= 6) || (kind >= 128 && kind <= 131)))
        throw std::invalid_argument("Unknown SR message");
    return {static_cast<type>(kind), get32(data, 12), get64(data, 16), get64(data, 24)};
}
inline std::array<unsigned char, header_bytes> encode_header(header value) {
    if (value.bytes > max_payload) throw std::invalid_argument("Oversized SR output");
    std::array<unsigned char, header_bytes> data{};
    put32(data, 0, magic); put32(data, 4, version);
    put32(data, 8, static_cast<std::uint32_t>(value.kind)); put32(data, 12, value.bytes);
    put64(data, 16, value.request); put64(data, 24, value.session);
    return data;
}
inline std::array<unsigned char, 64> capabilities(bool compiled) {
    // Min/max input, max output, four wire format IDs, message bound,
    // session/in-flight bounds, SR|DLAA feature bits, compile support, frame cap.
    // Compile support is NOT a device, driver or runtime availability claim.
    const std::array<std::uint32_t, 16> values{64, 64, 1920, 1080, 3840, 2160,
        1, 1, 1, 1, max_payload, 1, 1, 3, compiled ? 1U : 0U, max_evaluations};
    std::array<unsigned char, 64> data{};
    for (std::size_t i = 0; i < values.size(); ++i) put32(data, i * 4, values[i]);
    return data;
}
inline sr::settings parse_settings_fields(std::span<const unsigned char> data) {
    if (data.size() != settings_bytes || get32(data, 28) != 0 || (get32(data, 24) & ~15U))
        throw std::invalid_argument("Invalid SR settings length or flags");
    const auto flags = get32(data, 24);
    sr::settings value{get32(data, 0), get32(data, 4), get32(data, 8), get32(data, 12),
        static_cast<sr::quality>(get32(data, 16)), get32(data, 20),
        (flags & 1U) != 0, (flags & 2U) != 0, (flags & 4U) != 0, (flags & 8U) != 0};
    sr::validate(value);
    return value;
}
inline sr::settings parse_settings(std::span<const unsigned char> data) {
    const auto value = parse_settings_fields(data);
    // CSR1 retains its advertised legacy envelope. CXR1 shares field decoding,
    // not this older transport's dimensional or message bounds.
    if (value.input_width > 1920 || value.input_height > 1080 ||
        value.output_width > 3840 || value.output_height > 2160 ||
        sr::output_bytes(value) + 8 > max_payload)
        throw std::invalid_argument("Requested size exceeds legacy CSR1 capabilities");
    return value;
}
struct frame_metadata {
    std::uint64_t pts_ns = 0;
    sr::frame_info value{};
};
inline frame_metadata parse_frame(std::span<const unsigned char> data,
                                 std::uint32_t task_frames, std::uint64_t previous_pts) {
    if (data.size() != frame_header_bytes || get32(data, 12) != 0 || (get32(data, 8) & ~1U))
        throw std::invalid_argument("Invalid SR frame metadata length or flags");
    auto f = [&](std::size_t offset) { return std::bit_cast<float>(get32(data, offset)); };
    frame_metadata frame{get64(data, 0), {f(16), f(20), f(24), f(28), f(32), f(36), f(40), f(44),
        (get32(data, 8) & 1U) != 0}};
    sr::validate(frame.value);
    if (frame.pts_ns > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()) ||
        (!task_frames && !frame.value.reset) || (task_frames && frame.pts_ns <= previous_pts))
        throw std::invalid_argument("SR task requires first reset and strictly increasing timestamps");
    return frame;
}
inline std::uint32_t frame_bytes(const sr::settings& settings) {
    sr::validate(settings);
    return static_cast<std::uint32_t>(frame_header_bytes + sr::input_bytes(settings, 8) +
        2 * sr::input_bytes(settings, 4));
}

// CPU-only request state validation. It is invoked before allocating a payload
// or touching a graphics device, so malicious sizes cannot bypass session bounds.
inline void validate_request(const header& request, std::uint64_t expected_request,
                             bool engine_present, std::uint64_t session,
                             std::uint32_t expected_frame_bytes, std::uint32_t evaluations,
                             std::uint32_t task_frames) {
    if (request.request != expected_request || expected_request == std::numeric_limits<std::uint64_t>::max())
        throw std::invalid_argument("SR request order mismatch");
    switch (request.kind) {
    case type::create:
        if (engine_present || !request.session || request.bytes != settings_bytes)
            throw std::invalid_argument("Invalid SR create state");
        break;
    case type::frame:
        if (!engine_present || request.session != session || request.bytes != expected_frame_bytes ||
            evaluations >= max_evaluations) throw std::invalid_argument("Invalid SR frame state or length");
        break;
    case type::end:
    case type::release:
        if (!engine_present || request.session != session || request.bytes ||
            (request.kind == type::end && !task_frames))
            throw std::invalid_argument("Invalid SR session control");
        break;
    case type::ping:
    case type::shutdown:
        if (request.session || request.bytes) throw std::invalid_argument("Invalid SR process control");
        break;
    default:
        throw std::invalid_argument("Unexpected SR response direction");
    }
}
}
