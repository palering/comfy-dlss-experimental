#pragma once

#include <cstdio>
#include <cstdint>
#include <string>
#include <string_view>

struct ID3D12CommandQueue;
struct ID3D12Device;

namespace comfy_dlss {

[[nodiscard]] bool run_frame_worker(
    FILE* input,
    FILE* output,
    ID3D12Device* device,
    ID3D12CommandQueue* queue,
    std::string& error);

[[nodiscard]] bool run_frame_worker_tcp(
    std::string_view host,
    std::uint16_t port,
    std::string_view authentication_token_hex,
    ID3D12Device* device,
    ID3D12CommandQueue* queue,
    std::string& error);

}  // namespace comfy_dlss
