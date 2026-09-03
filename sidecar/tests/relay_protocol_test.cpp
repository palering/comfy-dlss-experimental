#include "relay_protocol.hpp"
#include <cassert>
#include <iostream>

int main() {
    namespace wire = comfy_dlss::relay_wire;
    auto header = wire::header(wire::kind::output, 65536);
    wire::kind type{};
    std::uint32_t size = 0;
    assert(wire::parse(header, type, size));
    assert(type == wire::kind::output && size == 65536);
    assert(!wire::parse(std::span(header).first(11), type, size));
    wire::put_u32(header, 8, 65537);
    assert(!wire::parse(header, type, size));
    wire::put_u32(header, 8, 0);
    wire::put_u32(header, 4, 99);
    assert(!wire::parse(header, type, size));
    header[0] = std::byte{0};
    assert(!wire::parse(header, type, size));
    bool threw = false;
    try { (void)wire::get_u32(std::span(header).first(3), 0); }
    catch (const std::out_of_range&) { threw = true; }
    assert(threw);
    std::cout << "relay protocol tests passed\n";
}
