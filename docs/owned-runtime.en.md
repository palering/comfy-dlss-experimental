# Experimental project-owned NR runtime

English · [简体中文](owned-runtime.zh-CN.md)

Audience: public

`owned_nr` explicitly selects our CNR1 Worker. `direct_nr` still selects the old
external D5V2 Worker. Existing presets/workflows are not migrated automatically.
Both existing Preview/Process nodes and the new Pipeline endpoints use this selection.

An opt-in `owned_sr` preset now selects the SDK-backed SR/DLAA source path in the
same Worker. It is separate from the NR setup below: no NR caller, no SR resident
mode, and no SR GPU acceptance yet. See [experimental SR](super-resolution.en.md).

## Files and installation

Start with the normal clone into ComfyUI's `custom_nodes` directory and the existing
[prerequisites](distribution.en.md). This backend does not require ReShade, RenoDX,
the external Video Converter Worker, a relay EXE or systemd.

Our build produces `comfy-dlss-worker.exe` and `caller/nvngx.dll`. The latter is a
thin library, not the same-named external executable or a model. The user supplies
the compatible `nvngx_dlssnr.dll` separately; it is never included in our helper ZIP.

1. Build our Worker/caller using [the build guide](owned-worker.en.md), or install
   an explicitly obtained helper bundle that declares `include_owned: true`.
   This option being implemented does not mean an owned-Worker release is published.
2. Copy [the preset example](../examples/runtime-presets/owned-nr.example.json)
   to the node's user-data `runtime-presets/owned-nr.json` and configure its paths.
   Relative paths resolve against the preset directory, not the Comfy root.
3. Select that preset in Runtime Configuration, with backend `auto` or `owned_nr`.
   Do not overwrite the old default preset or its components.
4. Windows executes our PE directly. Linux selects an installed Proton; the node
   owns dedicated `prefixes/owned-*` directories under its data root. Linux native
   NR remains unavailable. Run Setup Helper for a read-only dependency check;
   file readiness is not GPU/model compatibility acceptance.
5. Reuse [the pipeline example](../example_workflows/dlss_pipeline_preview.json).
   Preview controls its own range; Pipeline Render → Save Video controls final output.

Suggested layout **under the node's user-data directory**, not a system folder:

```text
runtime-presets/owned-nr.json
components/owned-nr/
  comfy-dlss-worker.exe          our executable
  caller/nvngx.dll              our thin library
  nvngx_dlssnr.dll              user-supplied model/runtime
```

An installed helper bundle can instead be referenced as `@bundled/worker` and
`@bundled/caller`. Keep the NR model path explicit. Runtime snapshots hash and copy
all three files before use; stale or changed components are rejected.

## Limits and lifecycle

- SDR media remains RGBA8. The adapter scales numerical values by 1/255 to FP16,
  without an implicit EOTF; finite output is clamped to [0,1] and quantized to
  RGBA8 after each pass. This is **not HDR or an end-to-end FP16 stack**.
- The current owned transport accepts 64–1920 × 64–1080. Actual model support at
  every size is not proven. Unsupported dimensions fail rather than silently resize.
- The old `worker_profile` control must remain at the current compatible value 1;
  other external-Worker profiles are rejected, not ignored. Look controls use CNR1.
- One resident Worker is reused only with matching runtime, dimensions and controls.
  END preserves allocations; the next task explicitly resets history. Changing Look
  currently restarts the managed Worker. Idle expiry is 30–900 seconds; manual
  release, task failure and cancellation retire it. Long tasks or multi-layer streams
  use isolated mode. Streaming layers have separate histories and reuse source motion.
- Linux optical flow stays native even when NR uses Proton. No duplicate Windows
  optical-flow helper is required for this route.

## Build a helper bundle

After building the existing target-specific flow helper, relay, and owned Worker/caller:

```bash
python scripts/package_helpers.py --target linux-x86_64 --version YOUR_VERSION --include-owned
python install.py --archive /path/to/helpers.zip --sha256 TRUSTED_ARCHIVE_SHA256
```

Use `windows-x86_64` for a bundle containing the Windows flow helper. Our NR PE and
caller are the same Windows targets in both bundles. The relay remains included for
legacy backend compatibility, but `owned_nr` does not launch it. Old bundles without
`include_owned` remain supported. No vendor runtime or driver is installed by this script.

## Verification boundary

Linux/GE-Proton, RTX 4070 Ti SUPER: 640×360 eight-frame cached/reused/streamed
outputs matched; two-layer streaming, cancellation and manual cleanup passed.
Real isolated Comfy API tests loaded 21 nodes, saved a processed MP4, rendered a
single-frame preview and reused the Worker across both tasks. This is short-sample
acceptance, not long-video stability, all-GPU validation, Windows GPU acceptance
or browser visual QA. SR/RR/FG are not enabled by this backend.

## Separate SR binding

SR uses `components/owned-sr/comfy-dlss-worker.exe` built with the official SDK,
plus a user-supplied `components/owned-sr/nvngx_dlss.dll`. Use
[`runtime-presets/owned-sr.json`](../examples/runtime-presets/owned-sr.example.json)
as a separate configured preset, including its project UUID. Runtime snapshots
contain these two files; SR does not load `caller/nvngx.dll` or `nvngx_dlssnr.dll`.

The default Zig NR Worker advertises SR as not compiled in the CSR1 handshake.
Existing `--include-owned` helper packages therefore do not promise SR support.
The optional [MSVC/SDK build route](super-resolution.en.md#runtime-files-and-build)
and manual build workflow are source provisions, not published binaries or GPU
acceptance. SR currently uses isolated streaming only and requires real matching
numeric device depth. Keep working NR presets and model files unchanged.
