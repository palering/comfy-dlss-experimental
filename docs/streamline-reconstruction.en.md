# Streamline reconstruction development backend

English · [简体中文](streamline-reconstruction.zh-CN.md)

Audience: public

## Status and boundaries

The separate CXR1 Worker executes SR, native-resolution DLAA, Ray Reconstruction
(RR), and RR with DLAA resolution mode through the public Streamline interface.
Python sends explicit camera and numerical planes; the Worker owns D3D12,
Streamline, fences and output readback. It does not use the NR caller shim or
link the NGX static import archive. Existing presets, CNR1/CSR1 binaries and
Comfy nodes are not switched automatically.

Controlled Linux/Proton short-sequence tests with supplied SL 2.14.1 binaries
passed actual Python-to-Worker GPU execution for all four modes. Output matched
the corresponding standalone reconstruction probe byte for byte. These tests
do not establish natural-video quality, arbitrary camera/motion correctness,
long-run stability or native-Windows acceptance. The explicit
[reconstruction nodes](reconstruction-nodes.en.md) have separately passed bounded
Comfy Render → SaveVideo tests for all four modes and a one-frame preview output.

This is a development API, not an ordinary VIDEO-to-RR node. RR needs genuine
renderer/material information. Relative monocular depth, RGB normal visualizations,
and optical flow alone do not supply that contract. This implementation neither
estimates camera matrices nor fabricates missing G-buffers.

## Build and runtime

```bash
python3 sidecar/build_sl_worker.py --streamline-include /path/to/streamline/include --native-tests
```

The existing Zig Windows-GNU cross-build produces
`sidecar/build/sl-worker/comfy-dlss-sl-worker.exe`. Native camera, RR and wire tests
run with address/undefined-behavior sanitizers. The build does not download or
package proprietary files and does not install a compiler/sysroot.

Supply an explicit runtime directory containing compatible, user-provided files:
`sl.interposer.dll`, `sl.common.dll`, `NvLowLatencyVk.dll`, `sl.dlss.dll`,
`nvngx_dlss.dll`, `sl.dlss_d.dll`, and `nvngx_dlssd.dll`. The launcher currently
requires the complete SR/RR set, even for an SR-only job. File presence is not
device support or version compatibility. The `owned_sl` preset and file/node
executor verify exact component hashes and use isolated snapshots; raw API
callers must still isolate and pin their runtime explicitly. OTA flags are not enabled.

Use `OwnedWorkerProcess(..., feature="sl", caller=None, project_id=...)` with a
canonical nonzero project UUID. Linux requires an explicit existing Proton
executable, dedicated test prefix and working graphical session. macOS can build
and test contracts but cannot execute this GPU backend. The hidden bookkeeping
window is not a claim of headless/display-server-free operation.

## Python inputs

The host-neutral types live in `comfy_dlss_experimental.sl_contract`; the client
is `sl_worker.ReconstructionClient`, returned as the process owner's `client`.

| Input | Required representation |
| --- | --- |
| Settings | Explicit input/output extents, `feature="sr"` or `"rr"`, quality mode or `"dlaa"`; exact aspect ratio, no downscale |
| Color | Packed little-endian RGBA16F; declared `srgb` or `linear_sdr`; RR requires `linear_sdr` |
| Motion | Packed RG16F, current-to-previous input-pixel XY motion, top-left convention, including camera motion; no jitter included |
| Depth | Packed R32F finite device depth in [0,1], matching the declared inverted-depth setting |
| Camera | Four row-major, unjittered 4×4 matrices: view→clip and inverse, current clip→previous clip and inverse; position, orthonormal axes and projection parameters |
| Frame metadata | Actual jitter in input pixels, motion scales, pre-exposure, exposure scale, RR exposure and reset; no invented jitter for `unjittered_video` |
| RR extras | Full-input-resolution linear diffuse/specular RGBA16F albedos, unit XYZ normal + linear roughness in RGBA16F, RG16F reflection motion, inverse world↔view matrices |

RR uses its default preset and an explicit positive 1×1 exposure value. The SR
route uses auto exposure and requires the otherwise-unused exposure value to
remain 1. HDR, jitter-containing motion, specular-hit-distance alternatives and
additional optional RR buffers are not supported by this first contract.

Caller sequence:

```python
client.create(settings, session_id=1)
output_rgba16f = client.process(
    color_rgba16f, motion_rg16f, device_depth_r32f, pts_ns,
    camera=camera_frame, metadata=frame_metadata, rr=rr_guides_or_none,
)
client.end()       # End this task; the next task's first frame resets history.
client.release()   # Release the feature before replacing its settings.
owner.shutdown()  # Close the transport and reap only owned child processes.
```

Always close the process owner in a `finally` block, including on invalid input,
transport failure or cancellation. `process()` returns raw output pixels, not
a VIDEO object or encoded file. A caller must provide matching source metadata,
consume one output at a time, and implement output encoding/storage separately.
Caller-supplied plane lengths and metadata are checked in Python; the native
engine independently checks sizes, finite values and guide semantics before
upload. Neither check proves that a declaration matches the original renderer.

## CXR1 wire and lifecycle

All fields are little-endian. The 32-byte header is `<4I2Q>`: magic `0x31525843`,
version 1, message type, payload length, ordered request ID and session ID.
Authentication, one-session/one-inflight control messages and CER1 typed errors
follow the owned transport structure, but CXR1 never accepts a CNR1/CSR1 header.
CER1 currently reports Streamline failures in the Worker domain, not as NGX codes.

CREATE is 40 bytes: the validated 32-byte SR settings representation, a feature
ID (1=SR, 2=RR), and jitter-policy flag (0=unjittered, 1=external actual metadata).
HDR and jittered-motion flags are rejected. Capabilities advertise compiled
SR|DLAA|RR bits, not current adapter support; initialization checks the device.

FRAME consists of `<QII7fI>` metadata (48 bytes), `<80fI>` camera data (324 bytes),
color, motion and depth. The seven floats are jitter XY, motion-scale XY,
pre-exposure, exposure scale and exposure; both reserved integers must be zero.
RR appends 32 matrix floats then diffuse, specular, normal/roughness and specular
motion planes. Frame lengths are exactly `372 + 16*input_pixels` for SR or
`500 + 44*input_pixels` for RR. RESULT is the original uint64 PTS followed by
output RGBA16F. PTS transport does not imply that SL consumes NGX's frame-time
parameter; that parameter is not exposed by this API.

Use the [orientation-neutral budgets](streamline-video-input.en.md): input minimum
side 64, maximum side 1920 and 2073600 pixels; output maximum side 3840 and
8294400 pixels. Message payload
at most 128 MiB, one million evaluations per session, and strictly increasing
nonnegative signed-64-bit-range PTS within each task. DLAA requires identical
input/output dimensions; SR requires larger output. The runtime's optimal-size
query remains authoritative for the chosen quality mode.

First frames and explicit cuts reset history. RR explicitly frees/recreates its
feature history at a cut: the tested reset bit alone did not fully isolate the
prior RR history. END retains the engine but resets task PTS; RELEASE drops it.
Bounded tests also covered END reuse and release/recreate for SR-mode DLAA and RR.
GPU work is fenced before readback/reuse; an unverified fence or cleanup failure
terminates this Worker instead of unwinding owners of potentially in-flight work.

## FG and MFG are separate

`fg_contract.py` defines a bounded timestamp/scene-cut timeline and explicit
hardware-capability gates. It is not a frame-generation renderer or a Comfy node.
A controlled Present-capture experiment obtained real intermediate images; the
tested configuration required a focused window. General frame/PTS association,
dropped-frame handling and background execution are not yet accepted.
It is not part of CXR1. Unsupported MFG is rejected rather
than synthesized by repeatedly applying ordinary FG or another interpolator.

### FG window focus and pause options

**The currently tested FG Present-capture path requires the Worker window on
the GPU host to remain foreground and focused.** This does not mean keeping the
ComfyUI browser selected. The tested runtime disables interpolation on focus
loss (`DLSS-G disabled: window not focused`). This is an observed restriction
of this runtime/path, not a proven hardware or algorithmic necessity. NR and
the tested SR/DLAA/RR reconstruction paths do not require foreground focus;
the latter still use a hidden window and a graphical session.

The bounded FG experiment requests focus on startup, checks it before each
input frame, and aborts if acquisition fails or focus is lost. At cleanup it
attempts to restore the previous window only if the FG window still owns focus
and the previous window still exists. It does not repeatedly steal focus.
Requesting focus is not guaranteed to succeed; being visible or topmost is not
the same as owning foreground focus. Windows restricts
[SetForegroundWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow);
Linux/Proton also needs acceptance in the actual graphical session.

The [official DLSS-G guide, section 6.4](https://github.com/NVIDIA-RTX/Streamline/blob/main/docs/ProgrammingGuideDLSS_G.md#64-disabling-frame-generation)
defines `DLSSGOptions::mode = DLSSGMode::eOff` as disabling generation, normally
freeing its resources. Adding `DLSSGFlags::eRetainResourcesWhenOff` in `flags`
keeps those resources allocated while generation is off, reducing reallocation
cost when re-enabled. It is not a mode and does not bypass focus checks or keep
generating frames in the background. Long-term deactivation with this flag
requires explicit resource release after disabling the feature.

A future pause/resume implementation could use these options, but must also
pause input consumption and output timeline advancement, then re-prime temporal
history and verify generated-frame association on resume. That behavior and a
dedicated graphical-session workaround have not been implemented or accepted.
Do not silently encode missing generated frames as a successful FG result.

Current diagnostics use `FG_FOREGROUND_REQUIRED`: the FG capability query emits
an `execution_notice` scoped to `tested_present_capture`, with `focus_checked=false`;
it does not create a window or inspect live focus. The Python contract's
unverified-offline-output error includes the same requirement. These diagnostics
do not register a Comfy FG node or certify background execution.

See [existing SR nodes](super-resolution.en.md), [execution boundaries](execution-boundary.en.md)
and [sidecar diagnostics](../sidecar/README.en.md) for the distinct older paths.
