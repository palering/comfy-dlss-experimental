# Input assembly and feature pipelines

English · [简体中文](media-pipeline.zh-CN.md)

Audience: public

The experimental pipeline nodes separate source inspection, guide selection and
effect stages. Existing workflows, Look/Stack controls, runtime presets and the
external D5V2 Worker remain supported. The opt-in [owned NR runtime](owned-runtime.en.md)
also uses this pipeline. A separate [experimental SR/DLAA path](super-resolution.en.md)
now connects SR Stage to Pipeline Render; it requires an SDK-enabled Worker and
numeric depth, and has not passed GPU acceptance. These nodes do not generate
depth or FG frames.

The [Streamline VIDEO path](streamline-video-input.en.md) connects the same
assembler to **Streamline SR / DLAA Stage** and Pipeline Render, with explicit
camera/device-depth inputs and `owned_sl` / CXR1. This separate path has bounded
synthetic Linux GPU and actual Comfy acceptance; it does not certify CSR1.

## Wiring

```text
Load Video → Video Input Adapter ────────────────┐
DIS Flow ──── a ┐                              │
NVIDIA Flow ─ b ├─ Flow Selector → Input Assembler → NR Stage → Pipeline Preview
               ┘                  ↑                ↑   └──→ Pipeline Render → Save Video
                             Video Guides        NR Look
```

Runtime connects separately to Preview/Render. Optional History Settings controls
pre-roll and warmup, not the output range. One output socket may feed several
consumers; a separate hub node is not required.

Import [the pipeline example](../example_workflows/dlss_pipeline_preview.json).
Select your video and configure `runtime-presets/default.json` using the
[existing installation steps](distribution.en.md). The example starts with DIS
(`a`), one enabled NR stage, a second disabled stage and a single-frame 50% preview.
Switch to `b` only after installing the native NVIDIA flow helper. No new DLL is
required for this node architecture.

## Node responsibilities

### Conditional controls and preserved values

Streamline Stage hides manual output dimensions in DLAA, which keeps input dimensions;
switching back to SR restores the previous values. SR also offers 1.5x/2x/3x,
calculated from the current input with manual dimensions hidden. Old workflows
default to manual; switching preserves values. Actual sizes appear after execution;
see [SR multipliers and budgets](streamline-video-input.en.md). Pipeline Render, Process Video,
Pipeline Preview and Preview Session hide manual duration when Process to end is enabled,
but preserve start time. Only start 0 covers the full input. Single-frame output still
returns one frame; Render/Process duration 0 keeps its legacy all-remaining meaning.
Preview and render ranges remain independent.

The note explains current settings; it neither runs work nor refreshes old reports.
Connected modes do not hide controls based on local values. Hiding preserves values,
widget order and links, including saved-workflow reloads. Save your work before refreshing
the frontend. No new dependencies or Worker, model, sampling or timing-contract changes.

| Node | Responsibility | Does it render? |
| --- | --- | --- |
| Video Input Adapter | Inspect source headers, color policy and host media tools. Its legacy Guides input is optional. | No NR; actual preparation is deferred. |
| Flow Selector | Select `a`, `b` or `c`. Only the selected lazy input is requested by this node. | No. |
| External Numerical Guide / Guide Selector | Load/select numerical guide declarations bound to the same VIDEO. Actual frame reads are deferred. | No. |
| Input Assembler | Keep the VIDEO reference; select configured/external motion and attach depth/camera/normals/mask/confidence with explicit usage status. | No. |
| Camera Timeline Input | Load a source-bound camera/metadata timeline, without running a camera estimator. | No. |
| Streamline SR / DLAA Stage | Declare one CXR1 stage and output dimensions on an input-only pipeline. | No; Pipeline Render uses owned_sl. |
| NR Stage | Append a Look or Pass Stack, with an enable switch. | No. |
| SR / DLAA Stage | Declare one SR/DLAA stage and explicit output dimensions on a fresh input pipeline. | No; SDK Worker execution is deferred. |
| Pipeline Preview | Execute the chosen NR frame/range; in-node A/B, wipe and diagnostics. | Yes, NR only; rejects SR. |
| Pipeline Render | Dispatch NR or one experimental SR/DLAA stage for its independent output range. SR dimensions belong to SR Stage; keep Render scale 100. | Yes, when consumed by Save Video or another output and required runtime/inputs are available. |

The selector does not fall back if the selected input is missing or invalid.
An unselected provider independently used by another output can still execute
for that consumer; lazy selection does not cancel other graph branches.

## Guide precedence and checks

Input Assembler's `motion_source` defaults to `configured`: its explicit flow
provider wins, then connected Video Guides settings, then legacy Input Adapter
settings. Analysis size, motion scaling and cut thresholds belong in Guides;
algorithm options belong in the DIS/NVIDIA provider. Configuration providers do
not compute frames while the graph is being assembled.

Select `external` to request `external_motion` instead. This branch reads numerical
current-to-previous pixel motion with source/hash/PTS checks during preparation;
it does not run DIS/NVIDIA or silently fall back. An unselected external-motion
branch is lazy. Optional `depth`, `normals`, `mask`, `confidence` ports preserve
typed numerical-guide attachments but **do not affect NR output**. See
[external numerical guides](external-guides.en.md) for the file/units/time contract.

Assembler, NR Stage, SR Stage and Streamline Stage cards offer **Check plan (no render)** and **Copy details**.
The check queues only that node and its ancestors, not downstream render/save
nodes. It does not instantiate an estimator, initialize NR or prove GPU support.
Source header errors remain visible. Timing/content checks run for the requested
range during execution. Cards show the last received result; check again after
changing upstream configuration. Reports are not embedded in saved workflows.

Connect the final feature stage's pipeline output to Setup Helper's optional
`pipeline` input to inspect the resolved media tools and selected guide
dependencies. This input overrides the helper's separate `sequence` and
`flow_provider` inputs. Helper checks the selected configuration conservatively,
even if a stage currently bypasses NR; it does not start the model. The existing
opt-in NVOF capability query remains separate.

## Serial stages and branches

Chain NR Stage nodes to apply different Looks in order; use a Look/Stack input
for repeated settings. The current limit is **three total passes**, counting
stages inside a connected Stack and disabled stages. A disabled stage is a bypass,
not removal of a slot. Later stages receive the previous uncompressed result;
there is no per-stage MP4 encoding. Source motion/timestamps/cut flags are reused,
not re-estimated after each stylization. This limitation remains visible in the
[multi-pass contract](MULTI_PASS_NR.en.md).

Branch an assembler/stage output to compare independent effects or preview an
earlier point. Branches get detached settings; they do not share mutable NR
history. Separate graph branches do **not** promise simultaneous GPU execution.
Small retained preparation entries may be shared according to the existing cache
key; streamed ranges recompute guides. If both B and any optional A comparison
bypass NR, the new pipeline path omits optical-flow estimation.

Preview's optional Look/Stack A applies to the same prepared source. With no A
configuration, A is the input reference. Preview outputs remain preview-sized and
preview-length. For a complete video, branch the final NR Stage to Pipeline Render,
select scale 100 and Process to end, then connect VIDEO to Save Video. Pressing a
card's preview button does not trigger that full-output branch.

## Separate SR/DLAA branch

Connect a fresh Input Assembler output to **SR / DLAA Stage**, with SR Settings,
then **Pipeline Render → Save Video**. Supply matching `device_z` depth and a
separate `owned_sr` runtime. Normalize input to sRGB; keep Render scale at 100
and Worker residency off. The current SR media executor streams frames and does
not retain prepared raw-frame caches or repeat NR warmup evaluations.

This first path permits exactly one SR/DLAA stage. Mixed NR/SR chains, repeated
SR stages and SR A/B Preview are rejected explicitly. NR and SR can instead
branch from the same assembled source; this is independent processing, not
composed effects or a promise of GPU concurrency. See
[SR requirements and build boundary](super-resolution.en.md) before importing
[the SR wiring template](../example_workflows/dlss_sr_experimental.json).

## Data and compatibility boundary

The pipeline carries an opaque VIDEO reference plus a detached recipe, not the
whole video's decoded tensors. NR color normalization, guide computation, cancellation,
worker residency and [storage quotas](STORAGE.en.md) use the existing executor.
Changing Look settings does not put effect parameters into the guide cache key.

The media adapter supplies RGBA8 color and current-to-previous pixel motion packed
as RG16F. Owned NR converts color to its CNR1 FP16 transport; legacy D5V2 retains
its original transport. External depth/normal/mask/confidence ports are numerical
metadata attachments, not estimator implementations or actual NR inputs; reports
label them unconsumed by either NR backend. No geometry, lighting or ray-tracing
buffers are invented from their presence.

The SR executor consumes the attached device-depth plane, not its visualization,
alongside prepared color and motion. It uses CSR1 and the official SDK rather
than the NR caller; runtime bindings, frame dimensions and feature support are
validated separately. Other attachments remain explicitly unconsumed by SR.

The new graph paths have portable contract/schema/frontend tests. These tests
do not replace real ComfyUI import, browser layout or Windows/Linux GPU acceptance.
Do not overwrite working presets or replace a legacy external Worker with a caller
shim. The owned Worker and [thin caller](caller-shim.en.md) are separate components
selected through an explicit owned runtime preset.
