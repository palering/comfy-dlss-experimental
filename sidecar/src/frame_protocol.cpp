#include "frame_protocol.hpp"

#include <algorithm>
#include <bit>
#include <limits>
#include <string_view>

namespace comfy_dlss::protocol {
namespace {

std::uint16_t read_u16(std::span<const std::byte> bytes, std::size_t offset) {
    const auto a = std::to_integer<std::uint16_t>(bytes[offset]);
    const auto b = std::to_integer<std::uint16_t>(bytes[offset + 1]);
    return static_cast<std::uint16_t>(a | static_cast<std::uint16_t>(b << 8U));
}

std::uint32_t read_u32(std::span<const std::byte> bytes, std::size_t offset) {
    std::uint32_t value = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        value |= static_cast<std::uint32_t>(std::to_integer<std::uint8_t>(bytes[offset + index]))
            << static_cast<unsigned int>(index * 8U);
    }
    return value;
}

std::uint64_t read_u64(std::span<const std::byte> bytes, std::size_t offset) {
    std::uint64_t value = 0;
    for (std::size_t index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(std::to_integer<std::uint8_t>(bytes[offset + index]))
            << static_cast<unsigned int>(index * 8U);
    }
    return value;
}

bool valid_message_type(std::uint16_t raw) {
    return raw >= static_cast<std::uint16_t>(message_type::hello) &&
        raw <= static_cast<std::uint16_t>(message_type::error);
}

bool decode_semantic(std::uint16_t raw, plane_semantic& output) {
    switch (raw) {
        case 1: output = plane_semantic::color; return true;
        case 2: output = plane_semantic::motion; return true;
        case 3: output = plane_semantic::depth; return true;
        case 4: output = plane_semantic::exposure; return true;
        case 5: output = plane_semantic::reactive_mask; return true;
        case 100: output = plane_semantic::output_color; return true;
        default: return false;
    }
}

bool decode_format(std::uint16_t raw, pixel_format& output, std::uint32_t& bytes_per_pixel) {
    switch (raw) {
        case 1:
            output = pixel_format::rgba8_unorm;
            bytes_per_pixel = 4;
            return true;
        case 2:
            output = pixel_format::rgba16_float;
            bytes_per_pixel = 8;
            return true;
        case 3:
            output = pixel_format::rg16_float;
            bytes_per_pixel = 4;
            return true;
        case 4:
            output = pixel_format::r32_float;
            bytes_per_pixel = 4;
            return true;
        case 5:
            output = pixel_format::r8_unorm;
            bytes_per_pixel = 1;
            return true;
        default: return false;
    }
}

bool valid_dimensions(std::uint32_t width, std::uint32_t height) {
    return width >= 1U && width <= max_dimension && height >= 1U && height <= max_dimension;
}

std::size_t semantic_slot(plane_semantic semantic) {
    switch (semantic) {
        case plane_semantic::color: return 0;
        case plane_semantic::motion: return 1;
        case plane_semantic::depth: return 2;
        case plane_semantic::exposure: return 3;
        case plane_semantic::reactive_mask: return 4;
        case plane_semantic::output_color: return 5;
    }
    return 6;
}

}  // namespace

std::uint32_t bytes_per_pixel(pixel_format format) noexcept {
    switch (format) {
        case pixel_format::rgba8_unorm: return 4;
        case pixel_format::rgba16_float: return 8;
        case pixel_format::rg16_float: return 4;
        case pixel_format::r32_float: return 4;
        case pixel_format::r8_unorm: return 1;
    }
    return 0;
}

bool validate_frame_inputs(const frame_description& frame, std::string& error) {
    error.clear();
    if (!valid_dimensions(frame.input_width, frame.input_height) ||
        !valid_dimensions(frame.output_width, frame.output_height) ||
        frame.planes.empty() || frame.planes.size() > max_planes) {
        error = "invalid renderer frame dimensions or plane count";
        return false;
    }
    std::array<bool, 6> seen{};
    for (const auto& plane : frame.planes) {
        const auto slot = semantic_slot(plane.semantic);
        if (slot >= seen.size() || seen[slot]) {
            error = "unknown or duplicate renderer input semantic";
            return false;
        }
        seen[slot] = true;
        const bool exposure = plane.semantic == plane_semantic::exposure;
        if (plane.width != (exposure ? 1U : frame.input_width) ||
            plane.height != (exposure ? 1U : frame.input_height)) {
            error = "input planes must match input dimensions; EXPOSURE must be 1x1";
            return false;
        }
        bool valid_format = false;
        switch (plane.semantic) {
            case plane_semantic::color:
                valid_format = plane.format == pixel_format::rgba8_unorm ||
                    plane.format == pixel_format::rgba16_float;
                break;
            case plane_semantic::motion:
                valid_format = plane.format == pixel_format::rg16_float;
                break;
            case plane_semantic::depth:
            case plane_semantic::exposure:
                valid_format = plane.format == pixel_format::r32_float;
                break;
            case plane_semantic::reactive_mask:
                valid_format = plane.format == pixel_format::r8_unorm;
                break;
            case plane_semantic::output_color:
                error = "OUTPUT_COLOR is not a renderer input";
                return false;
        }
        if (!valid_format ||
            plane.row_pitch < static_cast<std::uint64_t>(plane.width) * bytes_per_pixel(plane.format) ||
            plane.data_bytes != static_cast<std::uint64_t>(plane.row_pitch) * plane.height) {
            error = "invalid renderer input format, row pitch, or byte count";
            return false;
        }
    }
    if (!seen[semantic_slot(plane_semantic::color)]) {
        error = "FRAME is missing its COLOR plane";
        return false;
    }
    return true;
}

bool parse_packet_header(
    std::span<const std::byte> bytes,
    packet_header& output,
    std::string& error) {
    output = {};
    error.clear();
    if (bytes.size() < packet_header_bytes) {
        error = "packet is shorter than its 32-byte header";
        return false;
    }
    if (!std::equal(magic.begin(), magic.end(), bytes.begin())) {
        error = "invalid sidecar packet magic";
        return false;
    }
    const std::uint16_t raw_version = read_u16(bytes, 8);
    if (raw_version != version) {
        error = "unsupported sidecar protocol version";
        return false;
    }
    const std::uint16_t raw_type = read_u16(bytes, 10);
    if (!valid_message_type(raw_type)) {
        error = "unsupported sidecar message type";
        return false;
    }
    const std::uint32_t raw_flags = read_u32(bytes, 12);
    constexpr std::uint32_t known_flags =
        static_cast<std::uint32_t>(packet_flags::reset_history) |
        static_cast<std::uint32_t>(packet_flags::end_of_stream);
    if ((raw_flags & ~known_flags) != 0U) {
        error = "unsupported sidecar packet flags";
        return false;
    }
    const std::uint64_t payload_bytes = read_u64(bytes, 24);
    if (payload_bytes > max_packet_bytes) {
        error = "sidecar packet payload exceeds the configured limit";
        return false;
    }
    output.type = static_cast<message_type>(raw_type);
    output.flags = static_cast<packet_flags>(raw_flags);
    output.request_id = read_u64(bytes, 16);
    output.payload_bytes = payload_bytes;
    return true;
}

bool parse_frame_payload(
    std::span<const std::byte> bytes,
    frame_description& output,
    std::string& error) {
    output = {};
    error.clear();
    if (bytes.size() < frame_header_bytes) {
        error = "frame payload is shorter than its 40-byte header";
        return false;
    }
    if (bytes.size() > max_packet_bytes) {
        error = "frame payload exceeds the configured limit";
        return false;
    }

    output.input_width = read_u32(bytes, 0);
    output.input_height = read_u32(bytes, 4);
    output.output_width = read_u32(bytes, 8);
    output.output_height = read_u32(bytes, 12);
    output.pts_ns = std::bit_cast<std::int64_t>(read_u64(bytes, 16));
    output.frame_index = read_u64(bytes, 24);
    const std::uint32_t plane_count = read_u32(bytes, 32);
    const std::uint32_t reserved = read_u32(bytes, 36);
    if (!valid_dimensions(output.input_width, output.input_height) ||
        !valid_dimensions(output.output_width, output.output_height)) {
        error = "frame dimensions are outside the protocol limit";
        return false;
    }
    if (plane_count < 1U || plane_count > max_planes) {
        error = "invalid frame plane count";
        return false;
    }
    if (reserved != 0U) {
        error = "frame header reserved field must be zero";
        return false;
    }
    const std::size_t descriptor_end = frame_header_bytes +
        static_cast<std::size_t>(plane_count) * plane_header_bytes;
    if (descriptor_end > bytes.size()) {
        error = "frame payload is shorter than its descriptor table";
        return false;
    }

    std::array<bool, 6> seen_semantics{};
    std::uint64_t data_offset = static_cast<std::uint64_t>(descriptor_end);
    output.planes.reserve(plane_count);
    for (std::uint32_t index = 0; index < plane_count; ++index) {
        const std::size_t offset = frame_header_bytes +
            static_cast<std::size_t>(index) * plane_header_bytes;
        plane_description plane{};
        std::uint32_t bytes_per_pixel = 0;
        if (!decode_semantic(read_u16(bytes, offset), plane.semantic) ||
            !decode_format(read_u16(bytes, offset + 2), plane.format, bytes_per_pixel)) {
            error = "unknown frame plane semantic or pixel format";
            return false;
        }
        const std::size_t slot = semantic_slot(plane.semantic);
        if (slot >= seen_semantics.size() || seen_semantics[slot]) {
            error = "duplicate frame plane semantic";
            return false;
        }
        seen_semantics[slot] = true;
        plane.width = read_u32(bytes, offset + 4);
        plane.height = read_u32(bytes, offset + 8);
        plane.row_pitch = read_u32(bytes, offset + 12);
        const std::uint32_t flags = read_u32(bytes, offset + 16);
        const std::uint32_t reserved_plane = read_u32(bytes, offset + 20);
        plane.data_bytes = read_u64(bytes, offset + 24);
        if (!valid_dimensions(plane.width, plane.height)) {
            error = "frame plane dimensions are outside the protocol limit";
            return false;
        }
        const std::uint64_t minimum_pitch =
            static_cast<std::uint64_t>(plane.width) * bytes_per_pixel;
        if (plane.row_pitch < minimum_pitch || flags != 0U || reserved_plane != 0U) {
            error = flags != 0U
                ? "unsupported frame plane flags"
                : (reserved_plane != 0U
                    ? "frame plane reserved field must be zero"
                    : "frame plane row pitch is too small");
            return false;
        }
        const std::uint64_t expected_bytes =
            static_cast<std::uint64_t>(plane.row_pitch) * plane.height;
        if (plane.data_bytes != expected_bytes) {
            error = "frame plane byte count does not equal row pitch times height";
            return false;
        }
        if (plane.data_bytes > max_packet_bytes - data_offset) {
            error = "frame plane data exceeds the packet limit";
            return false;
        }
        plane.data_offset = data_offset;
        data_offset += plane.data_bytes;
        output.planes.push_back(plane);
    }
    if (data_offset != bytes.size()) {
        error = "frame plane byte counts do not match the payload size";
        return false;
    }
    return true;
}

}  // namespace comfy_dlss::protocol
