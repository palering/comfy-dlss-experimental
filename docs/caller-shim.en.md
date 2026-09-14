# Project-owned caller shim (development only)

English · [简体中文](caller-shim.zh-CN.md)

Audience: public

`sidecar/src/nr_caller.cpp` implements a small stateless forwarding DLL. It is
**not the active video Worker**, not a model, and not a drop-in replacement for
Video Converter's executable named `nvngx.dll` or another project's caller ABI.
Legacy presets do not select it. The experimental [owned_nr runtime](owned-runtime.en.md)
does, and helper bundles may explicitly include it with `--include-owned`.
This does not imply a published binary release or an interchangeable caller ABI.

## Responsibilities

The host supplies correctly typed function pointers and owns module lifetimes,
parameter storage, device/queue/resource state, synchronization, feature lifecycle,
model selection and task transport. The shim retains none of these. It returns
the callee's result unchanged; a null function pointer returns InvalidParameter
without calling anything or modifying output storage. No exports patch DLL bytes,
resolve symbols, create threads, load models or implement a Parameter vtable.

| Export | Contract |
| --- | --- |
| `ComfyNR_CallerAbiVersion` | Returns project caller ABI version 1. |
| `ComfyNR_CallInitParameters` | Init with `version, const Parameter*` as the last two arguments. |
| `ComfyNR_CallInitFeatureInfo` | Separate Init ABI with `const FeatureCommonInfo*, version`. |
| `ComfyNR_CallCreate` | Snippet CreateFeature contract, including const Parameter input. |
| `ComfyNR_CallEvaluate` | EvaluateFeature with handle, parameters and optional progress callback. |
| `ComfyNR_CallRelease` | ReleaseFeature for one supplied handle. |
| `ComfyNR_CallShutdown` | Legacy no-argument Shutdown, not Shutdown1(device). |

Official SDK declarations provide the core types and callable signatures. The
two Init layouts are deliberately separate: a model export's filename/name
alone does not establish which ABI it uses. A backend must verify the exact
runtime contract before binding it; no heuristic selection is implemented.
Unknown NR Parameter keys or private model ABI are not made official by this shim.

Forwarding wrappers are non-inlined, keep a post-call result operation and build
without sibling-call optimization/LTO. This preserves a return address within
the shim instead of a tail jump. It does **not** prove that a model's caller
validation accepts it. Callees/callbacks must not throw exceptions across the C
boundary. Build-time checks do not establish runtime lifetime correctness.

## Build and validate

Only developers need the official NVIDIA DLSS SDK headers and an existing Zig
installation. Headers are supplied separately and not copied into this repository.
No NGX static library, MSVC host or llvm-mingw installation is used.

```bash
NGX_SDK_INCLUDE=/path/to/NVIDIA-DLSS/include bash sidecar/build_caller.sh
NGX_SDK_INCLUDE=/path/to/NVIDIA-DLSS/include bash sidecar/build_caller_test.sh
```

The first produces the Windows x86-64 DLL `sidecar/build/caller/nvngx.dll`.
The second uses the host's `clang++` (or `CXX`) for fake-target argument, result,
callback and null-target tests under AddressSanitizer/UndefinedBehaviorSanitizer.
It loads no model and uses no GPU. Build/cache/test products stay under ignored
`sidecar/build/`; do not upload them as source or copy them over user DLLs.

Local validation has covered sanitized forwarding tests, PE export inspection
and non-tail-call disassembly. Actual model Init/Create/Evaluate acceptance,
Windows/Proton execution and a project-owned video Worker are **not completed by
this component**. See [runtime roles](RUNTIME_ROLES.en.md) and
[C++ quality constraints](sidecar-cpp-quality.en.md).

The separate [owned acceptance host](owned-worker.en.md) supplies parameter
storage and D3D12 lifecycle for target testing; these responsibilities do not
move into the shim.
