# NR video input reconstruction: contracts and candidate providers

English · [简体中文](nr-input-reconstruction.zh-CN.md)

Audience: public

2026-09-03. Connectable DIS/NVIDIA flow, caching and diagnostics are implemented. Depth, segmentation and external MASK contracts below are extension considerations, not implemented NR inputs.

## Separate three questions

1. **What a game engine knows:** geometry, materials, camera transforms, depth, per-pixel motion and separate UI layers for different rendering stages.
2. **What NR inference needs:** NVIDIA's September 1 research description names current color, engine motion vectors, continued temporal state and artistic controls. Scene-property supervision during training does not imply a full runtime G-buffer.
3. **What we can transmit:** the external Worker's D5V2 accepts RGBA8 color, RG16_FLOAT motion, frame index/timestamp/reset and Look controls. It has no depth or external-mask plane. Its internal placeholder textures cannot be inferred from the protocol.

Sources: [NVIDIA ADLR](https://research.nvidia.com/labs/adlr/DLSS5/) and [technical/artistic-control overview](https://www.nvidia.com/en-eu/geforce/news/dlss-5-3d-guided-neural-rendering/). These descriptions are not the complete ABI of the experimental RTX40 Worker/DLL.

SR and FG have separate contracts: SR includes depth, motion and input/output color; FG also uses depth/motion and recommends UI-free color plus UI data. Do not apply these requirements wholesale to NR. See [SR](https://github.com/NVIDIA-RTX/Streamline/blob/main/docs/ProgrammingGuideDLSS.md) and [FG](https://github.com/NVIDIA-RTX/Streamline/blob/main/docs/ProgrammingGuideDLSS_G.md) integration guides.

## Inputs and approximations

| Information | Video-side source | Current status / possible use |
| --- | --- | --- |
| Color, size, timeline | Decode with explicit color interpretation; preserve trim/timestamps | Used: SDR RGBA8; not HDR or recovery of lost detail |
| Current pixel's previous position | Dense flow retaining camera and object motion | DIS/NVIDIA; zero-vector diagnostic retained |
| Temporal continuity/reset | Ordered frames, real pre-roll, cuts/discontinuities | Pre-roll, warmup, simple cut resets; detection needs refinement |
| Flow confidence/occlusion/disocclusion | Bidirectional consistency, bounds, reprojection error; potentially depth | Diagnostic statistics only, no confidence plane sent to NR |
| Artistic control mask | Segmentation, hand-painted MASK, temporal tracking | No external transport; not automatic runtime masking |
| Depth | Relative/inverse/metric estimation with temporal stability | Cannot transmit; investigate guide benefits and actual runtime consumption first |
| Normals/materials/calibration | Approximate and generally non-unique RGB inference | Not mandatory current NR inputs; evaluate against actual backend needs |

Flow is not ground-truth geometry motion. Occlusion, reflections, transparency, blur, repeated textures and generated-video deformations can break correspondence. Larger or fabricated motion is not stronger NR; the goal is correct correspondence.

A clay/depth visualization is not numerical depth. Single-image relative depth guarantees neither physical scale nor cross-frame scale, and cannot alone recover dynamic-object motion. Device Z requires explicit conventions and projection parameters; arbitrary grayscale 0–1 is not calibrated camera depth.

DLSS-COM's README at locally inspected commit 3aeca71 reports identical output with different depth textures but a responsive control mask. This is version-specific negative evidence, not proof every NR DLL ignores depth. [Project observation](https://github.com/MYT-YEP/DLSS-COM#%E6%B7%B1%E5%BA%A6%E5%9B%BE%E4%B8%BA%E4%BB%80%E4%B9%88%E6%B2%A1%E6%9C%89%E6%98%8E%E6%98%BE%E5%8F%98%E5%8C%96).

## Proposed shared guide contract

Do not bind algorithm names to the Worker. Providers should produce independently cacheable, standardized packages:

- Metadata: source fingerprint, crop/window, frame PTS/size, scale/padding, provider/model/version/settings, precision, valid region, native versus estimated provenance.
- Motion: H×W×2 float, current→previous, top-left origin, +X right/+Y down, displacement in current processing pixels. Explicit first/cut reset. A color visualization is insufficient. Negating forward flow does not generally yield backward flow.
- Confidence/occlusion: H×W validity/confidence with defined 0/1, bounds and unknown values; not equivalent to an artistic mask.
- Optional depth: H×W float, depth/inverse, near/far direction, units, relative-only status, invalid values and temporal normalization. Avoid independent per-frame min/max scale jumps.
- Optional effect mask: aligned H×W values, enhancement/protection direction and feathering. Post-composite mixing may be separate but must not be called internal NR guidance.

The backend selects supported planes. Reports distinguish **generated / transmitted / confirmed to affect output**. Unsupported options must fail explicitly, not silently drop connected data.

## Node ownership and extension boundaries

- Input Adapter: lightweight media compatibility, interpretation and metadata.
- Video Guides: common guide settings and connected flow provider; depth/MASK need a new contract.
- Guide diagnostics: flow/depth/confidence visualizations, cuts and reprojection errors; errors are not aesthetic scores.
- NR Look: appearance and strength; Preview/Process consume the same guides/Look.

Before extending depth, compare absent/constant/estimated/shuffled depth and decide whether the Worker must change. Use explicit version/capability negotiation, never append arbitrary planes to D5V2. Include new planes in memory/disk budgets. Previews compute only selected ranges and needed context; Look changes reuse guides, while model/crop/scale changes invalidate relevant caches.

## Information needed for candidate providers

1. Task (flow/depth/segmentation/tracking) and integration form (Comfy node, Python library or executable).
2. Numeric input/output, direction/scale/order, ability to export raw float fields.
3. Per-frame/pair/whole-clip execution, future frames, block overlap and fixed-size requirements.
4. Temporal stability, confidence/occlusion outputs and depth scale consistency.
5. Linux/CUDA/PyTorch/ONNX dependencies, weights, VRAM and speed, possible environment conflicts.
6. Code/weight licenses and download procedure; no automatic third-party installation/bundling by default.

Users mainly choose speed versus stability/detail, target size/duration and acceptable VRAM/weights. Interface details should not become wiring burdens.
