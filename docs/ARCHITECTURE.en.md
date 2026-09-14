# Architecture

English · [简体中文](ARCHITECTURE.zh-CN.md)

Audience: public

The ComfyUI process owns schemas, input inspection, media preparation, cache keys,
job orchestration and process supervision. It never loads vendor graphics DLLs.
Full-video orchestration now has [host-neutral file entries](execution-boundary.en.md):
Comfy supplies a VIDEO adapter and execution context; the shared executor returns
a file path and report. Preview/UI endpoints remain Comfy-specific.

## Active video path

```text
Comfy video / input adapter
  -> cached color preparation + selected DIS or NVIDIA motion provider
  -> direct_nr.py (D5V2 color/motion adapter)
  -> authenticated loopback TCP (CLR1)
  -> dlss-native-relay.exe
  -> user-supplied nvngx.dll --video Worker + matching nvngx_dlssnr.dll
  -> checked RGBA8 frames -> host encoding -> Preview / standard VIDEO
```

The relay is a process/pipe bridge, not an NGX implementation. The external
Worker is a Windows executable despite its filename; substituting an arbitrary
driver DLL or caller shim does not implement its video protocol.
See [the direct NR contract](direct-nr-relay.en.md).

Explicit `owned_nr` presets select a second implemented route: the same host
media layer -> `OwnedMediaClient` -> authenticated CNR1 -> our C++ Worker -> thin
caller + NR model. It does not use the external relay. Legacy presets are not
changed automatically; see [owned NR](owned-runtime.en.md).

Python handles video decoding, optional SDR working-transfer normalization,
optical flow, scene-cut resets, pre-roll, audio, encoding and atomic output.
Small inputs use quota-bounded disk caches. Larger ranges scan timing and then
stream decode/flow/NR/encoding with one input frame in flight. Look changes can
reuse retained small entries; large streamed ranges recompute guides.
See [storage and limits](STORAGE.en.md).

An optional `DLSS NR Pass Stack` runs one to three stages over the same prepared
input. A later stage consumes the prior uncompressed RGBA result; all stages reuse
source motion/timestamps/cut flags, reset independent history, and encode only the
final output. This is a host-orchestrated experimental cascade; it does not implement
SR/FG. Streaming uses one independent Worker per layer; small cached ranges retain
the sequential whole-pass implementation. See [multi-pass NR](MULTI_PASS_NR.en.md).

Host media tools are separate from Proton. Input Adapter paths travel with the
sequence into task-local execution, without global PATH mutation. Prepared caches
include tool identity. PyAV and external command versions are reported separately;
see [media tools](media-tools.en.md).

## Internal execution boundaries

Both cached and streaming NR paths use `processing_plan.py` to lower existing
Look/Stack payloads into ordered feature stages. `backend_contracts.py` describes
the implemented `external_d5v2_nr` wire contract: color and motion in, same-size
RGBA8 out, one result per submitted frame with its timestamp preserved. This is
a static implementation description, not a GPU/driver/readiness probe. The ID is
internal; existing runtime presets and output socket types retain their meanings.

The legacy adapter preserves pass inheritance, disabled/zero-mix stages, guide
reuse and report fields. Existing profile validation, history handling and Worker
execution remain responsible for their original checks; this does not implement SR/FG.

The optional new path adds Flow Selector, Input Assembler, NR Stage, Pipeline
Preview and Pipeline Render. `media_pipeline.py` carries detached recipes with an
opaque source VIDEO, lowers NR stages to the same executor, and preserves branch
isolation. No stage materializes the full video or encodes an intermediate file.
Selected flow and header/contract checks are visible in node cards; actual content,
time and GPU checks are deferred rather than claimed complete. See
[pipeline wiring and limits](media-pipeline.en.md).

`guide_providers.py` constructs only the chosen estimator and defines its
estimate/reset/close boundary. DIS/NVIDIA settings remain serializable without
creating an estimator. The temporal layer still owns resizing, pixel-unit
conversion, cut detection and motion packing. Zero-motion mode creates no
estimator; NVIDIA failures do not silently fall back to DIS.

## Platform boundary

- Windows launches the PE relay and Worker directly.
- Linux uses one selected user-installed Proton and an isolated prefix.
  The active NR route requires Steam's client directory and Xwayland.
- macOS is a development/test host, not an NR execution host.
- Runtime platform selection is validated against the actual Comfy server.
  Windows does not run Linux discovery, display probing or /proc inspection.
- The optional NVIDIA optical-flow helper is native to the host: Linux ELF or
  Windows PE. Its driver calls do not go through Proton or Comfy Python.

Automatic Linux display selection uses Xwayland even on a Wayland desktop.
Native Wayland remains diagnostic-only; native Linux NR is an explicitly
rejected placeholder. See [platform installation](distribution.en.md).

## Runtime ownership and lifecycle

Runtime presets bind exact files, configuration and component hashes.
Snapshots keep per-run files separate from source and original components.
DLL, Proton, dimensions or NR-header parameter changes cannot hot-swap an
already initialized NR instance.

Default policy is isolated: release after the task. Opt-in residency lazily
starts a compatible worker/model pair, serializes tasks, resets history between
tasks, and retains it until idle expiry, explicit release or incompatibility.
Reuse is restricted to a verified pair of content hashes. Broken streams are
discarded. [Lifecycle and monitoring](preview-performance.en.md) describe limits.

A worker crash fails its job rather than loading failed native state into
Comfy. Windows Job Objects and Linux marked-process cleanup bound ownership;
unrelated Wine processes must never be killed by name. A subprocess or Proton
prefix is a crash boundary, **not a security sandbox for untrusted binaries**.

### Worker/shim refactoring boundary

The active external `nvngx.dll --video` is a complete console Worker, not the
thin forwarding DLL meant by Zonnery's `caller/nvngx.dll`. A project-owned backend
will split them explicitly: `comfy-dlss-worker.exe` owns devices, resources,
protocol, and feature lifecycle; an optional `caller/nvngx.dll` forwards only
typed Init/Create/Evaluate/Release calls and is enabled only for a runtime that
actually enforces caller validation. The current D5V2 backend remains available
during migration without silently changing old presets. See [runtime roles](RUNTIME_ROLES.en.md).

The project-owned [caller forwarding component](caller-shim.en.md) builds
separately, has portable ABI-forwarding tests and is used only by explicitly
selected `owned_nr`. It is not a replacement executable for the legacy Worker.

## Media contracts and unsupported capabilities

Active D5V2 accepts RGBA8 color and current-to-previous RG16F motion. Optical
flow estimates image displacement; it is not ground-truth engine motion.
Depth, normals, materials, arbitrary masks and exposure textures cannot be
passed through this Worker interface. Optional [SR source execution](super-resolution.en.md)
uses its own CSR1 and SDK build; default NR builds do not enable it. FG and HDR
processing are not implemented. Exposing an experimental Look field does not guarantee that
every Worker/model pair responds visually.

The retained ReShade carrier, NGX bootstrap experiments, and
`sidecar/protocol/frame-stream-v1.en.md` are separate diagnostics.
That multi-plane protocol validates transport/D3D12 copies; it is not the
active D5V2 protocol, and its depth/mask planes are not active NR inputs.
A future supported backend must explicitly adapt the semantic media contract,
validate its capabilities and reject unsupported inputs.
