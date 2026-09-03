# Video input adapter

English · [简体中文](video-input-adapter.zh-CN.md)

Audience: public

The existing `DLSSExperimentalPrepareTemporalSequence` ID now displays as
**DLSS Video Input Adapter**. It accepts VIDEO plus Video Guides settings,
returns the same sequence/report socket types, and is also an output node so
its **Check input (no NR)** button can queue only its ancestors. No Worker,
runtime preset or GPU initialization participates in inspection.

## Information and status

The inline card shows the file/demuxer, size, codec/profile, reported bit depth,
pixel format, encoded dimensions, active VIDEO dimensions/trim, timestamps,
reported frame counts, color metadata, audio information and preparation plan.
Unknown fields remain unknown; demuxer names are not treated as a unique
container subtype, and nominal frame rate does not prove CFR.

States are ready, ready with assumptions, needs confirmation, unsupported,
unreadable, or deferred. Reports separate header readability from decoding and
timing validation. File-backed VIDEO is probed without decoding all frames.
Generic/tensor/stream VIDEO defers header validation to selected-range
materialization. The card explicitly labels its last check; rerun after changing
upstream media. GPU/Proton readiness remains a separate Runtime concern.

## Explicit missing-color policy

| Input | Values | Meaning |
| --- | --- | --- |
| `color_policy` | `strict`, `fill_missing` | Strict requires supported tags; fill only absent/unknown fields |
| `assumed_transfer` | `bt709`, `iec61966-2-1` | Missing transfer is interpreted as BT.709 or sRGB |
| `assumed_range` | `tv`, `pc` | Missing range is interpreted as limited or full |
| `normalize_to_srgb` | boolean, default false | Actually convert supported SDR input into sRGB working RGB before flow/NR |

In fill mode, missing matrix and primaries are interpreted as BT.709. Known
tags always win; unsupported known tags still fail. Original metadata and
effective interpretation are separate, and every assumed field is recorded.
This is a declaration about how to decode the existing pixels, **not** a
recovery of the true original metadata, a rewrite of the source, or HDR tone
mapping. Source files remain unchanged.

With normalization disabled the preparation layer uses the effective matrix/range
for RGBA8 conversion, preserves the nonlinear transfer convention, and records
that transfer on output. A/B compares the adapted original against the NR result. Exporting a
tagged result does not prove that the user's input interpretation was correct.

## Optional sRGB normalization

**Normalize to sRGB (SDR)** defaults off, including omitted fields in old API workflows.
When enabled, a supported BT.709 input is decoded to full-range RGBA8, then its
RGB components are actually re-encoded from nominal BT.709 to sRGB before motion
estimation and NR. Alpha is unchanged. Source sRGB is an exact identity path:
there is no repeated transfer conversion. This does not change primaries (both
use BT.709/D65), estimate white balance/exposure, infer absent tags or tone-map HDR.

The conversion uses the nominal BT.709 inverse OETF and the IEC sRGB transfer,
not an assumed BT.1886 viewing transform. It is an explicit experimental working
space choice, not proof that the external NR model requires sRGB. The existing
8-bit worker means transfer conversion is quantized; no lossless round trip or
HDR/high-bit-depth preservation is claimed. The tested 8-bit component LUT has
at most one code-value error for a BT.709 -> sRGB -> BT.709 round trip before
video encoding. Lossy video encoding introduces its own error.

Export applies the inverse working-to-source transfer to **both** the original
comparison and processed result, then uses the existing BT.709 YUV/limited-range
encoding and appropriate transfer metadata. It does not merely relabel pixels.
Sources remain unmodified. The report separates source interpretation, working
transfer and output transfer; `color_pipeline` in the manifest/report records
the transform. Unknown/unsupported source color remains blocked until explicitly
resolved; the switch never overrides known HDR/wide-gamut information.

Reference definitions:
- https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.709-6-201506-I%21%21PDF-E.pdf
- https://registry.color.org/rgb-registry/srgb

Linux validation includes byte-for-byte agreement between the export filter and
the independent Python inverse LUT, alpha/identity checks, tagged SDR video
preparation/export, and original-source SHA-256 preservation.

Current limitations still fail explicitly: HDR tags/auxiliary HDR metadata,
unsupported primaries or matrix, rotation, non-square pixels, invalid frame
rate, unsupported dimensions, and known unequal audio/video start times.
Upstream-cropped file VIDEO with missing color tags is also blocked: Comfy's
materialization could already decode using an unknown interpretation before
we could apply it. Tagged crops continue to use the upstream public VIDEO API.
Generic VIDEO headers may remain unknown until materialization; the result is
then checked again before NR. No preflight promises whole-file decodability.

## Motion modes

Video Guides adds an optional `motion_provider` after its existing inputs:

- `dis` (default): CPU DIS motion estimation and consistency diagnostics.
- `zero`: zero-filled vectors; no DIS object is constructed or evaluated.
  Scene-cut detection and continuous NR history remain active. This is not NR
  bypass and is not independent per-frame processing. Consistency is reported
  as unavailable when no motion was estimated.

Connected DIS or NVIDIA flow providers take precedence over this legacy widget.
See [connectable flow](nvidia-optical-flow.en.md). First frames and cuts still reset
history. External flow tensors, per-frame isolation, depth and arbitrary masks
are not advertised as implemented.

## Media tool settings

The advanced `ffmpeg_path` and `ffprobe_path` inputs optionally select absolute
host executable paths or bin directories. Blank uses backend PATH; with an
explicit FFmpeg path, blank ffprobe selects its companion in the same directory.
Invalid explicit paths fail without fallback. The card reports availability,
resolved paths, source, versions and PyAV version at the last input check.
Preview and Process reuse this configuration and record their actual tools.
See [media dependencies and resolution rules](media-tools.en.md).

## Cache and execution

Source content, selected range/scale, guide settings and color policy all
participate in the prepared-cache key (version 6), along with the selected media
tool identity. Changing NR intensity can
reuse these guides; changing input interpretation or motion mode cannot reuse
an incompatible spool. Preparation writes no intermediate transcoded whole
video. The old 30-second and 2-GiB limits have been removed; current admission
checks available disk space. See [range and storage](comfy-video-nodes.en.md#output-range-and-processing-mechanisms).

The cache stores **converted color frames as well as optical flow**, not just
metadata. `normalize_to_srgb` is part of its identity; NR Look intensity/style/
other appearance parameters are not. Preview and Process share this cache when
source, selected range, scale, pre-roll, color policy and guide parameters match.
The preview footer shows **Prepared color/flow cache reused** on a cache hit. The disk cache
persists across Comfy restarts; cheap source integrity/header checks can still run.
An exported video must still be encoded after NR, even when preparation is cached.

A full-range/full-resolution render cannot reuse a different low-resolution
preview entry. Partial overlaps between ranges are not currently stitched from
cached frames. Changing a guide setting rebuilds the combined color/flow entry;
there is not yet a separate cross-provider color cache or automatic disk eviction.
No source or user's saved output is deleted by enabling this option.

Inspection failures remain visible as a successful inspection result, with
`ready=false`. Preview/Process refuse those inputs before launching a Worker.
Preview publishes a fresh failed session rather than leaving an old successful
session displayed as if it belonged to the new input.

## Verified 2026-09-02

- 77 Python tests pass on Linux. On dependency-free macOS, 6 media tests skip.
- Four JavaScript tests cover formatting, assumptions, session isolation and
  branch-only submission.
- A 1344×768, 24 fps untagged H.264 clip reports all four missing color fields
  under strict policy. Browser inspection submitted only Load/Guides/Adapter.
- Explicit BT.709/limited assumptions allow real NR processing in both DIS and
  zero-vector modes. Both return 48-frame, 672×384 previews in about 6 seconds.
  Repeating zero mode reuses its cache; switching from DIS does not.
- Original tagged-video regression passes without assumptions.
- Full input processing and native Save Video return 124 frames at 1344×768,
  with BT.709 tags and audio. Video is 5.166667 s; encoded audio is 5.184 s
  (packet/container rounding), not a claimed sample-exact audio match.
- Worker cleanup reports zero remaining owned processes. Source SHA-256 is
  unchanged before/after processing.

`scripts/check_input_adapter.py` reproduces the opt-in GPU tests against a
dedicated configured Comfy instance using an existing nodes-1..6 test prompt.
It assumes the untagged test source should be interpreted as BT.709/limited
**for that test only**, and saves its report without modifying the source or
the user's open workflow.

Use the updated `dlss_native_preview.json` template after restarting Comfy and
loading a fresh frontend document. Old workflow links stay compatible, with
new optional controls defaulting to strict/dis. The original browser page may
cancel reloads while it has unsaved changes; preserve it and open a fresh page
if new controls are missing.
