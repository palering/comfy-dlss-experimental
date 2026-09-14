#include "sl_wire.hpp"
#include <cassert>
#include <vector>

using namespace comfy_dlss;
namespace wire = sl_wire;
template<class F> void rejects(F run) { bool rejected = false; try { run(); } catch (const std::exception&) { rejected = true; } assert(rejected); }

int main() {
    std::array<unsigned char,40> near4k{};
    wire::put32(near4k,0,736); wire::put32(near4k,4,1280);
    wire::put32(near4k,8,2208); wire::put32(near4k,12,3840);
    wire::put32(near4k,16,3); wire::put32(near4k,24,8); wire::put32(near4k,32,1);
    assert(wire::parse_settings(near4k).common.output_width == 2208);
    rejects([&] { sr_wire::parse_settings(std::span(near4k).first(32)); });
    wire::put32(near4k,0,1024); wire::put32(near4k,4,1024);
    wire::put32(near4k,8,4096); wire::put32(near4k,12,4096);
    rejects([&] { wire::parse_settings(near4k); }); // RGBA16F plus PTS exceeds 128 MiB.
    std::array<unsigned char, 40> raw{};
    for (std::size_t at = 0; at < 16; at += 4) wire::put32(raw, at, 64);
    wire::put32(raw, 16, 5); wire::put32(raw, 24, 8); wire::put32(raw, 32, 1);
    const auto sr = wire::parse_settings(raw);
    assert(!sr.rr && wire::frame_bytes(sr) == 48 + 324 + 4096 * 16);
    wire::put32(raw, 32, 2); rejects([&] { wire::parse_settings(raw); });
    wire::put32(raw, 24, 0);
    const auto rr = wire::parse_settings(raw);
    assert(rr.rr && wire::frame_bytes(rr) == 48 + 324 + 128 + 4096 * 44);
    wire::put32(raw, 20, 11); rejects([&] { wire::parse_settings(raw); }); wire::put32(raw, 20, 0);
    wire::put32(raw, 36, 2); rejects([&] { wire::parse_settings(raw); });
    std::array<unsigned char, 324> camera{};
    auto putf = [](auto& bytes, std::size_t at, float f) { wire::put32(bytes, at, std::bit_cast<std::uint32_t>(f)); };
    for (std::size_t block = 0; block < 4; ++block) for (std::size_t d = 0; d < 4; ++d) putf(camera, block * 64 + d * 20, 1);
    putf(camera, 272, 1); putf(camera, 280, 1); putf(camera, 300, 1);
    putf(camera, 304, .1F); putf(camera, 308, 100); putf(camera, 312, 1.57F); putf(camera, 316, 1);
    assert(wire::parse_camera(camera, sr).aspect == 1);
    putf(camera, 0, 0); rejects([&] { wire::parse_camera(camera, sr); }); putf(camera, 0, 1);
    wire::put32(camera, 320, 2); rejects([&] { wire::parse_camera(camera, sr); });
    std::array<unsigned char, 48> frame{};
    for (std::size_t at = 24; at <= 40; at += 4) putf(frame, at, 1);
    rejects([&] { wire::parse_frame(frame, sr, 0, 0); });
    wire::put32(frame, 8, 1);
    assert(wire::parse_frame(frame, sr, 0, 0).info.reset);
    rejects([&] { wire::parse_frame(frame, sr, 1, 0); });
    putf(frame, 16, .25F); rejects([&] { wire::parse_frame(frame, sr, 0, 0); }); putf(frame, 16, 0);
    putf(frame, 40, 2); rejects([&] { wire::parse_frame(frame, sr, 0, 0); });
    assert(wire::parse_frame(frame, rr, 0, 0).exposure == 2);
    wire::put32(frame, 44, 1); rejects([&] { wire::parse_frame(frame, rr, 0, 0); });
    auto header = wire::encode_header({wire::type::create, 40, 1, 7});
    wire::validate_request(wire::parse_header(header), 1, false, 0, 0, 0, 0);
    rejects([&] { wire::validate_request(wire::parse_header(header), 2, false, 0, 0, 0, 0); });
    wire::put32(header, 12, 32); rejects([&] { wire::validate_request(wire::parse_header(header), 1, false, 0, 0, 0, 0); });
    wire::put32(header, 0, sr_wire::magic); rejects([&] { wire::parse_header(header); });
    header = wire::encode_header({wire::type::frame, wire::frame_bytes(rr), 2, 7});
    wire::validate_request(wire::parse_header(header), 2, true, 7, wire::frame_bytes(rr), 0, 0);
    rejects([&] { wire::validate_request(wire::parse_header(header), 2, true, 7, wire::frame_bytes(sr), 0, 0); });
    rejects([&] { wire::validate_request(wire::parse_header(header), 2, true, 7, wire::frame_bytes(rr), wire::max_evaluations, 0); });
    rejects([&] { wire::parse_rr({}, rr, 1); });
}
