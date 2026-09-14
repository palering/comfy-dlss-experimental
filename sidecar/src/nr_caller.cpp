#define COMFY_NR_CALLER_BUILD
#include "nr_caller.hpp"

using namespace comfy_dlss::caller;

extern "C" {
uint32_t NVSDK_CONV ComfyNR_CallerAbiVersion() noexcept { return abi_version; }

// Keep a real return site inside this DLL: a tail jump would erase this caller
// boundary. The build also disables sibling-call optimization and LTO. Volatile
// is local, not a shared synchronization mechanism or persistent state.
COMFY_NR_NOINLINE NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallInitParameters(
    init_parameters_fn target, unsigned long long app, const wchar_t* path,
    ID3D12Device* device, NVSDK_NGX_Version version, const NVSDK_NGX_Parameter* params) noexcept {
    if (!target) return NVSDK_NGX_Result_FAIL_InvalidParameter;
    volatile NVSDK_NGX_Result result = target(app, path, device, version, params);
    return result;
}

COMFY_NR_NOINLINE NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallInitFeatureInfo(
    init_feature_info_fn target, unsigned long long app, const wchar_t* path,
    ID3D12Device* device, const NVSDK_NGX_FeatureCommonInfo* info, NVSDK_NGX_Version version) noexcept {
    if (!target) return NVSDK_NGX_Result_FAIL_InvalidParameter;
    volatile NVSDK_NGX_Result result = target(app, path, device, info, version);
    return result;
}

COMFY_NR_NOINLINE NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallCreate(
    create_fn target, ID3D12GraphicsCommandList* commands, NVSDK_NGX_Feature feature,
    const NVSDK_NGX_Parameter* params, NVSDK_NGX_Handle** handle) noexcept {
    if (!target) return NVSDK_NGX_Result_FAIL_InvalidParameter;
    volatile NVSDK_NGX_Result result = target(commands, feature, params, handle);
    return result;
}

COMFY_NR_NOINLINE NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallEvaluate(
    evaluate_fn target, ID3D12GraphicsCommandList* commands, const NVSDK_NGX_Handle* handle,
    const NVSDK_NGX_Parameter* params, PFN_NVSDK_NGX_ProgressCallback callback) noexcept {
    if (!target) return NVSDK_NGX_Result_FAIL_InvalidParameter;
    volatile NVSDK_NGX_Result result = target(commands, handle, params, callback);
    return result;
}

COMFY_NR_NOINLINE NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallRelease(
    release_fn target, NVSDK_NGX_Handle* handle) noexcept {
    if (!target) return NVSDK_NGX_Result_FAIL_InvalidParameter;
    volatile NVSDK_NGX_Result result = target(handle);
    return result;
}

COMFY_NR_NOINLINE NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallShutdown(shutdown_fn target) noexcept {
    if (!target) return NVSDK_NGX_Result_FAIL_InvalidParameter;
    volatile NVSDK_NGX_Result result = target();
    return result;
}
}
