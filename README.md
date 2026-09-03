# comfy-dlss-experimental

An experimental ComfyUI V3 custom node for researching DLSS neural rendering as an offline video post-process.

The repository can be cloned directly into `ComfyUI/custom_nodes`:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/palering/comfy-dlss-experimental.git
```

Cloning loads the nodes but does not install native helpers or external runtimes.
See [installation, prebuilt helper packages and platform selection](docs/distribution.md).
`install.py --archive <helper.zip> --sha256 <trusted-hash>` installs only our helpers,
not drivers, SDKs, Proton or vendor DLLs. This initial publication is source-only;
prebuilt helper assets have not yet been published to GitHub Releases.

Current experimental nodes include:

- **DLSS 5 Runtime Probe** detects Windows/Linux, NVIDIA, FFmpeg, Vulkan and installed Proton wrappers.
- **DLSS 5 Runtime Configuration** binds a backend, exact Proton version, runtime preset and worker policy.
- **DLSS Carrier Bootstrap Test** stages and executes the real Proton/ReShade/RenoDX/D3D12 chain without claiming a DLSS frame result.
- **DLSS NR Look** controls NR intensity, style/preset IDs, local tone/structure,
  skin structure, automatic mask, advanced UI/profile flags, original/result
  blend and bypass. See the [parameter guide and measured response](docs/nr-look.md).
- **DLSS Process Video** produces a standard VIDEO for native Save Video or downstream nodes.
- **DLSS Optical Flow · DIS / NVIDIA** are connectable, lazy optical-flow providers.
- **DLSS Video Guides** combines the connected provider with analysis resolution, cut resets and consistency diagnostics; old DIS/zero workflows remain supported.
- **DLSS Video Input Adapter** displays video information and a compatibility/interpretation report. Its optional **统一为 sRGB（SDR）** switch converts the selected range before flow/NR, caches the converted frames, and reverses the working transfer for matched A/B export; default off.
- **DLSS History Settings** controls first-frame warmup and preceding context, not DLAA/SR quality.
- **DLSS Preview Session** renders a selected range with A/B controls directly inside its node card.
- **DLSS Compare Video (Scaffold)** reserves deterministic comparison-video export.

On Linux, Proton is discovered from the final launcher directories used by Steam, ProtonPlus, Lutris and Heroic. ProtonPlus is not a runtime dependency: it installs tools into launcher-owned locations, and the node reads those installations. Set `COMFY_DLSS_PROTON_PATHS` (colon-separated on Linux) for custom directories.

No NVIDIA, ReShade, RenoDX, Proton, or other third-party binaries are included or downloaded. Runtime files and prefixes live outside the repository, normally at:

```text
ComfyUI/user/default/comfy-dlss-experimental/
  components/
  runtime-presets/
  prefixes/
  jobs/
  cache/
  prepared-clips/
  runtime-snapshots/
```

The optional NVIDIA optical-flow provider uses a project-built **native helper**
(Linux ELF or Windows PE) and the host's driver libraries, not Proton. The Windows
port is cross-compiled but awaits real Windows GPU validation. Users install prebuilt
packages; maintainers build with `python sidecar/build_nvof.py --target ...` and
existing Zig/CUDA headers. No driver,
OpenCV, PyTorch or CUDA toolkit is installed automatically. The two NVIDIA API
headers in `sidecar/vendor/nvof` retain their source redistribution notices.
See [NVIDIA optical flow setup and validation](docs/nvidia-optical-flow.md).
`workflows/dlss_nvidia_flow_preview.json` adds a connected provider to the preview.

The browser extension implements a draggable split, side-by-side, flicker,
visual difference, frame seeking and status directly inside the Preview Session
node card. Render preview queues only that node's dependencies, not unrelated
export branches. Changing a look reuses the independently cached media guides.
Cancellation belongs to the current preview session. Outputs remain separate
from the input; no vendor DLL is loaded into Comfy's Python process.

The active **direct Feature 18 backend** produces real processed frames
on Linux through Proton, using the user's existing video-converter worker and
NR model. Our small C++ relay is built with Zig; this route does not require
compiling the vendor SDK, running the converter's web server, ReShade, or
RenoDX. It is connected to both Preview and Process Video nodes. See
[the direct NR guide](docs/direct-nr-relay.md) for the verified results,
external-file boundary, build instructions, and remaining work.

The [real-video diagnostic](docs/video-nr-validation.md) now adds bounded clip
decoding, CPU motion guides, pre-roll, matched original/processed exports and
audio. The Comfy integration uses these same modules; see the
[node quickstart and validation](docs/comfy-video-nodes.md).

The [input adapter guide](docs/video-input-adapter.md) explains strict versus
explicit missing-color interpretation, the inline Check Input button, and
DIS/zero-vector switching. Unknown input tags are not silently guessed.

Start with `workflows/dlss_native_preview.json` and
`workflows/dlss_native_process.json`. Set the input video, installed Proton and
a local `direct_nr` runtime preset; `examples/runtime-presets/direct-nr.example.json`
shows the three required components. Importing a JSON does not save it to
Comfy's workflow library; use Comfy's Save command for that. Old scaffold
workflows use different controls: recreate them using the new templates.
The example filename `example-input.mp4` is a placeholder; choose or upload your
own input video. No sample media is distributed.

Initial bounds: supported tagged SDR or explicitly interpreted missing SDR tags,
native resolution (optional preview downscale),
CFR, disk-backed color/motion preparation. Preview and Process offer a
**处理到视频末尾** toggle: start at 0 for the whole input, or keep a nonzero
start to process its remainder, without entering a duration. The former
30-second/2-GiB cutoffs are removed; frame-count and available-disk checks remain.
See [output ranges versus processing mechanisms](docs/comfy-video-nodes.md#output-range-and-processing-mechanisms).
Overlapped decode/NR/encode streaming, HDR, upscaling, frame generation, external depth/mask
textures are not implemented. Some exposed experimental
NR fields produce identical pixels on the tested worker/model; availability
is not a promise of visual response. Preview downscaling can change
NR appearance; use 100% for a final look check. Windows code paths exist but
real GPU validation has only been performed on Linux/Proton.

Runtime Configuration offers **按需启动并常驻** (off by default). When enabled,
the verified worker/model pair can retain one lazy-started GPU instance for
repeated previews with the same NR controls and dimensions. Idle timeout defaults
to 300 seconds; the runtime card can release it manually. Changing NR controls,
dimensions, or runtime still rebuilds the instance. See
[resident worker behavior and measurements](docs/preview-performance.md#opt-in-resident-worker).

On Linux, Runtime Configuration exposes `auto`, `xwayland`, and
`wayland_native_experimental`. `auto` deliberately selects the verified
Xwayland route. Direct NR rejects native Wayland until it is verified; the
older carrier diagnostic retains its opt-in Wayland experiments. A Wayland desktop does not need to be
replaced with an X11 session: on Hyprland, the existing rootless Xwayland socket
is the intended default execution route.

The [runtime compatibility note](docs/dlss5-runtime-research-2026-09.md)
distinguishes the active external-worker backend from retained ReShade/NGX
diagnostics and future extension points.

Preview now supports cursor-frame or range rendering, cached encoded original A,
per-execution stage timing, and a Runtime Configuration worker/history foldout.
The loopback relay enables TCP_NODELAY on both peers to avoid delayed-ACK frame
stalls. See [preview performance and diagnostics](docs/preview-performance.md)
for measured results, monitoring limits, and the staged concurrency plan.

## License and contributions

Original code: [MIT](LICENSE). Included API headers retain their original
notices; see [third-party boundaries](THIRD_PARTY_NOTICES.md).
Start with the [documentation index](docs/README.md) and
[development guide](docs/DEVELOPMENT.md). Before sharing issue reports, redact
absolute paths, private hostnames, video filenames and local runtime locations.
Never attach proprietary DLLs, source videos, credentials or raw runtime folders.
