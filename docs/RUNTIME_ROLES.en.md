# NGX, Worker, and shim role boundaries

English · [简体中文](RUNTIME_ROLES.zh-CN.md)

Audience: public

This page resolves a recurring ambiguity: unrelated projects use `nvngx.dll`
for three different programs. A filename is not an identity; inspect the PE type,
entry point/exports, launch method, and protocol together.

## Two confirmed community wrappers

| Project file | Binary/call identity | Responsibility | Interchangeable? |
| --- | --- | --- | --- |
| Video Converter `bin/runtime/nvngx.dll` | Windows x64 console PE with an entry point and no export directory; launched as `nvngx.dll --video` | Complete video Worker: implements D5V2 and hosts the Feature 18 lifecycle | No |
| Zonnery `caller/nvngx.dll` | Loaded by the player; its contract requires `DLSSNR_CallInit/Create/Evaluate/Release` exports | Thin in-process forwarding layer that makes the real NR call originate in a module accepted by the runtime's caller check | No |

Zonnery does not distribute that shim binary or its source in the repository. Its
contract is confirmed by the player's `LoadLibrary/GetProcAddress` code and README.
The Video Converter file was inspected directly: it is a console executable and
has no export table. The Zonnery shim cannot implement our D5V2 process protocol,
and the Converter Worker cannot provide the four exports Zonnery resolves.

The complete Converter Worker is also named `nvngx.dll`, very likely allowing the
host itself to satisfy the same caller-identity condition. Upstream descriptions
and observed behavior strongly support this, but corresponding auditable Worker
source is unavailable. We therefore claim only the verified PE identity, D5V2
behavior, and external dependency—not a source-level reconstruction.

A third namesake is NVIDIA's driver-managed **NGX bootstrap/core `nvngx.dll`**.
It is neither the video Worker nor a project caller shim and must not overwrite either.

## Active implementation

```text
ComfyUI Python
  -> dlss-native-relay.exe             ours: process/pipe relay
  -> nvngx.dll --video                 external Video Converter Worker
  -> nvngx_dlssnr.dll                  matching NR runtime
```

The project currently builds neither the real Feature 18 Worker nor an additional
Zonnery caller shim. `dlss-native-relay.exe` supervises a process and transports
D5V2 bytes; it does not create a D3D12 device or NGX feature.

## Accepted refactoring direction

```text
ComfyUI Python
  -> project transport/process layer
  -> comfy-dlss-worker.exe             clearly named project-owned Feature Host
      -> caller/nvngx.dll              optional, tiny, project-owned, auditable
          -> nvngx_dlssnr.dll          user-supplied NR feature runtime
      -> NGX core / Streamline         explicitly owned by the selected backend
```

The refactor follows these rules:

1. A complete Worker receives a clear project name; it is not disguised as an
   ordinary `nvngx.dll`.
2. The caller shim contains only typed Init/Create/Evaluate/Release forwarding.
   It owns no video protocol, D3D12 resources, cache, thread, model selection, or
   Worker lifecycle.
3. Enable the shim only when the selected runtime enforces caller validation; it
   is not a universal NGX dependency.
4. The Worker owns devices, resources, synchronization, capability negotiation,
   and failure boundaries. NR, SR, and FG are explicit capability backends, not
   features activated by collecting similarly named DLLs.
5. User-supplied NVIDIA/community-modified feature runtimes are paired by hash,
   version, target GPU, and verified Worker combination. Unknown namesakes are not
   assumed compatible.
6. Keep the current external D5V2 Worker backend during migration. A project-owned
   backend uses a new ID/protocol version and never silently changes old presets.

A thin shim isolates caller-validation changes, but cannot make NR work by itself.
The substantive work remains D3D12/NGX resource contracts, input reconstruction,
feature lifecycle, and cross-platform process boundaries.

## Feature DLLs are not hosts

| File family | Primary role | Integration still required |
| --- | --- | --- |
| `nvngx_dlssnr.dll` | Experimental NR / Feature 18 runtime | NR Worker and color/motion input contract |
| `nvngx_dlss.dll` | Super Resolution / DLAA | SR dimensions, jitter, motion, exposure, and resource states |
| `nvngx_dlssd.dll` | Official Ray Reconstruction route | Required lighting/depth/motion engine semantics |
| `nvngx_dlssg.dll` | Frame Generation | Presentation, depth, motion, HUD/UI, synchronization, and platform checks |
| `sl.*.dll` | Streamline framework/plugins | Streamline initialization, resource tags, constants, and lifecycle |

These are not command-line video tools. Placing one in a directory only satisfies
one dependency of a backend; it does not automatically implement NR, SR, or FG.
See [external runtime files](DLL_PREPARATION.en.md) and [architecture](ARCHITECTURE.en.md).
