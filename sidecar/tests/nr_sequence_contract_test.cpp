#include "nr_sequence_contract.hpp"
#include <array>
#include <cassert>
#include <limits>
using namespace comfy_dlss::sequence_probe;
template<class F> void rejected(F run) {
    bool failed = false;
    try { run(); } catch (const std::runtime_error&) { failed = true; }
    assert(failed);
}
void put(std::span<unsigned char> bytes, std::size_t offset, std::uint32_t value) {
    for (unsigned int i = 0; i < 4; ++i) bytes[offset+i] = static_cast<unsigned char>((value >> (8*i)) & 255);
}
int main() {
    std::array<unsigned char, header_bytes> header{};
    put(header, 0, magic); put(header, 4, 1); put(header, 8, 640); put(header, 12, 360); put(header, 16, 8);
    constexpr std::uint64_t size = header_bytes + 8 * (frame_header_bytes + 640ULL*360*12);
    const auto shape = parse_layout(header, size);
    assert(shape.width == 640 && shape.height == 360 && shape.count == 8);
    assert(shape.color_bytes == 640*360*8 && shape.motion_bytes == 640*360*4);
    rejected([&]{ parse_layout(header, size-1); });
    rejected([&]{ parse_layout(header, size+1); });
    rejected([&]{ parse_layout(std::span(header).first(23), size); });
    for (auto offset : {0U, 4U, 8U, 12U, 16U, 20U}) {
        auto bad = header; put(bad, offset, 0xffffffffU);
        rejected([&]{ parse_layout(bad, size); });
    }
    auto huge = header; put(huge, 8, 1920); put(huge, 12, 1080); put(huge, 16, 16);
    rejected([&]{ parse_layout(huge, header_bytes+16*(frame_header_bytes+1920ULL*1080*12)); });
    std::array<unsigned char, frame_header_bytes> frame{};
    rejected([&]{ parse_frame(frame, 0, 0); });
    put(frame, 8, 1); assert(parse_frame(frame, 0, 0).reset);
    rejected([&]{ parse_frame(frame, 1, 0); });
    put(frame, 0, 100); put(frame, 8, 0); assert(parse_frame(frame, 1, 0).pts == 100);
    rejected([&]{ parse_frame(frame, 1, 101); });
    put(frame, 8, 2); rejected([&]{ parse_frame(frame, 1, 0); });
    put(frame, 8, 0); put(frame, 4, 0x80000000); rejected([&]{ parse_frame(frame, 1, 0); });
    put(frame, 4, 0); put(frame, 12, 1); rejected([&]{ parse_frame(frame, 1, 0); });
    rejected([&]{ u32(frame, std::numeric_limits<std::size_t>::max()); });
    for (unsigned int half = 0; half <= 0xffff; ++half) {
        const std::array<unsigned char, 2> bytes{static_cast<unsigned char>(half & 255), static_cast<unsigned char>(half >> 8)};
        assert(finite_half_plane(bytes) == ((half & 0x7c00) != 0x7c00));
    }
    const std::array<unsigned char, 1> odd{0}; assert(!finite_half_plane(odd));
}
