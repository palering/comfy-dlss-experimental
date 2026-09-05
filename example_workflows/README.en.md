# Example workflows

English · [简体中文](README.zh-CN.md)

Audience: public

These are importable **workflow JSON** files, not API-format prompts. Drag a
file into ComfyUI or use **Open**. Each uses ComfyUI's native Load Video/Save
Video nodes; replace `example-input.mp4` with your own uploaded/selected file.

| File | Flow | First safe test |
| --- | --- | --- |
| [dlss_native_preview.json](dlss_native_preview.json) | Load Video → DIS Flow → Guides → Input Adapter → Runtime + Look + History → Preview | Check input, then render one cursor frame. |
| [dlss_native_process.json](dlss_native_process.json) | Same preparation → Process Video → Save Video | Verify a preview first; then queue full duration at 100 percent. |
| [dlss_nvidia_flow_preview.json](dlss_nvidia_flow_preview.json) | NVIDIA Flow replaces DIS | Confirm the native NVOF helper before rendering one frame. |
| [dlss_compare_looks.json](dlss_compare_looks.json) | Two NR Looks feed A and B | Change B intensity while keeping A as reference, then render one frame. |

Every example expects `runtime-presets/default.json`. Prepare it from
[the direct NR preset template](../examples/runtime-presets/direct-nr.example.json)
and install/build the relay first. The full-video example uses start 0,
duration 0, size 100 and **Process to end** enabled. Preview examples default to
one cursor frame at 50 percent to reduce iteration cost; use a short range and
100 percent before judging final temporal/full-resolution quality.

To try multi-pass NR in any example, add **DLSS NR Pass Stack**, connect the
existing Look to pass 1, and start with two passes. Connect a second Look to
pass 2 or leave it disconnected to inherit pass 1. Connect the stack output to
the Preview/Process socket that previously received the Look. Compare one and
two passes on a frame before a moving range; see [multi-pass NR](../docs/MULTI_PASS_NR.en.md).

No Worker, model DLL or source media is included. Importing does not add the
workflow to Comfy's saved workflow library; save it explicitly if wanted.
