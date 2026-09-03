#include "ngx_smoke.hpp"

#include "win32_raii.hpp"

#include <nvsdk_ngx.h>

#include <array>
#include <string>

namespace comfy_dlss {
namespace {

constexpr unsigned int kWidth = 640;
constexpr unsigned int kHeight = 360;
constexpr unsigned long long kProbeApplicationId = 0x01000000ULL;

using init_ext_fn = NVSDK_NGX_Result (NVSDK_CONV *)(
    unsigned long long, const wchar_t*, ID3D12Device*, int, const void*);
using allocate_parameters_fn = NVSDK_NGX_Result (NVSDK_CONV *)(NVSDK_NGX_Parameter**);
using destroy_parameters_fn = NVSDK_NGX_Result (NVSDK_CONV *)(NVSDK_NGX_Parameter*);
using create_feature_fn = NVSDK_NGX_Result (NVSDK_CONV *)(
    ID3D12GraphicsCommandList*, int, NVSDK_NGX_Parameter*, NVSDK_NGX_Handle**);
using evaluate_feature_fn = NVSDK_NGX_Result (NVSDK_CONV *)(
    ID3D12GraphicsCommandList*, const NVSDK_NGX_Handle*, const NVSDK_NGX_Parameter*, void*);
using release_feature_fn = NVSDK_NGX_Result (NVSDK_CONV *)(NVSDK_NGX_Handle*);
using shutdown_fn = NVSDK_NGX_Result (NVSDK_CONV *)(ID3D12Device*);

struct ngx_api final {
    unique_module module;
    init_ext_fn init_ext = nullptr;
    allocate_parameters_fn allocate_parameters = nullptr;
    destroy_parameters_fn destroy_parameters = nullptr;
    create_feature_fn create_feature = nullptr;
    evaluate_feature_fn evaluate_feature = nullptr;
    release_feature_fn release_feature = nullptr;
    shutdown_fn shutdown = nullptr;

    [[nodiscard]] bool complete() const noexcept {
        return module && init_ext && allocate_parameters && destroy_parameters &&
            create_feature && evaluate_feature && release_feature && shutdown;
    }
};

template <typename Function>
Function resolve(const unique_module& module, const char* name) noexcept {
    return reinterpret_cast<Function>(module.symbol(name));
}

bool load_ngx_api(ngx_api& api, std::string& error) {
    // nvngx.dll is NVIDIA's public driver bootstrap. Calling _nvngx.dll directly
    // bypasses that bootstrap and is not a supported entry path under Proton.
    // Resolving the public forwarding exports still avoids linking the SDK's
    // MSVC-only static archive into this MinGW-built carrier.
    api.module.reset(LoadLibraryW(L"nvngx.dll"));
    if (!api.module) {
        error = "LoadLibraryW(nvngx.dll) failed";
        return false;
    }
    api.init_ext = resolve<init_ext_fn>(api.module, "NVSDK_NGX_D3D12_Init_Ext");
    api.allocate_parameters = resolve<allocate_parameters_fn>(
        api.module, "NVSDK_NGX_D3D12_AllocateParameters");
    api.destroy_parameters = resolve<destroy_parameters_fn>(
        api.module, "NVSDK_NGX_D3D12_DestroyParameters");
    api.create_feature = resolve<create_feature_fn>(api.module, "NVSDK_NGX_D3D12_CreateFeature");
    api.evaluate_feature = resolve<evaluate_feature_fn>(api.module, "NVSDK_NGX_D3D12_EvaluateFeature");
    api.release_feature = resolve<release_feature_fn>(api.module, "NVSDK_NGX_D3D12_ReleaseFeature");
    api.shutdown = resolve<shutdown_fn>(api.module, "NVSDK_NGX_D3D12_Shutdown1");
    if (!api.complete()) {
        error = "nvngx.dll is missing one or more required D3D12 exports";
        return false;
    }
    return true;
}

std::string hex_result(unsigned long value) {
    char buffer[16]{};
    std::snprintf(buffer, sizeof(buffer), "0x%08lx", value);
    return buffer;
}

class command_context final {
public:
    bool initialize(ID3D12Device* device, ID3D12CommandQueue* queue, std::string& error) {
        queue_ = queue;
        void* raw_allocator = nullptr;
        HRESULT result = device->CreateCommandAllocator(
            D3D12_COMMAND_LIST_TYPE_DIRECT, IID_ID3D12CommandAllocator, &raw_allocator);
        allocator_.reset(static_cast<ID3D12CommandAllocator*>(raw_allocator));
        if (FAILED(result) || !allocator_) {
            error = "CreateCommandAllocator failed: " + hex_result(static_cast<unsigned long>(result));
            return false;
        }
        void* raw_list = nullptr;
        result = device->CreateCommandList(
            0,
            D3D12_COMMAND_LIST_TYPE_DIRECT,
            allocator_.get(),
            nullptr,
            IID_ID3D12GraphicsCommandList,
            &raw_list);
        list_.reset(static_cast<ID3D12GraphicsCommandList*>(raw_list));
        if (FAILED(result) || !list_) {
            error = "CreateCommandList failed: " + hex_result(static_cast<unsigned long>(result));
            return false;
        }
        result = list_->Close();
        if (FAILED(result)) {
            error = "initial command-list Close failed: " + hex_result(static_cast<unsigned long>(result));
            return false;
        }
        void* raw_fence = nullptr;
        result = device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_ID3D12Fence, &raw_fence);
        fence_.reset(static_cast<ID3D12Fence*>(raw_fence));
        if (FAILED(result) || !fence_) {
            error = "CreateFence failed: " + hex_result(static_cast<unsigned long>(result));
            return false;
        }
        event_.reset(CreateEventW(nullptr, FALSE, FALSE, nullptr));
        if (!event_) {
            error = "CreateEventW failed";
            return false;
        }
        return true;
    }

    bool begin(std::string& error) {
        if (!wait(error)) return false;
        HRESULT result = allocator_->Reset();
        if (FAILED(result)) {
            error = "command allocator Reset failed: " + hex_result(static_cast<unsigned long>(result));
            return false;
        }
        result = list_->Reset(allocator_.get(), nullptr);
        if (FAILED(result)) {
            error = "command list Reset failed: " + hex_result(static_cast<unsigned long>(result));
            return false;
        }
        open_ = true;
        return true;
    }

    bool submit_and_wait(std::string& error) {
        HRESULT result = list_->Close();
        open_ = false;
        if (FAILED(result)) {
            error = "command list Close failed: " + hex_result(static_cast<unsigned long>(result));
            return false;
        }
        ID3D12CommandList* lists[] = {list_.get()};
        queue_->ExecuteCommandLists(1, lists);
        ++fence_value_;
        result = queue_->Signal(fence_.get(), fence_value_);
        if (FAILED(result)) {
            error = "command queue Signal failed: " + hex_result(static_cast<unsigned long>(result));
            return false;
        }
        return wait(error);
    }

    void abort() noexcept {
        if (open_) {
            list_->Close();
            open_ = false;
        }
    }

    [[nodiscard]] ID3D12GraphicsCommandList* list() const noexcept {
        return list_.get();
    }

private:
    bool wait(std::string& error) {
        if (fence_value_ == 0 || fence_->GetCompletedValue() >= fence_value_) return true;
        HRESULT result = fence_->SetEventOnCompletion(fence_value_, event_.get());
        if (FAILED(result)) {
            error = "SetEventOnCompletion failed: " + hex_result(static_cast<unsigned long>(result));
            return false;
        }
        const DWORD wait_result = WaitForSingleObject(event_.get(), 10'000);
        if (wait_result != WAIT_OBJECT_0) {
            error = "GPU fence wait failed or timed out: " + hex_result(wait_result);
            return false;
        }
        return true;
    }

    ID3D12CommandQueue* queue_ = nullptr;
    com_ptr<ID3D12CommandAllocator> allocator_;
    com_ptr<ID3D12GraphicsCommandList> list_;
    com_ptr<ID3D12Fence> fence_;
    unique_handle event_;
    unsigned long long fence_value_ = 0;
    bool open_ = false;
};

class ngx_session final {
public:
    const ngx_api* api = nullptr;
    ID3D12Device* device = nullptr;
    NVSDK_NGX_Parameter* parameters = nullptr;
    NVSDK_NGX_Handle* feature = nullptr;
    bool initialized = false;

    ~ngx_session() {
        if (api == nullptr) return;
        if (feature != nullptr) api->release_feature(feature);
        if (parameters != nullptr) api->destroy_parameters(parameters);
        if (initialized) api->shutdown(device);
    }
};

com_ptr<ID3D12Resource> create_texture(
    ID3D12Device* device, DXGI_FORMAT format, bool unordered_access, std::string& error) {
    D3D12_HEAP_PROPERTIES heap{};
    heap.Type = D3D12_HEAP_TYPE_DEFAULT;
    D3D12_RESOURCE_DESC description{};
    description.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    description.Width = kWidth;
    description.Height = kHeight;
    description.DepthOrArraySize = 1;
    description.MipLevels = 1;
    description.Format = format;
    description.SampleDesc.Count = 1;
    description.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;
    description.Flags = unordered_access
        ? D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS
        : D3D12_RESOURCE_FLAG_NONE;
    void* raw_resource = nullptr;
    const HRESULT result = device->CreateCommittedResource(
        &heap,
        D3D12_HEAP_FLAG_NONE,
        &description,
        D3D12_RESOURCE_STATE_COMMON,
        nullptr,
        IID_ID3D12Resource,
        &raw_resource);
    com_ptr<ID3D12Resource> resource{static_cast<ID3D12Resource*>(raw_resource)};
    if (FAILED(result) || !resource) {
        error = "CreateCommittedResource failed: " + hex_result(static_cast<unsigned long>(result));
        return {};
    }
    return resource;
}

void set_create_parameters(NVSDK_NGX_Parameter* parameters) {
    parameters->Set(NVSDK_NGX_Parameter_CreationNodeMask, 1u);
    parameters->Set(NVSDK_NGX_Parameter_VisibilityNodeMask, 1u);
    parameters->Set(NVSDK_NGX_Parameter_Width, kWidth);
    parameters->Set(NVSDK_NGX_Parameter_Height, kHeight);
    parameters->Set(NVSDK_NGX_Parameter_OutWidth, kWidth);
    parameters->Set(NVSDK_NGX_Parameter_OutHeight, kHeight);
    parameters->Set(NVSDK_NGX_Parameter_PerfQualityValue, static_cast<int>(NVSDK_NGX_PerfQuality_Value_DLAA));
    const int flags = NVSDK_NGX_DLSS_Feature_Flags_MVLowRes |
        NVSDK_NGX_DLSS_Feature_Flags_AutoExposure |
        NVSDK_NGX_DLSS_Feature_Flags_DepthInverted;
    parameters->Set(NVSDK_NGX_Parameter_DLSS_Feature_Create_Flags, flags);
    parameters->Set(NVSDK_NGX_Parameter_DLSS_Enable_Output_Subrects, 0);
}

void set_evaluate_parameters(
    NVSDK_NGX_Parameter* parameters,
    ID3D12Resource* color,
    ID3D12Resource* output,
    ID3D12Resource* depth,
    ID3D12Resource* motion,
    bool reset) {
    parameters->Set(NVSDK_NGX_Parameter_Color, color);
    parameters->Set(NVSDK_NGX_Parameter_Output, output);
    parameters->Set(NVSDK_NGX_Parameter_Depth, depth);
    parameters->Set(NVSDK_NGX_Parameter_MotionVectors, motion);
    parameters->Set(NVSDK_NGX_Parameter_Jitter_Offset_X, 0.0f);
    parameters->Set(NVSDK_NGX_Parameter_Jitter_Offset_Y, 0.0f);
    parameters->Set(NVSDK_NGX_Parameter_Reset, reset ? 1 : 0);
    parameters->Set(NVSDK_NGX_Parameter_MV_Scale_X, 1.0f);
    parameters->Set(NVSDK_NGX_Parameter_MV_Scale_Y, 1.0f);
    parameters->Set(NVSDK_NGX_Parameter_DLSS_Render_Subrect_Dimensions_Width, kWidth);
    parameters->Set(NVSDK_NGX_Parameter_DLSS_Render_Subrect_Dimensions_Height, kHeight);
    parameters->Set(NVSDK_NGX_Parameter_DLSS_Pre_Exposure, 1.0f);
    parameters->Set(NVSDK_NGX_Parameter_DLSS_Exposure_Scale, 1.0f);
}

}  // namespace

void run_ngx_smoke(
    const wchar_t* runtime_directory,
    ID3D12Device* device,
    ID3D12CommandQueue* queue,
    unsigned int evaluations,
    ngx_smoke_result& result) {
    result.requested = true;
    result.evaluations_requested = evaluations;
    ngx_api api;
    if (!load_ngx_api(api, result.error)) return;
    result.exports_resolved = true;
    ngx_session session;
    session.api = &api;
    session.device = device;

    // This synthetic feasibility probe follows the driver's Init_Ext ABI. It
    // deliberately uses the same non-production probe ID as the community
    // D3D12 bridge probe; production rendering must use a legitimate ProjectID.
    const NVSDK_NGX_Result init_result = api.init_ext(
        kProbeApplicationId,
        runtime_directory,
        device,
        static_cast<int>(NVSDK_NGX_Version_API),
        nullptr);
    result.init_result = static_cast<unsigned long>(init_result);
    if (NVSDK_NGX_FAILED(init_result)) {
        result.error = "NVSDK_NGX_D3D12_Init_Ext probe failed: " + hex_result(result.init_result);
        return;
    }
    session.initialized = true;
    result.initialized = true;

    Sleep(800);
    const NVSDK_NGX_Result allocate_result = api.allocate_parameters(&session.parameters);
    if (NVSDK_NGX_FAILED(allocate_result) || session.parameters == nullptr) {
        result.error = "NVSDK_NGX_D3D12_AllocateParameters failed: " +
            hex_result(static_cast<unsigned long>(allocate_result));
        return;
    }
    result.parameters_allocated = true;
    set_create_parameters(session.parameters);

    command_context commands;
    if (!commands.initialize(device, queue, result.error) || !commands.begin(result.error)) return;
    const NVSDK_NGX_Result create_result = api.create_feature(
        commands.list(),
        static_cast<int>(NVSDK_NGX_Feature_SuperSampling),
        session.parameters,
        &session.feature);
    result.create_result = static_cast<unsigned long>(create_result);
    if (NVSDK_NGX_FAILED(create_result) || session.feature == nullptr) {
        commands.abort();
        result.error = "NVSDK_NGX_D3D12_CreateFeature failed: " + hex_result(result.create_result);
        return;
    }
    if (!commands.submit_and_wait(result.error)) return;
    result.feature_created = true;

    auto color = create_texture(device, DXGI_FORMAT_R8G8B8A8_UNORM, false, result.error);
    auto output = create_texture(device, DXGI_FORMAT_R8G8B8A8_UNORM, true, result.error);
    auto depth = create_texture(device, DXGI_FORMAT_R32_FLOAT, false, result.error);
    auto motion = create_texture(device, DXGI_FORMAT_R16G16_FLOAT, false, result.error);
    if (!color || !output || !depth || !motion) return;

    for (unsigned int index = 0; index < evaluations; ++index) {
        set_evaluate_parameters(
            session.parameters,
            color.get(),
            output.get(),
            depth.get(),
            motion.get(),
            index == 0);
        if (!commands.begin(result.error)) return;
        const NVSDK_NGX_Result evaluate_result = api.evaluate_feature(
            commands.list(), session.feature, session.parameters, nullptr);
        result.last_evaluate_result = static_cast<unsigned long>(evaluate_result);
        if (NVSDK_NGX_FAILED(evaluate_result)) {
            commands.abort();
            result.error = "NVSDK_NGX_D3D12_EvaluateFeature failed: " +
                hex_result(result.last_evaluate_result);
            return;
        }
        if (!commands.submit_and_wait(result.error)) return;
        ++result.evaluations_completed;
    }
}

}  // namespace comfy_dlss
