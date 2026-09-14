# Streamline reconstruction nodes

English · [简体中文](reconstruction-nodes.zh-CN.md)

Audience: public

## What is connected

The explicit `owned_sl` path connects **Reconstruction Bundle Input →
Reconstruction Render → Save Video**. It runs SR, DLAA, RR or RR at native
resolution through the separate CXR1 Worker. Runtime Configuration feeds the
Render node; it does not change existing `direct_nr`, `owned_nr` or `owned_sr`
workflows. FG/MFG and the NR in-card A/B preview are not part of these nodes.

Bounded Linux/Proton tests passed all four modes through actual Comfy node
execution and Save Video, plus a one-frame output with preceding history.
File-core output matched the existing standalone GPU probe byte for byte.
Independent checks confirmed the saved frame counts, zero-origin 30 fps timing
and SDR tags. A separate three-frame file-core audio test passed aligned seeking,
AAC output and exclusion of preceding history frames from the output.
These are synthetic-scene integration tests, not natural-video quality,
long-run stability, native-Windows or browser interaction acceptance.

Ordinary MP4 files do **not** contain the required camera, device depth and RR
material buffers. Supply actual renderer exports; this path neither estimates
nor invents missing data. The manifest declarations and checks cannot prove
that supplied planes describe the original renderer correctly.

## First run

For an existing VIDEO plus separately reconstructed camera/depth data, use the
[unified VIDEO input path](streamline-video-input.en.md) for SR/DLAA instead.
That entry shares the executor but does not require a raw-color renderer bundle.

1. Build `comfy-dlss-sl-worker.exe` using the
   [Streamline build instructions](streamline-reconstruction.en.md#build-and-runtime).
2. Configure the [owned_sl preset template](../examples/runtime-presets/owned-sl.example.json)
   with an explicit Worker and a compatible user-supplied seven-DLL set. Relative
   component paths resolve against the preset file. No vendor files are bundled.
3. Import [dlss_reconstruction.json](../example_workflows/dlss_reconstruction.json).
   Set its runtime preset path and the input manifest's absolute **backend-host**
   path. Keep Worker residency disabled. Linux needs existing Proton and Xwayland;
   it does not need a foreground window. macOS cannot execute the GPU backend.
4. Keep `frame_count=1` for inspection, then try a short range. `frame_count=0`
   processes the remaining bundle. `start_frame` is zero-based; excess ranges fail.
5. `feature=sr, mode=dlaa` selects DLAA; `feature=rr, mode=dlaa` selects native-size
   RR. DLAA ignores output width/height. Other modes require larger even output
   dimensions with the exact input aspect ratio and a compatible optimal-size query.

Save Video displays the returned standard VIDEO. A one-frame result is a
one-frame video, not the NR interactive A/B card. `history_frames` evaluates up
to 120 preceding frames without exporting them; the first evaluated frame and
explicit source cuts reset temporal history. Changing settings does not reuse
an old GPU instance. Settings use the runtime's default model preset.

## Renderer bundle version 1

The UTF-8 JSON object has **exactly** these keys:

| Key | Value |
| --- | --- |
| `schema_version`, `kind` | `1`, `"dlss_reconstruction_bundle"` |
| `width`, `height` | Integers, 64×64 through 1920×1080; full-resolution planes |
| `fps` | Rational string such as `"30/1"`; CFR in [1,120] |
| `color_transfer` | `"srgb"` or `"linear_sdr"`; RR requires linear SDR |
| `jitter_policy` | `"unjittered_video"` or `"external_render_metadata"` |
| `depth_inverted`, `has_rr` | Explicit booleans |
| `frames` | 1–100000 ordered frame objects, also bounded by a 32 MiB manifest limit |
| `audio_source` | `null`, or a hash-bound local audio/media file aligned to timeline zero |

All color uses BT.709/sRGB primaries, full range and little-endian raw numerical
planes. This schema does not represent HDR, arbitrary color primaries, resized
guides, crops, VFR, missing timestamps or motion containing sampling jitter.

Each frame has exactly `pts_ns`, `camera`, `metadata`, `planes`; RR frames also
have `world_to_view` and `view_to_world`. Timestamps start at zero and match
`round(index × 1000000000 / fps)` within one nanosecond. Do not relabel VFR as CFR.

`camera` contains every `CameraFrame` field: four flat row-major 16-number
matrices `view_to_clip`, `clip_to_view`, `clip_to_previous`, `previous_to_clip`;
three-number `position`, `up`, `right`, `forward`; numerical `near_plane`,
`far_plane`, `vertical_fov` (radians), `aspect`; and boolean `orthographic`.
Matrix pairs must be inverse and camera axes orthonormal. RR's two world/view
matrices are another inverse pair of flat 16-number arrays.

`metadata` contains all eight `SLFrameMetadata` fields, even when constant:
`jitter_x`, `jitter_y`, `motion_scale_x`, `motion_scale_y`, `pre_exposure`,
`exposure_scale`, `exposure`, and boolean `reset`. Jitter is the actual source
sampling offset in input pixels, at most ±0.5; unjittered input requires zero.
Exposure values are positive. SR's unused manual exposure must be 1; RR uses it.

| `planes` role | Packed format and meaning |
| --- | --- |
| `color` | RGBA16F |
| `motion` | RG16F XY, current-to-previous input-pixel motion, including camera motion |
| `depth` | R32F device depth in [0,1], matching `depth_inverted` |
| `diffuse_albedo`, `specular_albedo` (RR) | Linear RGBA16F reflectance |
| `normal_roughness` (RR) | RGBA16F unit XYZ normal and linear roughness |
| `specular_motion` (RR) | RG16F reflection motion |

Each plane and optional `audio_source` is an object with exactly `path` and
lowercase `sha256`, for example `{"path":"frames/color-0000.rgba16f","sha256":"<64 hex digits>"}`.
Paths use relative POSIX separators and must resolve to regular files inside
the manifest directory; absolute paths, parent traversal and escaping symlinks
are rejected. Frames may reference the same unchanged plane file. All expected
roles must be present and unexpected roles fail. The input node checks metadata
and lengths; each render read checks pixel hashes before sending data to CXR1.
The Worker independently validates numerical values before GPU evaluation.

## Output, audio and resource boundaries

RGBA16F results are finite-checked, clamped to SDR and quantized to RGBA8 for
H.264 YUV420 output. Linear RGB receives an explicit sRGB transfer conversion;
alpha is not gamma-corrected and is not retained by the video codec. Reports
include raw-FP16/export hashes and clipping counts. No float/HDR master is saved.

Optional audio must be content-bound, start at timeline zero and cover the
selected range. The exporter seeks to the selected first-frame time, re-encodes
audio as AAC and re-bases video time to zero. Declaring alignment is the exporter's
responsibility; this does not estimate lip sync or repair offset audio.

Runtime files are SHA-256-checked and copied into an isolated runtime snapshot;
changed sources or snapshots fail without being overwritten. Each job streams
one input/output frame at a time, uses existing disk quotas and cancellation,
and closes only its own Worker. Missing frames, failed evaluation or encoder
truncation are errors, not successful fallback output. Existing GPU serialization
and idle-NR release are reused; simultaneous feature sessions are not enabled.
