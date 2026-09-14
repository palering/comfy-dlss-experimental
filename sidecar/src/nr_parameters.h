#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif
// No C++ object layout crosses the host boundary. Only ParameterInterface may
// be passed to NGX, and only opaquely: the MinGW host must not call its vtable.
typedef struct ComfyNRParameters ComfyNRParameters;
ComfyNRParameters* ComfyNR_ParametersCreate(void);
void ComfyNR_ParametersDestroy(ComfyNRParameters*);
void* ComfyNR_ParameterInterface(ComfyNRParameters*);
uint32_t ComfyNR_ParametersStatus(const ComfyNRParameters*);
void ComfyNR_ParametersReset(ComfyNRParameters*);
void ComfyNR_SetU32(ComfyNRParameters*, const char*, uint32_t);
void ComfyNR_SetI32(ComfyNRParameters*, const char*, int32_t);
void ComfyNR_SetU64(ComfyNRParameters*, const char*, uint64_t);
void ComfyNR_SetF32(ComfyNRParameters*, const char*, float);
void ComfyNR_SetF64(ComfyNRParameters*, const char*, double);
void ComfyNR_SetResource12(ComfyNRParameters*, const char*, void*);
void ComfyNR_SetPointer(ComfyNRParameters*, const char*, void*);
#ifdef __cplusplus
}
#endif
