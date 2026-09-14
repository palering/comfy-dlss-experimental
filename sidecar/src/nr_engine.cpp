// Owned NR engine: graphics/model lifetime only, independent of transport.
#include "probe_report.hpp"
#include "nr_engine.hpp"
#include "nr_caller.hpp"
#include "nr_parameters.h"
#include "nr_input_contract.hpp"
#include "nr_sequence_contract.hpp"
#include "owned_wire.hpp"
#include "win32_raii.hpp"
#include <d3d12.h>
#include <dxgi1_4.h>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>

namespace comfy_dlss {
namespace {
using namespace comfy_dlss;
using params_ptr = std::unique_ptr<ComfyNRParameters, decltype(&ComfyNR_ParametersDestroy)>;
std::string code_text(unsigned long code) {
    char b[16]{}; std::snprintf(b, sizeof(b), "0x%08lx", code); return b;
}
void check(HRESULT code, const char* operation) {
    if (FAILED(code)) throw std::runtime_error(std::string(operation) + ": " + code_text(static_cast<unsigned long>(code)));
}
void ngx_check(NVSDK_NGX_Result code, const char* operation) {
    if (code != NVSDK_NGX_Result_Success)
        throw std::runtime_error(std::string(operation) + ": " + code_text(static_cast<unsigned long>(code)));
}
template<class T> T symbol(const unique_module& module, const char* name) {
    const auto address = module.symbol(name);
    if (!address) throw std::runtime_error(std::string("Missing export: ") + name);
    return reinterpret_cast<T>(address); // Win32 GetProcAddress contract, exact declared function type.
}
unique_module load_local(const std::filesystem::path& path) {
    if (!path.is_absolute() || !std::filesystem::is_regular_file(path))
        throw std::runtime_error("DLL path must be an existing absolute file");
    unique_module module{LoadLibraryExW(path.c_str(), nullptr,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32)};
    if (!module) throw std::runtime_error("LoadLibraryEx failed: " + code_text(GetLastError()));
    return module;
}

}
struct texture {
    com_ptr<ID3D12Resource> image, transfer;
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};
    UINT64 bytes = 0;
};

class nr_engine::implementation final {
    probe_report& report_;
    // Modules declared before their consumers: destructed last.
    unique_module dxgi_, d3d_, model_, caller_;
    com_ptr<IDXGIFactory1> factory_;
    com_ptr<ID3D12Device> device_;
    com_ptr<ID3D12CommandQueue> queue_;
    com_ptr<ID3D12CommandAllocator> allocator_;
    com_ptr<ID3D12GraphicsCommandList> commands_;
    com_ptr<ID3D12Fence> fence_;
    unique_handle event_;
    texture color_, motion_, output_;
    params_ptr parameters_{nullptr, &ComfyNR_ParametersDestroy};
    NVSDK_NGX_Handle* feature_ = nullptr;
    bool initialized_ = false;
    UINT64 fence_value_ = 0;
    decltype(&ComfyNR_CallRelease) call_release_ = nullptr;
    decltype(&ComfyNR_CallShutdown) call_shutdown_ = nullptr;
    decltype(&ComfyNR_CallEvaluate) call_evaluate_ = nullptr;
    caller::release_fn release_ = nullptr;
    caller::shutdown_fn shutdown_ = nullptr;
    caller::evaluate_fn evaluate_ = nullptr;
    unsigned int width_, height_;

    [[noreturn]] void inflight_failure(const char* stage, const char* what, unsigned long code) noexcept {
        report_.event(stage, "failed", code);
        if (report_.console_enabled()) {
            std::fprintf(stderr, "GPU completion unverified: %s 0x%08lx; terminating owned probe\n", what, code);
            std::fflush(stderr);
        }
        std::_Exit(70);
    }
    void submit_wait(const char* stage) {
        report_.event(stage, "begin");
        check(commands_->Close(), "CommandList.Close");
        if (fence_value_ == std::numeric_limits<UINT64>::max() - 1)
            throw std::runtime_error("Fence value exhausted");
        ++fence_value_;
        ID3D12CommandList* lists[]{commands_.get()};
        queue_->ExecuteCommandLists(1, lists);
        // No throwing code or allocation from submission until proven completion.
        auto hr = queue_->Signal(fence_.get(), fence_value_);
        if (FAILED(hr)) inflight_failure(stage, "Signal", static_cast<unsigned long>(hr));
        hr = fence_->SetEventOnCompletion(fence_value_, event_.get());
        if (FAILED(hr)) inflight_failure(stage, "SetEventOnCompletion", static_cast<unsigned long>(hr));
        const auto wait = WaitForSingleObject(event_.get(), 30000);
        const auto completed = fence_->GetCompletedValue();
        if (wait != WAIT_OBJECT_0 || completed == std::numeric_limits<UINT64>::max() || completed < fence_value_)
            inflight_failure(stage, "WaitForSingleObject", wait);
        report_.event(stage, "done");
    }
    void begin() {
        check(allocator_->Reset(), "Allocator.Reset");
        check(commands_->Reset(allocator_.get(), nullptr), "CommandList.Reset");
    }
    void barrier(ID3D12Resource* resource, D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after) {
        D3D12_RESOURCE_BARRIER b{}; b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition = {resource, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, before, after};
        commands_->ResourceBarrier(1, &b);
    }
    texture make_texture(DXGI_FORMAT format, bool output) {
        texture t;
        D3D12_HEAP_PROPERTIES heap{}; heap.Type = D3D12_HEAP_TYPE_DEFAULT;
        heap.CreationNodeMask = heap.VisibleNodeMask = 1;
        D3D12_RESOURCE_DESC desc{}; desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        desc.Width = width_; desc.Height = height_; desc.DepthOrArraySize = desc.MipLevels = 1;
        desc.Format = format; desc.SampleDesc.Count = 1;
        desc.Flags = output ? D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS : D3D12_RESOURCE_FLAG_NONE;
        void* raw = nullptr;
        const auto image_result = device_->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc,
            output ? D3D12_RESOURCE_STATE_UNORDERED_ACCESS : D3D12_RESOURCE_STATE_COPY_DEST,
            nullptr, IID_ID3D12Resource, &raw);
        t.image.reset(static_cast<ID3D12Resource*>(raw));
        check(image_result, "Create texture");
        if (!t.image) throw std::runtime_error("Create texture returned no resource");
        UINT rows = 0; UINT64 row_bytes = 0;
        device_->GetCopyableFootprints(&desc, 0, 1, 0, &t.footprint, &rows, &row_bytes, &t.bytes);
        const UINT64 expected_row = static_cast<UINT64>(width_) * (format == DXGI_FORMAT_R16G16_FLOAT ? 4 : 8);
        if (rows != height_ || row_bytes != expected_row || t.footprint.Footprint.RowPitch < row_bytes ||
            t.bytes == 0 || t.bytes > 128ULL * 1024 * 1024 || t.footprint.Offset > t.bytes ||
            t.footprint.Footprint.Width != width_ || t.footprint.Footprint.Height != height_ ||
            t.footprint.Footprint.Depth != 1 || t.footprint.Footprint.Format != format ||
            static_cast<UINT64>(height_ - 1) * t.footprint.Footprint.RowPitch + row_bytes > t.bytes - t.footprint.Offset)
            throw std::runtime_error("Invalid texture footprint");
        heap.Type = output ? D3D12_HEAP_TYPE_READBACK : D3D12_HEAP_TYPE_UPLOAD;
        desc = {}; desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; desc.Width = t.bytes;
        desc.Height = desc.DepthOrArraySize = desc.MipLevels = 1; desc.SampleDesc.Count = 1;
        desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR; raw = nullptr;
        const auto transfer_result = device_->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc,
            output ? D3D12_RESOURCE_STATE_COPY_DEST : D3D12_RESOURCE_STATE_GENERIC_READ,
            nullptr, IID_ID3D12Resource, &raw);
        t.transfer.reset(static_cast<ID3D12Resource*>(raw));
        check(transfer_result, "Create transfer");
        if (!t.transfer) throw std::runtime_error("Create transfer returned no resource");
        return t;
    }
    void upload(texture& t, bool color) {
        void* mapped = nullptr; D3D12_RANGE no_read{0, 0};
        check(t.transfer->Map(0, &no_read, &mapped), "Map upload");
        // No allocation/exception while mapped. 0x3800 = FP16 0.5; 0x3c00 = 1.
        auto* bytes = static_cast<unsigned char*>(mapped);
        std::memset(bytes, 0, static_cast<std::size_t>(t.bytes));
        if (color) for (unsigned int y = 0; y < height_; ++y) for (unsigned int x = 0; x < width_; ++x) {
            const std::array<std::uint16_t, 4> pixel{0x3800, 0x3800, 0x3800, 0x3c00};
            const auto offset = t.footprint.Offset + static_cast<UINT64>(y) * t.footprint.Footprint.RowPitch + x * 8ULL;
            std::memcpy(bytes + offset, pixel.data(), sizeof(pixel));
        }
        D3D12_RANGE written{0, static_cast<SIZE_T>(t.bytes)}; t.transfer->Unmap(0, &written);
    }
    void upload_plane(texture& t, std::span<const unsigned char> plane, std::size_t row_bytes) {
        if (plane.size() != row_bytes * height_ || row_bytes > t.footprint.Footprint.RowPitch)
            throw std::runtime_error("Invalid input plane dimensions");
        void* mapped = nullptr; D3D12_RANGE no_read{0, 0};
        check(t.transfer->Map(0, &no_read, &mapped), "Map frame upload");
        auto* bytes = static_cast<unsigned char*>(mapped);
        for (unsigned int y = 0; y < height_; ++y)
            std::memcpy(bytes + t.footprint.Offset + static_cast<UINT64>(y) * t.footprint.Footprint.RowPitch,
                        plane.data() + y * row_bytes, row_bytes);
        D3D12_RANGE written{0, static_cast<SIZE_T>(t.bytes)}; t.transfer->Unmap(0, &written);
    }
    void copy(texture& t, bool to_gpu) {
        D3D12_TEXTURE_COPY_LOCATION gpu{}; gpu.pResource = t.image.get();
        gpu.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        D3D12_TEXTURE_COPY_LOCATION cpu{}; cpu.pResource = t.transfer.get();
        cpu.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT; cpu.PlacedFootprint = t.footprint;
        if (to_gpu) commands_->CopyTextureRegion(&gpu, 0, 0, 0, &cpu, nullptr);
        else commands_->CopyTextureRegion(&cpu, 0, 0, 0, &gpu, nullptr);
    }
    void set_u(const char* name, unsigned int value) { ComfyNR_SetU32(parameters_.get(), name, value); }
    void set_f(const char* name, float value) { ComfyNR_SetF32(parameters_.get(), name, value); }
    void set_resource(const char* name, ID3D12Resource* resource) { ComfyNR_SetResource12(parameters_.get(), name, resource); }
    const NVSDK_NGX_Parameter* parameter_interface() {
        if (ComfyNR_ParametersStatus(parameters_.get()) != static_cast<uint32_t>(NVSDK_NGX_Result_Success))
            throw std::runtime_error("Parameter storage rejected a value");
        return static_cast<const NVSDK_NGX_Parameter*>(ComfyNR_ParameterInterface(parameters_.get()));
    }
public:
    implementation(unsigned int width, unsigned int height, probe_report& report)
        : report_(report), width_(width), height_(height) {
        if (width < 64 || width > 1920 || height < 64 || height > 1080)
            throw std::runtime_error("NR engine dimensions outside bounds");
    }
    implementation(const implementation&) = delete;
    implementation& operator=(const implementation&) = delete;
    ~implementation() {
        // All submitted work was fenced, or the process already terminated.
        if (feature_) {
            report_.event("release", "begin");
            const auto result = call_release_(release_, feature_);
            report_.event("release", "returned", static_cast<unsigned long>(result));
            if (result != NVSDK_NGX_Result_Success) {
                if (report_.console_enabled()) std::fprintf(stderr, "ReleaseFeature failed: 0x%08x\n", static_cast<unsigned int>(result));
                std::_Exit(71);
            }
            feature_ = nullptr;
        }
        if (initialized_) {
            report_.event("shutdown", "begin");
            const auto result = call_shutdown_(shutdown_);
            report_.event("shutdown", "returned", static_cast<unsigned long>(result));
            if (result != NVSDK_NGX_Result_Success) {
                if (report_.console_enabled()) std::fprintf(stderr, "Shutdown failed: 0x%08x\n", static_cast<unsigned int>(result));
                std::_Exit(72);
            }
        }
    }
    void initialize_device() {
        if (device_) throw std::runtime_error("NR device already initialized");
        report_.event("device", "begin");
        dxgi_.reset(LoadLibraryExW(L"dxgi.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32));
        d3d_.reset(LoadLibraryExW(L"d3d12.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32));
        auto factory_fn = symbol<decltype(&CreateDXGIFactory1)>(dxgi_, "CreateDXGIFactory1");
        auto create_device = symbol<PFN_D3D12_CREATE_DEVICE>(d3d_, "D3D12CreateDevice");
        void* raw = nullptr;
        const auto factory_result = factory_fn(IID_IDXGIFactory1, &raw);
        factory_.reset(static_cast<IDXGIFactory1*>(raw));
        check(factory_result, "CreateDXGIFactory1");
        if (!factory_) throw std::runtime_error("CreateDXGIFactory1 returned no factory");
        for (UINT n = 0; ; ++n) {
            IDXGIAdapter1* adapter_raw = nullptr;
            const auto hr = factory_->EnumAdapters1(n, &adapter_raw);
            if (hr == DXGI_ERROR_NOT_FOUND) break;
            com_ptr<IDXGIAdapter1> adapter{adapter_raw};
            check(hr, "EnumAdapters1");
            if (!adapter) throw std::runtime_error("EnumAdapters1 returned no adapter");
            DXGI_ADAPTER_DESC1 desc{};
            check(adapter->GetDesc1(&desc), "GetDesc1");
            report_.event("adapter_vendor", "observed", desc.VendorId);
            if (desc.VendorId != 0x10de || (desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)) continue;
            raw = nullptr;
            const auto result = create_device(adapter.get(), D3D_FEATURE_LEVEL_12_0, IID_ID3D12Device, &raw);
            com_ptr<ID3D12Device> candidate{static_cast<ID3D12Device*>(raw)};
            report_.event("device_create", "returned", static_cast<unsigned long>(result));
            if (SUCCEEDED(result) && candidate) { device_ = std::move(candidate); break; }
        }
        if (!device_) throw std::runtime_error("No NVIDIA D3D12 hardware adapter");
        report_.event("device", "done");
    }
    void initialize(const std::filesystem::path& model, const std::filesystem::path& caller,
                    const std::filesystem::path& logs, bool init_only = false,
                    const owned_wire::settings* look = nullptr) {
        initialize_device();
        void* raw = nullptr;
        D3D12_COMMAND_QUEUE_DESC desc{}; desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
        raw = nullptr; check(device_->CreateCommandQueue(&desc, IID_ID3D12CommandQueue, &raw), "Create queue");
        queue_.reset(static_cast<ID3D12CommandQueue*>(raw));
        raw = nullptr; check(device_->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_ID3D12CommandAllocator, &raw), "Create allocator");
        allocator_.reset(static_cast<ID3D12CommandAllocator*>(raw));
        raw = nullptr; check(device_->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator_.get(), nullptr,
            IID_ID3D12GraphicsCommandList, &raw), "Create command list");
        commands_.reset(static_cast<ID3D12GraphicsCommandList*>(raw));
        raw = nullptr; check(device_->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_ID3D12Fence, &raw), "Create fence");
        fence_.reset(static_cast<ID3D12Fence*>(raw));
        event_.reset(CreateEventW(nullptr, FALSE, FALSE, nullptr));
        if (!event_) throw std::runtime_error("CreateEvent failed");

        report_.event("model_load", "begin");
        model_ = load_local(model); caller_ = load_local(caller);
        report_.event("model_load", "done");
        const auto version = symbol<decltype(&ComfyNR_CallerAbiVersion)>(caller_, "ComfyNR_CallerAbiVersion");
        if (version() != comfy_dlss::caller::abi_version) throw std::runtime_error("Caller ABI mismatch");
        const auto call_init = symbol<decltype(&ComfyNR_CallInitParameters)>(caller_, "ComfyNR_CallInitParameters");
        const auto call_create = symbol<decltype(&ComfyNR_CallCreate)>(caller_, "ComfyNR_CallCreate");
        call_evaluate_ = symbol<decltype(&ComfyNR_CallEvaluate)>(caller_, "ComfyNR_CallEvaluate");
        call_release_ = symbol<decltype(&ComfyNR_CallRelease)>(caller_, "ComfyNR_CallRelease");
        call_shutdown_ = symbol<decltype(&ComfyNR_CallShutdown)>(caller_, "ComfyNR_CallShutdown");
        const auto init = symbol<comfy_dlss::caller::init_parameters_fn>(model_, "NVSDK_NGX_D3D12_Init_Ext");
        const auto create = symbol<comfy_dlss::caller::create_fn>(model_, "NVSDK_NGX_D3D12_CreateFeature");
        evaluate_ = symbol<comfy_dlss::caller::evaluate_fn>(model_, "NVSDK_NGX_D3D12_EvaluateFeature");
        release_ = symbol<comfy_dlss::caller::release_fn>(model_, "NVSDK_NGX_D3D12_ReleaseFeature");
        shutdown_ = symbol<comfy_dlss::caller::shutdown_fn>(model_, "NVSDK_NGX_D3D12_Shutdown");
        // This probe is explicitly for the SDK snippet InitParameters ABI only.
        report_.event("init", "begin");
        const auto init_result = call_init(init, 0, logs.c_str(), device_.get(), NVSDK_NGX_Version_API, nullptr);
        report_.event("init", "returned", static_cast<unsigned long>(init_result));
        ngx_check(init_result, "InitParameters");
        initialized_ = true;
        if (init_only) return;
        report_.event("resources", "begin");
        parameters_.reset(ComfyNR_ParametersCreate());
        if (!parameters_) throw std::runtime_error("Parameter allocation failed");
        color_ = make_texture(DXGI_FORMAT_R16G16B16A16_FLOAT, false);
        motion_ = make_texture(DXGI_FORMAT_R16G16_FLOAT, false);
        output_ = make_texture(DXGI_FORMAT_R16G16B16A16_FLOAT, true);
        upload(color_, true); upload(motion_, false);
        set_u("DLSSNR.Width", width_); set_u("DLSSNR.Height", height_);
        set_u("DLSSNR.InputWidth", width_); set_u("DLSSNR.InputHeight", height_);
        set_u("DLSSNR.OutputWidth", width_); set_u("DLSSNR.OutputHeight", height_);
        set_u("DLSSNR.Hint.Render.Preset", 0); set_u("DLSSNR.Style", 0);
        set_u("DLSSNR.Enabled", 1); set_u("DLSSNR.Reset", 1); set_u("DLSSNR.UseAutoMask", 1);
        set_u("DLSSNR.UICorrection", 0); set_u("DLSSNR.DepthInverted", 1);
        set_f("DLSSNR.Intensity", 1); set_f("DLSSNR.LocalToneStrength", 1);
        set_f("DLSSNR.LocalStructureStrength", 1); set_f("DLSSNR.SkinStructureStrength", -1);
        set_f("DLSSNR.ScalingRatio", 1); set_f("DLSSNR.MVecScaleX", 1); set_f("DLSSNR.MVecScaleY", 1);
        set_resource("DLSSNR.Color", color_.image.get()); set_resource("DLSSNR.MVec", motion_.image.get());
        set_resource("DLSSNR.Output", output_.image.get()); set_resource("DLSSNR.Backbuffer", output_.image.get());
        set_nr_full_frame_subrects(parameters_.get(), width_, height_);
        if (look) {
            set_u("DLSSNR.Hint.Render.Preset", look->preset); set_u("DLSSNR.Style", look->style);
            set_u("DLSSNR.UseAutoMask", look->auto_mask); set_u("DLSSNR.UICorrection", look->ui);
            set_f("DLSSNR.Intensity", look->intensity); set_f("DLSSNR.LocalToneStrength", look->tone);
            set_f("DLSSNR.LocalStructureStrength", look->structure); set_f("DLSSNR.SkinStructureStrength", look->skin);
        }
        set_u("CreationNodeMask", 1); set_u("VisibilityNodeMask", 1);
        copy(color_, true); copy(motion_, true);
        barrier(color_.image.get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        barrier(motion_.image.get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        report_.event("resources", "done");
        report_.event("create", "begin");
        const auto create_result = call_create(create, commands_.get(), static_cast<NVSDK_NGX_Feature>(18), parameter_interface(), &feature_);
        report_.event("create", "returned", static_cast<unsigned long>(create_result));
        ngx_check(create_result, "CreateFeature18");
        if (!feature_) throw std::runtime_error("CreateFeature succeeded without a handle");
        submit_wait("create_fence");
    }
    unsigned long long evaluate(unsigned int count) {
        if (!feature_ || !initialized_ || !count || count > 240) throw std::runtime_error("Invalid NR evaluation state/count");
        for (unsigned int frame = 0; frame < count; ++frame) {
            begin(); set_u("DLSSNR.Reset", frame == 0 ? 1U : 0U);
            ComfyNR_SetU64(parameters_.get(), "DLSSNR.FrameIndex", frame);
            report_.event("evaluate", "begin", frame);
            const auto evaluate_result = call_evaluate_(evaluate_, commands_.get(), feature_, parameter_interface(), nullptr);
            report_.event("evaluate", "returned", static_cast<unsigned long>(evaluate_result));
            ngx_check(evaluate_result, "EvaluateFeature18");
            D3D12_RESOURCE_BARRIER b{}; b.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV; b.UAV.pResource = output_.image.get();
            commands_->ResourceBarrier(1, &b);
            if (frame + 1 == count) {
                barrier(output_.image.get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_COPY_SOURCE);
                copy(output_, false);
                barrier(output_.image.get(), D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
            }
            submit_wait("evaluate_fence");
        }
        void* mapped = nullptr; D3D12_RANGE read{0, static_cast<SIZE_T>(output_.bytes)};
        check(output_.transfer->Map(0, &read, &mapped), "Map readback");
        const auto* bytes = static_cast<const unsigned char*>(mapped);
        unsigned long long checksum = 14695981039346656037ULL;
        unsigned int nonfinite = 0;
        for (unsigned int y = 0; y < height_; ++y) for (unsigned int x = 0; x < width_ * 8; ++x) {
            const auto offset = output_.footprint.Offset + static_cast<UINT64>(y) * output_.footprint.Footprint.RowPitch + x;
            checksum = (checksum ^ bytes[offset]) * 1099511628211ULL;
            if (x % 2 == 0) {
                std::uint16_t half = 0; std::memcpy(&half, bytes + offset, sizeof(half));
                if ((half & 0x7c00) == 0x7c00) ++nonfinite;
            }
        }
        D3D12_RANGE no_write{0, 0}; output_.transfer->Unmap(0, &no_write);
        if (nonfinite) throw std::runtime_error("NR output contains non-finite FP16 values");
        return checksum;
    }
    void evaluate_frame(std::span<const unsigned char> color, std::span<const unsigned char> motion,
                        unsigned int frame, bool reset, std::span<unsigned char> result) {
        if (!feature_ || !initialized_) throw std::runtime_error("NR session is not initialized");
        const auto row_bytes = static_cast<std::size_t>(width_) * 8;
        if (result.size() != row_bytes * height_ || !sequence_probe::finite_half_plane(color) ||
            !sequence_probe::finite_half_plane(motion)) throw std::runtime_error("Invalid frame planes");
        // The previous create/evaluate fence completed before mapped uploads reuse memory.
        upload_plane(color_, color, row_bytes); upload_plane(motion_, motion, static_cast<std::size_t>(width_) * 4);
        begin();
        barrier(color_.image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_COPY_DEST);
        barrier(motion_.image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_COPY_DEST);
        copy(color_, true); copy(motion_, true);
        barrier(color_.image.get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        barrier(motion_.image.get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        set_u("DLSSNR.Reset", reset ? 1U : 0U);
        ComfyNR_SetU64(parameters_.get(), "DLSSNR.FrameIndex", frame);
        report_.event("evaluate", "begin", frame);
        const auto status = call_evaluate_(evaluate_, commands_.get(), feature_, parameter_interface(), nullptr);
        report_.event("evaluate", "returned", static_cast<unsigned long>(status));
        ngx_check(status, "Evaluate sequence frame");
        D3D12_RESOURCE_BARRIER b{}; b.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV; b.UAV.pResource = output_.image.get();
        commands_->ResourceBarrier(1, &b);
        barrier(output_.image.get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_COPY_SOURCE);
        copy(output_, false);
        // Restore for the next frame; no cumulative staging files/textures.
        barrier(output_.image.get(), D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
        submit_wait("evaluate_fence");
        void* mapped = nullptr; D3D12_RANGE read{0, static_cast<SIZE_T>(output_.bytes)};
        check(output_.transfer->Map(0, &read, &mapped), "Map sequence output");
        const auto* bytes = static_cast<const unsigned char*>(mapped);
        for (unsigned int y = 0; y < height_; ++y)
            std::memcpy(result.data() + y * row_bytes,
                        bytes + output_.footprint.Offset + static_cast<UINT64>(y) * output_.footprint.Footprint.RowPitch, row_bytes);
        D3D12_RANGE no_write{0, 0}; output_.transfer->Unmap(0, &no_write);
        if (!sequence_probe::finite_half_plane(result)) throw std::runtime_error("Non-finite sequence output");
        report_.event("frame", "done", frame);
    }
};


nr_engine::nr_engine(unsigned int width, unsigned int height, probe_report& report)
    : implementation_(std::make_unique<implementation>(width,height,report)) {}
nr_engine::~nr_engine() = default;
void nr_engine::initialize_device() { implementation_->initialize_device(); }
void nr_engine::initialize(const std::filesystem::path& model, const std::filesystem::path& caller,
                           const std::filesystem::path& logs, bool init_only, const owned_wire::settings* look) {
    implementation_->initialize(model,caller,logs,init_only,look);
}
unsigned long long nr_engine::evaluate(unsigned int count) { return implementation_->evaluate(count); }
void nr_engine::evaluate_frame(std::span<const unsigned char> color,std::span<const unsigned char> motion,
                               unsigned int frame,bool reset,std::span<unsigned char> result) {
    implementation_->evaluate_frame(color,motion,frame,reset,result);
}
}
