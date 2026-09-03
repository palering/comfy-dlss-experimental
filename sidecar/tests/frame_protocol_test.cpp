#include "frame_protocol.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <limits>
#include <span>
#include <string>
#include <vector>

namespace {

void append_u16(std::vector<std::byte>& bytes, std::uint16_t value) {
    for (unsigned int shift = 0; shift < 16U; shift += 8U) {
        bytes.push_back(static_cast<std::byte>((value >> shift) & 0xffU));
    }
}

void append_u32(std::vector<std::byte>& bytes, std::uint32_t value) {
    for (unsigned int shift = 0; shift < 32U; shift += 8U) {
        bytes.push_back(static_cast<std::byte>((value >> shift) & 0xffU));
    }
}

void append_u64(std::vector<std::byte>& bytes, std::uint64_t value) {
    for (unsigned int shift = 0; shift < 64U; shift += 8U) {
        bytes.push_back(static_cast<std::byte>((value >> shift) & 0xffULL));
    }
}

bool require(bool condition, const char* message) {
    if (!condition) std::cerr << message << '\n';
    return condition;
}

std::vector<std::byte> valid_frame() {
    std::vector<std::byte> bytes;
    bytes.reserve(76);
    append_u32(bytes, 1);  // input width
    append_u32(bytes, 1);  // input height
    append_u32(bytes, 2);  // output width
    append_u32(bytes, 2);  // output height
    append_u64(bytes, 33'366'700);  // pts
    append_u64(bytes, 7);  // frame index
    append_u32(bytes, 1);  // plane count
    append_u32(bytes, 0);  // reserved
    append_u16(bytes, 1);  // COLOR
    append_u16(bytes, 1);  // RGBA8
    append_u32(bytes, 1);
    append_u32(bytes, 1);
    append_u32(bytes, 4);  // row pitch
    append_u32(bytes, 0);  // flags
    append_u32(bytes, 0);  // reserved
    append_u64(bytes, 4);  // byte count
    bytes.insert(bytes.end(), 4, std::byte{0x7f});
    return bytes;
}

}  // namespace

int main() {
    using namespace comfy_dlss::protocol;
    std::string error;

    std::array<std::byte, packet_header_bytes> header{};
    std::copy(magic.begin(), magic.end(), header.begin());
    header[8] = std::byte{1};
    header[10] = std::byte{4};
    header[16] = std::byte{42};
    packet_header decoded_header{};
    if (!require(parse_packet_header(header, decoded_header, error), error.c_str()) ||
        !require(decoded_header.type == message_type::frame, "message type mismatch") ||
        !require(decoded_header.request_id == 42, "request id mismatch")) {
        return 1;
    }

    auto bytes = valid_frame();
    frame_description frame{};
    if (!require(parse_frame_payload(bytes, frame, error), error.c_str()) ||
        !require(frame.frame_index == 7, "frame index mismatch") ||
        !require(frame.planes.size() == 1, "plane count mismatch") ||
        !require(frame.planes[0].data_offset == 72, "plane data offset mismatch")) {
        return 2;
    }

    bytes.pop_back();
    if (!require(!parse_frame_payload(bytes, frame, error), "truncated frame was accepted")) {
        return 3;
    }
    bytes = valid_frame();
    for (std::size_t size = 0; size < bytes.size(); ++size) {
        if (!require(!parse_frame_payload(std::span(bytes).first(size), frame, error),
                     "truncated frame accepted at a byte boundary")) return 4;
    }
    if (!parse_frame_payload(bytes, frame, error) ||
        !require(validate_frame_inputs(frame, error), error.c_str())) return 5;
    const frame_description valid = frame;
    frame.planes[0].format = pixel_format::r32_float;
    if (!require(!validate_frame_inputs(frame, error), "R32 COLOR was accepted")) return 6;
    frame = valid;
    frame.planes[0].semantic = plane_semantic::output_color;
    if (!require(!validate_frame_inputs(frame, error), "OUTPUT_COLOR input was accepted")) return 7;
    frame = valid;
    frame.planes.push_back({plane_semantic::motion, pixel_format::rg16_float, 1, 1, 7, 0, 7});
    frame.planes.push_back({plane_semantic::depth, pixel_format::r32_float, 1, 1, 4, 0, 4});
    frame.planes.push_back({plane_semantic::reactive_mask, pixel_format::r8_unorm, 1, 1, 1, 0, 1});
    frame.planes.push_back({plane_semantic::exposure, pixel_format::r32_float, 1, 1, 4, 0, 4});
    frame.planes[0].format = pixel_format::rgba16_float;
    frame.planes[0].row_pitch = 8;
    frame.planes[0].data_bytes = 8;
    if (!require(validate_frame_inputs(frame, error), error.c_str())) return 8;
    frame.planes.back().width = 2;
    if (!require(!validate_frame_inputs(frame, error), "non-1x1 exposure accepted")) return 9;
    frame = valid;
    frame.planes.push_back(frame.planes[0]);
    if (!require(!validate_frame_inputs(frame, error), "duplicate renderer input accepted")) return 10;
    frame = valid;
    frame.planes[0].row_pitch = 3;
    if (!require(!validate_frame_inputs(frame, error), "short row accepted")) return 11;
    frame = valid;
    frame.planes[0].semantic = plane_semantic::depth;
    frame.planes[0].format = pixel_format::r32_float;
    if (!require(!validate_frame_inputs(frame, error), "missing color accepted")) return 12;
    frame = valid;
    frame.planes[0].data_bytes = std::numeric_limits<std::uint64_t>::max();
    if (!require(!validate_frame_inputs(frame, error), "oversized byte count accepted")) return 13;
    bytes[42] = std::byte{0xff};
    if (!require(!parse_frame_payload(bytes, frame, error), "unknown format accepted")) return 14;
    header[31] = std::byte{0xff};
    if (!require(!parse_packet_header(header, decoded_header, error), "oversized packet accepted")) return 15;
    if (!require(bytes_per_pixel(pixel_format::rgba16_float) == 8, "FP16 byte width mismatch") ||
        !require(bytes_per_pixel(static_cast<pixel_format>(0)) == 0, "unknown byte width accepted")) return 16;
    std::cout << "frame protocol and renderer input contract self-tests passed\n";
    return 0;
}
