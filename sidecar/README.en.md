# Windows sidecar

English · [简体中文](README.zh-CN.md)

Audience: public

The active video path uses **dlss-native-relay.exe plus an external Worker/model**;
see [direct NR](../docs/direct-nr-relay.en.md) and [helper distribution](../docs/distribution.en.md).
It builds with existing Zig, without a Windows/MSVC host, NGX SDK or ReShade.
Native optical flow builds separately for each host platform.

Optional refactored paths: [owned NR runtime](../docs/owned-runtime.en.md) uses
our Worker and thin caller; [SR/DLAA](../docs/super-resolution.en.md) uses the same
Worker built with the official SDK, without the NR caller. SR source/CPU checks
do not establish a linked or GPU-tested SDK binary. Existing relay presets are
not migrated automatically. The sections below retain the legacy/diagnostic context.

## Streamline CXR1 reconstruction Worker and diagnostic

The separate [CXR1 development backend](../docs/streamline-reconstruction.en.md)
now connects explicit Python camera/SR/RR inputs to this feature engine. Build
with `build_sl_worker.py`; the resulting `comfy-dlss-sl-worker.exe --serve-sl`
is not the default NR/CSR1 Worker. Its controlled GPU/transport validation is
separate from the synthetic probe below. The explicit
[owned_sl reconstruction nodes](../docs/reconstruction-nodes.en.md) have also
passed bounded Comfy SaveVideo integration tests.

`sl_sr_engine.*` is a separate C++ feature adapter with explicit numerical camera
constants and no Comfy/media/transport code. `sl_sr_probe.cpp` supplies only a
synthetic static checker scene: RGBA16F color, zero RG16F motion and a flat R32F
device-depth plane. It is not the `owned_sr` implementation or a video node.

Build with an existing Zig and user-supplied official Streamline headers:

```bash
python3 sidecar/build_sl_sr_probe.py --streamline-include /path/to/streamline/include --native-tests
```

This cross-build dynamically resolves SL exports, without linking NGX or SL
import archives or installing an MSVC sysroot. It produces
`sidecar/build/sl-sr-probe/comfy-dlss-sl-sr-probe.exe` plus a build/hash report.
Supply matching `sl.interposer.dll`, `sl.common.dll`, `sl.dlss.dll` and
`nvngx_dlss.dll` and their runtime dependencies separately. No vendor files are
downloaded or copied by this build script.

The diagnostic accepts absolute runtime and new job directories, then
`device|frame|sequence` and a nonzero project UUID, optionally followed by
`sr|dlaa|rr|rr-dlaa` (default `sr`). RR additionally requires `sl.dlss_d.dll` and
`nvngx_dlssd.dll`; its fixture supplies explicit synthetic material buffers.
Run stages in that order,
with an external timeout and isolated process ownership. It writes JSONL events
and FP16 output files. It uses a hidden swapchain and an actual hooked Present
per frame for SL lifecycle bookkeeping; processed output is read from a separate
offscreen texture. OTA flags are not enabled. GPU waits and the overall probe
have deadlines; failure does not imply a safe automatic retry.

The bounded 640×360 -> 960×540 synthetic test has passed device/Present, one-frame
and eight-frame inference on Linux/Proton with supplied SL 2.14.1 binaries. All
channels were finite; resetting frame five reproduced the first four outputs
exactly. This is not natural-video quality, long-run, native Windows, full ABI,
CSR1 transport or Comfy SR acceptance. CSR1 currently lacks this adapter's camera
metadata; the adapter does not silently invent it for a video. SL SR's public
interface also does not expose the NGX frame-time parameter. Existing backend
selection and default Worker capabilities are unchanged.

The separate [project-owned caller shim](../docs/caller-shim.en.md) is a
development component with typed forwarding tests. It is not used by the active
video path and must not replace the external Worker. Its optional build uses
official SDK headers; ordinary relay installation still does not need them.

The remaining sections describe retained loader/GPU-copy/ABI diagnostics,
not installation prerequisites or the active NR input contract.
The diagnostic sidecar is a separate Windows process that owns D3D12 and NGX. The Python
node must never load NVIDIA, ReShade, or RenoDX DLLs into the ComfyUI process.
On Windows it runs natively; on Linux the same executable runs through a
user-selected Proton installation.

The versioned control and frame wire formats are documented in
`protocol/sidecar-v1.schema.json` and `protocol/frame-stream-v1.en.md`. The Python
reference codec lives in `comfy_dlss_experimental/sidecar_protocol.py`; it is
kept independent of ComfyUI so malformed-packet and round-trip tests can run on
macOS before the matching Windows reader is added.

`src/frame_protocol.cpp` is the matching allocation-bounded C++20 parser. Its
platform-neutral self-test can run on the development host without Wine, a GPU,
or proprietary files:

```bash
bash sidecar/build_protocol_test.sh
```

## Loader probe

`src/probe.cpp` is the first executable milestone. It creates a D3D12 device,
loads a user-selected `nvngx_dlssnr.dll`, checks the NGX D3D12 exports, and
writes one JSON result to stdout or to the optional second path argument. It
does not evaluate a frame yet.

Build it on the Linux ComfyUI host with the already-installed Zig toolchain:

```bash
bash sidecar/build_probe.sh
```

This produces `sidecar/build/dlss-sidecar-probe.exe`. Zig provides the Windows
target headers and libraries, so this repository does not require llvm-mingw or
a system-wide MinGW installation.

The project never distributes NVIDIA DLLs. Point the probe at a DLL supplied by
the user and stored outside the repository's tracked files.

## D3D12 carrier bootstrap

`src/carrier.cpp` is the next executable milestone. It loads an app-local
ReShade proxy as `dxgi.dll`, creates a D3D12 12_0 device and hidden windowed
swapchain, then emits a bounded Present heartbeat so ReShade can load the
app-local `renodx-dlss5.addon64` add-on. It reports the result as JSON and
explicitly reports that DLSS frame evaluation is not implemented yet.

Build it with the same existing Zig toolchain:

```bash
bash sidecar/build_carrier.sh
```

Run the resulting PE only from an isolated job directory containing:

- `dlss-carrier.exe`
- ReShade renamed to `dxgi.dll`
- `renodx-dlss5.addon64`
- `nvngx_dlss.dll`
- `nvngx_dlssnr.dll`

The carrier creates `ReShade.ini` defaults only for settings that are absent.
On Linux, the process needs a graphical Wine/Proton session even though its
carrier window is hidden. It never installs or selects Proton itself.

`run_carrier_smoke.sh` stages copies into a new, non-reused job directory and
runs the carrier with a user-selected Proton and prefix. It applies the Wine
DLL overrides required by this route:

```text
d3dcompiler_47=n;dxgi=n,b
```

All seven paths are explicit arguments; the script never searches for or
downloads proprietary components.

## D3D12 frame-stream milestone

`--frame-worker` keeps the initialized NVIDIA D3D12 device and command queue
alive after swapchain creation and enters the binary frame protocol. Native
Windows can use stdin/stdout. Proton's launcher redirects a Windows child's
standard streams, so Linux uses `--tcp 127.0.0.1 PORT TOKEN_HEX` to connect back
to an ephemeral loopback-only Python server. A random 256-bit token is verified
before any packet is accepted. The diagnostic worker accepts input-sized
`RGBA8_UNORM` or `RGBA16_FLOAT` color, `RG16_FLOAT` motion, `R32_FLOAT`
depth, `R8_UNORM` reactive mask, and 1x1 `R32_FLOAT` exposure. Color is
required; guides are optional. It uploads all planes to default-heap textures,
transitions them from copy destination to copy source, and reads them back
behind a bounded fence wait. It returns `OUTPUT_COLOR` and the guide planes,
with tightly packed rows, for byte-level verification. It performs no color
conversion, guide estimation, or NGX evaluation.

`HELLO` advertises `ngx:false` and `configuration_supported:false`.
`CONFIGURE` replies `accepted:false`: this renderer must never imply that
DLSS/RenoDX effect settings were applied. Diagnostic frame copies remain usable
without configuration. Input and output dimensions must be equal.

Worker mode skips the flip-model Present heartbeat after swapchain creation.
No presentation is needed for these offscreen copies; ordinary bootstrap mode
retains the bounded Present test. The previously observed transport stall was
traced to Proton standard-stream redirection, not proven Present backpressure.
RenoDX module
presence is still checked after the skipped heartbeat, so the worker fails
closed if the add-on was not loaded synchronously from the D3D12 device hook.

This is a real GPU transport test, not the final visual effect. It separates
protocol, Proton pipes, row-pitch handling, resource barriers, synchronization,
and readback failures from later NGX integration. A deterministic target-host
test is available as:

```bash
python scripts/run_frame_worker_smoke.py \
  --preset /path/to/runtime-preset.json \
  --proton /path/to/proton \
  --data-root /path/to/test-data \
  --display-backend xwayland
```

The test runs 15 frames / 39 planes in one worker, alternating dimensions and
formats, issuing three RESETs and checking frame indices, timestamps, and active
row bytes. Cases include padded odd-sized rows, FP16 HDR values, signed motion,
depth, exposure and masks. Success also requires ReShade, RenoDX, the NVIDIA
adapter, swapchain, clean shutdown and exit code zero. The job contains
`frame-stream-report.json` and `frame-worker-result.json`.

The synchronous client completes short writes before reading each response;
it never sends a full video's packets before consuming results. Stderr goes to
a job log rather than an undrained pipe. On failure, the Linux test supervisor
targets its launch group and exact per-job environment marker via pidfds, not
a broad command-line regex. This test helper is not yet the production pool.

Portable validation can be run with:

```bash
python -m unittest discover -s tests -v
SANITIZE=1 bash sidecar/build_protocol_test.sh
bash sidecar/build_carrier.sh
```

## Synthetic NGX research build

`src/ngx_smoke.cpp` adds a bounded synthetic DLSS/DLAA Create/Evaluate probe.
It dynamically resolves the public driver bootstrap `nvngx.dll`; it does not
link or package `nvsdk_ngx_d.lib`. The official SDK headers are required only at
build time and remain outside this repository:

```bash
NGX_SDK_INCLUDE=/path/to/NVIDIA-DLSS/include bash sidecar/build_renderer.sh
```

This executable is a negative-control diagnostic, not the default carrier.
Target-host testing showed that dynamically reconstructing the exported NGX
bootstrap ABI crashes inside NGX even after the NVIDIA adapter is selected
explicitly, while an SDK-linked control host initializes successfully. It is
retained to keep that result reproducible, not as the production renderer. A
failed NGX call is contained by the job process and must not be interpreted as
a ComfyUI or Python crash.

This historical SDK-linking experiment concerns a possible official backend,
not the current direct relay. The supplied
SDK archive was compiled with the MSVC `/MD` ABI and depends on `msvcprt`,
`MSVCRT`, MSVC exception unwinding, stack-cookie support and thread-safe static
initialization. Zig's MinGW target is sufficient for the baseline carrier but
is not a safe direct linker for that archive. Do not implement substitute CRT/SEH symbols. Any future official backend must
follow its supported toolchain contract; this is not a current build prerequisite.

For Linux jobs the Python launcher makes the graphical path explicit:

- Xwayland: `WINE_GRAPHICS_DRIVER=x11` with a valid `DISPLAY`;
- experimental native Wayland: `WINE_GRAPHICS_DRIVER=wayland` with a valid
  `WAYLAND_DISPLAY`.

The two modes use different Proton prefixes.

These display options apply to diagnostics; active direct NR uses Xwayland only.
