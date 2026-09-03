# DLSS NR Look

Audience: public

The node now exposes every look-related field in our current D5V2 worker
contract. This is **not** a claim to expose every NVIDIA NR feature or every
ReShade/RenoDX filter. The node uses native Comfy V3 inputs inside the card;
there is no separate control panel or new frontend dependency.

## Controls

| Input | Default / values | Purpose and limits |
| --- | --- | --- |
| `nr_enabled` | true | False bypasses the NR worker. |
| `intensity` | 0.25 / 0–3 | Native NR intensity; zero is not guaranteed to be an identity transform. |
| `mix` | 1 / 0–1 | Post-render original/result blend; zero bypasses the worker. Not an NGX parameter. |
| `nr_preset` (advanced) | 模型默认（建议） / 实验预设 A, B, C | Native IDs 0–3; unknown effect semantics are not presented as named quality modes. |
| `nr_style` | 自然 · Natural / 默认风格, 自然 · Natural, 电影感 · Cinematic | Native IDs 0–2 respectively. Community names do not guarantee the same appearance across DLLs. |
| `local_tone_strength` | 1 / 0–3 | Native local tone control, not a separate grading filter. |
| `local_structure_strength` | 1 / 0–3 | Native structure/detail control, not conventional unsharp masking. |
| `skin_structure_strength` | -1 / -1–3 | Native skin structure control. -1 retains the runtime's default behavior; no Python skin post-processing is added. |
| `automatic_mask` | false | Requests runtime automatic masking; does not submit an external mask texture. |
| `ui_correction` (advanced) | false | Experimental runtime UI correction. No separate UI texture is supplied; not a subtitle-protection guarantee. |
| `worker_profile` (advanced) | 当前兼容配置（建议） / 实验配置 A, 当前兼容配置（建议）, 实验配置 B, C | Native IDs 0–3 respectively; distinct from NR preset. Keep the current compatible setting (ID 1). |

The first three widget positions and the schema-2 socket remain unchanged.
Old schema-2 workflows get the same native defaults when the new optional
inputs are absent. Old schema-1 scaffold settings still fail explicitly.
Integers are not silently truncated/coerced; non-finite/out-of-range strengths
and non-boolean flags are rejected before starting a worker.

The card uses Chinese field labels and readable dropdown captions, not raw
integer menus. Native IDs are an implementation detail preserved in the profile
output and protocol. A small frontend migration converts old integer widget
values on workflow load; backend validation also accepts exact legacy integers
for headless API workflows. Unknown captions, numeric strings and booleans are
not silently coerced. Existing user workflow files are not overwritten.
Experimental model presets and worker configuration stay in advanced inputs;
we do not invent “quality” names for values with no verified visual meaning.

Preview and Process share `profile_settings()` and `render_variant()`:
the same Look output feeds either node. The settings actually transmitted
appear in the render report's `settings`; the UI profile appears under
`profile`. They are useful for tracing a result, but not proof that a closed
runtime internally honors every field.

## Using the controls

1. Open a preview workflow; select a short range and render the default Look.
2. Change one parameter, then click **Render preview** again. Already prepared
   motion guides are reused. Moving the wipe only compares existing results.
3. For two-effect comparison, connect a second NR Look to **Pinned Look (A)**;
   **Working Look (B)** is the editable variant. Unconnected A means the adapted
   original. A is another node's settings, not a frozen snapshot of previous B.
4. Connect the chosen Look to Process Video for export. Check at 100% preview
   scale before judging full-resolution appearance.

Use `intensity`, `nr_style`, `local_tone_strength`, and
`local_structure_strength` first. Automatic mask is also responsive on our
sample, but its exact spatial behavior has not been independently established.
Skin structure, preset and advanced fields remain experimental. Larger values
are not inherently higher quality. Mix is a nonlinear RGBA8 blend, not
linear-light color management.

Depth, externally generated masks and motion estimation belong to input
guidance and require their own transport support. They are not implemented by
these look sliders. SR output resolution, frame generation, seed, text prompts,
film grain and unrelated ReShade shaders are not part of this worker contract.
Warmup/pre-roll remain in History Settings; Proton/DLL selection remain in
Runtime Configuration.

See [NR input reconstruction](nr-input-reconstruction.md) for the distinction
between NR inputs, game-engine data, and proposed optical-flow/depth providers.

## Measured response

2026-09-02: ComfyUI 0.34.0, Linux RTX 4070 Ti SUPER, GE-Proton11-6, current
user-supplied RTX40 video-converter 0.1.0 worker/model pair. Test sample:
tagged SDR 544×960, 24 fps; first 0.5 seconds rendered at 50% (272×480,
12 visible frames), 120-frame warmup, DIS motion, otherwise default Look.

The comparison hashes concatenated **visible RGBA bytes before lossy video
encoding**, after mix. Old defaults, explicit new defaults and a repeated
render were byte-identical. A changed hash establishes a pixel response only,
not perceptual strength, improved quality, or intended parameter semantics.

| Changed input | Tested values | Compared with default output |
| --- | --- | --- |
| `nr_style` | 0, 2 | Different pixels |
| `intensity` | 0, 2 | Different pixels |
| `local_tone_strength` | 0, 2 | Different pixels |
| `local_structure_strength` | 0, 2 | Different pixels |
| `automatic_mask` | true | Different pixels |
| `nr_preset` | 1, 2, 3 | Identical pixels |
| `skin_structure_strength` | 0, 2 | Identical pixels |
| `ui_correction` | true | Identical pixels |
| `worker_profile` | 0, 2, 3 | Identical pixels |
| `mix` | 0, 0.5 | Different pixels; zero starts no worker |
| `nr_enabled` | false | Same pixels as mix=0; starts no worker |

Identical results do not prove universal lack of support: the model, material,
other settings or missing auxiliary textures may matter. The native header
was verified independently for each field; no undocumented effect is invented
to make an unresponsive control look functional.

All 24 parameter/default/bypass cases completed successfully. Each non-bypass
case reported zero remaining owned processes. Two distinct Look nodes also
rendered successfully; the combined nondefault Look then exported 8 seconds,
544×960, 192 frames with 8-second audio and retained BT.709/sRGB tags through
Process Video → native Save Video. Timings on this sample: roughly 3.3–3.8
seconds per half-second preview, 6.3 seconds for two looks, 23.9 seconds for
full-resolution export. These are observations, not performance promises.

## Reproduction

`scripts/check_nr_look.py` is opt-in and queues real GPU work. Use only a
dedicated, idle Comfy test instance, with the configured API preview graph
(node IDs 1–6), installed runtime and tagged test input already available:

```sh
python scripts/check_nr_look.py --url http://127.0.0.1:8199 \
  --prompt /path/to/preview-api.json --video tagged-test.mp4 \
  --report /path/to/nr-look-report.json
```

This fixture expects 24 fps and an 8-second source for the documented outcome.
It uses a unique workflow/client ID, checks exact reported native values,
compares repeated hashes, tests bypass, two looks and full export, and writes
the API results to the specified report. It refuses an initially busy queue.
Do not enqueue production work concurrently. The script does not download or
redistribute vendor DLLs, and the node changes do not require rebuilding C++.

`scripts/check_nr_labels.py` checks named versus legacy numeric API choices on
the same sample, including identical default pixels and rejection of invalid
choices. Comfy may still queue the independent input-inspector output when
rejecting an invalid NR branch; validation is checked in `node_errors`, not
only the HTTP status. Unit coverage is now 87 Python / 6 JavaScript tests.
