#include "nr_parameters.h"
#include <nvsdk_ngx_params.h>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <limits>
#include <thread>

extern "C" uint32_t ComfyNR_ParameterAbiCheck();
extern "C" void ComfyNR_TestFailNextAllocation();
extern "C" unsigned int ComfyNR_TestOutstandingAllocations();

int main() {
    ComfyNR_TestFailNextAllocation();
    assert(!ComfyNR_ParametersCreate());
    assert(ComfyNR_TestOutstandingAllocations() == 0);
    assert(ComfyNR_ParameterAbiCheck() == 0);
    auto* store = ComfyNR_ParametersCreate();
    assert(store);
    auto* p = static_cast<NVSDK_NGX_Parameter*>(ComfyNR_ParameterInterface(store));
    constexpr auto success = NVSDK_NGX_Result_Success;
    ComfyNR_SetU32(store, "Width", 1920);
    unsigned long long width = 0;
    assert(p->Get("Width", &width) == success && width == 1920);
    ComfyNR_SetF32(store, "Intensity", .25f);
    double d = 0;
    assert(p->Get("Intensity", &d) == success && d == .25);
    int i = 77;
    assert(p->Get("Intensity", &i) != success && i == 77);
    ComfyNR_SetU64(store, "Max", UINT64_MAX);
    unsigned int ui = 12;
    assert(p->Get("Max", &width) == success && width == UINT64_MAX);
    assert(p->Get("Max", &ui) != success && ui == 12);
    ComfyNR_SetF64(store, "Beyond", 18446744073709551616.0);
    assert(p->Get("Beyond", &width) != success && width == UINT64_MAX);
    ComfyNR_SetI32(store, "Negative", -1);
    assert(p->Get("Negative", &width) != success);
    ComfyNR_SetF64(store, "Large", 1e100);
    float f = 4;
    assert(p->Get("Large", &f) != success && f == 4);
    assert(p->Get("Unknown", &f) == NVSDK_NGX_Result_FAIL_UnsupportedParameter);
    alignas(16) int resource = 1;
    ComfyNR_SetResource12(store, "Color", &resource);
    void* pointer = nullptr;
    assert(p->Get("Color", &pointer) == success && pointer == &resource);
    assert(p->Get("Width", &pointer) != success && pointer == &resource);
    assert(p->Get("Color", &width) != success);
    ComfyNR_SetF64(store, "Bad", std::numeric_limits<double>::infinity());
    assert(ComfyNR_ParametersStatus(store) != success);
    ComfyNR_ParametersReset(store);
    assert(ComfyNR_ParametersStatus(store) == success);
    assert(p->Get("Width", &width) != success);
    char name[160]{};
    for (auto& c : name) c = 'x';
    name[159] = '\0';
    ComfyNR_SetU32(store, name, 1);
    assert(ComfyNR_ParametersStatus(store) != success);
    ComfyNR_ParametersReset(store);
    for (unsigned int n = 0; n < 192; ++n) {
        std::snprintf(name, sizeof(name), "Key%u", n);
        ComfyNR_SetU32(store, name, n);
    }
    assert(ComfyNR_ParametersStatus(store) == success);
    ComfyNR_SetU32(store, "Key0", 99); // Replacing a key in a full table is valid.
    assert(ComfyNR_ParametersStatus(store) == success);
    ComfyNR_SetU32(store, "Overflow", 1);
    assert(ComfyNR_ParametersStatus(store) != success);
    ComfyNR_ParametersReset(store);
    const auto exercise = [store, p] {
        for (unsigned int n = 0; n < 1000; ++n) {
            ComfyNR_SetU32(store, "Shared", n);
            unsigned int value = 0;
            assert(p->Get("Shared", &value) == success && value < 1000);
        }
    };
    std::thread a(exercise), b(exercise); a.join(); b.join();
    assert(ComfyNR_ParametersStatus(store) == success);
    ComfyNR_ParametersDestroy(store);
    ComfyNR_ParametersDestroy(nullptr);
    assert(!ComfyNR_ParameterInterface(nullptr));
    assert(ComfyNR_ParametersStatus(nullptr) != success);
    assert(ComfyNR_TestOutstandingAllocations() == 0);
}
