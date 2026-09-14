# External runtime files: identity, provenance and placement

English · [简体中文](DLL_PREPARATION.zh-CN.md)

Audience: public

**Clone the node first, then prepare dependencies. Do not install the entire converter.**
The active application-level minimum is our relay, an external video Worker and
its matching NR model. NVIDIA flow additionally needs our host-native NVOF helper;
driver/Proton dependencies still apply. See [installation and layout](distribution.en.md).

## At a glance: files already collected

This is the result for the **current active `direct_nr` video backend**, not a
claim that the other files are useless to their original projects.

| Collected file | Used now? | Decision |
| --- | --- | --- |
| `nvngx.dll` from `DLSS5VideoConverter/bin/runtime/` | **Yes, required** | External D5V2 video Worker; started as a PE program, not imported into Python |
| `nvngx_dlssnr.dll` from the same converter runtime directory | **Yes, required** | Matching NR model/runtime loaded by that Worker |
| A same-named `nvngx_dlssnr.dll` obtained with RenoDX/ReShade files | **No, unless independently paired and validated** | Same filename does not establish a matching ABI, patch level or GPU target |
| ReShade package `dxgi.dll` / extracted `ReShade64.dll` | **No** | Graphics proxy/hook for the retained ReShade route; direct_nr has no injected application |
| `renodx-dlss5.addon64` or similarly named RenoDX add-on | **No** | ReShade add-on; the current Worker protocol does not load it |
| `nvngx_dlss.dll` | **No** | DLSS Super Resolution component for other routes; current output remains same-resolution NR |
| `D3DCompiler_47.dll` | **No** | Only relevant to specific ReShade/Wine shader-compiler setups |
| `sl.*.dll`, `nvngx_dlssg.dll` | **No** | Streamline/Frame Generation components; those backends are not implemented |

The two required external files must come from one known-compatible package.
Do not choose a file merely because its name matches. Our Setup Helper verifies
presence, PE identity and the Runtime Configuration hashes; it also labels the
exact tested pair below. It cannot prove that an unknown pair is semantically
compatible without a GPU execution test.

### Three different things named `nvngx.dll`

| Identity | Typical location | Used by this backend? |
| --- | --- | --- |
| **Video Worker executable** | User data `components/nr/<bundle>/nvngx.dll`; 67,072-byte tested file | **Yes.** Relay starts it with `--video`. This is the file meant by our preset's `worker` role. |
| **NVIDIA driver NGX bootstrap** | DriverStore or a Proton prefix's `system32/nvngx.dll`; size/version managed by the driver environment | **Indirect driver environment only.** Never copy it over the Worker or add it to the preset. |
| **Caller-validation shim** | Zonnery-style `caller/nvngx.dll` beside that project's player | **No.** It is a thin forwarding DLL for that player and does not implement our D5V2 process protocol. |

The matching NR file is named `nvngx_dlssnr.dll`, not a fourth meaning of
`nvngx.dll`.
See [NGX, Worker, and shim role boundaries](RUNTIME_ROLES.en.md) for the binary/
export evidence and the future clearly named Worker plus separate thin-shim rule.

## 1. Files actually used

| File | Identity and role | Provenance / acquisition lead | How this project uses it |
| --- | --- | --- | --- |
| `dlss-native-relay.exe` | Our C++ transport/process bridge, not DLSS | This repository's `sidecar/src/native_relay.cpp`, built with Zig; intended for our helper Releases | Native Windows or Proton on Linux; creates pipes and starts the Worker |
| `nvngx.dll` | Despite the extension, a **Windows x64 console PE executable** consuming D5V2 video and evaluating NR | Tested file from the RTX40 converter archive below, under bin/runtime; upstream attributes it to an adapted DLSS5-Feeder host | Launched as `nvngx.dll --video`; we currently neither build it nor add a separate caller shim |
| `nvngx_dlssnr.dll` | NR model/runtime loaded by the Worker, not ordinary SR | From the **same RTX40 archive**; a community-modified build, not an original we obtained from the official SDK | Kept with the Worker and loaded from a per-runtime snapshot |
| `dlss-nvof-helper` / `dlss-nvof-helper.exe` | Our NVIDIA Optical Flow API adapter; only needed for NVIDIA flow | This repository's `sidecar/src/nvof_helper.cpp`, separate Linux/Windows builds | Host-native, outside Proton; neither the NR Worker nor its model |

The upstream [component notice](https://github.com/perseval-BLR/DLSS5-Video-Converter/blob/main/others/THIRD_PARTY.md)
attributes the Worker to the DLSS5-Feeder host. We verified acquired bytes and
protocol behavior, not a byte-reproducible upstream source build. Do not label
that external executable as our original binary.

### Driver libraries used by NVIDIA optical flow

These are driver interfaces, not extra files to copy into the NR bundle:

| Host | Libraries loaded by our NVOF helper | Source / placement |
| --- | --- | --- |
| Windows | `nvcuda.dll`, `nvofapi64.dll` | NVIDIA driver installation; the helper loads them from the system directory |
| Linux | `libcuda.so.1`, `libnvidia-opticalflow.so.1` | NVIDIA driver packages and the host dynamic loader's library paths |

If unavailable, check the host's NVIDIA driver components. Do not download
same-named DLLs from arbitrary sites or copy Windows libraries into the Linux
helper directory. The CUDA/Optical Flow development headers are build-time
inputs; end users of our precompiled helper do not need a compiler or the CUDA
Toolkit merely to run this helper.

## 2. Exact tested acquisition

Rechecked against the existing local archive on 2026-09-03, without downloading
or executing third-party programs again:

- Project: [perseval-BLR/DLSS5-Video-Converter](https://github.com/perseval-BLR/DLSS5-Video-Converter).
- [v0.1.0 release](https://github.com/perseval-BLR/DLSS5-Video-Converter/releases/tag/v0.1.0).
- Archive: `DLSS5-Video-Converter-v0.1.0-RTX40-win64.zip`.
- Selected members:

  ```text
  DLSS5VideoConverter/bin/runtime/nvngx.dll
  DLSS5VideoConverter/bin/runtime/nvngx_dlssnr.dll
  ```

| File | Bytes | SHA-256 of our tested file |
| --- | ---: | --- |
| nvngx.dll | 67,072 | `99ef1f2976d9cd16b7fc269adb6c6450fb64c81a522c9b9e6edc6a28201dc904` |
| nvngx_dlssnr.dll | 165,840,496 | `28bdc080d28686decdb63f6f4246b022274916b80aafdab266fe0fb63b2b9265` |

These identify a test fixture, not safety, permission or a minimum version.
Upstream main's THIRD_PARTY.md lists a different model hash, `DCC0DC…D36F`;
it must not replace validation of the exact RTX40 release asset.

The RTX40 release attributes its Ada modification to Uncle Burrito / dev-camo.
That is distributor attribution, not our independent verification of authorship,
original model provenance or the full modification chain. Confirm those and
redistribution rights with the distributor. We provide neither DLL mirrors nor
automatic downloads.

### GPU-series selection

| Series | Evidence in this project |
| --- | --- |
| RTX 40 | The exact pair above passed NR tests on RTX 4070 Ti SUPER with Linux/Proton; not an entire-series guarantee |
| RTX 50 | Reference projects offer RTX50 leads; independently verify D5V2 Worker compatibility and GPU behavior, rather than using the RTX40 patch automatically |
| RTX 20/30 | Not validated here. Upstream overview and RTX40-asset restrictions differ; an overview's beta claim does not establish support for this archive |

Filenames do not establish ABI, protocol or GPU compatibility. Mixed versions,
resident reuse and Windows GPU execution each require validation. Start unverified
pairs in isolated mode; see [runtime boundaries](dlss5-runtime-research-2026-09.en.md).

## 3. Other similarly named files

| Name | Reference-project role | Manually required for our direct path? |
| --- | --- | --- |
| Driver `nvngx.dll` / renamed `_nvngx.dll` | NVIDIA NGX bootstrap core, used by Zonnery's route | Not one of our three application components; cannot substitute for the video executable. A correct driver remains required |
| `caller/nvngx.dll` | Zonnery's caller-validation forwarding DLL, loaded by its player | No extra copy; it does not implement our executable video protocol |
| `nvngx_dlss.dll` | Super Resolution runtime | Not required for NR. Required by the separate experimental [SDK SR path](super-resolution.en.md), together with an SDK-enabled Worker; copying this DLL alone does not enable SR |
| `nvngx_dlssg.dll`, `sl.*.dll` | FG / Streamline components | Not currently required; these backends are not integrated |

[Zonnery's file table](https://github.com/Zonnery/dlss5-nr-player)
belongs to its player, not our shopping list. In particular, its statement about
driver-bundled NR models is not a guarantee for every installed driver.

[NR-Media-UI](https://github.com/perseval-BLR/NR-Media-UI) supplies image-app/GPU-package
leads, not a verified replacement video Worker.
[DLSS-COM](https://github.com/MYT-YEP/DLSS-COM) has its own D3D12 Worker and asks users
to supply the model; that does not mean our relay implements its Worker.

### How the two referenced README file lists map to this project

| Upstream | Files/runtime it describes | What we reuse |
| --- | --- | --- |
| [Zonnery/dlss5-nr-player](https://github.com/Zonnery/dlss5-nr-player) | Direct NGX player build: driver core renamed `_nvngx.dll`, `nvngx_dlssnr.dll`, NGX headers, `caller/nvngx.dll`; its DX11 bridge additionally needs `nvngx_dlss.dll`; ffmpeg/ffprobe are external tools | **None of its separately named bootstrap/shim/header files.** Its README is valuable for direct-NGX concepts, but our selected external Worker already owns its own calling contract. |
| [perseval-BLR/DLSS5-Video-Converter](https://github.com/perseval-BLR/DLSS5-Video-Converter) | A complete Windows web application with embedded Python/FFmpeg and `bin/runtime`; its release runtime contains the video Worker and model | From the exact tested RTX40 archive, only `bin/runtime/nvngx.dll` and matching `nvngx_dlssnr.dll`. We do not reuse the web server, embedded Python, packaged ffmpeg, `nvidia-smi.exe`, job/output folders or launcher. |

These projects package different calling architectures. Their file lists are
not additive and same-named files are not safely interchangeable.

## 4. Where to put external files

Keep versioned bundles **outside node source, inside Comfy user data**:

```text
ComfyUI/
  custom_nodes/comfy-dlss-experimental/        # git clone
  user/default/comfy-dlss-experimental/       # default data root
    components/nr/converter-v0.1.0-rtx40/
      nvngx.dll
      nvngx_dlssnr.dll
    runtime-presets/default.json
```

The complete source/data/generated directory tree is documented in
[project layout](PROJECT_LAYOUT.en.md).

A custom Comfy user directory or COMFY_DLSS_HOME changes this root. Never place
these in System32, DriverStore, site-packages or Proton system directories, or
overwrite driver files. Do not run DLSS5VideoConverter.exe or import its embedded
Python/web server.

Copy [the preset](../examples/runtime-presets/direct-nr.example.json) to default.json:

```json
{
  "schema_version": 1,
  "id": "direct-nr-local",
  "backend": "direct_nr",
  "components": {
    "relay": "@bundled/relay",
    "worker": "../components/nr/converter-v0.1.0-rtx40/nvngx.dll",
    "nvngx_dlssnr": "../components/nr/converter-v0.1.0-rtx40/nvngx_dlssnr.dll"
  }
}
```

Relative paths resolve from **the preset JSON directory**, not Comfy root or the
shell. Host-absolute paths also work; Windows JSON can use forward slashes.
This example does not download/import files or rewrite existing presets.

Execution creates hash-identified runtime-snapshots copies. Modify the source
bundle/preset, not generated snapshots. Use separate presets for versions;
changed DLLs require a new instance, not hot replacement.

## 5. Retained ReShade/RenoDX diagnostic files

These belong to a research route, **not the active direct_nr preset**:

| File | Provenance and role |
| --- | --- |
| ReShade `ReShade64.dll` → local `dxgi.dll` | x64 add-on-capable [ReShade](https://reshade.me/) distribution; proxy/graphics hook, not system DXGI |
| `renodx-dlss5*.addon64` | Experimental add-on previously obtained from a RenoDX community channel; exact publisher/version/link still needs verification, not interchangeable with a generic RenoDX package |
| `nvngx_dlss.dll` / `nvngx_dlssnr.dll` | That diagnostic's matching SR/NR files; old Discord files are not automatically the tested RTX40 video pair |
| `D3DCompiler_47.dll` | Microsoft shader compiler, native version only where specific Wine compatibility requires it; verify a trusted Microsoft distribution, not random DLL sites |

The diagnostic may use child-scoped `d3dcompiler_47=n;dxgi=n,b` overrides
(native/builtin). They neither install DLLs nor implement the video protocol and
are not used by current direct NR.
The [old component manifest](../examples/component-manifests/renodx-dlss5.example.json)
contains no runtime files.

## 6. Remaining acquisition checks

For each asset confirm publisher, version/date, GPU target, exact download entry,
archive members, hashes and usage/redistribution terms. Project homepages/READMEs
are leads, not permission or compatibility guarantees. We do not mirror, patch
or auto-acquire these external NR binaries. External programs execute native
code; a process/Proton prefix **is not a security sandbox**.
