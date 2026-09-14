# External numerical guides

English · [简体中文](external-guides.zh-CN.md)

Audience: public

External guide nodes connect the numerical output of a separate optical-flow or
depth estimator to the input pipeline. They do **not** download models, run an
estimator, infer missing camera data or turn a colored flow/depth visualization
into a numerical buffer.

## What works now

- **External Numerical Guide** reads a JSON manifest and binds it to the same
  inspected `sequence` from Video Input Adapter. Its `guide` output has type
  `DLSS_GUIDE_PROVIDER`; `report` contains copyable metadata.
- **External Guide Selector** chooses `a`, `b` or `c`. Only the selected lazy
  branch is requested. A missing or invalid selection fails without fallback.
- **Input Assembler** accepts `external_motion`, `depth`, `normals`, `mask` and
  `confidence`. Set **Motion source → External numerical guide** to use external
  motion; the default **Configured flow estimator** retains DIS/NVIDIA behavior.
- NR reads the selected external motion for the requested frames. Depth,
  normals, masks and confidence are typed attachments only: **NR does not consume
  them**, and their presence does not add lighting, geometry or ray-tracing inputs.

```text
Video Input Adapter ────────────────────────────→ Input Assembler → NR Stage → Preview/Render
        ├→ External Numerical Guide (motion) ──→ external_motion
        └→ External Numerical Guide (depth) ───→ depth (metadata only for NR)
DIS/NVIDIA Flow ───────────────────────────────→ flow_provider
```

Connect the same inspected sequence to the guide loaders and assembler. Switch
`motion_source` explicitly when comparing configured and external motion. Use
External Guide Selector when comparing several external estimators. These switches
do not cancel a branch independently requested by another output node.

## Manifest and files

Place estimator outputs in a directory you manage, outside the Git checkout when
practical. `manifest_path` is a path on the **ComfyUI host**, not the browser's
computer. Frame paths are relative to the manifest and cannot escape its directory.
The node reads individual frames on demand, not the entire video into memory.

This is a two-frame motion-manifest template. Replace both source and frame hashes
with actual lowercase SHA-256 values; the all-zero placeholders are not valid
content identities for your files. Add an entry for every frame needed by preview,
history pre-roll and final output.

```json
{
  "schema_version": 1,
  "source": {
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "view_id": "source",
    "time_origin": "video_stream_start",
    "width": 640,
    "height": 360
  },
  "role": "motion",
  "semantics": "current_to_previous_pixels_top_left_xy",
  "units": "input_pixels",
  "grid": {"width": 640, "height": 360, "sampling": "pixel_centers"},
  "dtype": "float32_le",
  "storage": "raw",
  "frames": [
    {"path": "motion/000000.f32", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "pts_ns": 0, "reset": true},
    {"path": "motion/000001.f32", "sha256": "0000000000000000000000000000000000000000000000000000000000000000", "pts_ns": 33333333}
  ],
  "metadata": {"includes_camera_motion": true, "includes_jitter": false}
}
```

For `raw`, files contain tightly packed row-major, interleaved channels, with no
header: motion is `[height, width, 2]` with X then Y, little-endian float16 or
float32. `npy` is also accepted for numeric, C-order `.npy` format 1.0/2.0 with
matching shape/dtype. Object arrays, pickle, `.npz`, PNG and RGB flow pictures are
not accepted. Single-channel `.npy` may be `[height, width]` or `[height, width, 1]`.
Each frame hash covers its entire stored file, including a `.npy` header.

## Motion contract and alignment

- NR requires **current → previous**, top-left origin, X/Y displacement in input
  pixels. Forward flow is not fixed by just negating it; normalized UV and forward
  flow manifests can be described by the loader but are rejected by NR.
- Include camera motion; exclude projection jitter. Use the original `source`
  view and a full-resolution grid matching the source dimensions. Preview resizing
  resamples the field and scales horizontal/vertical displacements accordingly.
- `source.sha256` must match the exact source file/materialization consumed by the
  executor. An unmodified, file-backed Load Video is the simplest starting point;
  a hash of some other encode of the same scene is not interchangeable. File-backed
  trim fast paths retain the original file hash and source timeline; do not rebase
  those guide timestamps to the trimmed clip's zero. Other transformations or
  in-memory VIDEO materialization require matching/rebuilt guides.
- `source.time_origin` must be `video_stream_start`. `pts_ns` is presentation time
  minus the video stream's start time, in integer nanoseconds, strictly increasing;
  it is neither raw container PTS nor arbitrary clip-relative zero.
  Requested frames must match uniquely within 1,000 ns rounding
  tolerance; there is no nearest-frame, interpolation or silent time resampling.
- Set `reset: true` at cuts/discontinuities. The first guide frame resets history.
  Provide guides for history pre-roll too, not only the visible preview frame.
- Values must be finite. A separate validity/confidence map does not currently
  mask invalid NR vectors. Rebuild invalid motion before importing it.

Manifest hashes form the guide cache identity. Selected frame files are checked
for content hash, exact shape/length and finite values when read. Editing a manifest
after assembly requires rerunning its provider; editing a frame without updating
the manifest fails when that frame is read. Cached preparation follows the normal
[storage policy](STORAGE.en.md); external input files remain user-managed.

## Other numerical roles

| Role | Semantics / units | Extra declarations |
| --- | --- | --- |
| Depth | `linear_view_z`: `meters` or `relative`; `inverse_view_z`: `inverse_meters` or `relative` | Empty metadata; values strictly positive. |
| Device depth | `device_z`, `zero_to_one` | `reversed_z`, finite `near`/`far`, `projection`: `perspective` or `orthographic`; values in [0, 1]. |
| Normals | `view_space_xyz` or `world_space_xyz`, `unit_vector` | Three finite components in [-1, 1]; empty metadata. |
| Confidence | `motion_confidence` or `depth_confidence`, `zero_to_one` | One channel in [0, 1]; empty metadata. |
| Mask | `validity`, `reactive` or `transparency_composition`, `zero_to_one` | One channel in [0, 1]; empty metadata. |

Relative monocular depth is not automatically device depth. Do not relabel it to
pass validation: camera/projection assumptions and conversion must be explicit.
See [SR input planning](super-resolution.en.md) for the separate, non-rendering SR
contract and [pipeline wiring](media-pipeline.en.md) for existing NR outputs.

Limits: manifest ≤8 MiB, 1–100,000 entries, each numerical frame ≤256 MiB. Frame
files are not copied into node helper packages and no third-party model weights
are distributed. Portable contract tests do not constitute a quality benchmark of
WAFT, U2Flow, MegaFlow or another estimator, nor GPU acceptance of external guides.
