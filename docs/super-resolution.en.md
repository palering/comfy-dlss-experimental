# Experimental SR / DLAA

English · [简体中文](super-resolution.zh-CN.md)

Audience: public

**Status: the ComfyUI → Python → CSR1 Worker → official NGX SDK source path is
implemented; an SDK-linked Worker and GPU output still require acceptance.**
The existing NR build does not enable SR. Do not treat successful source/CPU
tests, a valid input plan or a file-readiness report as GPU validation.

A separate [camera-aware Streamline CXR1 backend](streamline-reconstruction.en.md)
has passed bounded Python-to-Worker GPU sequences for SR, DLAA, RR and RR+DLAA.
It is not connected to these nodes: its explicit camera/G-buffer contract and
lifecycle do not validate or silently replace the CSR1/`owned_sr` integration here.
Use the separate [owned_sl reconstruction nodes](reconstruction-nodes.en.md)
for the tested CXR1 route; those nodes do not accept an ordinary VIDEO alone.
For VIDEO with explicit camera/depth, use the new
[Streamline Stage in the unified pipeline](streamline-video-input.en.md).

## Nodes and wiring

```text
Load Video → Video Input Adapter → Input Assembler → SR / DLAA Stage → Pipeline Render → Save Video
              normalize to sRGB       ↑                 ↑                 ↑
                         Flow provider + depth      SR Settings      owned_sr Runtime
```

Use one SR/DLAA stage on a fresh input pipeline. SR Settings selects the feature
mode; SR Stage declares output dimensions; Pipeline Render controls only the
output time range and must keep its percentage scale at **100**. DLAA keeps source
size; SR uses the explicit larger target. The Worker, not a resize filter,
produces that target. The resulting VIDEO connects to native Save Video.

The [experimental workflow](../example_workflows/dlss_sr_experimental.json) is a
wiring template, **not an immediately runnable example**: supply a matching
numeric-depth manifest and an SDK-enabled `owned_sr` preset first.

**SR Input Plan remains a non-rendering inspector.** It accepts SR Settings plus
either a sequence or an assembled pipeline. It does not decode frames, estimate
flow, initialize NGX, read every guide tensor or launch a Worker. Its
`runnable: false` means the report itself is not execution or runtime acceptance.
It is not the node that produces VIDEO; connect SR Stage to Pipeline Render.

| Control | Meaning |
| --- | --- |
| Mode | DLAA keeps actual source dimensions. Quality/Balanced/Performance/Ultra Performance request SR quality modes, not fixed scaling factors or NR styles. |
| Stage output width/height | Explicit larger SR target, preserving the exact aspect ratio. DLAA ignores these fields and keeps source size. |
| Preset | Model default, or SDK hints J/K/L/M. Availability and behavior depend on the runtime; start with default. |
| Reversed-Z depth | Declaration of real device-depth encoding, not a depth-image inversion or conversion switch. |
| NGX automatic exposure | Keep enabled for the current media executor. Manual exposure metadata is represented in the low-level contract but not supplied by this media path. |
| Source jitter policy | Use finished-video zero jitter. Actual-render metadata remains a contract option, not an implemented media input. |
| Motion contains render jitter | Keep disabled for this media executor. Do not invent sampling offsets in finished video. |
| Declare HDR input | Not supported by the current executor; enabling it fails explicitly before GPU execution. |

Shared validation now uses maximum-side and pixel budgets; see
[Streamline dimensions](streamline-video-input.en.md). Legacy CSR1 Workers retain
their original landscape handshake bounds. Original-grid portrait acceptance is
specific to CXR1, not independent CSR1 GPU proof. Software budgets are not GPU
capability claims; the SDK query must still accept the quality mode and size.

## Required reconstructed inputs

| Input | Transport | Required meaning |
| --- | --- | --- |
| Color | Little-endian RGBA16F | Current media path: SDR pixels explicitly normalized to the sRGB working transfer. FP16 alone does not mean linear or HDR. |
| Motion | Little-endian RG16F | Current-to-previous displacement in input pixels, top-left XY. DIS, NVIDIA flow or matching external flow can supply estimates. |
| Depth | Little-endian R32F | Finite device depth [0, 1], actual forward/reversed-Z encoding and render-depth or calibrated-projection provenance. |
| Frame metadata | Numeric metadata | Same frame's PTS, interval and history-reset flag; zero jitter and automatic exposure in this media path. |

Depth is **required**. Connect an External Numerical Guide to the assembler's
depth input, using `device_z`, matching the source hash, view, input grid and
timestamps—including pre-roll frames. A PNG visualization, white clay render or
relative monocular-depth map is not device depth. Relabeling one does not perform
the required calibration. See [external guide format](external-guides.en.md).

Motion may use the selected DIS/NVIDIA provider or a validated external guide.
Zero motion remains diagnostic, not a substitute for useful motion on moving
video. All planes must refer to the same frame. First frame, cuts and sequence
discontinuities reset history. Normals, materials and ray-tracing buffers are not
requirements here; this is **not Ray Reconstruction**.

Finished video lacks independent subpixel-jittered render samples. Optical flow
cannot recreate those samples or guarantee correct disocclusion/transparency
motion. Valid inputs therefore do not establish game-equivalent SR quality.

## Runtime files and build

SR uses the **same project Worker executable**, built with the official SDK, but
a separate `owned_sr` preset and CSR1 protocol. It does not use NR's thin caller.

```text
node user-data directory/
  runtime-presets/owned-sr.json
  components/owned-sr/
    comfy-dlss-worker.exe        our SDK-enabled Windows executable
    nvngx_dlss.dll               user-supplied SR model/runtime
```

Copy [the SR preset](../examples/runtime-presets/owned-sr.example.json) to the
shown preset location and set explicit paths. `compatibility.project_id` must
be a canonical nonzero UUID identifying the integration; it is not a GPU-support
certificate. Runtime selection checks file hashes; CSR1 then confirms that the
selected Worker actually includes SR. The default Zig NR build reports SR as
not compiled, instead of silently falling back to NR.

For developers with an existing Windows x64 MSVC toolchain and official SDK:

```powershell
cmake -S sidecar -B sidecar/build-sdk -A x64 -DCOMFY_NGX_SDK_ROOT=C:/path/to/NVIDIA-DLSS
cmake --build sidecar/build-sdk --config Release
ctest --test-dir sidecar/build-sdk -C Release --output-on-failure
```

The Worker target is `comfy-dlss-worker`, output
`sidecar/build-sdk/Release/comfy-dlss-worker.exe`. The `caller` target outputs
`sidecar/build-sdk/caller/Release/nvngx.dll` for NR only. Default SDK archive:
`lib/Windows_x86_64/x64/nvsdk_ngx_d.lib`; override `COMFY_NGX_SDK_LIBRARY` when
needed. `_d` denotes the dynamic CRT; this build uses `/MD`, not debug CRT.

The SDK archive needs compatible MSVC runtime/startup libraries. C++ compile-only
success with Zig is not a successfully linked SDK Worker; the existing minimal
Zig NR toolchain does not supply those libraries. A manual
[SDK Worker build workflow](../.github/workflows/build-sdk-worker.yml) is provided
for a Windows build environment. Its presence is not a published binary or a
completed build/GPU run. It does not bundle NVIDIA DLLs, SDK libraries or models.
Its development ZIP is not an `install.py` helper bundle: verify its checksum
and provenance, then place the explicitly selected Worker at the preset path.
The `/MD` build also needs a compatible Microsoft Visual C++ x64 runtime in its
execution environment; Windows or a Proton prefix must provide it separately.

SDK declarations are based on [NVIDIA's official DLSS repository](https://github.com/NVIDIA/DLSS).
The project does not redistribute proprietary runtimes. Do not replace an NR
model slot with `nvngx_dlss.dll`; keep NR and SR bindings separate.

## Execution and acceptance limits

- Windows launches the PE directly; Linux uses the selected Proton. This is not
  a native Linux SR executor, even if upstream SDKs also offer Linux components.
- SR currently requires **isolated/release-after-task** mode. No resident SR
  session reuse or in-node SR A/B preview is implemented.
- One-frame-at-a-time streaming bounds decoded data and intermediate memory;
  no full-video raw color/motion/depth cache is created. The retain-prepared-cache
  toggle applies to NR, not SR. Encoded output and task records still use disk.
- History pre-roll can feed earlier frames without exporting them; NR warmup
  repetition is not applied to SR. Output start/duration remain separate controls.
- HDR, manual exposure, external render jitter, mixed NR/SR stacks, repeated SR
  stages, FG and Ray Reconstruction are rejected or unavailable, not emulated.
- Source, contract and mocked transport tests do not establish Windows or
  Linux/Proton SR GPU acceptance, output quality or long-video stability.

Existing [owned NR workflows](owned-runtime.en.md) remain independent and do not
need an SR download or an SDK build.
