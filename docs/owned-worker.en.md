# Owned Worker acceptance host

English · [简体中文](owned-worker.zh-CN.md)

Audience: public

`sidecar/src/nr_probe.cpp` builds an experimental `comfy-dlss-worker.exe` with
bounded, separate acceptance modes. It owns its D3D12 resources and calls the supplied
NR model through our [thin caller](caller-shim.en.md). The experimental
[owned_nr runtime](owned-runtime.en.md) integrates it into Comfy via the separate
[CNR1 interface](owned-protocol.en.md). It does not implement D5V2 or silently replace
existing presets. Short Linux/Comfy acceptance passed; long-video and Windows GPU
acceptance remain pending. Historical diagnostic modes are documented below.

## Components

| Component | Responsibility |
| --- | --- |
| `nr_probe.cpp` | Diagnostic modes and CNR1 request/session loop. |
| `nr_engine.*` | Hardware selection, local DLL loading, Feature lifecycle, textures/transfers and GPU fences. |
| `nr_parameters.cpp` | Compiler-generated implementation of the official Parameter interface; bounded storage for 192 keys, checked conversions, synchronized access. |
| `nr_parameters.h` | Opaque C interface used by the host; never call the Parameter vtable from a MinGW translation unit. |
| `nr_input_contract.hpp` | Explicit full-frame Color/MVec/Output subrects, checked through the SDK interface in the ABI self-test. |
| `nr_parameter_memory.cpp` | Allocation/free in the same host runtime through application-owned C functions. |
| `nr_caller.cpp` | Stateless typed forwarding only; no video or parameter storage. |
| `probe_report.hpp` | Authenticated loopback readiness, ordered stage reports and acknowledged completion; bounded I/O. |
| `run_owned_probe.py` | Linux CPU-only acceptance runner using `proton run`, independent of wrapper exit/stdout. |

The overloaded Parameter interface has different virtual slot ordering under
Microsoft and MinGW C++ ABIs. We compile its implementation and a separate virtual
call test as **Microsoft-ABI objects** using the existing Zig compiler. The host
uses the MinGW target and crosses that boundary only through C functions and
opaque pointers. No hand-written vtable or substitute MSVC runtime symbols are used.

The build uses Zig's bundled Windows C headers and `ntdllcrt` import library for
the floating-point-use marker, resolved from the platform's `ntdll.dll`. It does
not link an NVIDIA SDK static archive or an MSVC C++ standard library, download
an SDK, or install another compiler. Windows/Proton loader acceptance remains a
required real-host check; cross-linking is not proof of runtime compatibility.

## Build

Supply the official NVIDIA DLSS SDK headers separately:

```bash
python3 sidecar/build_owned_worker.py --ngx-include /path/to/NVIDIA-DLSS/include --native-tests
```

An existing `zig` and host `clang++` (or `CXX`) are required. Omit `--native-tests`
if host sanitizer tooling is unavailable. `--zig` and `--zig-lib-dir` support
explicit existing tool locations. All objects, executables, tests and default
compiler caches stay under ignored `sidecar/build/`.

## Target-host acceptance order

The newer [continuous-frame/streaming results](owned-protocol.en.md#verification-boundary)
extend the initial single-frame evidence below. CNS1 `--probe-sequence` is a
separate diagnostic: at most 16 frames and 64 MiB, preflighted before GPU use,
one-frame memory, exclusive output-file creation. It is not a video cache format.

The owned model path has passed **one bounded 640×360 synthetic-frame test** on
Linux/GE-Proton with an RTX 4070 Ti SUPER: Init/Create/Evaluate, both GPU fences,
finite FP16 readback, Release and Shutdown. This is not video or visual acceptance.
A previous smaller experimental run encountered device loss. We corrected missing
motion subrect fields, but dimensions and launch setup also changed, so the earlier
failure's unique cause is not proven. Do not run automatic installation-time GPU
probes or infer supported dimensions from this program's argument bounds.

For Linux, prefer the CPU-only runner below. It uses the established `proton run`
path and waits for authenticated Worker messages, not the launcher's return code:

```bash
python3 sidecar/run_owned_probe.py \
  --worker /path/to/comfy-dlss-worker.exe \
  --proton /path/to/proton \
  --prefix /path/to/isolated-existing-compatdata \
  --steam /path/to/Steam \
  --output /path/to/new-attempt
```

The prefix must already be initialized and dedicated to testing. The output
directory must be new, with an existing parent. The runner preserves logs, holds
a prefix lock, imposes a deadline and shuts down **only that prefix** afterwards.
Do not point it at a game or daily Comfy prefix. `--mode report-failure` performs
a deliberate CPU-only failure; expected Worker/runner code is 1 even if Proton
returns 0. No model/GPU mode is exposed by this Python runner.

The C++ host optionally accepts `--report PORT TOKEN` (runner-generated loopback
endpoint and 32-byte random token in hexadecimal). Protocol 1 uses bounded JSON
lines with sequence/stage/state/code, an initial authentication acknowledgment
and a final completion acknowledgment. In connected mode our host emits no console
result/error messages; diagnostics use the socket so console handling cannot
block the completion path. Third-party runtime logging is not controlled by this.
Stage reports distinguish API return from GPU completion, especially
`create_fence` and `evaluate_fence`. Transport failure exits 74 without unwinding
potentially in-flight owners. This is diagnostics, not a video transport protocol.

Linux/Proton CPU acceptance has also passed for readiness, all 17 parameter slots
and the deliberate failure path. Windows-native acceptance remains pending.

First run the GPU-free ABI check on Windows or through an isolated Proton prefix:

```text
comfy-dlss-worker.exe --self-test-parameter-abi
```

It exercises all 17 official virtual Set/Get/Reset slots through a separately
compiled Microsoft-ABI consumer. Success reports `error_bits: 0` and
`gpu_started: false`. This verifies neither model loading nor rendering.

After reviewing the selected model's **InitParameters** ABI and resolving any
prior GPU failure, use separate native diagnostic stages before evaluation:

| Mode | Scope |
| --- | --- |
| `--probe-device` | Device creation/release only; no model. |
| `--probe-init-parameters MODEL CALLER LOG_DIR` | Device, model initialization, shutdown; no Feature creation. |
| `--probe-create-parameters MODEL CALLER LOG_DIR WIDTH HEIGHT` | Feature creation, submission fence, release; no Evaluate. |
| `--probe-nr-init-parameters MODEL CALLER LOG_DIR WIDTH HEIGHT EVALS` | Full bounded probe, not a production video task. |

Initialization and creation may themselves execute GPU work inside the runtime.
Device, initialization and creation were individually verified before the bounded
single-frame evaluation above. All
paths must be absolute; the dedicated log directory must already exist. Example
syntax only, not a recommendation to retry a failed GPU configuration:

```text
comfy-dlss-worker.exe --probe-nr-init-parameters MODEL_ABS CALLER_ABS LOG_DIR_ABS 640 360 1
```

This mode uses synthetic FP16 gray color and zero motion, not user video. It
performs Init/Create/Evaluate/Release/Shutdown, with one evaluation in flight and
at most 240 evaluations. Width is limited to 64–1920, height to 64–1080. Resources
are reused; only the final output is read back, checked for nonfinite values and
checksummed. Color, motion and output each declare an explicit valid subrect;
the selected model defaults absent motion extents to zero. GPU waits are bounded. Unverified GPU completion terminates only this
process without destructing possibly in-flight resources. Success is printed
only after release/shutdown succeed.

The NR probe also has a 120-second process watchdog covering model initialization
and cleanup, not just GPU fence waits. This bounds a hung foreign call. The
GPU-free ABI-only command should still be run under the test runner's timeout.

The probe does not bootstrap a separate official NGX core or guess private
runtime parameters/ABI. If a selected model requires more initialization or
rejects the supplied inputs, record its operation/error and stop. Do not retry
different signatures on the same native state. No DLL is patched or overwritten.

Local tests cover table bounds, numeric conversion, null allocation, concurrent
access, sanitized lifetime handling and cross-compilation. Actual Windows/Proton
GPU execution, model acceptance, image quality, video transport, residency and SR/FG
are not established by these local results. A report explicitly keeps
`video_protocol` and `visual_acceptance` false.
