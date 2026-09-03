# Direct Feature 18: isolated external-worker adapter

English · [简体中文](direct-nr-relay.zh-CN.md)

Audience: public

Status: connected to ComfyUI video processing and node-card preview; see
[the Comfy integration](comfy-video-nodes.en.md). A working NGX invocation is
not proof of perceptual quality or temporal stability.
The follow-up [real-video validation](video-nr-validation.en.md) now implements
bounded clip preparation, motion guides and matched video exports.

## What runs, and what we build

```text
Host Python (media inputs / settings / supervision)
  -> authenticated loopback TCP, ephemeral port (CLR1)
  -> dlss-native-relay.exe (our C++, compiled with existing Zig)
  -> Windows anonymous stdin/stdout/stderr pipes
  -> nvngx.dll --video (original user-supplied worker executable)
  -> nvngx_dlssnr.dll (matching user-supplied NR model/runtime)
  -> processed RGBA8 frames back along the same path
```

The relay is a transport/process bridge, not a renderer or NGX caller shim.
The external worker retains its original `nvngx.dll` executable filename and
its existing direct Feature 18 implementation. We neither rename it to the
relay nor introduce another `caller/nvngx.dll`. The matched worker/model pair
was tested with the RTX40 v0.1.0 converter package.

No converter web service, Windows `nvidia-smi.exe`, ReShade, RenoDX, Streamline,
extra SDK library, or Windows build host is required for this route.
GPU preflight belongs on the native host; successful runtime creation/evaluation is the
actual compatibility check. The ReShade backend remains available for research.

The relay build produces these Windows artifacts (native optical flow is separate):

- `sidecar/build/dlss-native-relay.exe`: the real transport bridge.
- `sidecar/build/mock-nr-worker.exe`: GPU-free protocol/failure test fixture,
  never a substitute for DLSS output.

Do not redistribute the external worker/model with the custom node. Users
provide trusted local files. Loading these files executes native code; a
separate process and Proton prefix are crash/isolation boundaries, **not** a
security sandbox. Record exact hashes rather than assuming DLL filenames
identify a compatible version.

## Protocol and process ownership

The relay connects to `127.0.0.1` and authenticates with a per-run 32-byte token.
It starts the child only after acknowledgment. CLR1 packets have a 12-byte
little-endian header: magic `0x31524C43`, kind, payload byte count. Payloads
are bounded to 65,536 bytes. Channels are stdin, stdin EOF, cancel, stdout,
stderr, exit, error, and startup process IDs. This is independent of the older
`sidecar/protocol/frame-stream-v1.en.md` renderer protocol.

`direct_nr.py` implements the selected external worker's D5V2 protocol:

| Message | Encoding | Payload |
| --- | --- | --- |
| Video header | `<10I4f>`, 56 bytes, magic `0x32563544` | dimensions, warmup, frame count, integer controls, float controls |
| Input frame | `<4Iq>`, 24 bytes, magic `0x314D5246` | index, reset, reserved zero, timestamp; RGBA8 color followed by RG16F motion |
| Output frame | `<5Iq>`, 28 bytes, magic `0x3154554F` | index, success flag, byte count, NGX result, timestamp; RGBA8 color |

Both input planes occupy width × height × 4 bytes. Frame size, index,
timestamp, success code, terminal exit and unexpected trailing output are
checked. A failed stream cannot be reused. Depth, exposure, and reactive masks
are not supported by this external-worker interface. This smoke test uses a
stationary chart with zero motion. Follow-up motion-vector tests and real-clip
evidence are recorded in [video-nr-validation.en.md](video-nr-validation.en.md).

In isolated mode one relay/worker is launched per run. Native code uses a Windows Job Object
with kill-on-close, suspended creation before assignment, an explicit inherited
handle allowlist, bounded packets and separate output/log draining threads.
Python caps buffered stdout at 256 MiB and retained worker diagnostics at
4 MiB while continuing to drain excess diagnostic bytes. Proton's separate
launcher/debug logs are not capped by that worker-log limit.

Cancellation is best-effort over the control channel, then socket shutdown.
A blocked child pipe can delay the relay's own cancellation handling. Linux
therefore also uses a random per-run environment marker, same-user checks,
and pidfds to terminate only that run's processes, including detached Proton
children. Cleanup fails if marked processes remain. It never kills by a
generic Wine/Proton process name. An exclusive prefix lock prevents simultaneous
reuse; failed cleanup retains the lock until a successful cleanup retry.
This Linux fallback requires pidfd support and readable same-user `/proc`.

The default adapter is isolated per run. Opt-in residency now keeps a bounded
D5V2 stream open for compatible tasks on the verified worker/model pair; each
task resets temporal history without repeating initial model warmup. Switching
DLLs, dimensions, or NR parameters still means a new instance, not DLL hot
replacement. Live in-process NR parameter editing is not implemented. See
[resident lifecycle and validation](preview-performance.en.md#opt-in-resident-worker).
Native Windows launch
is supported by the code path but has not been exercised on a Windows host.

## Build and test

From the repository root, using the existing Zig installation:

```bash
bash sidecar/build_relay.sh
python3 -m unittest discover -s tests -v
c++ -std=c++20 -Wall -Wextra -Wpedantic -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer -I sidecar/src \
  sidecar/tests/relay_protocol_test.cpp -o sidecar/build/relay-protocol-test
sidecar/build/relay-protocol-test
```

No NVIDIA headers or static libraries are needed for this build. Native code
follows [sidecar-cpp-quality.en.md](sidecar-cpp-quality.en.md). Portable protocol
sanitizer tests do not imply that Windows/Proton code was sanitizer-tested.

On Linux, prepare a dedicated test directory alongside ComfyUI, not under a
system directory or a shared software installation:

```text
dlss/direct-nr-experimental/
  bin/                 # our relay and mock executables
  runtime/             # user-supplied nvngx.dll and nvngx_dlssnr.dll
  code/                # Python adapter, scripts and tests
  prefix-mock/         # GPU-free test prefix
  prefix-direct/       # separate real-runtime prefix
  cache/
  tmp/
  mock/<run-id>/        # report and per-case diagnostics
  direct/<run-id>/      # report, logs and raw frames
```

The following paths are examples; use the installed Proton version and the
active desktop session's display values. The diagnostic requires `DISPLAY`
and selects Xwayland. It does not switch the Wayland desktop session or verify
Proton's native Wayland rendering path.

```bash
python3 scripts/run_direct_nr_smoke.py \
  --relay /path/to/dlss/direct-nr-experimental/bin/dlss-native-relay.exe \
  --worker /path/to/dlss/direct-nr-experimental/bin/mock-nr-worker.exe \
  --proton '/path/to/installed/Proton/proton' \
  --root /path/to/dlss/direct-nr-experimental --mock-suite --timeout 30

python3 scripts/run_direct_nr_smoke.py \
  --relay /path/to/dlss/direct-nr-experimental/bin/dlss-native-relay.exe \
  --worker /path/to/dlss/direct-nr-experimental/runtime/nvngx.dll \
  --proton '/path/to/installed/Proton/proton' \
  --root /path/to/dlss/direct-nr-experimental \
  --timeout 300 --warmup 120 --frames 8 --intensity 1.0
```

The current smoke launcher assumes Steam is installed at
`~/.local/share/Steam`; it is a diagnostic, not the general runtime discovery
UI. The first prefix launch may take longer than a reused-prefix run. Do not
point tests at a prefix used by games or another running application.

## Verified evidence and remaining limits

Target: RTX 4070 Ti SUPER, Linux NVIDIA driver 610.57.04, GE-Proton11-6,
Wayland desktop with Xwayland variables. Tested RTX40 bundle fingerprints:

- Worker SHA-256:
  `99ef1f2976d9cd16b7fc269adb6c6450fb64c81a522c9b9e6edc6a28201dc904`
- Model SHA-256:
  `28bdc080d28686decdb63f6f4246b022274916b80aafdab266fe0fb63b2b9265`

The portable C++ protocol test passed with AddressSanitizer and
UndefinedBehaviorSanitizer on macOS.
The GPU-free Proton suite exercises exact byte round trips, interleaved log flooding,
early exit, truncated output, NGX-error propagation, cancellation, disconnect,
timeout, and cancellation with blocked input. All nine passed, with zero
remaining run-owned processes. These tests explicitly do not call NGX.

The real 640×360 stationary-chart test created direct Feature 18 with
`0x00000001`, completed 120 warmup evaluations plus 8 output evaluations, and
returned all 8 full-size frames with a successful worker exit. Output differs
from input. Runs at intensity 0.25 and 1.0 produced different frame hashes.
Resetting history at frame index 2 reproduced the initial sequence in this
fixture. Independent post-test checks found no relay/worker process and GPU
memory returned to the idle baseline of 3 MiB. Roughly three seconds per
reused-prefix smoke run is **not** a video throughput benchmark.

The 120-evaluation run above is a historical synthetic test configuration, not
a requirement to repeat that warmup for every preview. The History Settings
node controls warmup and context; residency reuses initial model setup where safe.

This establishes that the selected worker/model combination can execute under
the selected Proton version; it does not establish universal version support,
natural-image quality, long-video stability, optical-flow correctness, native
Wayland support. Comfy preview/export nodes were validated separately after
this diagnostic (linked above). Other header controls
must be checked against worker behavior before claiming they affect output.

Short-clip preparation, scene resets and motion-guide tests now exist. The
Comfy integration has since added external-worker presets, node-card preview,
bounded video export and input diagnostics. See the linked integration guides.
DLL selection, media preparation, effects, and process policy remain separate.
