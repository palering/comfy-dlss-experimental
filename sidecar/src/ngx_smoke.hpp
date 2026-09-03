#pragma once

#include <d3d12.h>

#include <string>

namespace comfy_dlss {

struct ngx_smoke_result final {
    bool requested = false;
    bool exports_resolved = false;
    bool initialized = false;
    bool parameters_allocated = false;
    bool feature_created = false;
    unsigned long init_result = 0;
    unsigned long create_result = 0;
    unsigned long last_evaluate_result = 0;
    unsigned int evaluations_requested = 0;
    unsigned int evaluations_completed = 0;
    std::string error;

    [[nodiscard]] bool ok() const noexcept {
        return requested && exports_resolved && initialized && parameters_allocated &&
            feature_created && evaluations_completed == evaluations_requested;
    }
};

#if defined(COMFY_DLSS_ENABLE_NGX)
void run_ngx_smoke(
    const wchar_t* runtime_directory,
    ID3D12Device* device,
    ID3D12CommandQueue* queue,
    unsigned int evaluations,
    ngx_smoke_result& result);
#endif

}  // namespace comfy_dlss
