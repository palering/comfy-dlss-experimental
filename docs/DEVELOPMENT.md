# Development and deployment

Audience: public

Clone this repository directly into
`ComfyUI/custom_nodes/comfy-dlss-experimental`. See
[installation and helper packaging](distribution.md) for user setup.

## Portable validation

From the node repository root:

```bash
python3 -m unittest discover -s tests -v
node --test tests/*.mjs
python3 scripts/check_publication.py
```

Use ComfyUI's own Python when testing media imports and live nodes. Pure tests
may skip optional media dependencies or hardware tests; a green result with
skips is not GPU validation. GPU checks are explicit opt-in and need a real
NVIDIA host. Integration scripts accept server, workflow and input arguments;
run each with `--help` rather than assuming a developer's filesystem layout.

Use a dedicated test data root and prefix. Never point destructive cleanup at
a shared Wine prefix, kill generic Wine process names, or overwrite the user's
main Comfy workflow. Logs can include absolute paths and source media names:
redact them before attaching to public issues.

## Native code

The active relay needs an existing Zig installation, not the NVIDIA NGX SDK:

```bash
bash sidecar/build_relay.sh
```

This also builds a GPU-free mock Worker for transport tests. The mock is not an
NR renderer and is excluded from helper packages. Native optical flow is built
separately for Linux/Windows using existing Zig and CUDA API headers; commands
and package layout are in [distribution.md](distribution.md).

Follow [C++ quality requirements](sidecar-cpp-quality.md): bounded inputs,
checked arithmetic, explicit ownership, GPU synchronization and safe cleanup.
Portable sanitizer tests do not replace Windows/Proton GPU testing.

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
