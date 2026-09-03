#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <fcntl.h>
#include <io.h>

#include "../src/relay_protocol.hpp"

#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <span>
#include <string_view>
#include <vector>

namespace {
namespace wire = comfy_dlss::relay_wire;
bool read_exact(std::span<std::byte> bytes) {
    while (!bytes.empty()) {
        const auto count = std::fread(bytes.data(), 1, bytes.size(), stdin);
        if (count == 0) return false;
        bytes = bytes.subspan(count);
    }
    return true;
}
bool write_exact(std::span<const std::byte> bytes) {
    while (!bytes.empty()) {
        const auto count = std::fwrite(bytes.data(), 1, bytes.size(), stdout);
        if (count == 0) return false;
        bytes = bytes.subspan(count);
    }
    return std::fflush(stdout) == 0;
}
} // namespace

int main(int argc, char** argv) {
    if (argc != 2 || std::strcmp(argv[1], "--video") != 0) return 2;
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
    std::fprintf(stderr, "MOCK ONLY: no GPU, NGX, or DLSS is loaded.\n");
    std::fflush(stderr);
    const char* raw_mode = std::getenv("COMFY_DLSS_MOCK_MODE");
    const std::string_view mode = raw_mode ? raw_mode : "echo";
    if (mode == "exit") return 23;
    if (mode == "hang") { Sleep(60000); return 24; }
    std::array<std::byte, 56> header{};
    if (!read_exact(header) || wire::get_u32(header, 0) != 0x32563544U) return 3;
    const auto width = wire::get_u32(header, 4), height = wire::get_u32(header, 8);
    const auto frames = wire::get_u32(header, 16);
    if (width < 64 || height < 64 || width > 1920 || height > 1080 || frames > 100) return 4;
    const auto size = width * height * 4U;
    std::vector<std::byte> color(size), motion(size);
    for (std::uint32_t frame = 0; frame < frames; ++frame) {
        std::array<std::byte, 24> input{};
        if (!read_exact(input) || wire::get_u32(input, 0) != 0x314d5246U ||
            !read_exact(color) || !read_exact(motion)) return 5;
        if (mode == "log_flood") {
            std::array<char, 4096> log{};
            log.fill('x');
            for (unsigned i = 0; i < 128; ++i) std::fwrite(log.data(), 1, log.size(), stderr);
            std::fflush(stderr);
        }
        std::array<std::byte, 28> result{};
        wire::put_u32(result, 0, 0x3154554fU);
        wire::put_u32(result, 4, wire::get_u32(input, 4));
        wire::put_u32(result, 8, 1);
        wire::put_u32(result, 12, size);
        wire::put_u32(result, 16, mode == "bad_result" ? 0xbad00002U : 1U);
        std::memcpy(result.data() + 20, input.data() + 16, 8);
        if (!write_exact(result)) return 6;
        if (mode == "truncated") { write_exact(std::span(color).first(7)); return 25; }
        if (!write_exact(color)) return 7;
    }
    return 0;
}
