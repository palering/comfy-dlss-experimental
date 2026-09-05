# comfy-dlss-experimental

English · [简体中文](README.zh-CN.md)

Audience: public

Experimental ComfyUI nodes for **offline video neural rendering (NR)**, with
in-node A/B previews and a separate final-export path.

> **Source-only preview release.** Cloning installs the node source, not a
> working NR runtime. You need our native helpers and a trusted, compatible
> external Worker/model pair. Prebuilt helper Releases are **not available yet**.
> Linux/Proton has GPU validation; Windows GPU validation is still pending.

[Quick Start](#quick-start) · [Prerequisites](#prerequisites) ·
[Project layout](#project-layout) · [Build from source](#build-from-source) ·
[Example workflows](#example-workflows)

<a id="quick-start"></a>

## Quick Start

The shortest path is **DIS flow → one-frame preview → full-video export**.
Complete the prerequisites below first; source-only cloning cannot render NR.

1. **Clone**, from your existing ComfyUI root:

   ```sh
   git clone https://github.com/palering/comfy-dlss-experimental.git custom_nodes/comfy-dlss-experimental
   ```

2. **Check dependencies** in Comfy's Python: `numpy`, `cv2` with DIS, `av`, `PIL`;
   also provide host `ffmpeg` and `ffprobe`.
   [Exact Linux/Windows commands](docs/distribution.en.md#step-2-identify-comfys-python) ·
   [FFmpeg official download page](https://ffmpeg.org/download.html) ·
   [Optional pip installation and path setup](docs/media-tools.en.md#pip-installation).

3. **Prepare our relay.** [Build from source](#build-from-source) or install a
   trusted helper ZIP using [install.py](docs/distribution.en.md#step-4-install-our-helpers).
   Public prebuilt Releases are not available yet. DIS needs no NVOF helper.

4. **Prepare the external pair**: `nvngx.dll` (video Worker executable) and
   `nvngx_dlssnr.dll` (matching model). Put them in a versioned directory and
   copy [the preset](examples/runtime-presets/direct-nr.example.json) to
   `runtime-presets/default.json` under the node data root, then set the paths.
   Default data root: `ComfyUI/user/default/comfy-dlss-experimental/`.
   [Exact files, provenance, hashes and copy layout](docs/DLL_PREPARATION.en.md).

5. **Restart Comfy, refresh, and import** [DIS preview](example_workflows/dlss_native_preview.json).
   Choose your video in **Load Video**; select your preset in **Runtime Configuration**
   and, on Linux, an installed Proton. In **Video Input Adapter**, set media paths
   only if needed and click **Check input**; resolve reported input/color issues.

   For a complete preflight, add **DLSS Setup Helper**, connect the Runtime
   Configuration and Video Input Adapter outputs, then click **Check selected
   configuration**. Its runtime output is an unchanged pass-through for Preview/Process.

6. **Preview and save.** Adjust **NR Look**, then click **Render current frame**
   in the A/B preview card. Check a short range before opening
   [Full-video export](example_workflows/dlss_native_process.json):
   **Process Video → Save Video**, output size 100, start 0, **Process to video end** enabled.

Preview and Process ranges are independent: saving Preview's `video_b` saves only
its selected frame/range and scale. Use 100% size to judge the full-resolution Look.
See [complete installation](docs/distribution.en.md) and [workflow instructions](example_workflows/README.en.md)
for portable Python, custom data directories and troubleshooting.

## What it does

- Preview a cursor frame or a selected range inside the node card.
- Compare original/result or two Looks with wipe, side-by-side, flicker and difference.
- Adjust NR intensity, style and experimental controls in **NR Look**.
- Cascade one to three Looks without intermediate video encoding using **NR Pass Stack**.
- Choose **DIS CPU** or optional **NVIDIA hardware optical flow**.
- Inspect video metadata; optionally normalize supported SDR input to sRGB.
- Preflight the selected runtime, component hashes, media/Python dependencies,
  platform bridge and actual optical-flow configuration in **Setup Helper**.
- Reuse prepared color/flow caches when changing Looks.
- Use isolated or lazy-resident Workers, with timing, history and manual release.
- Export a standard **VIDEO** through ComfyUI's native **Save Video** node.

**Not implemented:** SR/upscaling, FG/frame generation, HDR processing, external
depth/material/mask inputs, native Linux NR, and comparison-video export.
Experimental controls may have no visible effect on some Worker/model pairs.

## Prerequisites

| Requirement | Details |
| --- | --- |
| ComfyUI | A recent release providing the V3 API, native `LoadVideo`/`SaveVideo`, custom-node locales and DOM widgets. Use its own Python, 3.11+. |
| GPU/runtime | Compatible NVIDIA GPU and driver **plus a matching external NR Worker/model pair**. A newer driver alone is not enough. |
| Python media packages | `numpy`, `cv2` with DIS, `av`, `PIL` in ComfyUI's environment. They are not installed automatically. |
| Host tools | Both host `ffmpeg` and `ffprobe`: [official download page](https://ffmpeg.org/download.html), or [optional pip-based setup](docs/media-tools.en.md#pip-installation). Select via backend PATH or Input Adapter; PyAV alone does not supply these commands. |
| Project helpers | `dlss-native-relay.exe`; also a platform-native NVOF helper if selecting NVIDIA flow. |
| Input/storage | Supported SDR, constant frame rate, square pixels. Enough disk for prepared frames and outputs; unsupported metadata fails explicitly. |

### Platform differences

- **Linux x86-64:** existing Proton installation, Steam client directory and
  Xwayland/`DISPLAY`. A Wayland desktop can use Xwayland; no X11 desktop switch
  is required. Select the installed Proton or enter its exact path.
- **Windows x86-64:** Runtime's automatic host selection launches the PE Worker
  natively, without Proton. Code paths and CI exist; **GPU acceptance is pending**.
- **macOS/ARM:** not supported for NR execution.

One tested Linux combination: RTX 4070 Ti SUPER, driver 610.57.04 and
GE-Proton 11-6. This is **not** a minimum-driver or universal GPU support claim.
See [platform and distribution details](docs/distribution.en.md).

### Required external DLLs

The active direct-NR path uses only the **external video Worker `nvngx.dll`** and
its **matching `nvngx_dlssnr.dll` model** as user-supplied application components.
The tested pair comes from the Video Converter v0.1.0 RTX40 archive; its Worker
is neither a driver DLL nor a caller shim. **ReShade, RenoDX, Streamline and the
converter's web service are not required.**

[The DLL guide](docs/DLL_PREPARATION.en.md) lists provenance, archive members,
SHA-256, GPU validation limits, placement, preset pairing and optional driver libraries.
Keep runtime files outside source; `COMFY_DLSS_HOME` can override the data root.

#### Downloaded-file audit for the current backend

| File | Current status |
| --- | --- |
| Video Converter `bin/runtime/nvngx.dll` | **Required external file.** It is the D5V2 video Worker executable, despite its `.dll` suffix. |
| Matching Video Converter `nvngx_dlssnr.dll` | **Required external file.** Use the model paired with that Worker; a same-named RenoDX/Discord file is not automatically interchangeable. |
| `dlss-native-relay.exe` | **Required project helper.** Built from this repository; it is not downloaded from NVIDIA and was not renamed from another DLL. |
| `dlss-nvof-helper[.exe]` | **Optional project helper.** Required only for NVIDIA optical flow; DIS does not use it. |
| ReShade `dxgi.dll`, `renodx-dlss5*.addon64` | **Not used** by the active `direct_nr` backend; retained only as research-route inputs. |
| `nvngx_dlss.dll`, `D3DCompiler_47.dll`, `sl.*.dll`, separate caller shim | **Not used** by the active backend. SR/Streamline/ReShade requirements must not be mixed into this NR setup. |

The [external-file guide](docs/DLL_PREPARATION.en.md) maps the different
requirements described by Zonnery's player and DLSS5 Video Converter to our
backend and distinguishes the three unrelated `nvngx.dll` identities.

<a id="project-layout"></a>

## Project layout

```text
ComfyUI/
├── custom_nodes/comfy-dlss-experimental/       # Git checkout: source/UI/tests/templates
└── user/default/comfy-dlss-experimental/       # user data; not part of Git
    ├── components/nr/<bundle>/
    │   ├── nvngx.dll                           # external Worker
    │   └── nvngx_dlssnr.dll                    # matching external model
    ├── runtime-presets/default.json             # user-selected bindings
    ├── prepared-clips/                          # generated color/motion caches
    ├── runtime-snapshots/                       # generated verified copies
    ├── prefixes/                                # generated Proton state
    └── executions/                              # generated task records
```

Our source-built helpers live under the checkout's `sidecar/build/`, or a
versioned helper package under `sidecar/bin/<platform>/<version>/`. They are not
external DLLs. See the [annotated repository and data tree](docs/PROJECT_LAYOUT.en.md).

<a id="build-from-source"></a>

## Build from source

Build **our helpers**, not the proprietary model or external Worker. From the node
repository root, with **Zig on PATH and Bash** available:

```sh
bash sidecar/build_relay.sh
```

This produces `sidecar/build/dlss-native-relay.exe` and a transport-test mock.
The relay alone is enough for **DIS flow**; no CUDA headers, NGX SDK, ReShade or
Windows/MSVC installation is needed for this build. With no installed helper
manifest, `@bundled/relay` also resolves this development output. The mock does not render NR.

For **NVIDIA flow**, additionally build the host-native helper with existing CUDA
headers (`cuda.h` and its includes). Here `python` is your build interpreter:

```sh
python sidecar/build_nvof.py --target linux-x86_64 --cuda-include /path/to/cuda/include
```

Use `--target windows-x86_64` for Windows. Outputs are `sidecar/build/dlss-nvof-helper`
or `.exe`; no nvcc is used. Target means the **Comfy host**, not the browser machine.

The full guide covers [toolchain preparation, local use and versioned ZIP packaging](docs/distribution.en.md#maintainer-builds),
including Bash on Windows, checksums and install destinations. Packaging requires
both helpers for the selected target. [Development/tests](docs/DEVELOPMENT.en.md)
and [C++ quality rules](docs/sidecar-cpp-quality.en.md) describe validation;
a successful cross-build is not Windows GPU acceptance.

## Example workflows

Download the JSON (use GitHub's **Raw / Download raw file**) and drag it into
ComfyUI, or use **Open**. These examples use core video nodes plus this project;
no third-party video-loader pack is required.

| Example | Purpose |
| --- | --- |
| [DIS preview](example_workflows/dlss_native_preview.json) | Start here: explicit DIS provider, input check, NR Look and in-node preview. |
| [Full-video export](example_workflows/dlss_native_process.json) | Full duration at input size through native Save Video. |
| [NVIDIA flow preview](example_workflows/dlss_nvidia_flow_preview.json) | Swappable native NVIDIA optical-flow provider; requires its helper. |
| [Two-Look comparison](example_workflows/dlss_compare_looks.json) | Two NR Looks connected to A and B; both share prepared inputs. |

[Example guide](example_workflows/README.en.md) explains wiring, defaults,
required setup and safe first tests. Source videos and proprietary binaries are
not included; replace `example-input.mp4` with your own file.
Importing a workflow does not save it to Comfy's workflow library—save it there
explicitly if desired. Old files under `workflows/` remain legacy templates.

## Language and diagnostics

Select **English** or **简体中文** in ComfyUI's language setting. Native fields use
ComfyUI's `locales/en,zh/nodeDefs.json`; our cards follow the same setting.
Other languages fall back to English for our card text; Chinese variants use
Simplified Chinese. A server restart is needed when adding locale files.

Machine identifiers, file paths, hashes, copied execution JSON and unrecognized
low-level errors remain unchanged for reproducible debugging.
See [translation coverage and contribution rules](docs/i18n.en.md).

## Documentation

- [Installation/platform details](docs/distribution.en.md)
- [External Worker and DLL preparation](docs/DLL_PREPARATION.en.md)
- [Nodes and output ranges](docs/comfy-video-nodes.en.md)
- [NR Look controls](docs/nr-look.en.md)
- [Multi-pass NR](docs/MULTI_PASS_NR.en.md)
- [FFmpeg setup and paths](docs/media-tools.en.md)
- [Input adapter and color](docs/video-input-adapter.en.md)
- [NVIDIA optical flow](docs/nvidia-optical-flow.en.md)
- [Performance, residency and monitoring](docs/preview-performance.en.md)
- [Storage limits, streaming and file cleanup](docs/STORAGE.en.md)
- [Architecture](docs/ARCHITECTURE.en.md) · [runtime roles and thin-shim rule](docs/RUNTIME_ROLES.en.md) · [direct NR protocol](docs/direct-nr-relay.en.md)
- [Project and runtime directory layout](docs/PROJECT_LAYOUT.en.md)
- [Development](docs/DEVELOPMENT.en.md) · [all documentation](docs/README.en.md)

## License and safety

Original code: [MIT](LICENSE). Included NVIDIA API headers retain their original
notices; see [third-party boundaries](THIRD_PARTY_NOTICES.md).
No proprietary NR Worker/model, driver, Proton or test media is bundled.

External files execute native code. A separate Worker/Proton prefix is a crash
boundary, **not a security sandbox**. Use trusted binaries and review source
terms. Redact local paths, hostnames and media names before sharing task details.
