#pragma once
#include <cstddef>
#include <cstdint>
#include <span>
#include <stdexcept>

namespace comfy_dlss::sequence_probe {
// CNS1 is a bounded diagnostic fixture, NOT the production Worker protocol.
// LE header: magic, version, width, height, count, reserved (all uint32).
// Each record: uint64 PTS ns, uint32 reset, uint32 reserved, RGBA16F, RG16F.
constexpr std::uint32_t magic = 0x31534e43;
constexpr std::size_t header_bytes = 24, frame_header_bytes = 16;
constexpr std::uint64_t maximum_bytes = 64ULL * 1024 * 1024;
inline std::uint32_t u32(std::span<const unsigned char> bytes, std::size_t offset) {
    if (offset > bytes.size() || bytes.size() - offset < 4) throw std::runtime_error("Truncated sequence field");
    std::uint32_t value = 0;
    for (unsigned int i = 0; i < 4; ++i) value |= static_cast<std::uint32_t>(bytes[offset+i]) << (8*i);
    return value;
}
inline std::uint64_t u64(std::span<const unsigned char> bytes, std::size_t offset) {
    return u32(bytes, offset) | (static_cast<std::uint64_t>(u32(bytes, offset+4)) << 32);
}
struct layout { std::uint32_t width, height, count; std::size_t color_bytes, motion_bytes; };
inline layout parse_layout(std::span<const unsigned char> bytes, std::uint64_t file_bytes) {
    if (bytes.size() != header_bytes || u32(bytes, 0) != magic || u32(bytes, 4) != 1 || u32(bytes, 20))
        throw std::runtime_error("Invalid sequence header");
    const auto w = u32(bytes, 8), h = u32(bytes, 12), count = u32(bytes, 16);
    if (w < 64 || w > 1920 || h < 64 || h > 1080 || count == 0 || count > 16)
        throw std::runtime_error("Sequence dimensions/count exceed diagnostic bounds");
    const std::uint64_t pixels = static_cast<std::uint64_t>(w) * h;
    const auto expected = header_bytes + count * (frame_header_bytes + pixels * 12);
    if (expected > maximum_bytes || expected != file_bytes) throw std::runtime_error("Invalid sequence file size");
    return {w, h, count, static_cast<std::size_t>(pixels*8), static_cast<std::size_t>(pixels*4)};
}
struct frame_info { std::uint64_t pts; bool reset; };
inline frame_info parse_frame(std::span<const unsigned char> bytes, std::uint32_t index,
                             std::uint64_t previous_pts) {
    if (bytes.size() != frame_header_bytes || u32(bytes, 12) || u32(bytes, 8) > 1)
        throw std::runtime_error("Invalid sequence frame header");
    const auto pts = u64(bytes, 0);
    if (pts > 0x7fffffffffffffffULL || (index && pts <= previous_pts) || (!index && !u32(bytes, 8)))
        throw std::runtime_error("Invalid sequence timestamp/reset");
    return {pts, u32(bytes, 8) != 0};
}
inline bool finite_half_plane(std::span<const unsigned char> bytes) noexcept {
    if (bytes.size() % 2) return false;
    for (std::size_t i = 1; i < bytes.size(); i += 2)
        if ((bytes[i] & 0x7c) == 0x7c) return false;
    return true;
}
}
