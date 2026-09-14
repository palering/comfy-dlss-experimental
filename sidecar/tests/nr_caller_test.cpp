#include "nr_caller.hpp"
#include <cassert>
#include <type_traits>

// Test-only completion of an opaque type: the shim never inspects its layout.
struct NVSDK_NGX_Handle { unsigned int test_value = 0; };

namespace {
constexpr auto success = NVSDK_NGX_Result_Success;
constexpr auto failure = NVSDK_NGX_Result_FAIL_InvalidParameter;
constexpr unsigned long long app_id = 42;
const wchar_t path[] = L"caller-test";
NVSDK_NGX_Handle handle{};
NVSDK_NGX_FeatureCommonInfo info{};
unsigned int calls = 0;
bool progress = false;

NVSDK_NGX_Result NVSDK_CONV init_parameters(unsigned long long app, const wchar_t* p,
    ID3D12Device* dev, NVSDK_NGX_Version ver, const NVSDK_NGX_Parameter* params) {
    assert(app == app_id && p == path && !dev && ver == NVSDK_NGX_Version_API && !params);
    ++calls; return success;
}
NVSDK_NGX_Result NVSDK_CONV init_info(unsigned long long app, const wchar_t* p,
    ID3D12Device* dev, const NVSDK_NGX_FeatureCommonInfo* inf, NVSDK_NGX_Version ver) {
    assert(app == app_id && p == path && !dev && inf == &info && ver == NVSDK_NGX_Version_API);
    ++calls; return failure;
}
void callback(float amount, bool& cancel) { assert(amount == .5f); progress = true; cancel = true; }
NVSDK_NGX_Result NVSDK_CONV create(ID3D12GraphicsCommandList* cmd, NVSDK_NGX_Feature feature,
    const NVSDK_NGX_Parameter* params, NVSDK_NGX_Handle** out) {
    assert(!cmd && feature == NVSDK_NGX_Feature_SuperSampling && !params && out);
    *out = &handle; ++calls; return success;
}
NVSDK_NGX_Result NVSDK_CONV evaluate(ID3D12GraphicsCommandList* cmd, const NVSDK_NGX_Handle* h,
    const NVSDK_NGX_Parameter* params, PFN_NVSDK_NGX_ProgressCallback cb) {
    assert(!cmd && h == &handle && !params && cb == &callback);
    bool cancel = false; cb(.5f, cancel); assert(cancel); ++calls; return failure;
}
NVSDK_NGX_Result NVSDK_CONV release(NVSDK_NGX_Handle* h) { assert(h == &handle); ++calls; return success; }
NVSDK_NGX_Result NVSDK_CONV shutdown() { ++calls; return failure; }
static_assert(std::is_same_v<decltype(&create), comfy_dlss::caller::create_fn>);
static_assert(std::is_same_v<decltype(&init_parameters), comfy_dlss::caller::init_parameters_fn>);
static_assert(std::is_same_v<decltype(&evaluate), comfy_dlss::caller::evaluate_fn>);
}

int main() {
    assert(ComfyNR_CallerAbiVersion() == 1);
    assert(ComfyNR_CallInitParameters(init_parameters, app_id, path, nullptr, NVSDK_NGX_Version_API, nullptr) == success);
    assert(ComfyNR_CallInitFeatureInfo(init_info, app_id, path, nullptr, &info, NVSDK_NGX_Version_API) == failure);
    NVSDK_NGX_Handle* output = nullptr;
    assert(ComfyNR_CallCreate(create, nullptr, NVSDK_NGX_Feature_SuperSampling, nullptr, &output) == success);
    assert(output == &handle);
    assert(ComfyNR_CallEvaluate(evaluate, nullptr, output, nullptr, callback) == failure && progress);
    assert(ComfyNR_CallRelease(release, output) == success);
    assert(ComfyNR_CallShutdown(shutdown) == failure);
    assert(calls == 6);
    assert(ComfyNR_CallInitParameters(nullptr, app_id, path, nullptr, NVSDK_NGX_Version_API, nullptr) == failure);
    assert(ComfyNR_CallInitFeatureInfo(nullptr, app_id, path, nullptr, &info, NVSDK_NGX_Version_API) == failure);
    assert(ComfyNR_CallCreate(nullptr, nullptr, NVSDK_NGX_Feature_SuperSampling, nullptr, &output) == failure);
    assert(output == &handle); // Null target must not silently modify caller-owned data.
    assert(ComfyNR_CallEvaluate(nullptr, nullptr, output, nullptr, callback) == failure);
    assert(ComfyNR_CallRelease(nullptr, output) == failure);
    assert(ComfyNR_CallShutdown(nullptr) == failure);
    assert(calls == 6);
}
