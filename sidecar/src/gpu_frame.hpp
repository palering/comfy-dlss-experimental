#pragma once

#include "frame_protocol.hpp"

struct ID3D12Device;
struct ID3D12CommandQueue;

namespace comfy_dlss {

// Diagnostic copy only: all planes are resident together, then copied back in
// descriptor order with tightly packed rows. No NGX evaluation or conversion.
[[nodiscard]] bool gpu_frame_round_trip(
    ID3D12Device* device,
    ID3D12CommandQueue* queue,
    const protocol::frame_description& frame,
    std::span<const std::byte> payload,
    std::vector<std::vector<std::byte>>& output,
    std::string& error);

}  // namespace comfy_dlss
