#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <stdexcept>

namespace comfy_dlss::relay_wire {

// Transport only: never interprets or modifies the external worker's frames.
inline constexpr std::uint32_t magic = 0x31524c43U; // CLR1, little endian
inline constexpr std::uint32_t max_payload = 65536;
inline constexpr std::size_t header_size = 12;
enum class kind : std::uint32_t {
    input = 1, input_end = 2, cancel = 3,
    output = 4, diagnostic = 5, exit = 6, error = 7, hello = 8,
};

inline void put_u32(std::span<std::byte> bytes, std::size_t offset, std::uint32_t value) {
    if (offset > bytes.size() || bytes.size() - offset < 4) throw std::out_of_range("u32 write");
    for (unsigned i = 0; i < 4; ++i) bytes[offset + i] = static_cast<std::byte>((value >> (i * 8U)) & 255U);
}

inline std::uint32_t get_u32(std::span<const std::byte> bytes, std::size_t offset) {
    if (offset > bytes.size() || bytes.size() - offset < 4) throw std::out_of_range("u32 read");
    std::uint32_t value = 0;
    for (unsigned i = 0; i < 4; ++i) value |= std::to_integer<std::uint32_t>(bytes[offset + i]) << (i * 8U);
    return value;
}

inline std::array<std::byte, header_size> header(kind type, std::uint32_t size) {
    if (size > max_payload) throw std::invalid_argument("relay payload too large");
    std::array<std::byte, header_size> result{};
    put_u32(result, 0, magic);
    put_u32(result, 4, static_cast<std::uint32_t>(type));
    put_u32(result, 8, size);
    return result;
}

inline bool parse(std::span<const std::byte> data, kind& type, std::uint32_t& size) {
    if (data.size() != header_size || get_u32(data, 0) != magic) return false;
    const auto raw = get_u32(data, 4);
    size = get_u32(data, 8);
    if (raw < 1 || raw > 8 || size > max_payload) return false;
    type = static_cast<kind>(raw);
    return true;
}
} // namespace comfy_dlss::relay_wire
