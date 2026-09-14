# Development and deployment

English · [简体中文](DEVELOPMENT.zh-CN.md)

Audience: public

Clone this repository directly into
`ComfyUI/custom_nodes/comfy-dlss-experimental`. See
[installation and helper packaging](distribution.en.md) for user setup.

## Portable validation

From the node repository root:

```bash
python3 -m unittest discover -s tests -v
node --test tests/*.mjs
python3 scripts/check_publication.py
```

Keep both locale catalogs complete and preserve combo machine values; see
[interface localization](i18n.en.md). Importable JSON examples live under
`example_workflows/`; the test suite checks links and default runtime paths.

Use ComfyUI's own Python when testing media imports and live nodes. Pure tests
may skip optional media dependencies or hardware tests; a green result with
skips is not GPU validation. GPU checks are explicit opt-in and need a real
NVIDIA host. Integration scripts accept server, workflow and input arguments;
run each with `--help` rather than assuming a developer's filesystem layout.

Use a dedicated test data root and prefix. Never point destructive cleanup at
a shared Wine prefix, kill generic Wine process names, or overwrite the user's
main Comfy workflow. Logs can include absolute paths and source media names:
redact them before attaching to public issues.

## Diagnostic UI transport

Custom `dlss_*` UI channels carry lists of JSON **strings**, produced by
`diagnostic_ui()`. Comfy Jobs treats dicts in any UI list as media descriptors;
an input report's nested `format` object is not a MIME string. Do not emit
raw report dicts in those lists or put diagnostics in the `images`/`video`
channels. Real media descriptors, internal report objects and STRING sockets
keep their existing contracts. The shared frontend decoder also accepts legacy
object payloads, including reports cached in workflow properties.

Update backend and frontend together and reload the frontend after preserving
unsaved workflow edits. New executions use the safe transport; this change does
not rewrite or delete old records already held by a running server. Frontend
legacy decoding alone cannot repair the server's old Jobs summaries.

Both cached and streaming exporters trim decoded audio to the visible video
endpoint before AAC encoding, using [FFmpeg's atrim filter](https://ffmpeg.org/ffmpeg-filters.html#atrim).
The output duration limit remains a backstop. This avoids extra tail duration
from a final AAC frame without changing frame count or shifting audio timestamps.
Tests cover 48/44.1 kHz, short ranges, fractional frame rate and nonzero start;
AAC padding and lossy encoding are not a sample-exact audio preservation claim.

## Native code

The active relay needs an existing Zig installation, not the NVIDIA NGX SDK:

```bash
bash sidecar/build_relay.sh
```

This also builds a GPU-free mock Worker for transport tests. The mock is not an
NR renderer and is excluded from helper packages. Native optical flow is built
separately for Linux/Windows using existing Zig and CUDA API headers; commands
and package layout are in [distribution.en.md](distribution.en.md).

Follow [C++ quality requirements](sidecar-cpp-quality.en.md): bounded inputs,
checked arithmetic, explicit ownership, GPU synchronization and safe cleanup.
Portable sanitizer tests do not replace Windows/Proton GPU testing.

The optional [caller shim build and forwarding tests](caller-shim.en.md) require
externally supplied official SDK headers. They do not load NR or change active
presets; do not confuse this DLL with a complete project-owned video Worker.

[Owned Worker acceptance host](owned-worker.en.md) documents the separate
Microsoft-ABI parameter object, bounded NR probe and GPU-free target ABI check.
The [owned runtime](owned-runtime.en.md) describes its optional Comfy integration;
legacy installation remains unchanged. [SR/DLAA](super-resolution.en.md) adds an
SDK-enabled build of the same Worker, with manual Windows/MSVC build instructions
and explicit source/CPU versus GPU validation boundaries.

The retained `build_carrier.sh` and `build_renderer.sh` are diagnostic paths,
not prerequisites for active NR. Do not introduce guessed ABI/vtable layouts
or substitute MSVC runtime symbols merely to make a research build link.

## Publication

Original code uses MIT; NVIDIA API headers retain their original notices.
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) defines the external-file boundary.
Source control excludes builds, helpers, proprietary Worker/model binaries,
private notes, prefixes, logs, caches and test media.

Before publishing, stage the intended source files and run
`python3 scripts/check_publication.py`. It reads the **Git index**, checks
publication policy and likely secrets, and prints only finding locations/rule
names. It is a guardrail, not an exhaustive security audit. Also manually
review staged content, author identity, third-party notices and destination.

Public architecture and user behavior belong in `docs/`; local test reports,
deployment coordinates and handoff notes belong in ignored `.agent-docs/`.
