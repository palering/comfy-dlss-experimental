#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <vector>

namespace comfy_dlss::protocol {

inline constexpr std::array<std::byte, 8> magic = {
    std::byte{'C'}, std::byte{'D'}, std::byte{'L'}, std::byte{'S'},
    std::byte{'S'}, std::byte{'P'}, std::byte{'1'}, std::byte{0},
};
inline constexpr std::uint16_t version = 1;
inline constexpr std::uint64_t max_packet_bytes = 512ULL * 1024ULL * 1024ULL;
inline constexpr std::uint32_t max_planes = 8;
inline constexpr std::uint32_t max_dimension = 16'384;
inline constexpr std::size_t packet_header_bytes = 32;
inline constexpr std::size_t frame_header_bytes = 40;
inline constexpr std::size_t plane_header_bytes = 32;

enum class message_type : std::uint16_t {
    hello = 1,
    configure = 2,
    configured = 3,
    frame = 4,
    frame_result = 5,
    reset = 6,
    cancel = 7,
    shutdown = 8,
    error = 9,
};

enum class packet_flags : std::uint32_t {
    none = 0,
    reset_history = 1U << 0U,
    end_of_stream = 1U << 1U,
};

enum class plane_semantic : std::uint16_t {
    color = 1,
    motion = 2,
    depth = 3,
    exposure = 4,
    reactive_mask = 5,
    output_color = 100,
};

enum class pixel_format : std::uint16_t {
    rgba8_unorm = 1,
    rgba16_float = 2,
    rg16_float = 3,
    r32_float = 4,
    r8_unorm = 5,
};

struct packet_header final {
    message_type type{};
    packet_flags flags{};
    std::uint64_t request_id = 0;
    std::uint64_t payload_bytes = 0;
};

struct plane_description final {
    plane_semantic semantic{};
    pixel_format format{};
    std::uint32_t width = 0;
    std::uint32_t height = 0;
    std::uint32_t row_pitch = 0;
    std::uint64_t data_offset = 0;
    std::uint64_t data_bytes = 0;
};

struct frame_description final {
    std::uint32_t input_width = 0;
    std::uint32_t input_height = 0;
    std::uint32_t output_width = 0;
    std::uint32_t output_height = 0;
    std::int64_t pts_ns = 0;
    std::uint64_t frame_index = 0;
    std::vector<plane_description> planes;
};

[[nodiscard]] bool parse_packet_header(
    std::span<const std::byte> bytes,
    packet_header& output,
    std::string& error);

[[nodiscard]] bool parse_frame_payload(
    std::span<const std::byte> bytes,
    frame_description& output,
    std::string& error);

[[nodiscard]] std::uint32_t bytes_per_pixel(pixel_format format) noexcept;

// Renderer input contract, separate from the bidirectional wire codec.
[[nodiscard]] bool validate_frame_inputs(const frame_description& frame, std::string& error);

}  // namespace comfy_dlss::protocol
