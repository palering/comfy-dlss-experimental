# Experimental runtime boundaries

Audience: public

Status: 2026-09-03. This is a compatibility note for this project's implemented
backend, not a claim about the latest NVIDIA SDK release.

## Active implementation

The Comfy nodes use an external Windows Worker that implements direct Feature
18 and a matching NR runtime. Our adapter exchanges D5V2 color/motion frames;
our relay only provides transport and process supervision. See
[architecture](ARCHITECTURE.md) and [external-file preparation](DLL_PREPARATION.md).

The external Worker is deliberately not vendored or rebuilt here. Its binary
protocol is not an official NVIDIA interface contract. The same filename is
insufficient to identify a compatible Worker or model: versioned presets and
hashes identify actual bytes.

## Why the other code remains

The repository retains a D3D12/ReShade carrier, a synthetic NGX bootstrap
experiment and multi-plane GPU-copy diagnostics. They isolate loader, adapter,
transport and resource-lifetime behavior. Loading a DLL, seeing feature exports,
or copying a depth texture through D3D12 does not prove NR evaluation or use of
that depth as an NR input.

Those diagnostics are not the default processing path, and their earlier
bootstrap limitations are not evidence that NGX generally cannot run under
Proton. The working direct route does not require linking the official NGX
static SDK or access to a Windows/MSVC build machine.

## Evidence boundary

Linux/Proton processing and motion-provider tests have run on RTX 4070 Ti SUPER,
driver 610.57.04 and GE-Proton 11-6 using Xwayland. This establishes one tested
combination, not a minimum driver requirement or support matrix for all GPUs.
Windows code paths and helper builds exist, but Windows GPU acceptance remains
outstanding. Native Linux NR and direct NR on native Wayland are not supported.

A future official backend must be integrated and tested against its published
SDK contract. Reserved backend names in this project are not implementations.
No claim of current SR, FG, HDR, depth/material input or universal model
compatibility should be inferred from those extension points.

Source provenance and redistribution boundaries are documented in
[third-party notices](../THIRD_PARTY_NOTICES.md); diagnostic implementation
details remain in the [direct relay guide](direct-nr-relay.md) and
[C++ quality guide](sidecar-cpp-quality.md).
