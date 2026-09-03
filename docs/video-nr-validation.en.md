# Real-video NR validation

English · [简体中文](video-nr-validation.zh-CN.md)

Audience: public

The diagnostic separates three reusable boundaries:

- `media_clip.py`: bounded, timestamp-preserving clip decoding and export.
- `temporal_guides.py`: CPU optical flow and scene-history resets.
- `direct_nr.py` / `native_relay.py`: external-worker protocol and supervision.

`scripts/run_video_nr_test.py` connects them for explicit tests. The Comfy nodes
now share this path through `video_pipeline.py`; see
[node integration](comfy-video-nodes.en.md). This is not a replacement preview
application or a bottom panel.

## Dependencies and scope

Linux tests use the Comfy environment's existing NumPy, OpenCV with DIS, PyAV,
Pillow, plus host FFmpeg and ffprobe executables (not supplied merely by PyAV).
See [media tool selection](media-tools.en.md). No packages or toolchains were installed. Tested:
NumPy 2.4.6, OpenCV 5.0.0, PyAV 18.0.0 and FFmpeg 9.0.1. Pure Python tests run
without media packages; missing-dependency integration tests explicitly skip.

The adapter supports tagged BT.709 primaries/matrix with BT.709 or sRGB transfer,
square pixels, even output dimensions and constant frame rate. It rejects
HDR/unknown color, rotation, changing resolution and variable/discontinuous
timestamps instead of silently reinterpreting them. The initial diagnostic had
30-second and 2-GiB preparation limits. Current nodes instead use available-space
admission and support full duration; see [current limits](comfy-video-nodes.en.md#output-range-and-processing-mechanisms).
Pre-roll is at most 5 seconds. Preparation remains disk-backed, not overlapping streaming.

Only the selected range and preceding context are spooled to disk, not a
whole-video RAM tensor. Seeking may decode from an earlier keyframe. Source
checksumming still reads the file. Spools are retained for reproducibility,
not automatically deleted. Sources and their embedded metadata remain intact.
Reports omit arbitrary container tags, including embedded Comfy workflow JSON.

## Timing, motion and color

Preview start/duration are relative to the video stream start. Actual decoded
timestamps are retained in nanoseconds and checked against the rational frame
rate. Pre-roll frames run through NR but are omitted from visible output.
Repeated-first-frame warmup is separate from real pre-roll motion. A and B
exports start at visible frame zero and contain the same frames. Visible-interval
audio is re-encoded to AAC, not bit-for-bit copied. Unusual source audio/video
start offsets still need dedicated validation.

OpenCV DIS estimates **current-to-previous** displacement in top-left-origin
pixel coordinates. Reduced-resolution flow is resized and each component
scaled back to full-resolution pixel units before little-endian FP16 packing.
A known (+6,+4) translation must yield approximately (-6,-4) vectors at both
full and half analysis scales. This follows the
[OpenCV optical-flow convention](https://docs.opencv.org/4.x/d4/dee/tutorial_optical_flow.html)
with the current frame passed first, and matches the inspected external
converter's guide interface. This is community-runtime compatibility evidence,
not an official Feature 18 specification.

Frame zero, explicit resets and detected cuts zero the guide plane and reset
history. Cut detection uses a configurable grayscale-difference threshold;
fast motion/lighting changes may cause false cuts. Forward/backward consistency
and warped-image errors are diagnostics, not reactive masks: this worker has
no such input. They do not prove temporal stability or perceptual improvement.

Decode explicitly converts YUV matrix/range to RGBA while preserving nonlinear
SDR code values by default. The optional [sRGB normalization](video-input-adapter.en.md)
adds an explicit working-transfer conversion and matching inverse on export. Export converts
RGBA to limited-range BT.709 YUV and tags the original transfer. The test host's
FFmpeg/libx264 dropped transfer/primaries with codec flags alone. Setting frame
metadata using `setparams` fixed the tested case. Export verifies color tags,
actual frame count, frame rate and audio presence before publishing a final
filename. Selected lossless PNGs help separate model changes from encoding.

## Usage

Use the relay, external worker/model pair, Proton installation and isolated
root from [direct-nr-relay.en.md](direct-nr-relay.en.md). Supply the active Xwayland
`DISPLAY`. The first command compares zero and estimated motion; the second
compares effect intensities using one shared preparation.

```bash
python scripts/run_video_nr_test.py \
  --source /path/to/source.mp4 --root /path/to/dlss/direct-nr-experimental \
  --relay /path/to/dlss-native-relay.exe --worker /path/to/runtime/nvngx.dll \
  --proton '/path/to/installed/Proton/proton' \
  --start 1 --duration 2 --pre-roll 0.5 --modes zero dis

python scripts/run_video_nr_test.py \
  --source /path/to/source.mp4 --root /path/to/dlss/direct-nr-experimental \
  --relay /path/to/dlss-native-relay.exe --worker /path/to/runtime/nvngx.dll \
  --proton '/path/to/installed/Proton/proton' \
  --start 0 --duration 8 --pre-roll 0 --modes dis --intensities 0.25 1.0
```

Each `video/<run-id>/` contains `prepared/manifest.json`, color/motion spools,
`original.mp4`, per-variant `processed.mp4`, selected PNGs, worker logs and JSON
reports. `passed` means protocol/export checks passed, **not** that the result
looks better. `visual_quality_approved` remains false until evaluated.

## Initial evidence (2026-09-02)

A user-provided 544×960, 24 fps animated portrait clip was tested without
resizing. The initial 2-second comparison used 12 context frames and 48 visible
frames, plus 120 warmup evaluations per variant (180 evaluations each). Zero
and DIS motion both returned complete sequences and cleaned up their processes.
GPU memory returned to the idle baseline of 3 MiB.

Across 59 transitions, mean grayscale matching error was 12.75 without warping,
4.48 with backward flow, and 17.91 with negated flow. This checks guide alignment,
not output quality. Initial MP4s in run `4c62f7f7f676` lacked transfer/primaries
tags and are **superseded**; its lossless PNGs/raw processing evidence remain
valid. Use the later, color-verified exports for appearance comparisons.

Final run `0b2287ec16bd` processed all 192 frames (8 seconds) at intensities
0.25 and 1.0, sharing one preparation. Each variant completed 312 evaluations
including warmup, returned 192 distinct frames and left no owned processes.
Both video and audio exported at 8 seconds; sRGB transfer, BT.709 primaries and
matrix, limited range and 24 fps were verified in the files. Preparation took
about 4.8 seconds, each worker run about 22 seconds, and the entire two-variant
diagnostic about 55 seconds including exports. These are single-clip diagnostic
timings, not a general performance guarantee. The high-intensity sampled frames
change shading/material appearance; user review is still needed for preference
and temporal artifacts.

Regression tests cover known translation/sign/scale, cuts, explicit reset,
invalid planes, CFR rounding, unsupported color/rotation, range/pre-roll,
independent decoder comparison, audio export, color-tag preservation, overwrite
rejection, cancellation and source integrity. All 56 tests passed on Linux;
four media tests skip on the dependency-free macOS interpreter.

Remaining media work: subjective motion/appearance review, long-video streaming,
and precise audio-offset/VFR support. Runtime presets, node-card A/B transport,
in-flight cancellation and stale-preview suppression are now connected and
tested in the separate Comfy integration described above.
