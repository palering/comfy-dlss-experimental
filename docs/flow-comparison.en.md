# Optical flow and SR / DLAA comparison

English · [简体中文](flow-comparison.zh-CN.md)

Audience: public

## Independent controls

DIS is a traditional CPU optical-flow estimator, not a learned model or a zero
motion placeholder. The current Balanced provider uses OpenCV MEDIUM with finest
pyramid scale 1; Quality uses MEDIUM with scale 0. Video Guides independently
defaults to 50% analysis width/height. Consequently changing only the provider
does not imply full-source-resolution analysis. Choose 100% explicitly to test
full input; it changes flow cost, not source RGB, SR output or geometry inference.

NVIDIA Flow uses the GPU's optical-flow accelerator via the host-native helper,
not a neural flow model or the Proton DLSS Worker. Query support before selecting
1/2/4-pixel output grids. Smaller grids are denser, not proof of better estimates.
The helper maintains separate directional temporal histories, resets on cuts,
and never falls back to DIS. A missing helper must be provided inside the project
using existing build dependencies or an approved package, not a global install.

The optional Flow Selector requests only its selected lazy provider. Connect it
to Video Guides and connect those settings to Video Input Adapter; Input Assembler
then inherits the same settings. Do not also connect an unintended overriding
provider at the assembler. Analysis percentage and provider preset are independent.

## Controlled comparison

Use identical source pixels, saved geometry, frame range, pre-roll, reset policy,
SR mode/preset and encoder settings. Change one variable at a time:

1. DIS Balanced at 50% versus 100% analysis.
2. At 100%, DIS Balanced versus DIS Quality.
3. At 100%, DIS Quality versus NVIDIA Quality, recording output grid and hints.
4. Compare each 2x SR output against a 2x Lanczos baseline at the same output size.
5. Compare original-size DLAA against original-size input separately. A scaled
   DLAA display is only a comparison presentation, not DLAA super-resolution.

Current StreamEncoder exports H.264, x264 fast, CRF 16. Equal CRF/settings does
NOT mean equal bitrate or file size. A delivery-efficiency comparison additionally
needs equal bitrate/size; do not infer quality from file size. Inspect 1:1 crops
and moving edges, hair, thin lines, occlusions, ghosting and shimmer. Flow warping
error/forward-backward consistency are diagnostics, not ground-truth quality
scores; blur and occlusion can distort them. Keep per-case settings, resources,
output hashes and completed files so a failed case does not invalidate the rest.

## Reasonable processing paths

The default alternatives are source + guides -> SR -> encode, or source + guides
-> DLAA -> encode. SR already contains temporal reconstruction/anti-aliasing.
SR -> DLAA is not supported by the current single-stage VIDEO executor. A valid
future experiment would need high-resolution motion (resampled with pixel units
scaled or re-estimated), matching depth/calibration/source identity, a new temporal
history and an uncompressed intermediate. Reusing the low-resolution guides or
encoding/reloading without rebinding is invalid. Double temporal filtering may
soften detail or amplify artifacts; it is not assumed to improve quality.

Learned flow (e.g. RAFT-class models) is a later optional provider experiment,
not an installed capability. It requires weight/license review, isolated runtime
compatibility, memory measurement and validation of direction, units, occlusions
and reset behavior. Finished video still lacks independently jittered render
samples and ground-truth engine geometry; a new flow model cannot promise to
recover these missing inputs.

See [size admission](streamline-video-input.en.md),
[OpenCV DIS](https://docs.opencv.org/4.x/de/d4f/classcv_1_1DISOpticalFlow.html),
[NVIDIA optical flow](https://docs.nvidia.com/video-technologies/optical-flow-sdk/nvofa-programming-guide/index.html)
and [Streamline DLSS integration](https://github.com/NVIDIA-RTX/Streamline/blob/v2.14.1/docs/ProgrammingGuideDLSS.md).
