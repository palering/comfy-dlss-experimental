#include "gpu_frame.hpp"
#include "win32_raii.hpp"

#include <d3d12.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <new>

namespace comfy_dlss {
namespace {
namespace wire = protocol;

std::string hex_hresult(HRESULT result) {
    char value[16]{};
    std::snprintf(value, sizeof(value), "0x%08lx", static_cast<unsigned long>(result));
    return value;
}

DXGI_FORMAT texture_format(wire::pixel_format format) noexcept {
    switch (format) {
        case wire::pixel_format::rgba8_unorm: return DXGI_FORMAT_R8G8B8A8_UNORM;
        case wire::pixel_format::rgba16_float: return DXGI_FORMAT_R16G16B16A16_FLOAT;
        case wire::pixel_format::rg16_float: return DXGI_FORMAT_R16G16_FLOAT;
        case wire::pixel_format::r32_float: return DXGI_FORMAT_R32_FLOAT;
        case wire::pixel_format::r8_unorm: return DXGI_FORMAT_R8_UNORM;
    }
    return DXGI_FORMAT_UNKNOWN;
}

// Once submitted, no error path may destroy resources before GPU completion.
// On lost synchronization, stop ONLY this isolated worker without unwinding.
// No allocation or exception is permitted between ExecuteCommandLists and fence.
[[noreturn]] void fail_inflight(const char* operation, unsigned long code) noexcept {
    std::fprintf(stderr,
        "{\"stage\":\"gpu-fence\",\"operation\":\"%s\",\"code\":\"0x%08lx\","
        "\"error\":\"GPU completion unverified; terminating isolated worker\"}\n",
        operation, code);
    std::fflush(stderr);
    std::_Exit(70);
}

bool create_buffer(
    ID3D12Device* device,
    D3D12_HEAP_TYPE heap_type,
    std::uint64_t size,
    D3D12_RESOURCE_STATES initial_state,
    com_ptr<ID3D12Resource>& output,
    std::string& error) {
    D3D12_HEAP_PROPERTIES heap{};
    heap.Type = heap_type;
    heap.CPUPageProperty = D3D12_CPU_PAGE_PROPERTY_UNKNOWN;
    heap.MemoryPoolPreference = D3D12_MEMORY_POOL_UNKNOWN;
    heap.CreationNodeMask = 1;
    heap.VisibleNodeMask = 1;

    D3D12_RESOURCE_DESC description{};
    description.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    description.Width = size;
    description.Height = 1;
    description.DepthOrArraySize = 1;
    description.MipLevels = 1;
    description.Format = DXGI_FORMAT_UNKNOWN;
    description.SampleDesc.Count = 1;
    description.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;

    void* raw_resource = nullptr;
    const HRESULT result = device->CreateCommittedResource(
        &heap,
        D3D12_HEAP_FLAG_NONE,
        &description,
        initial_state,
        nullptr,
        IID_ID3D12Resource,
        &raw_resource);
    output.reset(static_cast<ID3D12Resource*>(raw_resource));
    if (FAILED(result) || !output) {
        error = "CreateCommittedResource(buffer) failed: " + hex_hresult(result);
        return false;
    }
    return true;
}

bool create_texture(
    ID3D12Device* device,
    std::uint32_t width,
    std::uint32_t height,
    DXGI_FORMAT format,
    com_ptr<ID3D12Resource>& output,
    std::string& error) {
    D3D12_HEAP_PROPERTIES heap{};
    heap.Type = D3D12_HEAP_TYPE_DEFAULT;
    heap.CPUPageProperty = D3D12_CPU_PAGE_PROPERTY_UNKNOWN;
    heap.MemoryPoolPreference = D3D12_MEMORY_POOL_UNKNOWN;
    heap.CreationNodeMask = 1;
    heap.VisibleNodeMask = 1;

    D3D12_RESOURCE_DESC description{};
    description.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    description.Width = width;
    description.Height = height;
    description.DepthOrArraySize = 1;
    description.MipLevels = 1;
    description.Format = format;
    description.SampleDesc.Count = 1;
    description.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;

    void* raw_resource = nullptr;
    const HRESULT result = device->CreateCommittedResource(
        &heap,
        D3D12_HEAP_FLAG_NONE,
        &description,
        D3D12_RESOURCE_STATE_COPY_DEST,
        nullptr,
        IID_ID3D12Resource,
        &raw_resource);
    output.reset(static_cast<ID3D12Resource*>(raw_resource));
    if (FAILED(result) || !output) {
        error = "CreateCommittedResource(texture) failed: " + hex_hresult(result);
        return false;
    }
    return true;
}


struct plane_resources final {
    com_ptr<ID3D12Resource> texture;
    com_ptr<ID3D12Resource> upload;
    com_ptr<ID3D12Resource> readback;
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};
    std::size_t buffer_bytes = 0;
    std::size_t tight_pitch = 0;
};

}  // namespace

bool gpu_frame_round_trip(
    ID3D12Device* device,
    ID3D12CommandQueue* queue,
    const wire::frame_description& frame,
    std::span<const std::byte> payload,
    std::vector<std::vector<std::byte>>& output,
    std::string& error) {
    if (device == nullptr || queue == nullptr) {
        error = "null D3D12 device or queue";
        return false;
    }
    if (!wire::validate_frame_inputs(frame, error)) return false;
    std::vector<plane_resources> resources(frame.planes.size());
    output.clear();
    output.resize(frame.planes.size());
    std::uint64_t aggregate_buffer_bytes = 0;
    for (std::size_t index = 0; index < frame.planes.size(); ++index) {
        const auto& plane = frame.planes[index];
        auto& resource = resources[index];
        if (plane.data_offset > payload.size() || plane.data_bytes > payload.size() - plane.data_offset) {
            error = "input plane range is outside its packet";
            return false;
        }
        if (!create_texture(device, plane.width, plane.height, texture_format(plane.format),
                            resource.texture, error)) return false;
        const auto description = resource.texture->GetDesc();
        UINT rows = 0;
        UINT64 row_bytes = 0;
        UINT64 buffer_bytes = 0;
        device->GetCopyableFootprints(
            &description, 0, 1, 0, &resource.footprint, &rows, &row_bytes, &buffer_bytes);
        const auto& layout = resource.footprint;
        const std::uint64_t tight_pitch =
            static_cast<std::uint64_t>(plane.width) * wire::bytes_per_pixel(plane.format);
        // These are uncompressed single-plane 2D textures, with BaseOffset zero.
        const std::uint64_t accessed_bytes =
            static_cast<std::uint64_t>(plane.height - 1U) * layout.Footprint.RowPitch + tight_pitch;
        if (layout.Offset != 0 || rows != plane.height || row_bytes != tight_pitch ||
            layout.Footprint.Width != plane.width || layout.Footprint.Height != plane.height ||
            layout.Footprint.Depth != 1 || layout.Footprint.Format != description.Format ||
            layout.Footprint.RowPitch < tight_pitch ||
            layout.Footprint.RowPitch % D3D12_TEXTURE_DATA_PITCH_ALIGNMENT != 0 ||
            buffer_bytes < accessed_bytes ||
            buffer_bytes > wire::max_packet_bytes - aggregate_buffer_bytes ||
            buffer_bytes > std::numeric_limits<std::size_t>::max()) {
            error = "invalid D3D12 copy footprint or aggregate staging limit exceeded";
            return false;
        }
        aggregate_buffer_bytes += buffer_bytes;
        resource.buffer_bytes = static_cast<std::size_t>(buffer_bytes);
        resource.tight_pitch = static_cast<std::size_t>(tight_pitch);
        // Allocate everything before submitting any GPU work.
        output[index].resize(resource.tight_pitch * plane.height);
        if (!create_buffer(device, D3D12_HEAP_TYPE_UPLOAD, buffer_bytes,
                           D3D12_RESOURCE_STATE_GENERIC_READ, resource.upload, error) ||
            !create_buffer(device, D3D12_HEAP_TYPE_READBACK, buffer_bytes,
                           D3D12_RESOURCE_STATE_COPY_DEST, resource.readback, error)) return false;
        void* mapped = nullptr;
        const D3D12_RANGE no_read{0, 0};
        const HRESULT result = resource.upload->Map(0, &no_read, &mapped);
        if (FAILED(result) || mapped == nullptr) {
            error = "Map(upload) failed: " + hex_hresult(result);
            return false;
        }
        auto* destination = static_cast<std::byte*>(mapped);
        const auto* source_bytes = payload.data() + static_cast<std::size_t>(plane.data_offset);
        std::memset(destination, 0, resource.buffer_bytes);
        for (std::size_t row = 0; row < plane.height; ++row) {
            std::memcpy(destination + row * layout.Footprint.RowPitch,
                        source_bytes + row * plane.row_pitch, resource.tight_pitch);
        }
        const D3D12_RANGE written{0, resource.buffer_bytes};
        resource.upload->Unmap(0, &written);
    }

    com_ptr<ID3D12CommandAllocator> allocator;
    void* raw_allocator = nullptr;
    HRESULT result = device->CreateCommandAllocator(
        D3D12_COMMAND_LIST_TYPE_DIRECT, IID_ID3D12CommandAllocator, &raw_allocator);
    allocator.reset(static_cast<ID3D12CommandAllocator*>(raw_allocator));
    if (FAILED(result) || !allocator) {
        error = "CreateCommandAllocator failed: " + hex_hresult(result);
        return false;
    }
    com_ptr<ID3D12GraphicsCommandList> command_list;
    void* raw_list = nullptr;
    result = device->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator.get(),
                                      nullptr, IID_ID3D12GraphicsCommandList, &raw_list);
    command_list.reset(static_cast<ID3D12GraphicsCommandList*>(raw_list));
    if (FAILED(result) || !command_list) {
        error = "CreateCommandList failed: " + hex_hresult(result);
        return false;
    }
    for (const auto& resource : resources) {
        D3D12_TEXTURE_COPY_LOCATION source{};
        source.pResource = resource.upload.get();
        source.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        source.PlacedFootprint = resource.footprint;
        D3D12_TEXTURE_COPY_LOCATION texture{};
        texture.pResource = resource.texture.get();
        texture.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        command_list->CopyTextureRegion(&texture, 0, 0, 0, &source, nullptr);
    }
    // All guides and color are alive together here. The future NGX adapter must
    // introduce its own resource-state/DSV/UAV contract; this only tests copies.
    for (const auto& resource : resources) {
        D3D12_RESOURCE_BARRIER barrier{};
        barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        barrier.Transition.pResource = resource.texture.get();
        barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_DEST;
        barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
        command_list->ResourceBarrier(1, &barrier);
        D3D12_TEXTURE_COPY_LOCATION texture{};
        texture.pResource = resource.texture.get();
        texture.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        D3D12_TEXTURE_COPY_LOCATION destination{};
        destination.pResource = resource.readback.get();
        destination.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        destination.PlacedFootprint = resource.footprint;
        command_list->CopyTextureRegion(&destination, 0, 0, 0, &texture, nullptr);
    }
    result = command_list->Close();
    if (FAILED(result)) {
        error = "Close(command list) failed: " + hex_hresult(result);
        return false;
    }
    com_ptr<ID3D12Fence> fence;
    void* raw_fence = nullptr;
    result = device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_ID3D12Fence, &raw_fence);
    fence.reset(static_cast<ID3D12Fence*>(raw_fence));
    if (FAILED(result) || !fence) {
        error = "CreateFence failed: " + hex_hresult(result);
        return false;
    }
    unique_handle event{CreateEventW(nullptr, FALSE, FALSE, nullptr)};
    if (!event) {
        error = "CreateEventW failed: " + std::to_string(GetLastError());
        return false;
    }
    ID3D12CommandList* lists[] = {command_list.get()};
    queue->ExecuteCommandLists(1, lists);
    constexpr std::uint64_t fence_value = 1;
    result = queue->Signal(fence.get(), fence_value);
    if (FAILED(result)) fail_inflight("Signal", static_cast<unsigned long>(result));
    if (fence->GetCompletedValue() < fence_value) {
        result = fence->SetEventOnCompletion(fence_value, event.get());
        if (FAILED(result)) fail_inflight("SetEventOnCompletion", static_cast<unsigned long>(result));
        const DWORD waited = WaitForSingleObject(event.get(), 30'000);
        if (waited != WAIT_OBJECT_0) {
            fail_inflight("WaitForSingleObject", waited == WAIT_FAILED ? GetLastError() : waited);
        }
    }
    const auto completed = fence->GetCompletedValue();
    if (completed == std::numeric_limits<std::uint64_t>::max() || completed < fence_value) {
        fail_inflight("GetCompletedValue", static_cast<unsigned long>(device->GetDeviceRemovedReason()));
    }
    // The fence is proven complete: normal error handling and RAII are safe.
    result = device->GetDeviceRemovedReason();
    if (FAILED(result)) {
        error = "D3D12 device was removed: " + hex_hresult(result);
        return false;
    }
    for (std::size_t index = 0; index < resources.size(); ++index) {
        const auto& plane = frame.planes[index];
        const auto& resource = resources[index];
        void* mapped = nullptr;
        const D3D12_RANGE read_range{0, resource.buffer_bytes};
        result = resource.readback->Map(0, &read_range, &mapped);
        if (FAILED(result) || mapped == nullptr) {
            error = "Map(readback) failed: " + hex_hresult(result);
            return false;
        }
        const auto* source_bytes = static_cast<const std::byte*>(mapped);
        for (std::size_t row = 0; row < plane.height; ++row) {
            std::memcpy(output[index].data() + row * resource.tight_pitch,
                        source_bytes + row * resource.footprint.Footprint.RowPitch,
                        resource.tight_pitch);
        }
        const D3D12_RANGE no_write{0, 0};
        resource.readback->Unmap(0, &no_write);
    }
    return true;
}

}  // namespace comfy_dlss
