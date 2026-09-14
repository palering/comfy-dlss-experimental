#pragma once
#include "nr_parameters.h"

namespace comfy_dlss {
// Acceptance host uses full-size, zero-origin Color/MVec/Output textures.
// Explicit extents matter: the selected NR runtime defaults missing MVec
// subrect fields to zero. Do not infer valid regions from resource dimensions.
inline void set_nr_full_frame_subrects(ComfyNRParameters* parameters,
                                      uint32_t width, uint32_t height) {
    struct fields { const char* x; const char* y; const char* width; const char* height; };
    constexpr fields regions[]{
        {"DLSSNR.ColorSubrectBaseX", "DLSSNR.ColorSubrectBaseY", "DLSSNR.ColorSubrectWidth", "DLSSNR.ColorSubrectHeight"},
        {"DLSSNR.MVecSubrectBaseX", "DLSSNR.MVecSubrectBaseY", "DLSSNR.MVecSubrectWidth", "DLSSNR.MVecSubrectHeight"},
        {"DLSSNR.OutputSubrectBaseX", "DLSSNR.OutputSubrectBaseY", "DLSSNR.OutputSubrectWidth", "DLSSNR.OutputSubrectHeight"},
    };
    for (const auto& region : regions) {
        ComfyNR_SetU32(parameters, region.x, 0);
        ComfyNR_SetU32(parameters, region.y, 0);
        ComfyNR_SetU32(parameters, region.width, width);
        ComfyNR_SetU32(parameters, region.height, height);
    }
}
}
