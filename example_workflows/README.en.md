# Example workflows

English · [简体中文](README.zh-CN.md)

Audience: public

These are importable **workflow JSON** files, not API-format prompts. Drag a
file into ComfyUI or use **Open**. Video-source examples use native Load Video;
replace `example-input.mp4` with your own file. Reconstruction instead requires
an explicit renderer bundle, not Load Video.

| File | Flow | First safe test |
| --- | --- | --- |
| [dlss_native_preview.json](dlss_native_preview.json) | Load Video → DIS Flow → Guides → Input Adapter → Runtime + Look + History → Preview | Check input, then render one cursor frame. |
| [dlss_native_process.json](dlss_native_process.json) | Same preparation → Process Video → Save Video | Verify a preview first; then queue full duration at 100 percent. |
| [dlss_nvidia_flow_preview.json](dlss_nvidia_flow_preview.json) | NVIDIA Flow replaces DIS | Confirm the native NVOF helper before rendering one frame. |
| [dlss_compare_looks.json](dlss_compare_looks.json) | Two NR Looks feed A and B | Change B intensity while keeping A as reference, then render one frame. |
| [dlss_pipeline_preview.json](dlss_pipeline_preview.json) | Flow Selector → Input Assembler → two NR Stages → Pipeline Preview | Experimental new wiring; DIS selected, second stage disabled, 50% single-frame preview. |
| [dlss_external_motion_preview.json](dlss_external_motion_preview.json) | Same pipeline plus External Numerical Guide → Input Assembler external_motion | Starts in Configured mode; the placeholder manifest is not requested. Supply a real manifest before switching to External. |
| [dlss_sr_experimental.json](dlss_sr_experimental.json) | Normalized input + flow + external device depth → SR Stage → Pipeline Render → Save Video | Experimental wiring only. Supply matching depth and an SDK-enabled Worker first; GPU acceptance is pending. |
| [dlss_reconstruction.json](dlss_reconstruction.json) | Renderer Bundle Input + owned_sl Runtime → Reconstruction Render → Save Video | Supply real bundle/runtime paths; starts with one SR frame. Supports SR/DLAA/RR; see [bundle format and node instructions](../docs/reconstruction-nodes.en.md). |
| [dlss_streamline_video.json](dlss_streamline_video.json) | Input Adapter + camera/depth → Input Assembler → Streamline Stage → Pipeline Render → Save Video | Set source-matched camera/depth manifests and owned_sl runtime; starts with a short SR range. See [camera inputs](../docs/streamline-video-input.en.md). |

The NR examples expect `runtime-presets/default.json`. Prepare it from
[the direct NR preset template](../examples/runtime-presets/direct-nr.example.json)
and install/build the relay first. The full-video example uses start 0,
duration 0, size 100 and **Process to end** enabled. Preview examples default to
one cursor frame at 50 percent to reduce iteration cost; use a short range and
100 percent before judging final temporal/full-resolution quality.

To try multi-pass NR in an NR example, add **DLSS NR Pass Stack**, connect the
existing Look to pass 1, and start with two passes. Connect a second Look to
pass 2 or leave it disconnected to inherit pass 1. Connect the stack output to
the Preview/Process socket that previously received the Look. Compare one and
two passes on a frame before a moving range; see [multi-pass NR](../docs/MULTI_PASS_NR.en.md).

No Worker, model DLL or source media is included. Importing does not add the
workflow to Comfy's saved workflow library; save it explicitly if wanted.

The pipeline example keeps source inspection, Guides and effect stages separate.
Selector `a` uses DIS; `b` needs the NVIDIA helper. Enable stage 2 to repeat the
Look, or connect a different Look. Branch the final stage to Pipeline Render and
Save Video for independent full output; the example deliberately omits that
branch to avoid accidental full renders. See [pipeline instructions and validation
limits](../docs/media-pipeline.en.md). Old examples require no rewiring.

The external-motion example adds a guide loader bound to the same inspected
sequence. Keep **Motion source → Configured** for the existing DIS baseline.
Set the loader's manifest path to a real file on the ComfyUI host, then explicitly
switch to **External**. Missing, mismatched or malformed selected guides fail;
the node never substitutes DIS silently. No estimator, manifest or numerical
frame files are bundled. See [external guide preparation](../docs/external-guides.en.md).

The SR example instead uses `runtime-presets/owned-sr.json` from the
[SR preset](../examples/runtime-presets/owned-sr.example.json). It requires our
SDK-enabled Worker, a user-supplied `nvngx_dlss.dll` and a real source/grid/PTS-matched
device-depth manifest. The depth placeholder is intentionally not a runnable
input. Use normalized sRGB, zero jitter, automatic exposure, isolated mode and
Render scale 100; output dimensions belong to SR Stage. There is no SR A/B preview
or mixed NR/SR stack. Source and mocked tests are not GPU acceptance; see
[SR prerequisites and build instructions](../docs/super-resolution.en.md).
