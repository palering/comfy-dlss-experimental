# Connectable optical flow and NVIDIA NVOFA

Audience: public

## Node wiring

Connect either **DLSS Optical Flow · NVIDIA** or **DLSS Optical Flow · DIS** to
`DLSS Video Guides.flow_provider`. Video Guides settings feed Video Input
Adapter alongside the source VIDEO; its sequence feeds Preview / Process Video.
These provider nodes emit serializable configuration, not an eagerly generated
whole-video tensor. The consumer estimates only its selected range and pre-roll.

Video Guides still owns analysis scale, cut detection and forward/backward
consistency diagnostics. NR Look still owns the NR appearance. A connected
provider overrides the legacy `motion_provider` widget; without a connection,
existing DIS and zero workflows behave as before. There is no implicit fallback
when NVIDIA is selected but unavailable.

NVIDIA controls are readable Fast / Balanced / Quality presets, 4x4 / 2x2 / 1x1
output grid, temporal hints and CUDA device ordinal. The driver validates grid
support. Denser output is not a guarantee of better NR. Linux is GPU-verified;
the Windows native helper now cross-compiles but still requires Windows GPU
validation. See [prebuilt installation and platform boundaries](distribution.md).

## Build and runtime dependencies

Run from the custom node repository on Linux:

```sh
bash sidecar/build_nvof.sh
sidecar/build/dlss-nvof-helper --probe 0
```

The script uses existing Zig, C++20, and CUDA Driver API headers. Default header
location is `/opt/cuda/targets/x86_64-linux/include`; set `NVOF_CUDA_INCLUDE` if
the host uses a different layout. It does not install software. There are no
CUDA kernels and no nvcc, TensorRT, model weights, FreeImage, OpenCV CUDA build,
or new Python package requirements. Runtime dynamically loads driver-provided
`libcuda.so.1` and `libnvidia-opticalflow.so.1`; neither is bundled.

The two pinned official headers use **NVOF API 2.0**, not the API 5.0 structures
shown in current programming documentation. They support the needed CUDA calls
and 1/2/4 grids. Forward/backward flow uses two independent sessions so temporal
hints do not mix directions. This is not the API 5.0 combined-direction call.

Sources:
- https://github.com/NVIDIA/NVIDIAOpticalFlowSDK/tree/edb50da3cf849840d680249aa6dbef248ebce2ca
- https://docs.nvidia.com/video-technologies/optical-flow-sdk/nvofa-programming-guide/index.html

## Data and lifetime contract

The Python preprocessing layer decodes RGBA8, constructs analysis-resolution
grayscale uint8 frames and communicates with one native helper per clip request.
The helper receives previous/current frames, index and reset flag over bounded
binary pipes. It validates dimensions, protocol framing and queried hardware
limits, respects GPU row pitch and explicitly synchronizes before reading flow.

Returned grids use two signed S10.5 components: divide by 32 to obtain input-
pixel displacement. Grid interpolation does **not** multiply displacement by the
grid size. Python densifies, restores processing-resolution pixel units and
packs the existing little-endian float16 XY plane. Direction remains current to
previous, top-left origin, positive X right / Y down. NR's D5V2 protocol is unchanged.

First frames and cuts produce zero vectors plus NR history reset. Both NVOF
histories are invalidated before the next estimation. A helper is reused across
the selected range and reaped before that request's NR processing. There is no
daemon, listener, Proton dependency or display-server dependency for optical flow.
Unrelated requests may still use the same GPU; no exclusive device ownership is
claimed. The existing guide cache lock serializes clip preparation.

Pipe waits are cancellable and have a 30-second per-exchange deadline, stderr
is bounded, and exceptions/normal completion close the exact child. C++ owners
are non-copyable RAII wrappers. GPU completion failures terminate the isolated
process without unwinding resources still potentially in flight.

Cache identity includes provider parameters, helper SHA-256, driver information,
device selection and the existing source/range/color settings. A capability probe
is separate from a successful computation; a cache hit still validates availability.
Manifest records backend identity and per-frame guide metrics. Consistency is
diagnostic only: no confidence, cost, depth or mask texture is fed into NR.

## Verification (Linux, 2026-09-03)

RTX 4070 Ti SUPER reported output grids 1/2/4 and bounds 32..8192 on each axis.
Our adapter retains the existing pipeline's stricter bounds. Existing CUDA headers
and Zig were sufficient; no system dependencies or main Comfy environment changed.

- Synthetic translation (+6,+4): current-to-previous median (-6,-4) for all three
  grids at both 50% and 100% analysis resolution; static-frame and reset checks.
- All three presets execute; cancellation, malformed input, bounded logs and
  subprocess reaping have automated tests.
- Dedicated Comfy integration: legacy DIS, connected DIS, NVIDIA 4x4 and 1x1 each
  produce a 12-frame NR preview from the same real video; repeat NVIDIA request
  hits cache while changing grid produces a distinct cache entry.

Run hardware tests explicitly (use the existing media-capable Python environment):

```sh
DLSS_TEST_NVOF=1 python -m unittest discover -s tests -p 'test_flow_provider.py' -v
```

`scripts/check_nvidia_flow.py` is an opt-in test-server integration runner.
`scripts/make_nvidia_flow_workflow.py SOURCE DESTINATION` creates a **new** UI
workflow, preserving runtime settings; it refuses to overwrite an existing file.

Performance/visual superiority over DIS has not been established by these tests.
No long-video streaming, zero-copy, external hints, cost-plane handling, learned
flow models, or new NR G-buffer inputs are implemented by this change.
