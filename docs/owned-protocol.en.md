# Owned NR streaming (development)

English · [简体中文](owned-protocol.zh-CN.md)

Audience: public

CNR1 is implemented and explicitly selectable through [owned_nr](owned-runtime.en.md).
It is distinct from legacy D5V2, diagnostic JSON and the CNS1 test-file format.
The `owned_cnr1_nr` contract is not an automatic installation or fallback.

## Components

| Component | Responsibility |
| --- | --- |
| `sidecar/src/nr_engine.*` | Model, Feature, textures, transfers and GPU fences. |
| `sidecar/src/nr_caller.*` | Stateless thin forwarding, no video/process ownership. |
| `sidecar/src/owned_wire.hpp` | Bounded binary layout and validated settings. |
| `sidecar/src/worker_connection.hpp` | Authenticated, deadline-bounded loopback transport. |
| `sidecar/src/nr_probe.cpp` | Diagnostics and `--serve-nr` session loop. |
| `comfy_dlss_experimental/owned_worker.py` | Ordered Python protocol client. |
| `comfy_dlss_experimental/owned_process.py` | Platform-aware process ownership, bounded launcher-log draining and cleanup. |

Windows directly executes the PE. Linux alone loads Proton/prefix/identity-pinned
process cleanup logic, using `proton run`. No relay PE or systemd is required.
Windows GPU acceptance remains pending; macOS is edit/build-only, not execution.

## Protocol and lifecycle

Worker connects to an IPv4 loopback host listener, sends a random 32-byte token,
and waits for a one-byte acknowledgment. The 32-byte little-endian header contains
uint32 magic `CNR1`, version 1, type, payload size, then uint64 request/session IDs.
Requests increase consecutively from 1; replies echo IDs. Malformed, oversized,
unknown or out-of-state requests fail closed; uncertain sessions are not reused.

| Request / response | Effect |
| --- | --- |
| CAPS (128) | Initial protocol capabilities, not GPU quality acceptance. |
| CREATE (1) / ACK (129) | Create one session with dimensions and Look parameters. |
| FRAME (2) / RESULT (130) | One input → one finite FP16 output, after its GPU fence, preserving PTS. |
| END (3) / ACK | End a nonempty task; keep allocations. Next task must reset; PTS may restart. |
| RELEASE (4) / ACK | Release session/GPU objects; process can accept another CREATE. |
| SHUTDOWN (5) / ACK | Release and exit. |
| PING (6) / ACK | Check connection without creating a GPU session. |
| ERROR (131) | Structured stage/code/frame diagnostics, then close; uncertain sessions are not reused. |

CREATE: eight uint32 fields (width, height, warmup, preset, style, auto-mask, UI
correction, reserved zero), then four float32 strengths (intensity, tone, structure,
skin). Look changes currently require RELEASE/CREATE; DLL hot-swapping is not offered.
FRAME: uint64 nonnegative PTS ns, uint32 reset/reserved zero, then tightly packed
RGBA16F color and RG16F current-to-previous motion in full-resolution pixels.
RESULT: same uint64 PTS plus RGBA16F color. No implicit transfer-function conversion,
depth synthesis, scaling or frame generation. Output can exceed SDR [0,1]; the
caller owns display/encoding policy. Bypass and A/B mixing also belong outside NR.

One session and one frame in flight. Bounds: 64–1920 × 64–1080, max payload
24,883,216 bytes; these bounds are not all-size GPU acceptance. No whole-video
intermediates are written by the streaming Worker. Warmup occurs before the first
delivered frame only; the delivered frame still honors reset. END does not repeat
warmup for the next task. Host idle expiry is 30–900 seconds; the socket has a
960-second idle grace limit. Active watchdog 120 seconds,
GPU fence 30 seconds, with bounded network I/O. Disconnect cancels transport, not
already dispatched kernels. Unconfirmed GPU completion terminates without unwinding
in-flight owners; the host reaps its own processes. Node idle scheduling and manual release are connected.

`OwnedWorkerError` carries `stage`, `code`, `code_domain`, `frame_index`,
`evaluation_index` and a readable message. NGX/HRESULT codes are displayed in
hexadecimal, distinguishing initialization, feature creation, evaluation and
transport failures without guessing from one generic error. Successful CNR1
messages are unchanged. The host also accepts the older four-byte error payload;
an older host receiving a newer error fails closed with less detail. Use matched
host/helper versions when diagnosing failures.

## CSR1: optional SDK SR/DLAA transport

CSR1 is a separate little-endian protocol on the same authenticated loopback
transport. It does not change or reinterpret CNR1. The same Worker executable
selects it with `--serve-sr MODEL_DIRECTORY LOG_DIRECTORY PROJECT_UUID PORT TOKEN`;
the public SDK loads SR without the NR caller. See [SR setup](super-resolution.en.md).

- Header: six fields, `uint32 magic=0x31525343, version=1, type, payload_bytes`,
  then `uint64 request_id, session_id` (32 bytes). Message IDs follow CNR1.
- CAPS: sixteen uint32 fields describing minimum input, maximum input/output,
  color/motion/depth/output format IDs, payload/session/in-flight bounds,
  SR/DLAA feature bits, SDK compile flag and cumulative frame cap. A compile flag
  of zero fails before device initialization; one is not GPU/model acceptance.
- CREATE: eight uint32 fields: input width/height, output width/height, quality,
  preset, flags, reserved zero. Flags bits 0–3: HDR, reversed depth, jittered
  motion, automatic exposure. Quality/preset values follow the public SDK.
- FRAME: `uint64 pts_ns`, `uint32 reset, reserved_zero`, then eight float32 values:
  jitter X/Y, motion scale X/Y, exposure, pre-exposure, exposure scale, frame
  interval milliseconds. The 48-byte metadata is followed by input-sized
  RGBA16F color, RG16F motion and R32F device depth.
- RESULT: the same uint64 PTS plus **output-sized** RGBA16F. Input/output sizes
  can differ, but there is still one output for each input frame, not FG.
- END retains the low-level session, RELEASE drops the feature, SHUTDOWN exits.
  The first frame of each task must reset history; PTS increases within a task.
  The Comfy SR executor currently chooses isolated mode, not session reuse.

One session/frame in flight; input up to 1920×1080, output up to 3840×2160,
payload at most 66,355,208 bytes and at most 1,000,000 evaluations per session.
Typed CER1 errors use the same bounded stage/code/frame diagnostic envelope as
CNR1. GPU fence timeout is 30 seconds, active watchdog 120 seconds. The host
reaps its own process on failure/cancel; no resize or NR fallback is performed.
Wire/source and CPU tests are implemented; SDK linking and GPU output remain
separate acceptance steps. The default Zig build contains the unavailable-SR
handshake, not an SDK-enabled SR engine.

## Verification boundary

On Linux/GE-Proton with one RTX 4070 Ti SUPER: real frames + DIS, repeated-clip
reset consistency, two tasks sharing a session, explicit release, changed-Look
recreation, malformed-frame rejection and idle disconnect cleanup passed. Streaming
output matched the corrected file probe byte-for-byte. Tests used 640×360 and one
discarded warmup, not production throughput or long-video acceptance. Old RGBA8 and
new FP16 images look similar; old/new bit equality is not claimed.

Short Comfy Render/Save and single-frame Preview acceptance passed with Worker reuse.
Optional helper packaging is implemented. Long-video/resource-growth tests, Windows
GPU acceptance and SR/FG remain separate. Keep old presets/DLLs unchanged.
See [build/ABI checks](owned-worker.en.md).
