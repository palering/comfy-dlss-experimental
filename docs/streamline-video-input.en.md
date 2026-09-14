# Streamline from VIDEO and explicit camera inputs

English · [简体中文](streamline-video-input.zh-CN.md)

Audience: public

## Connected path

`Video Input Adapter -> Input Assembler -> Streamline SR / DLAA Stage -> Pipeline Render -> SaveVideo`.
Connect numerical device depth and the new **Camera Timeline Input** to the same
assembler. Both providers must originate from the same input sequence. Existing
DIS/NVIDIA/external/explicit-zero motion configuration remains usable. A Sequence
Hub may distribute that sequence without copying or changing its VIDEO identity.

This path uses `owned_sl` / CXR1 and the same GPU/encoder lifecycle as the
[renderer-bundle path](reconstruction-nodes.en.md). It does not silently dispatch
the separate `owned_sr` / CSR1 backend. NR remains available separately. Mixed
NR/SL stages, RR material inference, FG and the NR interactive A/B card are not
part of this VIDEO path. RR still uses explicit renderer bundles.

Import [the VIDEO Streamline example](../example_workflows/dlss_streamline_video.json).
Set the backend-local camera/depth manifest paths and the `owned_sl` preset;
disable Worker persistence. Input normalization must produce sRGB SDR. Keep
Pipeline Render `scale=100`; the stage sets the actual target dimensions.
DLAA preserves the input dimensions; other modes require a larger even target
with the exact input aspect ratio, accepted by the runtime's size query.

The stage adds `output_size_mode`: manual dimensions, 1.5x, 2x and 3x. Multipliers
resolve from the actual pipeline input on every execution; changing the source
does not require manually looking up its size. They hide and ignore manual
width/height while preserving those values for switching back. DLAA hides both
size choices and preserves source size. Old workflows default to manual.
The closest legal even grid preserves exact aspect, so unusual sizes can differ
from the nominal multiplier; see the stage report. Quality is not a multiplier:
2x can use performance, subject to the runtime query, without automatic fallback.
Pipeline Render still requires `scale=100`.

`sidecar/protocol/sr_limits.json` generates Python/native bounds. Output no longer
uses a named 4K rectangle: the existing CXR1 RESULT envelope is 128 MiB, containing
8 bytes of PTS plus 8 bytes per RGBA16F pixel. Thus `8 + width*height*8 <= 134217728`,
or at most 16777215 pixels. Maximum side 16384 follows the D3D11 texture bound;
even dimensions, exact aspect and runtime optimal-settings checks still apply.
736x1280 at 3x is 2208x3840 and fits (67829768 result bytes). Landscape, portrait,
DCI-like and nearby sizes follow the same formula; no silent resizing occurs.
This is a transport ceiling, NOT a VRAM estimate or guarantee. GPU intermediates,
SDK allocations, CPU copies and encoder buffers are additional. Stage reports
include `output_budget` with actual byte usage and its basis.

This output-only expansion leaves the earlier input budget unchanged: minimum
side 64, maximum side 1920 and 2073600 input pixels. It does not certify 4K source
decoding, higher-resolution geometry or two-pass SR/DLAA. Older landscape and
portrait CXR1 Workers remain usable at their original bounds; a byte-budget
Worker is required for expanded output. Old portrait Workers still have their
8294400-pixel cap, even if each side fits. CSR1 retains its separate legacy limits.
The binary camera structure and 128 MiB CXR1 message envelope are unchanged.

For controlled flow comparisons and why SR followed by DLAA is not a default
pipeline, see [flow and reconstruction comparison](flow-comparison.en.md).

Pipeline Render owns its range independently of Preview. `duration=0` or
`process_to_end=true` selects the remainder. `single_frame=true` exports one
frame at/after the requested start, following available pre-roll. History
Settings supplies pre-roll seconds (at most 120 actual history frames); NR
repeat-warmup is never applied. History is evaluated but not exported. Input
audio is aligned to the first exported frame and re-encoded to AAC. Output is
zero-origin CFR H.264 YUV420 SDR, not HDR or a floating-point master.
Both export paths trim audio before AAC framing; the short-range tail fix and
Jobs-safe diagnostic transport are described in [development notes](DEVELOPMENT.en.md#diagnostic-ui-transport).

The requirement report separates **required**, **optional_consumed** and
**not_consumed** inputs. Camera and device depth are required for this path;
standalone normals/masks/confidence do not improve its output because they are
not consumed. Audio is host-side export data, not a DLSS model input. A complete
declaration is not GPU availability or an accuracy assessment.

## Validation boundary

On 2026-09-14, an independent MapAnything Bridge passed original-grid CXR1 SR:
736x1280 to 1472x2560, 2x/performance, all 362 frames at 24 fps over 15.083 seconds,
with matching PTS and audio. Additional 24-frame cases passed 1.5x/quality and
2x/performance. Geometry reused saved low-resolution Omega estimates; no estimator
dependency was added here. This proves grid/timeline/GPU execution, not motion
quality or camera accuracy. The SR Worker handles off-center projection; this
round adds no independent CSR1, RR or NR GPU acceptance.

Bounded synthetic Linux/Proton tests passed SR (640×360 to 960×540), DLAA
(640×360), the DIS branch, a single exported frame with three history frames,
and three exported audio/video frames with two history frames. The same five
cases passed actual Comfy execution through SaveVideo. Independent decoding
confirmed frame counts, 30 fps zero-origin PTS, SDR tags and audio interval
alignment. File and Comfy raw outputs matched; SR/DLAA also matched the shared
renderer-bundle entry given identical decoded source pixels. All four existing
bundle modes (SR/DLAA/RR/RR+DLAA) passed regression after executor sharing.

Subsequent browser regression passed UI import, SR/DLAA mode switching, single-
and eight-frame rendering, SaveVideo and Completed Jobs. The saved A/V durations
matched in all four cases. Independent CNR1 NR single/eight-frame preview+save
also passed, including A/B loading and frame stepping. This is bounded browser
acceptance, not a comprehensive UI audit.

The historical tests below use a static synthetic camera scene. Camera estimator
accuracy, moving-camera GPU quality, long-video
stability, broader browser flows, native Windows and native Linux execution
without Proton remain unverified. No new estimator or daily Comfy deployment
is implied by this acceptance.

## Additional information beyond motion and depth

| Information | Source or derivation |
| --- | --- |
| Camera intrinsics `K` | Calibrate or estimate focal length/principal point on the actual input pixel grid. Account explicitly for undistortion, resizing, padding and cropping. |
| Per-frame world-to-camera pose `[R\|t]` | Export from the renderer or reconstruct a consistent camera trajectory. Do not independently normalize each frame's world/scale. |
| Projection, inverse projection, FOV, camera axes/position | Derive numerically from the camera model and pose. Near/far and reversed-Z are explicit conventions that must agree with the existing depth representation. |
| Current/previous clip transforms | Derive from adjacent camera poses/projections; reset on the first frame and cuts. These are not an additional neural prediction. |
| Frame identity and cuts | Preserve the decoded source PTS, filename-to-frame mapping, source SHA-256 and shot boundaries. No implicit interpolation for missing poses. |
| Jitter, exposure, color | This finished-video path declares zero sampling jitter, unit exposure/pre-exposure scales, and SL SR auto exposure. It does not recover pre-tonemap radiance or invent render jitter. Color normalization uses explicit input metadata. |

Streamline specifies separate unjittered matrices and sampling jitter, plus
camera and temporal constants. The row-vector temporal composition used here is
`inverse(P_current) * inverse(V_current) * V_previous * P_previous`.
See the [official common-constants contract](https://github.com/NVIDIA-RTX/Streamline/blob/main/docs/ProgrammingGuide.md).

For footage without camera metadata, practical external candidates are
[VGGT](https://github.com/facebookresearch/vggt), which estimates camera intrinsics
and extrinsics, or a [COLMAP SfM reconstruction](https://colmap.github.io/tutorial.html).
Neither is installed, invoked or quality-validated by these nodes. Start with a
short single shot, retain a mapping from every frame to its original PTS, and
verify the resulting trajectory by reprojection before converting it. If an
estimator processes a resized/cropped grid, transform its intrinsics back or
materialize that exact view and regenerate **all** source-bound inputs.

COLMAP exports intrinsics in `cameras` and world-to-camera poses in `images`;
its image IDs are not sequential frame indices. Use image names to recover the
frame mapping. Its camera basis is +X right, +Y down, +Z forward.
See [COLMAP output conventions](https://colmap.github.io/format.html).
[VGGT's pose utility](https://github.com/facebookresearch/vggt/blob/main/vggt/utils/pose_enc.py)
also declares OpenCV world-to-camera extrinsics and pixel-space intrinsics.

Engineering guidance: fit the static background when independently moving
subjects dominate; treat cuts as separate trajectories. A truly locked camera
may use a fixed calibrated pose, but a moving shot must not be labelled fixed
just to fill required fields. Missing registration, large reprojection error,
unmodelled lens distortion, rolling shutter and frame-to-frame scale drift are
reasons to reject/refine the reconstruction, not reasons to inject identity
matrices. Synthetic/estimated provenance must remain visible.

## Camera timeline version 1

UTF-8 JSON must contain exactly these top-level keys:

| Key | Contract |
| --- | --- |
| `schema_version`, `kind` | `1`, `"dlss_camera_timeline"` |
| `source` | Exactly `sha256`, `view_id="source"`, `width`, `height`, `time_origin="video_stream_start"` |
| `fps` | Rational string in [1,120], such as `"30000/1001"` |
| `depth_inverted` | Boolean matching the projection and device-depth manifest |
| `provenance` | `renderer`, `calibrated`, `estimated` or `synthetic_test`; declaration, not certification |
| `frames` | Every source CFR frame from zero, 1–100000 entries, also bounded by 32 MiB JSON |

Each frame contains exactly `pts_ns`, `camera`, `metadata`. `camera` carries all
[CameraFrame fields](reconstruction-nodes.en.md#renderer-bundle-version-1).
`metadata` explicitly carries `jitter_x=0`, `jitter_y=0`, `motion_scale_x=1`,
`motion_scale_y=1`, `pre_exposure=1`, `exposure_scale=1`, `exposure=1` and boolean
`reset`. The first source frame must reset. The first evaluated frame is also
reset when seeking. A camera cut, guide reset or detected cut resets history.

The source extent uses the orientation-neutral budgets above. Matrices must be finite and mutually
inverse, axes orthonormal, aspect consistent, and projected near/far consistent
with device Z. PTS matching allows at most 1 microsecond of rounding, not time
resampling. The input must cover every frame in the selected range and history.
Temporal trims retain source PTS. A materialized spatial crop/re-encode has a new
source hash and requires rebuilt inputs. Manifests are reopened before execution;
depth payloads are read/hash-checked one frame at a time.

## Pure numerical OpenCV converter

`camera_math.camera_from_opencv(K, world_to_camera, ...)` returns a `CameraFrame`.
It imports NumPy only when called, performs no model or file I/O, converts the
OpenCV basis consistently to the worker's +Y-up basis, and constructs row-vector
projection and temporal matrices. Supply `previous_intrinsics` and
`previous_world_to_camera`, or explicitly use `reset=True` at the first frame/cut.
Serialize with `dataclasses.asdict`, alongside `SLFrameMetadata(reset=...)` and
the original source `pts_ns`.

The converter now accepts zero-skew, undistorted perspective intrinsics on the
final VIDEO grid, including off-center principal points and unequal fx/fy.
K uses edge-origin pixel centers i+0.5; add half a pixel when converting an
integer-center OpenCV K. Full projection retains the principal offset and the
temporal transform uses the previous frame's actual K, without invented jitter.
The SR Worker derives view-frustum aspect from projection, distinct from pixel
grid aspect in the manifest. It rejects rather than approximates skew, invalid rigid
poses or missing previous poses. This is an exporter building block, not an
automatic COLMAP/VGGT importer or a guarantee that estimated camera motion helps
DLSS. The matrix converter has CPU projection/reprojection tests; real footage
reconstruction and perceptual quality remain separate acceptance work.
