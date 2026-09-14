// Official NGX SDK SR/DLAA implementation. This is deliberately a different
// translation unit from the NR snippet ABI and caller-name compatibility shim.
#include "probe_report.hpp"
#include "sr_engine.hpp"
#include "win32_raii.hpp"
#ifdef NGX_SNIPPET_BUILD
#error "sr_engine requires the public NGX SDK ABI, not NGX_SNIPPET_BUILD"
#endif
#include <d3d12.h>
#include <dxgi1_4.h>
#include <nvsdk_ngx_helpers.h>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>

namespace comfy_dlss {
namespace {
static_assert(static_cast<int>(sr::quality::performance) == NVSDK_NGX_PerfQuality_Value_MaxPerf);
static_assert(static_cast<int>(sr::quality::balanced) == NVSDK_NGX_PerfQuality_Value_Balanced);
static_assert(static_cast<int>(sr::quality::quality) == NVSDK_NGX_PerfQuality_Value_MaxQuality);
static_assert(static_cast<int>(sr::quality::ultra_performance) == NVSDK_NGX_PerfQuality_Value_UltraPerformance);
static_assert(static_cast<int>(sr::quality::ultra_quality) == NVSDK_NGX_PerfQuality_Value_UltraQuality);
static_assert(static_cast<int>(sr::quality::dlaa) == NVSDK_NGX_PerfQuality_Value_DLAA);
static_assert(NVSDK_NGX_DLSS_Hint_Render_Preset_J == 10 && NVSDK_NGX_DLSS_Hint_Render_Preset_M == 13);

std::string code_text(unsigned long code) {
    char buffer[16]{};
    std::snprintf(buffer, sizeof(buffer), "0x%08lx", code);
    return buffer;
}
void check(HRESULT result, const char* action) {
    if (FAILED(result)) throw std::runtime_error(std::string(action) + ": " + code_text(static_cast<unsigned long>(result)));
}
void ngx_check(NVSDK_NGX_Result result, const char* action) {
    if (result != NVSDK_NGX_Result_Success)
        throw std::runtime_error(std::string(action) + ": " + code_text(static_cast<unsigned long>(result)));
}
template<class T> T symbol(const unique_module& module, const char* name) {
    const auto address = module.symbol(name);
    if (!address) throw std::runtime_error(std::string("Missing system graphics export: ") + name);
    return reinterpret_cast<T>(address);
}
struct texture {
    com_ptr<ID3D12Resource> image, transfer;
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};
    UINT64 bytes = 0;
    unsigned int width = 0, height = 0, pixel_bytes = 0;
};
}

class sr_engine::implementation final {
    sr::settings settings_;
    probe_report& report_;
    unique_module dxgi_, d3d_;
    com_ptr<IDXGIFactory1> factory_;
    com_ptr<ID3D12Device> device_;
    com_ptr<ID3D12CommandQueue> queue_;
    com_ptr<ID3D12CommandAllocator> allocator_;
    com_ptr<ID3D12GraphicsCommandList> commands_;
    com_ptr<ID3D12Fence> fence_;
    unique_handle event_;
    texture color_, motion_, depth_, exposure_, output_;
    com_ptr<ID3D12Resource> scratch_;
    NVSDK_NGX_Parameter* parameters_ = nullptr;
    NVSDK_NGX_Parameter* capabilities_ = nullptr;
    NVSDK_NGX_Handle* feature_ = nullptr;
    bool initialized_ = false, attempted_ = false, closed_ = false, failed_ = false;
    UINT64 fence_value_ = 0;
    std::uint32_t frame_index_ = 0;

    [[noreturn]] void inflight_failure(const char* action, unsigned long code) noexcept {
        report_.event(action, "failed", code);
        // This class runs in the isolated Worker. Unwinding COM owners while
        // submitted commands may still reference them is not safe.
        std::_Exit(70);
    }
    void submit_wait(const char* stage) {
        report_.event(stage, "begin");
        check(commands_->Close(), "SR command list close");
        if (fence_value_ >= std::numeric_limits<UINT64>::max() - 1)
            throw std::runtime_error("SR fence exhausted");
        ++fence_value_;
        ID3D12CommandList* lists[]{commands_.get()};
        queue_->ExecuteCommandLists(1, lists);
        // No throwing call/allocation until GPU completion is proven.
        auto result = queue_->Signal(fence_.get(), fence_value_);
        if (FAILED(result)) inflight_failure("sr_signal", static_cast<unsigned long>(result));
        result = fence_->SetEventOnCompletion(fence_value_, event_.get());
        if (FAILED(result)) inflight_failure("sr_completion_event", static_cast<unsigned long>(result));
        const auto wait = WaitForSingleObject(event_.get(), 30000);
        const auto completed = fence_->GetCompletedValue();
        if (wait != WAIT_OBJECT_0 || completed == std::numeric_limits<UINT64>::max() || completed < fence_value_)
            inflight_failure("sr_completion_timeout", wait);
        report_.event(stage, "done");
    }
    void begin() {
        check(allocator_->Reset(), "SR allocator reset");
        check(commands_->Reset(allocator_.get(), nullptr), "SR command list reset");
    }
    void barrier(ID3D12Resource* resource, D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after) {
        D3D12_RESOURCE_BARRIER value{};
        value.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        value.Transition = {resource, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, before, after};
        commands_->ResourceBarrier(1, &value);
    }
    texture make_texture(unsigned int width, unsigned int height, DXGI_FORMAT format,
                         unsigned int pixel_bytes, bool output) {
        texture result;
        result.width = width; result.height = height; result.pixel_bytes = pixel_bytes;
        D3D12_HEAP_PROPERTIES heap{};
        heap.Type = D3D12_HEAP_TYPE_DEFAULT;
        heap.CreationNodeMask = heap.VisibleNodeMask = 1;
        D3D12_RESOURCE_DESC desc{};
        desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
        desc.Width = width; desc.Height = height;
        desc.DepthOrArraySize = desc.MipLevels = 1;
        desc.Format = format; desc.SampleDesc.Count = 1;
        desc.Flags = output ? D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS : D3D12_RESOURCE_FLAG_NONE;
        void* raw = nullptr;
        auto hr = device_->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc,
            output ? D3D12_RESOURCE_STATE_UNORDERED_ACCESS : D3D12_RESOURCE_STATE_COPY_DEST,
            nullptr, IID_ID3D12Resource, &raw);
        result.image.reset(static_cast<ID3D12Resource*>(raw));
        check(hr, "SR create texture");
        if (!result.image) throw std::runtime_error("SR texture allocation returned no resource");
        UINT rows = 0;
        UINT64 row_bytes = 0;
        device_->GetCopyableFootprints(&desc, 0, 1, 0, &result.footprint, &rows, &row_bytes, &result.bytes);
        const auto& placed = result.footprint;
        if (rows != height || row_bytes != static_cast<UINT64>(width) * pixel_bytes ||
            result.bytes == 0 || result.bytes > 128ULL * 1024 * 1024 || placed.Offset > result.bytes ||
            placed.Footprint.RowPitch < row_bytes || placed.Footprint.Width != width ||
            placed.Footprint.Height != height || placed.Footprint.Depth != 1 || placed.Footprint.Format != format ||
            static_cast<UINT64>(height - 1) * placed.Footprint.RowPitch + row_bytes > result.bytes - placed.Offset)
            throw std::runtime_error("SR invalid GPU texture footprint");
        heap.Type = output ? D3D12_HEAP_TYPE_READBACK : D3D12_HEAP_TYPE_UPLOAD;
        desc = {}; desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; desc.Width = result.bytes;
        desc.Height = desc.DepthOrArraySize = desc.MipLevels = 1; desc.SampleDesc.Count = 1;
        desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
        raw = nullptr;
        hr = device_->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc,
            output ? D3D12_RESOURCE_STATE_COPY_DEST : D3D12_RESOURCE_STATE_GENERIC_READ,
            nullptr, IID_ID3D12Resource, &raw);
        result.transfer.reset(static_cast<ID3D12Resource*>(raw));
        check(hr, "SR create transfer buffer");
        if (!result.transfer) throw std::runtime_error("SR transfer allocation returned no resource");
        return result;
    }
    void upload(texture& target, std::span<const unsigned char> plane) {
        const std::size_t row_bytes = static_cast<std::size_t>(target.width) * target.pixel_bytes;
        if (plane.size() != row_bytes * target.height) throw std::invalid_argument("SR upload size mismatch");
        void* mapped = nullptr;
        D3D12_RANGE no_read{0, 0};
        check(target.transfer->Map(0, &no_read, &mapped), "SR map upload");
        if (!mapped) {
            target.transfer->Unmap(0, &no_read);
            throw std::runtime_error("SR map returned no upload memory");
        }
        auto* destination = static_cast<unsigned char*>(mapped);
        for (unsigned int y = 0; y < target.height; ++y)
            std::memcpy(destination + target.footprint.Offset + static_cast<UINT64>(y) * target.footprint.Footprint.RowPitch,
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
    void initialize_device() {
        report_.event("sr_device", "begin");
        dxgi_.reset(LoadLibraryExW(L"dxgi.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32));
        d3d_.reset(LoadLibraryExW(L"d3d12.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32));
        const auto make_factory = symbol<decltype(&CreateDXGIFactory1)>(dxgi_, "CreateDXGIFactory1");
        const auto make_device = symbol<PFN_D3D12_CREATE_DEVICE>(d3d_, "D3D12CreateDevice");
        void* raw = nullptr;
        auto hr = make_factory(IID_IDXGIFactory1, &raw);
        factory_.reset(static_cast<IDXGIFactory1*>(raw));
        check(hr, "SR create DXGI factory");
        if (!factory_) throw std::runtime_error("SR no DXGI factory");
        for (UINT index = 0; ; ++index) {
            IDXGIAdapter1* pointer = nullptr;
            hr = factory_->EnumAdapters1(index, &pointer);
            com_ptr<IDXGIAdapter1> adapter{pointer};
            if (hr == DXGI_ERROR_NOT_FOUND) break;
            check(hr, "SR enumerate adapters");
            if (!adapter) throw std::runtime_error("SR adapter enumeration returned no adapter");
            DXGI_ADAPTER_DESC1 desc{};
            check(adapter->GetDesc1(&desc), "SR adapter description");
            if (desc.VendorId != 0x10de || (desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)) continue;
            raw = nullptr;
            hr = make_device(adapter.get(), D3D_FEATURE_LEVEL_12_0, IID_ID3D12Device, &raw);
            com_ptr<ID3D12Device> candidate{static_cast<ID3D12Device*>(raw)};
            if (SUCCEEDED(hr) && candidate) { device_ = std::move(candidate); break; }
        }
        if (!device_) throw std::runtime_error("SR requires an NVIDIA D3D12 hardware device");
        D3D12_COMMAND_QUEUE_DESC queue_desc{};
        queue_desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
        raw = nullptr; hr = device_->CreateCommandQueue(&queue_desc, IID_ID3D12CommandQueue, &raw);
        queue_.reset(static_cast<ID3D12CommandQueue*>(raw)); check(hr, "SR create queue");
        raw = nullptr; hr = device_->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_ID3D12CommandAllocator, &raw);
        allocator_.reset(static_cast<ID3D12CommandAllocator*>(raw)); check(hr, "SR create allocator");
        raw = nullptr; hr = device_->CreateCommandList(0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator_.get(), nullptr,
            IID_ID3D12GraphicsCommandList, &raw);
        commands_.reset(static_cast<ID3D12GraphicsCommandList*>(raw)); check(hr, "SR create command list");
        raw = nullptr; hr = device_->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_ID3D12Fence, &raw);
        fence_.reset(static_cast<ID3D12Fence*>(raw)); check(hr, "SR create fence");
        if (!queue_ || !allocator_ || !commands_ || !fence_) throw std::runtime_error("SR missing graphics resource");
        event_.reset(CreateEventW(nullptr, FALSE, FALSE, nullptr));
        if (!event_) throw std::runtime_error("SR create completion event failed");
        report_.event("sr_device", "done");
    }
    NVSDK_NGX_DLSS_Create_Params create_parameters() const {
        NVSDK_NGX_DLSS_Create_Params value{};
        value.Feature.InWidth = settings_.input_width; value.Feature.InHeight = settings_.input_height;
        value.Feature.InTargetWidth = settings_.output_width; value.Feature.InTargetHeight = settings_.output_height;
        value.Feature.InPerfQualityValue = static_cast<NVSDK_NGX_PerfQuality_Value>(settings_.mode);
        value.InFeatureCreateFlags = NVSDK_NGX_DLSS_Feature_Flags_MVLowRes;
        if (settings_.hdr) value.InFeatureCreateFlags |= NVSDK_NGX_DLSS_Feature_Flags_IsHDR;
        if (settings_.depth_inverted) value.InFeatureCreateFlags |= NVSDK_NGX_DLSS_Feature_Flags_DepthInverted;
        if (settings_.motion_jittered) value.InFeatureCreateFlags |= NVSDK_NGX_DLSS_Feature_Flags_MVJittered;
        if (settings_.auto_exposure) value.InFeatureCreateFlags |= NVSDK_NGX_DLSS_Feature_Flags_AutoExposure;
        return value;
    }
    void create_scratch() {
        // The scratch query needs the same basic feature description as Create.
        const auto value = create_parameters();
        NVSDK_NGX_Parameter_SetUI(parameters_, NVSDK_NGX_Parameter_Width, value.Feature.InWidth);
        NVSDK_NGX_Parameter_SetUI(parameters_, NVSDK_NGX_Parameter_Height, value.Feature.InHeight);
        NVSDK_NGX_Parameter_SetUI(parameters_, NVSDK_NGX_Parameter_OutWidth, value.Feature.InTargetWidth);
        NVSDK_NGX_Parameter_SetUI(parameters_, NVSDK_NGX_Parameter_OutHeight, value.Feature.InTargetHeight);
        NVSDK_NGX_Parameter_SetI(parameters_, NVSDK_NGX_Parameter_PerfQualityValue, value.Feature.InPerfQualityValue);
        NVSDK_NGX_Parameter_SetI(parameters_, NVSDK_NGX_Parameter_DLSS_Feature_Create_Flags, value.InFeatureCreateFlags);
        std::size_t size = 0;
        ngx_check(NVSDK_NGX_D3D12_GetScratchBufferSize(NVSDK_NGX_Feature_SuperSampling, parameters_, &size), "SR scratch size");
        if (size == 0) return;
        if (size > 512ULL * 1024 * 1024) throw std::runtime_error("SR scratch exceeds experimental 512 MiB bound");
        D3D12_HEAP_PROPERTIES heap{}; heap.Type = D3D12_HEAP_TYPE_DEFAULT;
        heap.CreationNodeMask = heap.VisibleNodeMask = 1;
        D3D12_RESOURCE_DESC desc{}; desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
        desc.Width = size; desc.Height = desc.DepthOrArraySize = desc.MipLevels = 1;
        desc.SampleDesc.Count = 1; desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
        desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
        void* raw = nullptr;
        const auto hr = device_->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_NONE, &desc,
            D3D12_RESOURCE_STATE_UNORDERED_ACCESS, nullptr, IID_ID3D12Resource, &raw);
        scratch_.reset(static_cast<ID3D12Resource*>(raw));
        check(hr, "SR create scratch");
        if (!scratch_) throw std::runtime_error("SR missing scratch resource");
        NVSDK_NGX_Parameter_SetD3d12Resource(parameters_, NVSDK_NGX_Parameter_Scratch, scratch_.get());
        NVSDK_NGX_Parameter_SetULL(parameters_, NVSDK_NGX_Parameter_Scratch_SizeInBytes, size);
    }
public:
    implementation(const sr::settings& settings, probe_report& report) : settings_(settings), report_(report) {
        sr::validate(settings_);
    }
    ~implementation() noexcept {
        try { close(); }
        catch (...) { report_.event("sr_cleanup", "failed"); std::_Exit(71); }
    }
    void initialize(const std::filesystem::path& model_directory, const std::filesystem::path& logs,
                    const std::string& project_id) {
        if (attempted_ || closed_) throw std::runtime_error("SR engine initialization is single-use");
        if (!sr::valid_project_id(project_id)) throw std::invalid_argument("SR project ID must be a GUID string");
        if (!model_directory.is_absolute() || !std::filesystem::is_directory(model_directory) ||
            !std::filesystem::is_regular_file(model_directory / "nvngx_dlss.dll") ||
            !logs.is_absolute() || !std::filesystem::is_directory(logs))
            throw std::invalid_argument("SR requires an absolute model directory with nvngx_dlss.dll and an existing log directory");
        attempted_ = true;
        initialize_device();
        const auto model_path = model_directory.wstring();
        const wchar_t* model_paths[]{model_path.c_str()};
        NVSDK_NGX_FeatureCommonInfo info{};
        info.PathListInfo.Path = model_paths; info.PathListInfo.Length = 1;
        report_.event("sr_init", "begin");
        ngx_check(NVSDK_NGX_D3D12_Init_with_ProjectID(project_id.c_str(), NVSDK_NGX_ENGINE_TYPE_CUSTOM,
            "comfy-dlss-experimental", logs.c_str(), device_.get(), &info, NVSDK_NGX_Version_API), "SR NGX SDK initialization");
        initialized_ = true;
        report_.event("sr_init", "done");
        ngx_check(NVSDK_NGX_D3D12_GetCapabilityParameters(&capabilities_), "SR query capabilities");
        if (!capabilities_) throw std::runtime_error("SR capability query returned no parameters");
        int available = 0, driver_update = 0;
        const auto update_result = NVSDK_NGX_Parameter_GetI(capabilities_, NVSDK_NGX_Parameter_SuperSampling_NeedsUpdatedDriver, &driver_update);
        if (update_result == NVSDK_NGX_Result_Success && driver_update)
            throw std::runtime_error("The NGX SDK reports that SR needs an updated NVIDIA driver");
        ngx_check(NVSDK_NGX_Parameter_GetI(capabilities_, NVSDK_NGX_Parameter_SuperSampling_Available, &available), "SR capability availability");
        if (!available) throw std::runtime_error("The NGX SDK reports SR unsupported on this device/runtime");
        unsigned int optimal_width = 0, optimal_height = 0, maximum_width = 0, maximum_height = 0;
        unsigned int minimum_width = 0, minimum_height = 0;
        float unused_sharpness = 0;
        ngx_check(NGX_DLSS_GET_OPTIMAL_SETTINGS(capabilities_, settings_.output_width, settings_.output_height,
            static_cast<NVSDK_NGX_PerfQuality_Value>(settings_.mode), &optimal_width, &optimal_height,
            &maximum_width, &maximum_height, &minimum_width, &minimum_height, &unused_sharpness), "SR optimal settings query");
        sr::validate_runtime_range(settings_, optimal_width, optimal_height, minimum_width, minimum_height,
            maximum_width, maximum_height);
        ngx_check(NVSDK_NGX_D3D12_AllocateParameters(&parameters_), "SR allocate parameters");
        if (!parameters_) throw std::runtime_error("SR parameter allocation returned no parameters");
        for (const char* name : {NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_DLAA,
                NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Quality, NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Balanced,
                NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Performance, NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_UltraPerformance,
                NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_UltraQuality})
            NVSDK_NGX_Parameter_SetUI(parameters_, name, settings_.preset);
        color_ = make_texture(settings_.input_width, settings_.input_height, DXGI_FORMAT_R16G16B16A16_FLOAT, 8, false);
        motion_ = make_texture(settings_.input_width, settings_.input_height, DXGI_FORMAT_R16G16_FLOAT, 4, false);
        depth_ = make_texture(settings_.input_width, settings_.input_height, DXGI_FORMAT_R32_FLOAT, 4, false);
        exposure_ = make_texture(1, 1, DXGI_FORMAT_R32_FLOAT, 4, false);
        output_ = make_texture(settings_.output_width, settings_.output_height, DXGI_FORMAT_R16G16B16A16_FLOAT, 8, true);
        create_scratch();
        auto create = create_parameters();
        report_.event("sr_create", "begin");
        ngx_check(NGX_D3D12_CREATE_DLSS_EXT(commands_.get(), 1, 1, &feature_, parameters_, &create), "SR create DLSS feature");
        if (!feature_) throw std::runtime_error("SR feature creation returned no handle");
        submit_wait("sr_create_fence");
        report_.event("sr_create", "done");
    }
    void evaluate_frame(std::span<const unsigned char> color, std::span<const unsigned char> motion,
                        std::span<const unsigned char> depth, const sr::frame_info& frame,
                        std::span<unsigned char> output) {
        if (!feature_ || closed_ || failed_ || frame_index_ >= 1000000)
            throw std::runtime_error("SR evaluation requires a healthy initialized feature within frame bounds");
        sr::validate(frame);
        sr::validate_planes(settings_, color, motion, depth, output);
        // Once resource state mutation begins, a failed task cannot reuse this
        // engine. It is released by the containing isolated Worker.
        failed_ = true;
        upload(color_, color); upload(motion_, motion); upload(depth_, depth);
        std::array<unsigned char, sizeof(float)> exposure_bytes{};
        std::memcpy(exposure_bytes.data(), &frame.exposure, sizeof(frame.exposure));
        upload(exposure_, exposure_bytes);
        begin();
        for (texture* input : {&color_, &motion_, &depth_, &exposure_}) {
            if (frame_index_ != 0)
                barrier(input->image.get(), D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_COPY_DEST);
            copy(*input, true);
            barrier(input->image.get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE);
        }
        NVSDK_NGX_D3D12_DLSS_Eval_Params evaluation{};
        evaluation.Feature.pInColor = color_.image.get(); evaluation.Feature.pInOutput = output_.image.get();
        evaluation.pInMotionVectors = motion_.image.get(); evaluation.pInDepth = depth_.image.get();
        evaluation.pInExposureTexture = settings_.auto_exposure ? nullptr : exposure_.image.get();
        evaluation.InJitterOffsetX = frame.jitter_x; evaluation.InJitterOffsetY = frame.jitter_y;
        evaluation.InMVScaleX = frame.motion_scale_x; evaluation.InMVScaleY = frame.motion_scale_y;
        evaluation.InPreExposure = frame.pre_exposure; evaluation.InExposureScale = frame.exposure_scale;
        evaluation.InFrameTimeDeltaInMsec = frame.frame_time_ms;
        evaluation.InRenderSubrectDimensions = {settings_.input_width, settings_.input_height};
        evaluation.InReset = (frame.reset || frame_index_ == 0) ? 1 : 0;
        report_.event("sr_evaluate", "begin", frame_index_);
        ngx_check(NGX_D3D12_EVALUATE_DLSS_EXT(commands_.get(), feature_, parameters_, &evaluation), "SR evaluate DLSS feature");
        D3D12_RESOURCE_BARRIER uav{}; uav.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV; uav.UAV.pResource = output_.image.get();
        commands_->ResourceBarrier(1, &uav);
        barrier(output_.image.get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_COPY_SOURCE);
        copy(output_, false);
        barrier(output_.image.get(), D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
        submit_wait("sr_evaluate_fence");
        void* mapped = nullptr;
        const D3D12_RANGE read{0, static_cast<SIZE_T>(output_.bytes)};
        check(output_.transfer->Map(0, &read, &mapped), "SR map readback");
        const D3D12_RANGE no_write{0, 0};
        if (!mapped) {
            output_.transfer->Unmap(0, &no_write);
            throw std::runtime_error("SR map returned no readback memory");
        }
        const auto* source = static_cast<const unsigned char*>(mapped);
        const std::size_t row_bytes = static_cast<std::size_t>(settings_.output_width) * 8;
        for (unsigned int y = 0; y < settings_.output_height; ++y)
            std::memcpy(output.data() + static_cast<std::size_t>(y) * row_bytes,
                source + output_.footprint.Offset + static_cast<UINT64>(y) * output_.footprint.Footprint.RowPitch, row_bytes);
        output_.transfer->Unmap(0, &no_write);
        sr::validate_half_plane(output);
        report_.event("sr_evaluate", "done", frame_index_);
        ++frame_index_;
        failed_ = false;
    }
    void close() {
        if (closed_) return;
        // Every submission has completed or submit_wait already terminated this
        // isolated process. Release precedes params, shutdown and COM resources.
        if (feature_) {
            const auto result = NVSDK_NGX_D3D12_ReleaseFeature(feature_);
            if (result != NVSDK_NGX_Result_Success) {
                report_.event("sr_release", "failed", static_cast<unsigned long>(result));
                std::_Exit(71);
            }
            feature_ = nullptr;
        }
        if (parameters_) {
            const auto result = NVSDK_NGX_D3D12_DestroyParameters(parameters_);
            if (result != NVSDK_NGX_Result_Success) {
                report_.event("sr_destroy_parameters", "failed", static_cast<unsigned long>(result));
                std::_Exit(71); // Do not retry a possibly partially destroyed SDK object.
            }
            parameters_ = nullptr;
        }
        if (capabilities_) {
            const auto result = NVSDK_NGX_D3D12_DestroyParameters(capabilities_);
            if (result != NVSDK_NGX_Result_Success) {
                report_.event("sr_destroy_capabilities", "failed", static_cast<unsigned long>(result));
                std::_Exit(71);
            }
            capabilities_ = nullptr;
        }
        if (initialized_) {
            const auto result = NVSDK_NGX_D3D12_Shutdown1(device_.get());
            if (result != NVSDK_NGX_Result_Success) {
                report_.event("sr_shutdown", "failed", static_cast<unsigned long>(result));
                std::_Exit(72);
            }
            initialized_ = false;
        }
        scratch_.reset(); color_ = {}; motion_ = {}; depth_ = {}; exposure_ = {}; output_ = {};
        commands_.reset(); allocator_.reset(); queue_.reset(); fence_.reset(); event_.reset();
        device_.reset(); factory_.reset(); d3d_.reset(); dxgi_.reset();
        closed_ = true;
    }
};

sr_engine::sr_engine(const sr::settings& settings, probe_report& report)
    : implementation_(std::make_unique<implementation>(settings, report)) {}
sr_engine::~sr_engine() = default;
void sr_engine::initialize(const std::filesystem::path& model_directory, const std::filesystem::path& logs,
                           const std::string& project_id) { implementation_->initialize(model_directory, logs, project_id); }
void sr_engine::evaluate_frame(std::span<const unsigned char> color, std::span<const unsigned char> motion,
    std::span<const unsigned char> depth, const sr::frame_info& frame, std::span<unsigned char> output) {
    implementation_->evaluate_frame(color, motion, depth, frame, output);
}
void sr_engine::close() { implementation_->close(); }
}
