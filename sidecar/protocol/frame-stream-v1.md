# Frame stream protocol v1

This protocol carries normalized frames between the ComfyUI Python process and
the isolated Windows D3D12/NGX worker. It does not carry NVIDIA binaries and it
does not require Python to load ReShade, RenoDX, or NGX libraries.

Packets may travel over binary standard streams or a reliable ordered byte
stream such as loopback TCP. Linux/Proton uses authenticated loopback TCP
because Proton launchers may redirect the PE process's standard streams. The
transport authentication preamble is outside this packet format.

All integer fields are little-endian. A sender must write complete packets and
a receiver must reject unknown versions, message types, flags, formats, plane
semantics, non-zero reserved fields, inconsistent byte counts, and packets over
its configured limit. The reference Python codec limits packets to 512 MiB and
dimensions to 16384 pixels.

## Packet header (32 bytes)

| Field | Type | Meaning |
| --- | --- | --- |
| magic | `char[8]` | `CDLSSP1\0` |
| version | `uint16` | `1` |
| message_type | `uint16` | See message table |
| flags | `uint32` | Packet flags |
| request_id | `uint64` | Correlates a request and response |
| payload_bytes | `uint64` | Bytes immediately following the header |

Messages are `HELLO=1`, `CONFIGURE=2`, `CONFIGURED=3`, `FRAME=4`,
`FRAME_RESULT=5`, `RESET=6`, `CANCEL=7`, `SHUTDOWN=8`, and `ERROR=9`.
Control-message payloads are compact UTF-8 JSON objects. `FRAME` and
`FRAME_RESULT` use the binary frame payload below. Packet flags are
`RESET_HISTORY=1` and `END_OF_STREAM=2`.

`RESET_HISTORY` is attached to the first frame, scene cuts, discontinuities,
and explicit user resets. The worker must not reuse temporal state across it.

## Frame header (40 bytes)

| Field | Type |
| --- | --- |
| input_width, input_height | `uint32`, `uint32` |
| output_width, output_height | `uint32`, `uint32` |
| pts_ns | `int64` |
| frame_index | `uint64` |
| plane_count | `uint32` |
| reserved | `uint32`, must be zero |

The frame header is followed by `plane_count` descriptors and then by each
plane's bytes in descriptor order. A semantic may occur at most once.

## Plane descriptor (32 bytes)

| Field | Type |
| --- | --- |
| semantic | `uint16` |
| pixel_format | `uint16` |
| width, height | `uint32`, `uint32` |
| row_pitch | `uint32` |
| flags | `uint32`, must be zero in v1 |
| reserved | `uint32`, must be zero |
| byte_count | `uint64`, must equal `row_pitch * height` |

Semantics are `COLOR=1`, `MOTION=2`, `DEPTH=3`, `EXPOSURE=4`,
`REACTIVE_MASK=5`, and `OUTPUT_COLOR=100`. Formats are `RGBA8_UNORM=1`,
`RGBA16_FLOAT=2`, `RG16_FLOAT=3`, `R32_FLOAT=4`, and `R8_UNORM=5`.

V1 accepts padded rows. `row_pitch` must be at least `width * bytes_per_pixel`.
Input color may be RGBA8 or direct FP16. RGBA8-to-linear-FP16 conversion belongs
to the future rendering path; the current diagnostic worker does not convert.
Motion, depth, exposure, and
reactive-mask planes are optional so guide providers can evolve independently
from the RenoDX/NGX backend.

The current diagnostic input contract is:

| Semantic | Format | Dimensions |
| --- | --- | --- |
| COLOR (required) | RGBA8_UNORM or RGBA16_FLOAT | Input size |
| MOTION | RG16_FLOAT | Input size |
| DEPTH | R32_FLOAT | Input size |
| REACTIVE_MASK | R8_UNORM | Input size |
| EXPOSURE | R32_FLOAT | 1x1 |

`OUTPUT_COLOR` is response-only. The diagnostic response returns color under
that semantic and echoes all supplied guides **after GPU readback**, not by
reusing the CPU input bytes. Only active pixel bytes are preserved; row padding
is discarded. It rejects resizing. R32_FLOAT depth is a copyable data texture,
not a verified NGX typeless/DSV binding. Motion scale, depth convention, exposure
interpretation and temporal history behavior are not established by this copy
test; the NGX renderer must validate its own contract.

For this worker, HELLO reports `configuration_supported:false`, CONFIGURE
returns `accepted:false` with a reason, and RESET acknowledges a stateless
reset. Successful copies do not mean that any effect configuration was applied.

## Process and lifecycle contract

One worker owns one configured D3D12/NGX runtime. It may process many frames and
jobs only while the runtime fingerprint remains unchanged. DLL or backend
changes require worker shutdown and a fresh process; they are never hot-swapped.

The Python parent owns timeout and crash containment. It may send `CANCEL` when
the worker is responsive, but must also be able to terminate the isolated
process. A frame is accepted only after a matching `FRAME_RESULT`. Incomplete or
unverified final outputs must not replace the user's destination file.
