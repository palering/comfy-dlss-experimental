#include "nr_parameters.h"
#include "nr_input_contract.hpp"
#include <nvsdk_ngx_params.h>

// Compile this consumer separately with the same Microsoft ABI as NVIDIA's
// interface, then link it to the MinGW host through a plain C function.
// No GPU/model or private hard-coded vtable offsets are used.
extern "C" uint32_t ComfyNR_ParameterAbiCheck() {
    auto* store = ComfyNR_ParametersCreate();
    if (!store) return 1;
    auto* p = static_cast<NVSDK_NGX_Parameter*>(ComfyNR_ParameterInterface(store));
    uint32_t errors = 0;
    unsigned long long ull = 0;
    unsigned int ui = 0;
    int i = 0;
    float f = 0;
    double d = 0;
    alignas(16) char marker = 0;
    void* vp = nullptr;
    ID3D11Resource* r11 = nullptr;
    ID3D12Resource* r12 = nullptr;
    p->Set("ull", 18446744073709551615ULL);
    p->Set("ui", 4000000000U);
    p->Set("i", -123);
    p->Set("f", .25f);
    p->Set("d", .125);
    p->Set("vp", static_cast<void*>(&marker));
    p->Set("r11", static_cast<ID3D11Resource*>(static_cast<void*>(&marker)));
    p->Set("r12", static_cast<ID3D12Resource*>(static_cast<void*>(&marker)));
    const auto success = NVSDK_NGX_Result_Success;
    if (p->Get("ull", &ull) != success || ull != 18446744073709551615ULL) errors |= 2;
    if (p->Get("ui", &ui) != success || ui != 4000000000U) errors |= 4;
    if (p->Get("i", &i) != success || i != -123) errors |= 8;
    if (p->Get("f", &f) != success || f != .25f) errors |= 16;
    if (p->Get("d", &d) != success || d != .125) errors |= 32;
    if (p->Get("vp", &vp) != success || vp != &marker) errors |= 64;
    if (p->Get("r11", &r11) != success || static_cast<void*>(r11) != &marker) errors |= 128;
    if (p->Get("r12", &r12) != success || static_cast<void*>(r12) != &marker) errors |= 256;
    p->Reset();
    if (p->Get("ull", &ull) == success || ComfyNR_ParametersStatus(store) != success) errors |= 512;
    comfy_dlss::set_nr_full_frame_subrects(store, 640, 360);
    // Independently spell the required contract; exercise reads through the
    // SDK virtual interface, not only the host's C setters.
    const char* origins[]{"DLSSNR.ColorSubrectBaseX", "DLSSNR.ColorSubrectBaseY",
        "DLSSNR.MVecSubrectBaseX", "DLSSNR.MVecSubrectBaseY",
        "DLSSNR.OutputSubrectBaseX", "DLSSNR.OutputSubrectBaseY"};
    const char* widths[]{"DLSSNR.ColorSubrectWidth", "DLSSNR.MVecSubrectWidth", "DLSSNR.OutputSubrectWidth"};
    const char* heights[]{"DLSSNR.ColorSubrectHeight", "DLSSNR.MVecSubrectHeight", "DLSSNR.OutputSubrectHeight"};
    for (auto* key : origins) if (p->Get(key, &ui) != success || ui != 0) errors |= 1024;
    for (auto* key : widths) if (p->Get(key, &ui) != success || ui != 640) errors |= 1024;
    for (auto* key : heights) if (p->Get(key, &ui) != success || ui != 360) errors |= 1024;
    if (ComfyNR_ParametersStatus(store) != success) errors |= 1024;
    ComfyNR_ParametersDestroy(store);
    return errors;
}
