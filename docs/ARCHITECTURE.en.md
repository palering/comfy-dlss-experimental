# Architecture

English · [简体中文](ARCHITECTURE.zh-CN.md)

Audience: public

The ComfyUI process owns schemas, input inspection, media preparation, cache keys,
job orchestration and process supervision. It never loads vendor graphics DLLs.

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

Python handles video decoding, optional SDR working-transfer normalization,
optical flow, scene-cut resets, pre-roll, audio, encoding and atomic output.
Preparation is disk-backed and currently sequential with NR and encoding.
Look changes reuse independent prepared-input caches when their keys match.
There is no concurrent chunk scheduler or overlapped decode/NR/encode pipeline.

Host media tools are separate from Proton. Input Adapter paths travel with the
sequence into task-local execution, without global PATH mutation. Prepared caches
include tool identity. PyAV and external command versions are reported separately;
see [media tools](media-tools.en.md).

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

## Media contracts and unsupported capabilities

Active D5V2 accepts RGBA8 color and current-to-previous RG16F motion. Optical
flow estimates image displacement; it is not ground-truth engine motion.
Depth, normals, materials, arbitrary masks and exposure textures cannot be
passed through this Worker interface. SR, FG and HDR processing are not
implemented. Exposing an experimental Look field does not guarantee that
every Worker/model pair responds visually.

The retained ReShade carrier, NGX bootstrap experiments, and
`sidecar/protocol/frame-stream-v1.en.md` are separate diagnostics.
That multi-plane protocol validates transport/D3D12 copies; it is not the
active D5V2 protocol, and its depth/mask planes are not active NR inputs.
A future supported backend must explicitly adapt the semantic media contract,
validate its capabilities and reject unsupported inputs.
