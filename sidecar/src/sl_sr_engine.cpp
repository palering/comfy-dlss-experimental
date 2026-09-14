// The vendor API stays entirely inside this translation unit. No NGX archive,
// NR shim, Comfy type, decoding, camera estimation or application protocol here.
#include "probe_report.hpp"
#include "sl_sr_engine.hpp"
#include "win32_raii.hpp"
#include <d3d12.h>
#include <dxgi1_4.h>
#include <sl.h>
#include <sl_dlss.h>
#include <sl_dlss_d.h>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>

namespace comfy_dlss {
namespace {
std::string error_text(const char* action, unsigned long code) {
    char number[16]{};
    std::snprintf(number, sizeof(number), "0x%08lx", code);
    return std::string(action) + ": " + number;
}
void check(HRESULT result, const char* action) {
    if (FAILED(result)) throw std::runtime_error(error_text(action, static_cast<unsigned long>(result)));
}
void sl_check(sl::Result result, const char* action) {
    if (result != sl::Result::eOk) throw std::runtime_error(error_text(action, static_cast<unsigned long>(result)));
}
template<class T> T symbol(const unique_module& module, const char* name) {
    const auto address = module.symbol(name);
    if (!address) throw std::runtime_error(std::string("Missing SL SR export: ") + name);
    return reinterpret_cast<T>(address);
}
struct texture {
    com_ptr<ID3D12Resource> image, transfer;
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};
    UINT64 bytes = 0;
    unsigned int width = 0, height = 0, pixel_bytes = 0;
};
sl::Boolean boolean(bool value) { return value ? sl::Boolean::eTrue : sl::Boolean::eFalse; }
sl::float3 vector(const std::array<float, 3>& value) { return {value[0], value[1], value[2]}; }
void matrix(sl::float4x4& output, const std::array<float, 16>& input) {
    static_assert(sizeof(output) == sizeof(input));
    std::memcpy(&output, input.data(), sizeof(output));
}
}

class sl_sr_engine::implementation final {
    sr::settings settings_;
    probe_report& report_;
    sl::Feature feature_;
    unique_module interposer_, dxgi_, d3d_;
    PFun_slInit* init_ = nullptr;
    PFun_slShutdown* shutdown_ = nullptr;
    PFun_slIsFeatureSupported* supported_ = nullptr;
    PFun_slSetD3DDevice* set_device_ = nullptr;
    PFun_slUpgradeInterface* upgrade_ = nullptr;
    PFun_slGetNativeInterface* native_ = nullptr;
    PFun_slGetFeatureFunction* feature_function_ = nullptr;
    PFun_slGetNewFrameToken* frame_token_ = nullptr;
    PFun_slSetConstants* constants_ = nullptr;
    PFun_slSetTagForFrame* tag_ = nullptr;
    PFun_slEvaluateFeature* evaluate_ = nullptr;
    PFun_slFreeResources* free_ = nullptr;
    PFun_slDLSSSetOptions* options_ = nullptr;
    PFun_slDLSSGetOptimalSettings* optimal_ = nullptr;
    PFun_slDLSSDSetOptions* rr_options_ = nullptr;
    PFun_slDLSSDGetOptimalSettings* rr_optimal_ = nullptr;
    com_ptr<IDXGIFactory1> factory_, proxy_factory_;
    com_ptr<IDXGIFactory2> proxy_factory2_;
    com_ptr<ID3D12Device> device_, proxy_device_;
    com_ptr<ID3D12CommandQueue> queue_, native_queue_;
    com_ptr<ID3D12CommandAllocator> allocator_;
    com_ptr<ID3D12GraphicsCommandList> commands_;
    com_ptr<ID3D12Fence> fence_;
    unique_handle event_;
    unique_window window_;
    com_ptr<IDXGISwapChain3> swapchain_;
    com_ptr<ID3D12DescriptorHeap> rtv_heap_, uav_cpu_, uav_gpu_;
    std::array<com_ptr<ID3D12Resource>, 2> backbuffers_;
    UINT rtv_stride_ = 0;
    texture color_, motion_, depth_, output_;
    texture diffuse_, specular_, normals_, specular_motion_, exposure_;
    sl::ViewportHandle viewport_{1};
    sl::DLSSOptions options_value_{};
    sl::DLSSDOptions rr_options_value_{};
    bool attempted_ = false, initialized_ = false, ready_ = false, closed_ = false, failed_ = false;
    bool evaluated_ = false;
    UINT64 fence_value_ = 0;
    std::uint32_t frame_index_ = 0, presents_ = 0;

    [[noreturn]] void inflight_failure(const char* stage, unsigned long code) noexcept {
        report_.event(stage, "failed", code);
        std::_Exit(70); // Do not unwind resource owners with unverified GPU work.
    }
    void wait_idle(const char* stage) {
        if (fence_value_ >= std::numeric_limits<UINT64>::max() - 1) inflight_failure(stage, 1);
        ++fence_value_;
        auto result = native_queue_->Signal(fence_.get(), fence_value_);
        if (FAILED(result)) inflight_failure(stage, static_cast<unsigned long>(result));
        result = fence_->SetEventOnCompletion(fence_value_, event_.get());
        if (FAILED(result)) inflight_failure(stage, static_cast<unsigned long>(result));
        const auto wait = WaitForSingleObject(event_.get(), 30000);
        const auto completed = fence_->GetCompletedValue();
        if (wait != WAIT_OBJECT_0 || completed == std::numeric_limits<UINT64>::max() || completed < fence_value_)
            inflight_failure(stage, wait);
        report_.event(stage, "done");
    }
    void submit_wait() {
        check(commands_->Close(), "SL SR close command list");
        ID3D12CommandList* lists[]{commands_.get()};
        native_queue_->ExecuteCommandLists(1, lists);
        wait_idle("sl_sr_evaluate_fence");
    }
    void begin() {
        check(allocator_->Reset(), "SL SR reset allocator");
        check(commands_->Reset(allocator_.get(), nullptr), "SL SR reset command list");
    }
    void barrier(ID3D12Resource* resource, D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after) {
        D3D12_RESOURCE_BARRIER value{};
        value.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        value.Transition = {resource, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, before, after};
        commands_->ResourceBarrier(1, &value);
    }
    void uav_barrier(ID3D12Resource* resource) {
        D3D12_RESOURCE_BARRIER value{}; value.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV;
        value.UAV.pResource = resource;
        commands_->ResourceBarrier(1, &value);
    }
    texture make_texture(unsigned int width, unsigned int height, DXGI_FORMAT format,
                         unsigned int pixel_bytes, bool output) {
        texture result;
        result.width = width; result.height = height; result.pixel_bytes = pixel_bytes;
        D3D12_HEAP_PROPERTIES heap{}; heap.Type = D3D12_HEAP_TYPE_DEFAULT;
        heap.CreationNodeMask = heap.VisibleNodeMask = 1;
        D3D12_RESOURCE_DESC desc{}; desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        desc.Width = width; desc.Height = height; desc.DepthOrArraySize = desc.MipLevels = 1;
        desc.Format = format; desc.SampleDesc.Count = 1;
        desc.Flags = output ? D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS : D3D12_RESOURCE_FLAG_NONE;
        void* raw = nullptr;
        auto hr = device_->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc,
            output ? D3D12_RESOURCE_STATE_UNORDERED_ACCESS : D3D12_RESOURCE_STATE_COPY_DEST,
            nullptr, IID_ID3D12Resource, &raw);
        result.image.reset(static_cast<ID3D12Resource*>(raw)); check(hr, "SL SR create texture");
        if (!result.image) throw std::runtime_error("SL SR texture missing");
        UINT rows = 0; UINT64 row_bytes = 0;
        device_->GetCopyableFootprints(&desc, 0, 1, 0, &result.footprint, &rows, &row_bytes, &result.bytes);
        const auto& placed = result.footprint;
        if (rows != height || row_bytes != static_cast<UINT64>(width) * pixel_bytes ||
            !result.bytes || result.bytes > 128ULL * 1024 * 1024 || placed.Offset > result.bytes ||
            placed.Footprint.RowPitch < row_bytes || placed.Footprint.Width != width ||
            placed.Footprint.Height != height || placed.Footprint.Depth != 1 || placed.Footprint.Format != format ||
            static_cast<UINT64>(height - 1) * placed.Footprint.RowPitch + row_bytes > result.bytes - placed.Offset)
            throw std::runtime_error("SL SR invalid texture footprint");
        heap.Type = output ? D3D12_HEAP_TYPE_READBACK : D3D12_HEAP_TYPE_UPLOAD;
        desc = {}; desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; desc.Width = result.bytes;
        desc.Height = desc.DepthOrArraySize = desc.MipLevels = 1; desc.SampleDesc.Count = 1;
        desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
        raw = nullptr;
        hr = device_->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc,
            output ? D3D12_RESOURCE_STATE_COPY_DEST : D3D12_RESOURCE_STATE_GENERIC_READ,
            nullptr, IID_ID3D12Resource, &raw);
        result.transfer.reset(static_cast<ID3D12Resource*>(raw)); check(hr, "SL SR create transfer buffer");
        if (!result.transfer) throw std::runtime_error("SL SR transfer buffer missing");
        return result;
    }
    void upload(texture& target, std::span<const unsigned char> plane) {
        const std::size_t row_bytes = static_cast<std::size_t>(target.width) * target.pixel_bytes;
        if (plane.size() != row_bytes * target.height) throw std::invalid_argument("SL SR upload size mismatch");
        void* mapped = nullptr; const D3D12_RANGE no_read{0, 0};
        check(target.transfer->Map(0, &no_read, &mapped), "SL SR map upload");
        if (!mapped) { target.transfer->Unmap(0, &no_read); throw std::runtime_error("SL SR upload memory missing"); }
        for (unsigned int y = 0; y < target.height; ++y)
            std::memcpy(static_cast<unsigned char*>(mapped) + target.footprint.Offset +
                        static_cast<UINT64>(y) * target.footprint.Footprint.RowPitch,
                        plane.data() + static_cast<std::size_t>(y) * row_bytes, row_bytes);
        const D3D12_RANGE written{0, static_cast<SIZE_T>(target.bytes)};
        target.transfer->Unmap(0, &written);
    }
    void copy(texture& value, bool to_gpu) {
        D3D12_TEXTURE_COPY_LOCATION gpu{}, cpu{};
        gpu.pResource = value.image.get(); gpu.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        cpu.pResource = value.transfer.get(); cpu.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        cpu.PlacedFootprint = value.footprint;
        if (to_gpu) commands_->CopyTextureRegion(&gpu, 0, 0, 0, &cpu, nullptr);
        else commands_->CopyTextureRegion(&cpu, 0, 0, 0, &gpu, nullptr);
    }
    template<class T> T* feature_function(const char* name) {
        void* raw = nullptr;
        sl_check(feature_function_(feature_, name, raw), name);
        if (!raw) throw std::runtime_error("SL SR feature function missing");
        return reinterpret_cast<T*>(raw);
    }
    template<class T> com_ptr<T> upgrade(T* value) {
        // slUpgradeInterface creates a new proxy which takes its own native
        // reference. Keep the original owner and adopt only that new proxy.
        void* raw = value;
        const auto result = upgrade_(&raw);
        if (raw == value) {
            sl_check(result, "SL SR upgrade interface");
            throw std::runtime_error("SL SR manual hooks did not provide a proxy");
        }
        com_ptr<T> proxy{static_cast<T*>(raw)};
        sl_check(result, "SL SR upgrade interface");
        if (!proxy) throw std::runtime_error("SL SR upgraded interface missing");
        return proxy;
    }
    void initialize_device() {
        report_.event("sl_sr_device", "begin");
        dxgi_.reset(LoadLibraryExW(L"dxgi.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32));
        d3d_.reset(LoadLibraryExW(L"d3d12.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32));
        const auto factory = symbol<decltype(&CreateDXGIFactory1)>(dxgi_, "CreateDXGIFactory1");
        const auto device = symbol<PFN_D3D12_CREATE_DEVICE>(d3d_, "D3D12CreateDevice");
        void* raw = nullptr; auto hr = factory(IID_IDXGIFactory1, &raw);
        factory_.reset(static_cast<IDXGIFactory1*>(raw)); check(hr, "SL SR DXGI factory");
        if (!factory_) throw std::runtime_error("SL SR factory missing");
        for (UINT index = 0; ; ++index) {
            IDXGIAdapter1* pointer = nullptr;
            hr = factory_->EnumAdapters1(index, &pointer);
            com_ptr<IDXGIAdapter1> adapter{pointer};
            if (hr == DXGI_ERROR_NOT_FOUND) break;
            check(hr, "SL SR enumerate adapters");
            if (!adapter) throw std::runtime_error("SL SR adapter missing");
            DXGI_ADAPTER_DESC1 desc{}; check(adapter->GetDesc1(&desc), "SL SR adapter description");
            if (desc.VendorId != 0x10de || (desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)) continue;
            sl::AdapterInfo info{};
            info.deviceLUID = reinterpret_cast<unsigned char*>(&desc.AdapterLuid);
            info.deviceLUIDSizeInBytes = sizeof(desc.AdapterLuid);
            sl_check(supported_(feature_, info), "SL reconstruction adapter feature support");
            raw = nullptr;
            hr = device(adapter.get(), D3D_FEATURE_LEVEL_12_0, IID_ID3D12Device, &raw);
            com_ptr<ID3D12Device> candidate{static_cast<ID3D12Device*>(raw)};
            if (SUCCEEDED(hr) && candidate) { device_ = std::move(candidate); break; }
        }
        if (!device_) throw std::runtime_error("SL SR requires a supported NVIDIA D3D12 hardware device");
        sl_check(set_device_(device_.get()), "SL SR set native device");
        proxy_device_ = upgrade(device_.get());
        proxy_factory_ = upgrade(factory_.get());
        raw = nullptr; hr = proxy_factory_->QueryInterface(IID_IDXGIFactory2, &raw);
        proxy_factory2_.reset(static_cast<IDXGIFactory2*>(raw)); check(hr, "SL SR factory2 proxy");
        if (!proxy_factory2_) throw std::runtime_error("SL SR factory2 missing");
        D3D12_COMMAND_QUEUE_DESC desc{}; desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
        raw = nullptr; hr = proxy_device_->CreateCommandQueue(&desc, IID_ID3D12CommandQueue, &raw);
        queue_.reset(static_cast<ID3D12CommandQueue*>(raw)); check(hr, "SL SR create hooked queue");
        if (!queue_) throw std::runtime_error("SL SR queue missing");
        raw = nullptr; const auto native_result = native_(queue_.get(), &raw);
        native_queue_.reset(static_cast<ID3D12CommandQueue*>(raw)); sl_check(native_result, "SL SR native queue");
        if (!native_queue_) throw std::runtime_error("SL SR native queue missing");
        raw = nullptr; hr = device_->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_ID3D12CommandAllocator, &raw);
        allocator_.reset(static_cast<ID3D12CommandAllocator*>(raw)); check(hr, "SL SR create allocator");
        if (!allocator_) throw std::runtime_error("SL SR allocator missing");
        raw = nullptr; hr = device_->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator_.get(), nullptr,
            IID_ID3D12GraphicsCommandList, &raw);
        commands_.reset(static_cast<ID3D12GraphicsCommandList*>(raw)); check(hr, "SL SR create command list");
        raw = nullptr; hr = device_->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_ID3D12Fence, &raw);
        fence_.reset(static_cast<ID3D12Fence*>(raw)); check(hr, "SL SR create fence");
        event_.reset(CreateEventW(nullptr, FALSE, FALSE, nullptr));
        if (!commands_ || !fence_ || !event_) throw std::runtime_error("SL SR completion resource missing");
        check(commands_->Close(), "SL SR close initial command list");
        report_.event("sl_sr_device", "done");
    }
    com_ptr<ID3D12DescriptorHeap> heap(D3D12_DESCRIPTOR_HEAP_TYPE type, UINT count, bool visible) {
        D3D12_DESCRIPTOR_HEAP_DESC desc{}; desc.Type = type; desc.NumDescriptors = count;
        desc.Flags = visible ? D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE : D3D12_DESCRIPTOR_HEAP_FLAG_NONE;
        void* raw = nullptr;
        const auto hr = device_->CreateDescriptorHeap(&desc, IID_ID3D12DescriptorHeap, &raw);
        com_ptr<ID3D12DescriptorHeap> result{static_cast<ID3D12DescriptorHeap*>(raw)};
        check(hr, "SL SR descriptor heap");
        if (!result) throw std::runtime_error("SL SR descriptor heap missing");
        return result;
    }
    void initialize_present() {
        report_.event("sl_sr_swapchain", "begin");
        window_.reset(CreateWindowExW(0, L"STATIC", L"DLSS offline lifecycle", WS_POPUP,
            0, 0, 32, 32, nullptr, nullptr, GetModuleHandleW(nullptr), nullptr));
        if (!window_) throw std::runtime_error("SL SR hidden window creation failed");
        // Never ShowWindow: the swapchain exists only for mandatory SL frame
        // bookkeeping. Processed pixels are read from the offscreen texture.
        DXGI_SWAP_CHAIN_DESC1 desc{}; desc.Width = desc.Height = 32;
        desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM; desc.SampleDesc.Count = 1;
        desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT; desc.BufferCount = 2;
        desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD; desc.Scaling = DXGI_SCALING_STRETCH;
        desc.AlphaMode = DXGI_ALPHA_MODE_IGNORE;
        IDXGISwapChain1* first = nullptr;
        const auto hr = proxy_factory2_->CreateSwapChainForHwnd(queue_.get(), window_.get(), &desc,
                                                              nullptr, nullptr, &first);
        com_ptr<IDXGISwapChain1> initial{first}; check(hr, "SL SR create hooked swapchain");
        if (!initial) throw std::runtime_error("SL SR swapchain missing");
        void* raw = nullptr; const auto query = initial->QueryInterface(IID_IDXGISwapChain3, &raw);
        swapchain_.reset(static_cast<IDXGISwapChain3*>(raw)); check(query, "SL SR swapchain3");
        if (!swapchain_) throw std::runtime_error("SL SR swapchain3 missing");
        rtv_heap_ = heap(D3D12_DESCRIPTOR_HEAP_TYPE_RTV, 2, false);
        rtv_stride_ = device_->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_RTV);
        auto handle = rtv_heap_->GetCPUDescriptorHandleForHeapStart();
        for (UINT i = 0; i < 2; ++i) {
            raw = nullptr; const auto got = swapchain_->GetBuffer(i, IID_ID3D12Resource, &raw);
            backbuffers_[i].reset(static_cast<ID3D12Resource*>(raw)); check(got, "SL SR backbuffer");
            if (!backbuffers_[i]) throw std::runtime_error("SL SR backbuffer missing");
            device_->CreateRenderTargetView(backbuffers_[i].get(), nullptr, handle);
            handle.ptr += rtv_stride_;
        }
        report_.event("sl_sr_swapchain", "done");
    }
    void clear_present_buffer() {
        const UINT index = swapchain_->GetCurrentBackBufferIndex();
        if (index >= backbuffers_.size()) throw std::runtime_error("SL SR invalid backbuffer index");
        auto handle = rtv_heap_->GetCPUDescriptorHandleForHeapStart();
        handle.ptr += static_cast<SIZE_T>(index) * rtv_stride_;
        barrier(backbuffers_[index].get(), D3D12_RESOURCE_STATE_PRESENT, D3D12_RESOURCE_STATE_RENDER_TARGET);
        constexpr float black[]{0, 0, 0, 1};
        commands_->ClearRenderTargetView(handle, black, 0, nullptr);
        barrier(backbuffers_[index].get(), D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_PRESENT);
    }
    void present() {
        report_.event("sl_sr_present", "begin", presents_);
        const auto result = swapchain_->Present(0, 0); // Not DXGI_PRESENT_TEST: GC must execute.
        wait_idle("sl_sr_present_fence");
        check(result, "SL SR hooked Present");
        report_.event("sl_sr_present", "done", static_cast<unsigned long>(result));
        ++presents_;
    }
public:
    implementation(const sr::settings& settings, probe_report& report, sl_reconstruction_feature feature)
        : settings_(settings), report_(report), feature_(feature == sl_reconstruction_feature::ray_reconstruction
            ? sl::kFeatureDLSS_RR : sl::kFeatureDLSS) {
        sr::validate(settings_);
        if (feature != sl_reconstruction_feature::ray_reconstruction && feature != sl_reconstruction_feature::super_resolution)
            throw std::invalid_argument("Unknown SL reconstruction feature");
        if (feature_ == sl::kFeatureDLSS && (settings_.hdr || !settings_.auto_exposure || settings_.motion_jittered))
            throw std::invalid_argument("SL SR diagnostic currently requires SDR auto-exposure and unjittered motion");
        if (feature_ == sl::kFeatureDLSS_RR && (settings_.preset != 0 || settings_.auto_exposure || settings_.motion_jittered))
            throw std::invalid_argument("SL RR requires default RR preset, explicit exposure and unjittered motion");
    }
    ~implementation() noexcept {
        try { close(); } catch (...) { report_.event("sl_sr_cleanup", "failed"); std::_Exit(71); }
    }
    void initialize(const std::filesystem::path& runtime, const std::filesystem::path& logs,
                    const std::string& project_id) {
        if (attempted_ || closed_) throw std::runtime_error("SL SR initialization is single-use");
        if (!runtime.is_absolute() || !logs.is_absolute() || !std::filesystem::is_directory(logs) ||
            !sr::valid_project_id(project_id) || project_id == "00000000-0000-0000-0000-000000000000")
            throw std::invalid_argument("SL SR needs absolute runtime/log directories and a nonzero project UUID");
        for (const auto name : {L"sl.interposer.dll", L"sl.common.dll",
                feature_ == sl::kFeatureDLSS_RR ? L"sl.dlss_d.dll" : L"sl.dlss.dll",
                feature_ == sl::kFeatureDLSS_RR ? L"nvngx_dlssd.dll" : L"nvngx_dlss.dll"})
            if (!std::filesystem::is_regular_file(runtime / name)) throw std::invalid_argument("SL SR runtime component missing");
        attempted_ = true;
        interposer_.reset(LoadLibraryExW((runtime / "sl.interposer.dll").c_str(), nullptr,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS));
        init_ = symbol<PFun_slInit*>(interposer_, "slInit");
        shutdown_ = symbol<PFun_slShutdown*>(interposer_, "slShutdown");
        supported_ = symbol<PFun_slIsFeatureSupported*>(interposer_, "slIsFeatureSupported");
        set_device_ = symbol<PFun_slSetD3DDevice*>(interposer_, "slSetD3DDevice");
        upgrade_ = symbol<PFun_slUpgradeInterface*>(interposer_, "slUpgradeInterface");
        native_ = symbol<PFun_slGetNativeInterface*>(interposer_, "slGetNativeInterface");
        feature_function_ = symbol<PFun_slGetFeatureFunction*>(interposer_, "slGetFeatureFunction");
        frame_token_ = symbol<PFun_slGetNewFrameToken*>(interposer_, "slGetNewFrameToken");
        constants_ = symbol<PFun_slSetConstants*>(interposer_, "slSetConstants");
        tag_ = symbol<PFun_slSetTagForFrame*>(interposer_, "slSetTagForFrame");
        evaluate_ = symbol<PFun_slEvaluateFeature*>(interposer_, "slEvaluateFeature");
        free_ = symbol<PFun_slFreeResources*>(interposer_, "slFreeResources");
        const auto runtime_path = runtime.wstring(); const wchar_t* paths[]{runtime_path.c_str()};
        sl::Preferences prefs{};
        prefs.pathsToPlugins = paths; prefs.numPathsToPlugins = 1; prefs.pathToLogsAndData = logs.c_str();
        prefs.logLevel = sl::LogLevel::eVerbose;
        prefs.flags = sl::PreferenceFlags::eUseManualHooking | sl::PreferenceFlags::eDisableCLStateTracking |
            sl::PreferenceFlags::eUseFrameBasedResourceTagging | sl::PreferenceFlags::eUseDXGIFactoryProxy;
        prefs.featuresToLoad = &feature_; prefs.numFeaturesToLoad = 1;
        prefs.engine = sl::EngineType::eCustom; prefs.engineVersion = "comfy-sl-sr-diagnostic-1";
        prefs.projectId = project_id.c_str(); prefs.renderAPI = sl::RenderAPI::eD3D12;
        report_.event("sl_sr_init", "begin");
        sl_check(init_(prefs, sl::kSDKVersion), "SL SR initialization"); initialized_ = true;
        report_.event("sl_sr_init", "done");
        initialize_device();
        if (feature_ == sl::kFeatureDLSS_RR) {
            rr_options_ = feature_function<PFun_slDLSSDSetOptions>("slDLSSDSetOptions");
            rr_optimal_ = feature_function<PFun_slDLSSDGetOptimalSettings>("slDLSSDGetOptimalSettings");
        } else {
            options_ = feature_function<PFun_slDLSSSetOptions>("slDLSSSetOptions");
            optimal_ = feature_function<PFun_slDLSSGetOptimalSettings>("slDLSSGetOptimalSettings");
        }
        // Enum mapping is explicit: SL has Off at zero, NGX/our SR wire does not.
        switch (settings_.mode) {
            case sr::quality::performance: options_value_.mode = sl::DLSSMode::eMaxPerformance; break;
            case sr::quality::balanced: options_value_.mode = sl::DLSSMode::eBalanced; break;
            case sr::quality::quality: options_value_.mode = sl::DLSSMode::eMaxQuality; break;
            case sr::quality::ultra_performance: options_value_.mode = sl::DLSSMode::eUltraPerformance; break;
            case sr::quality::ultra_quality: options_value_.mode = sl::DLSSMode::eUltraQuality; break;
            case sr::quality::dlaa: options_value_.mode = sl::DLSSMode::eDLAA; break;
        }
        options_value_.outputWidth = settings_.output_width; options_value_.outputHeight = settings_.output_height;
        options_value_.colorBuffersHDR = sl::Boolean::eFalse; options_value_.useAutoExposure = sl::Boolean::eTrue;
        options_value_.alphaUpscalingEnabled = sl::Boolean::eFalse;
        const auto preset = static_cast<sl::DLSSPreset>(settings_.preset);
        options_value_.dlaaPreset = options_value_.qualityPreset = options_value_.balancedPreset = preset;
        options_value_.performancePreset = options_value_.ultraPerformancePreset = options_value_.ultraQualityPreset = preset;
        if (feature_ == sl::kFeatureDLSS_RR) {
            rr_options_value_.mode = options_value_.mode;
            rr_options_value_.outputWidth = settings_.output_width; rr_options_value_.outputHeight = settings_.output_height;
            rr_options_value_.colorBuffersHDR = boolean(settings_.hdr);
            rr_options_value_.normalRoughnessMode = sl::DLSSDNormalRoughnessMode::ePacked;
            rr_options_value_.alphaUpscalingEnabled = sl::Boolean::eFalse;
            sl::DLSSDOptimalSettings optimal{};
            sl_check(rr_optimal_(rr_options_value_, optimal), "SL RR optimal settings");
            sr::validate_runtime_range(settings_, optimal.optimalRenderWidth, optimal.optimalRenderHeight,
                optimal.renderWidthMin, optimal.renderHeightMin, optimal.renderWidthMax, optimal.renderHeightMax);
            // World/view transforms and exposure are supplied by each frame.
        } else {
            sl::DLSSOptimalSettings optimal{}; sl_check(optimal_(options_value_, optimal), "SL SR optimal settings");
            sr::validate_runtime_range(settings_, optimal.optimalRenderWidth, optimal.optimalRenderHeight,
                optimal.renderWidthMin, optimal.renderHeightMin, optimal.renderWidthMax, optimal.renderHeightMax);
            sl_check(options_(viewport_, options_value_), "SL SR initial options");
        }
        initialize_present();
        color_ = make_texture(settings_.input_width, settings_.input_height, DXGI_FORMAT_R16G16B16A16_FLOAT, 8, false);
        motion_ = make_texture(settings_.input_width, settings_.input_height, DXGI_FORMAT_R16G16_FLOAT, 4, false);
        depth_ = make_texture(settings_.input_width, settings_.input_height, DXGI_FORMAT_R32_FLOAT, 4, false);
        output_ = make_texture(settings_.output_width, settings_.output_height, DXGI_FORMAT_R16G16B16A16_FLOAT, 8, true);
        if (feature_ == sl::kFeatureDLSS_RR) {
            diffuse_ = make_texture(settings_.input_width, settings_.input_height, DXGI_FORMAT_R16G16B16A16_FLOAT, 8, false);
            specular_ = make_texture(settings_.input_width, settings_.input_height, DXGI_FORMAT_R16G16B16A16_FLOAT, 8, false);
            normals_ = make_texture(settings_.input_width, settings_.input_height, DXGI_FORMAT_R16G16B16A16_FLOAT, 8, false);
            specular_motion_ = make_texture(settings_.input_width, settings_.input_height, DXGI_FORMAT_R16G16_FLOAT, 4, false);
            exposure_ = make_texture(1, 1, DXGI_FORMAT_R32_FLOAT, 4, false);
        }
        // SL may leave alpha unchanged. Initialize every output pixel to finite
        // RGBA before inference rather than reading undefined alpha as data.
        uav_cpu_ = heap(D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV, 1, false);
        uav_gpu_ = heap(D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV, 1, true);
        D3D12_UNORDERED_ACCESS_VIEW_DESC view{}; view.Format = DXGI_FORMAT_R16G16B16A16_FLOAT;
        view.ViewDimension = D3D12_UAV_DIMENSION_TEXTURE2D;
        device_->CreateUnorderedAccessView(output_.image.get(), nullptr, &view, uav_cpu_->GetCPUDescriptorHandleForHeapStart());
        device_->CreateUnorderedAccessView(output_.image.get(), nullptr, &view, uav_gpu_->GetCPUDescriptorHandleForHeapStart());
        ready_ = true; report_.event("sl_sr_ready", "done");
    }
    void present_only() {
        if (!ready_ || closed_ || failed_ || frame_index_ || presents_) throw std::runtime_error("SL SR lifecycle probe is single-use");
        failed_ = true;
        begin(); clear_present_buffer(); submit_wait(); present();
        failed_ = false;
    }
    void evaluate_frame(std::span<const unsigned char> color, std::span<const unsigned char> motion,
                        std::span<const unsigned char> depth, const sl_sr::frame_info& frame,
                        const sl_sr::camera_frame& camera, std::span<unsigned char> output,
                        const sl_rr::guides* rr = nullptr) {
        if (!ready_ || closed_ || failed_ || frame_index_ >= 1'000'000 || presents_ != frame_index_)
            throw std::runtime_error("SL SR evaluation needs a healthy, sequential feature");
        sl_sr::validate(frame); sl_sr::validate(camera, settings_);
        sr::validate_planes(settings_, color, motion, depth, output);
        if ((feature_ == sl::kFeatureDLSS_RR) != (rr != nullptr))
            throw std::invalid_argument("SL reconstruction feature and RR guide input disagree");
        if (rr) sl_rr::validate(*rr, settings_);
        failed_ = true;
        if (rr && frame.reset && evaluated_) {
            // A scene boundary must not reuse ray-reconstruction history. RR
            // reset-bit output was not byte-identical to a fresh segment on
            // the tested runtime. Recreate the feature at explicit cuts; all
            // previous Evaluate/Present work has already been fenced here.
            report_.event("sl_rr_hard_reset", "begin");
            const auto released = free_(feature_, viewport_);
            if (released != sl::Result::eOk) {
                report_.event("sl_rr_hard_reset", "failed", static_cast<unsigned long>(released)); std::_Exit(71);
            }
            evaluated_ = false; report_.event("sl_rr_hard_reset", "done");
        }
        upload(color_, color); upload(motion_, motion); upload(depth_, depth);
        if (rr) {
            upload(diffuse_, rr->diffuse_albedo); upload(specular_, rr->specular_albedo);
            upload(normals_, rr->normal_roughness); upload(specular_motion_, rr->specular_motion);
            upload(exposure_, {reinterpret_cast<const unsigned char*>(&rr->exposure), sizeof(rr->exposure)});
        }
        sl::FrameToken* token = nullptr; sl_check(frame_token_(token, &frame_index_), "SL SR frame token");
        if (!token) throw std::runtime_error("SL SR frame token missing");
        sl::Constants common{};
        matrix(common.cameraViewToClip, camera.view_to_clip); matrix(common.clipToCameraView, camera.clip_to_view);
        matrix(common.clipToPrevClip, camera.clip_to_previous); matrix(common.prevClipToClip, camera.previous_to_clip);
        common.jitterOffset = {frame.jitter_x, frame.jitter_y};
        common.mvecScale = {frame.motion_scale_x / static_cast<float>(settings_.input_width),
                            frame.motion_scale_y / static_cast<float>(settings_.input_height)};
        if (rr) common.cameraPinholeOffset = {0, 0}; // Preserve existing RR contract.
        // cameraPinholeOffset is optional and unused by the SL SR plugin.
        // Keep it unspecified, rather than guessing its units or replacing an
        // off-center projection with synthetic jitter. Full matrices above carry K.
        common.cameraPos = vector(camera.position);
        common.cameraUp = vector(camera.up); common.cameraRight = vector(camera.right); common.cameraFwd = vector(camera.forward);
        common.cameraNear = camera.near_plane; common.cameraFar = camera.far_plane;
        common.cameraFOV = camera.vertical_fov; common.cameraAspectRatio = camera.aspect;
        if (!rr && !camera.orthographic && camera.view_to_clip[0] > 0 && camera.view_to_clip[5] > 0)
            common.cameraAspectRatio = camera.view_to_clip[5] / camera.view_to_clip[0];
        common.depthInverted = boolean(settings_.depth_inverted); common.cameraMotionIncluded = sl::Boolean::eTrue;
        common.motionVectors3D = sl::Boolean::eFalse; common.reset = boolean(frame.reset || !frame_index_);
        common.orthographicProjection = boolean(camera.orthographic);
        common.motionVectorsDilated = sl::Boolean::eFalse; common.motionVectorsJittered = sl::Boolean::eFalse;
        sl_check(constants_(common, *token, viewport_), "SL SR common constants");
        if (rr) {
            rr_options_value_.preExposure = frame.pre_exposure; rr_options_value_.exposureScale = frame.exposure_scale;
            matrix(rr_options_value_.worldToCameraView, rr->world_to_view);
            matrix(rr_options_value_.cameraViewToWorld, rr->view_to_world);
            sl_check(rr_options_(viewport_, rr_options_value_), "SL RR frame options");
        } else {
            options_value_.preExposure = frame.pre_exposure; options_value_.exposureScale = frame.exposure_scale;
            sl_check(options_(viewport_, options_value_), "SL SR frame options");
        }
        begin();
        for (auto* plane : {&color_, &motion_, &depth_}) {
            if (frame_index_) barrier(plane->image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_COPY_DEST);
            copy(*plane, true);
            barrier(plane->image.get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        }
        if (rr) for (auto* plane : {&diffuse_, &specular_, &normals_, &specular_motion_, &exposure_}) {
            if (frame_index_) barrier(plane->image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_COPY_DEST);
            copy(*plane, true);
            barrier(plane->image.get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        }
        ID3D12DescriptorHeap* heaps[]{uav_gpu_.get()}; commands_->SetDescriptorHeaps(1, heaps);
        constexpr float initial[]{0, 0, 0, 1};
        commands_->ClearUnorderedAccessViewFloat(uav_gpu_->GetGPUDescriptorHandleForHeapStart(),
            uav_cpu_->GetCPUDescriptorHandleForHeapStart(), output_.image.get(), initial, 0, nullptr);
        uav_barrier(output_.image.get());
        std::array<sl::Resource, 4> resources{
            sl::Resource(sl::ResourceType::eTex2d, color_.image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE),
            sl::Resource(sl::ResourceType::eTex2d, motion_.image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE),
            sl::Resource(sl::ResourceType::eTex2d, depth_.image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE),
            sl::Resource(sl::ResourceType::eTex2d, output_.image.get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS)};
        for (auto& resource : resources) resource.flags = 0;
        const sl::Extent input_extent{0, 0, settings_.input_width, settings_.input_height};
        const sl::Extent output_extent{0, 0, settings_.output_width, settings_.output_height};
        constexpr auto lifecycle = sl::ResourceLifecycle::eValidUntilPresent;
        std::array<sl::ResourceTag, 4> tags{
            sl::ResourceTag(&resources[0], sl::kBufferTypeScalingInputColor, lifecycle, &input_extent),
            sl::ResourceTag(&resources[1], sl::kBufferTypeMotionVectors, lifecycle, &input_extent),
            sl::ResourceTag(&resources[2], sl::kBufferTypeDepth, lifecycle, &input_extent),
            sl::ResourceTag(&resources[3], sl::kBufferTypeScalingOutputColor, lifecycle, &output_extent)};
        sl_check(tag_(*token, viewport_, tags.data(), static_cast<std::uint32_t>(tags.size()), commands_.get()), "SL SR resource tags");
        if (rr) {
            std::array<sl::Resource, 5> extra{
                sl::Resource(sl::ResourceType::eTex2d, diffuse_.image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE),
                sl::Resource(sl::ResourceType::eTex2d, specular_.image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE),
                sl::Resource(sl::ResourceType::eTex2d, normals_.image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE),
                sl::Resource(sl::ResourceType::eTex2d, specular_motion_.image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE),
                sl::Resource(sl::ResourceType::eTex2d, exposure_.image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE)};
            for (auto& resource : extra) resource.flags = 0;
            const sl::Extent exposure_extent{0, 0, 1, 1};
            std::array<sl::ResourceTag, 5> rr_tags{
                sl::ResourceTag(&extra[0], sl::kBufferTypeAlbedo, lifecycle, &input_extent),
                sl::ResourceTag(&extra[1], sl::kBufferTypeSpecularAlbedo, lifecycle, &input_extent),
                sl::ResourceTag(&extra[2], sl::kBufferTypeNormalRoughness, lifecycle, &input_extent),
                sl::ResourceTag(&extra[3], sl::kBufferTypeSpecularMotionVectors, lifecycle, &input_extent),
                sl::ResourceTag(&extra[4], sl::kBufferTypeExposure, lifecycle, &exposure_extent)};
            sl_check(tag_(*token, viewport_, rr_tags.data(), static_cast<std::uint32_t>(rr_tags.size()), commands_.get()), "SL RR guide tags");
        }
        const sl::BaseStructure* inputs[]{&viewport_};
        report_.event("sl_sr_evaluate", "begin", frame_index_);
        sl_check(evaluate_(feature_, *token, inputs, 1, commands_.get()), "SL reconstruction evaluate feature");
        evaluated_ = true;
        // Following commands need no compute bindings to be restored: only UAV
        // ordering, copy, and a render-target clear for the hidden swapchain.
        uav_barrier(output_.image.get());
        barrier(output_.image.get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_COPY_SOURCE);
        copy(output_, false);
        barrier(output_.image.get(), D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
        clear_present_buffer(); submit_wait();
        void* mapped = nullptr; const D3D12_RANGE read{0, static_cast<SIZE_T>(output_.bytes)};
        check(output_.transfer->Map(0, &read, &mapped), "SL SR map readback");
        const D3D12_RANGE no_write{0, 0};
        if (!mapped) { output_.transfer->Unmap(0, &no_write); throw std::runtime_error("SL SR readback memory missing"); }
        const std::size_t row_bytes = static_cast<std::size_t>(settings_.output_width) * 8;
        for (unsigned int y = 0; y < settings_.output_height; ++y)
            std::memcpy(output.data() + static_cast<std::size_t>(y) * row_bytes,
                static_cast<const unsigned char*>(mapped) + output_.footprint.Offset +
                static_cast<UINT64>(y) * output_.footprint.Footprint.RowPitch, row_bytes);
        output_.transfer->Unmap(0, &no_write);
        sr::validate_half_plane(output);
        present();
        report_.event("sl_sr_evaluate", "done", frame_index_);
        ++frame_index_; failed_ = false;
    }
    void close() {
        if (closed_) return;
        if (initialized_) {
            if (evaluated_) {
                report_.event("sl_sr_free", "begin");
                const auto result = free_(feature_, viewport_);
                if (result != sl::Result::eOk) {
                    report_.event("sl_sr_free", "failed", static_cast<unsigned long>(result)); std::_Exit(71);
                }
                evaluated_ = false; report_.event("sl_sr_free", "done");
            }
            report_.event("sl_sr_shutdown", "begin");
            const auto result = shutdown_();
            if (result != sl::Result::eOk) {
                report_.event("sl_sr_shutdown", "failed", static_cast<unsigned long>(result)); std::_Exit(72);
            }
            initialized_ = false; report_.event("sl_sr_shutdown", "done");
        }
        // Keep the module loaded until its proxy vtables and device objects are
        // gone. Shutdown precedes destruction as required by the SL public API.
        backbuffers_ = {}; swapchain_.reset(); window_.reset(); rtv_heap_.reset();
        uav_cpu_.reset(); uav_gpu_.reset(); color_ = {}; motion_ = {}; depth_ = {}; output_ = {};
        diffuse_ = {}; specular_ = {}; normals_ = {}; specular_motion_ = {}; exposure_ = {};
        commands_.reset(); allocator_.reset(); native_queue_.reset(); queue_.reset(); fence_.reset(); event_.reset();
        proxy_factory2_.reset(); proxy_factory_.reset(); proxy_device_.reset(); device_.reset(); factory_.reset();
        interposer_.reset(); d3d_.reset(); dxgi_.reset();
        closed_ = true; report_.event("sl_sr_closed", "done", presents_);
    }
};

sl_sr_engine::sl_sr_engine(const sr::settings& settings, probe_report& report, sl_reconstruction_feature feature)
    : implementation_(std::make_unique<implementation>(settings, report, feature)) {}
sl_sr_engine::~sl_sr_engine() = default;
void sl_sr_engine::initialize(const std::filesystem::path& runtime, const std::filesystem::path& logs,
                             const std::string& project_id) { implementation_->initialize(runtime, logs, project_id); }
void sl_sr_engine::present_only() { implementation_->present_only(); }
void sl_sr_engine::evaluate_frame(std::span<const unsigned char> color, std::span<const unsigned char> motion,
    std::span<const unsigned char> depth, const sl_sr::frame_info& frame, const sl_sr::camera_frame& camera,
    std::span<unsigned char> output) { implementation_->evaluate_frame(color, motion, depth, frame, camera, output); }
void sl_sr_engine::evaluate_rr_frame(std::span<const unsigned char> color, std::span<const unsigned char> motion,
    std::span<const unsigned char> depth, const sl_sr::frame_info& frame, const sl_sr::camera_frame& camera,
    const sl_rr::guides& guides, std::span<unsigned char> output) {
    implementation_->evaluate_frame(color, motion, depth, frame, camera, output, &guides);
}
void sl_sr_engine::close() { implementation_->close(); }
}
