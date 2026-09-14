#pragma once

// SDK declarations are build-time inputs supplied by the developer, not vendored.
// This TU uses the feature/snippet contract, NOT a reconstructed Parameter vtable.
#ifndef NGX_SNIPPET_BUILD
#error "Build the caller with NGX_SNIPPET_BUILD; do not mix NGX interfaces in one TU"
#endif
#ifdef NVSDK_NGX_H
#error "Include nr_caller.hpp before other NGX headers to keep its declaration mode explicit"
#endif
#ifndef NGX_ENABLE_DEPRECATED_SHUTDOWN
#define NGX_ENABLE_DEPRECATED_SHUTDOWN
#endif
#include <nvsdk_ngx.h>
#include <stdint.h>

#if defined(_WIN32)
#if defined(COMFY_NR_CALLER_BUILD)
#define COMFY_NR_EXPORT __declspec(dllexport)
#else
// The Worker resolves these functions through GetProcAddress; it neither
// imports a caller at load time nor exports an undefined caller declaration.
#define COMFY_NR_EXPORT
#endif
#define COMFY_NR_NOINLINE __declspec(noinline)
#else
#define COMFY_NR_EXPORT __attribute__((visibility("default")))
#define COMFY_NR_NOINLINE __attribute__((noinline))
#endif

namespace comfy_dlss::caller {
inline constexpr uint32_t abi_version = 1;
static_assert(sizeof(NVSDK_NGX_Result) == 4 && sizeof(NVSDK_NGX_Version) == 4);
using init_parameters_fn = decltype(&NVSDK_NGX_D3D12_Init_Ext);
// A distinct ABI: final arguments are feature-info THEN version. Never cast an
// Init_Ext symbol to this type solely because another runtime uses that ordering.
using init_feature_info_fn = NVSDK_NGX_Result (NVSDK_CONV *)(
    unsigned long long, const wchar_t*, ID3D12Device*,
    const NVSDK_NGX_FeatureCommonInfo*, NVSDK_NGX_Version);
using create_fn = decltype(&NVSDK_NGX_D3D12_CreateFeature);
using evaluate_fn = decltype(&NVSDK_NGX_D3D12_EvaluateFeature);
using release_fn = decltype(&NVSDK_NGX_D3D12_ReleaseFeature);
// Explicitly expose the legacy declaration above. This is NOT Shutdown1(device).
using shutdown_fn = decltype(&NVSDK_NGX_D3D12_Shutdown);
} // namespace comfy_dlss::caller

// The host owns module lifetimes, function resolution, arguments, GPU fences and
// serialization. These wrappers never load libraries, allocate or retain state.
// Foreign callees must not throw C++ exceptions across this C ABI.
extern "C" {
COMFY_NR_EXPORT uint32_t NVSDK_CONV ComfyNR_CallerAbiVersion() noexcept;
COMFY_NR_EXPORT NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallInitParameters(
    comfy_dlss::caller::init_parameters_fn, unsigned long long, const wchar_t*,
    ID3D12Device*, NVSDK_NGX_Version, const NVSDK_NGX_Parameter*) noexcept;
COMFY_NR_EXPORT NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallInitFeatureInfo(
    comfy_dlss::caller::init_feature_info_fn, unsigned long long, const wchar_t*,
    ID3D12Device*, const NVSDK_NGX_FeatureCommonInfo*, NVSDK_NGX_Version) noexcept;
COMFY_NR_EXPORT NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallCreate(
    comfy_dlss::caller::create_fn, ID3D12GraphicsCommandList*, NVSDK_NGX_Feature,
    const NVSDK_NGX_Parameter*, NVSDK_NGX_Handle**) noexcept;
COMFY_NR_EXPORT NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallEvaluate(
    comfy_dlss::caller::evaluate_fn, ID3D12GraphicsCommandList*, const NVSDK_NGX_Handle*,
    const NVSDK_NGX_Parameter*, PFN_NVSDK_NGX_ProgressCallback) noexcept;
COMFY_NR_EXPORT NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallRelease(
    comfy_dlss::caller::release_fn, NVSDK_NGX_Handle*) noexcept;
COMFY_NR_EXPORT NVSDK_NGX_Result NVSDK_CONV ComfyNR_CallShutdown(
    comfy_dlss::caller::shutdown_fn) noexcept;
}
