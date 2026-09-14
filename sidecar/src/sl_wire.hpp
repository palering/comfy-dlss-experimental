#pragma once

#include "sr_wire.hpp"
#include "sl_sr_input_contract.hpp"
#include "sl_rr_input_contract.hpp"

namespace comfy_dlss::sl_wire {
// CXR1 is distinct from direct-NGX CSR1: camera and RR data are mandatory
// explicit inputs, not silently inferred by the transport or graphics engine.
constexpr std::uint32_t magic = 0x31525843, version = 1;
constexpr std::size_t header_bytes = 32, settings_bytes = 40, frame_header_bytes = 48, camera_bytes = 324;
constexpr std::uint32_t max_payload = sr::limits::cxr_max_payload, max_evaluations = 1000000;
static_assert(sr::limits::output_header_bytes == 8 && sr::limits::output_bytes_per_pixel == 8);
static_assert(sr::limits::output_max_pixels == (max_payload - 8) / 8);
using owned_wire::type;
using owned_wire::header;
using owned_wire::get32;
using owned_wire::get64;
using owned_wire::put32;
using owned_wire::put64;

inline header parse_header(std::span<const unsigned char> data) {
    if (data.size() != header_bytes || get32(data, 0) != magic || get32(data, 4) != version ||
        get32(data, 12) > max_payload) throw std::invalid_argument("Invalid reconstruction protocol header");
    const auto kind = get32(data, 8);
    if (!((kind >= 1 && kind <= 6) || (kind >= 128 && kind <= 131)))
        throw std::invalid_argument("Unknown reconstruction message");
    return {static_cast<type>(kind), get32(data, 12), get64(data, 16), get64(data, 24)};
}
inline std::array<unsigned char, header_bytes> encode_header(header h) {
    if (h.bytes > max_payload) throw std::invalid_argument("Oversized reconstruction response");
    auto data = owned_wire::encode_header(h);
    put32(data, 0, magic); put32(data, 4, version);
    return data;
}
inline std::array<unsigned char, 64> capabilities() {
    // SR|DLAA|RR compiled feature bits, NOT a device/runtime support claim.
    constexpr std::array<std::uint32_t, 16> fields{sr::limits::min_side, sr::limits::min_side,
        sr::limits::input_max_side, sr::limits::input_max_side, sr::limits::output_max_side, sr::limits::output_max_side,
        1, 1, 1, 1, max_payload, 1, 1, 7, 1, max_evaluations};
    std::array<unsigned char, 64> data{};
    for (std::size_t i = 0; i < fields.size(); ++i) put32(data, i * 4, fields[i]);
    return data;
}
struct settings {
    sr::settings common{};
    bool rr = false, external_jitter = false;
};
inline settings parse_settings(std::span<const unsigned char> data) {
    if (data.size() != settings_bytes || (get32(data, 32) != 1 && get32(data, 32) != 2) || get32(data, 36) > 1)
        throw std::invalid_argument("Invalid reconstruction settings");
    settings value{sr_wire::parse_settings_fields(data.first(32)), get32(data, 32) == 2, get32(data, 36) == 1};
    if (value.common.hdr || value.common.motion_jittered || value.common.auto_exposure == value.rr ||
        (value.rr && value.common.preset != 0))
        throw std::invalid_argument("Unsupported reconstruction exposure, preset or HDR policy");
    const auto request_bytes = frame_header_bytes + camera_bytes +
        sr::input_bytes(value.common, 4) * (value.rr ? 11U : 4U) + (value.rr ? 128U : 0U);
    if (request_bytes > max_payload || 8 + sr::output_bytes(value.common) > max_payload)
        throw std::invalid_argument("Reconstruction frame/result exceeds CXR1 byte budget");
    return value;
}
inline std::uint32_t frame_bytes(const settings& s) {
    sr::validate(s.common);
    return static_cast<std::uint32_t>(frame_header_bytes + camera_bytes +
        sr::input_bytes(s.common, 4) * (s.rr ? 11 : 4) + (s.rr ? 128 : 0));
}
inline void validate_request(const header& request, std::uint64_t expected_request,
                             bool engine, std::uint64_t session, std::uint32_t expected_bytes,
                             std::uint32_t evaluations, std::uint32_t task_frames) {
    // Shared state machine is identical except CREATE has an extended payload.
    auto equivalent = request;
    if (request.kind == type::create) {
        if (request.bytes != settings_bytes) throw std::invalid_argument("Invalid reconstruction create length");
        equivalent.bytes = sr_wire::settings_bytes;
    }
    sr_wire::validate_request(equivalent, expected_request, engine, session, expected_bytes, evaluations, task_frames);
}
inline float number(std::span<const unsigned char> data, std::size_t offset) {
    return std::bit_cast<float>(get32(data, offset));
}
struct frame_metadata {
    std::uint64_t pts_ns = 0;
    sl_sr::frame_info info{};
    float exposure = 1;
};
inline frame_metadata parse_frame(std::span<const unsigned char> data, const settings& s,
                                  std::uint32_t frames, std::uint64_t previous_pts) {
    if (data.size() != frame_header_bytes || get32(data, 8) > 1 || get32(data, 12) || get32(data, 44))
        throw std::invalid_argument("Invalid reconstruction frame metadata");
    frame_metadata value{get64(data, 0), {number(data, 16), number(data, 20), number(data, 24),
        number(data, 28), number(data, 32), number(data, 36), get32(data, 8) == 1}, number(data, 40)};
    sl_sr::validate(value.info);
    if (value.pts_ns > static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()) ||
        (!frames && !value.info.reset) || (frames && value.pts_ns <= previous_pts) ||
        !std::isfinite(value.exposure) || value.exposure <= 0 || value.exposure > 65504 ||
        (!s.rr && value.exposure != 1) || (!s.external_jitter && (value.info.jitter_x != 0 || value.info.jitter_y != 0)))
        throw std::invalid_argument("Invalid reconstruction timestamp, reset or exposure/jitter policy");
    return value;
}
inline sl_sr::camera_frame parse_camera(std::span<const unsigned char> data, const settings& s) {
    if (data.size() != camera_bytes || get32(data, 320) > 1)
        throw std::invalid_argument("Invalid explicit camera payload");
    sl_sr::camera_frame value{};
    std::size_t at = 0;
    auto read = [&](auto& fields) { for (auto& field : fields) { field = number(data, at); at += 4; } };
    read(value.view_to_clip); read(value.clip_to_view); read(value.clip_to_previous); read(value.previous_to_clip);
    read(value.position); read(value.up); read(value.right); read(value.forward);
    value.near_plane = number(data, 304); value.far_plane = number(data, 308);
    value.vertical_fov = number(data, 312); value.aspect = number(data, 316);
    value.orthographic = get32(data, 320) == 1;
    sl_sr::validate(value, s.common);
    return value;
}
inline sl_rr::guides parse_rr(std::span<const unsigned char> data, const settings& s, float exposure) {
    const auto plane = sr::input_bytes(s.common, 8), motion = sr::input_bytes(s.common, 4);
    if (!s.rr || data.size() != 128 + 3 * plane + motion)
        throw std::invalid_argument("Missing or mismatched RR renderer guides");
    sl_rr::guides value{};
    for (std::size_t i = 0; i < 16; ++i) {
        value.world_to_view[i] = number(data, i * 4); value.view_to_world[i] = number(data, 64 + i * 4);
    }
    value.diffuse_albedo = data.subspan(128, plane); value.specular_albedo = data.subspan(128 + plane, plane);
    value.normal_roughness = data.subspan(128 + 2 * plane, plane);
    value.specular_motion = data.subspan(128 + 3 * plane, motion); value.exposure = exposure;
    sl_rr::validate(value, s.common);
    return value;
}
}
