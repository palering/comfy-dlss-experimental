# Comfy video nodes — experimental first integration

English · [简体中文](comfy-video-nodes.zh-CN.md)

Audience: public

Verified 2026-09-02 with ComfyUI 0.34.0, frontend 1.51.9, Linux RTX 4070 Ti
SUPER, GE-Proton11-6 and the previously validated external worker/model. No
new system packages or toolchains were installed. These are V3 Python nodes;
the inline frontend uses Comfy's DOM widget extension API, not a bottom panel.
This does not claim a separately implemented native Nodes 2.0 Vue component.

## Two workflows

- `example_workflows/dlss_native_preview.json`: Load Video, Video Guides, Video Input
  Adapter, Runtime Configuration, NR Look, Preview Session.
- `example_workflows/dlss_native_process.json`: the same inputs, Process Video, native
  Save Video. VIDEO outputs can also feed other video nodes.

Select your input and configure a `direct_nr` preset using
`examples/runtime-presets/direct-nr.example.json`. Paths are relative to the
preset JSON or absolute. Select an installed Proton explicitly for repeatable
tests. The direct preset has exactly `relay`, `worker`, `nvngx_dlssnr`; no
ReShade proxy, RenoDX add-on, Streamline or Wine DLL override is needed here.
The older ReShade carrier diagnostic remains separate.

Importing a workflow JSON only opens it. Save it in Comfy to have it appear in
that instance's workflow library. A dedicated test instance has a separate user
directory and will not show workflows saved in the normal instance.

## Preview

Choose start time, duration and scale, then click **Render current frame** or **Render range** inside
the node. Only this node and its ancestors are queued, excluding unrelated
export branches. Subgraph-local preview submission is not implemented; use
Comfy's own execution controls there. First-run prefix creation can take longer.

The card offers split/wipe dragging, side-by-side, flicker, visual difference,
play/pause and one-frame stepping. Visual difference is browser compositing,
not a quantitative image metric. Videos are muted in the comparator; exported
clips retain audio. Browser playback is drift-corrected, not a frame-locked
scientific video player. Paused stepping seeks both videos to the same frame.

A defaults to an original clip prepared at the same size as B. Connect another
NR Look to the optional A input for two-look comparison. Changing a look does
not invalidate motion-guide preparation. Each explicit preview execution
creates a new session; node/workflow IDs and monotonic revisions reject stale
or unrelated events. Reload can restore a session while the backend and its
temporary files still exist. Cancel stops that session, not unrelated jobs.

Preview generation is explicit: after changing a look, click a render button
again to evaluate the selected range. The previously rendered video is not a
live view of changed upstream parameters. Wipe position, comparison mode,
playback and frame stepping operate on existing results without rerunning NR.

### Parameters and save scope

Preview Session is a real video-producing node, not only a viewer. Its `video_b`
and `video_a` outputs retain the preview's range, single-frame mode and dimensions.
Connecting one to Save Video saves that preview, not an automatic full-size/full-length
render. The card's two render buttons queue only the preview and upstream inputs;
they do not execute downstream Save Video. Comfy's normal queue uses `preview_mode`
and can execute the connected Save Video node.

| Parameter | Meaning |
| --- | --- |
| `start_time` | Seconds relative to the input VIDEO, including upstream trim. |
| `process_to_end` | Resolve remaining duration, ignoring manual duration; start 0 means the whole VIDEO. Frame mode still outputs one frame. |
| `duration` | Manual interval length, not an end time, batch size or warmup. Clamped at the end. |
| `preview_scale` | Percentage of each axis; 50% is about a quarter of the pixels. Changes NR input/output size, not UI zoom or SR. |
| `preview_mode` | Normal queue uses range/frame; card buttons override this submission only. |
| `cursor_time` | Frame timestamp = start + offset within the interval; ignored in range mode. |
| Working Look B / reference A | B connects NR Look; unconnected A is adapted input. A may connect another live Look, not a frozen snapshot. |
| `contract` | Optional History Settings: default 0.5 s pre-roll and 120 cold warmup evaluations, excluded from visible output. |

Example: `start_time=10, duration=3, cursor_time=1.5`. Range mode produces the
10–13 second interval; frame mode produces one frame at/after 11.5 seconds,
aligned to decoded frame timing. Near the end of the input the result can be
shorter. Single-frame VIDEO is a one-frame clip, not the entire duration.

For an independent final render, share Input Adapter `sequence`, Runtime and
NR Look with **DLSS Process Video → Save Video**. Process has its own start,
duration and scale; Preview settings do not control them. Use scale 100 for input
resolution, and enable **Process to video end** for the remaining VIDEO. Process also
preserves legacy duration 0 semantics. Display-only wipe/side/difference modes never
alter the A/B output video or export a combined comparison layout.

The node card contains a visible save-scope warning and a collapsible
**Parameters and saving**. Input display names and tooltips follow the selected language, while
socket IDs, widget order and default values remain compatible. The
new optional `process_to_end` boolean is appended, defaulting off for old workflows.
The explanation-only update ran 133 Python tests on Linux (4 skipped) and 22
JavaScript tests successfully. The expanded inline help was checked at 283px
widget width without horizontal overflow. No new GPU render was needed for this
metadata/layout change; the user's workflow was not saved or rewritten by the check.

### Output range and processing mechanisms

These are separate concepts; `duration` has never meant an internal batch size.

| Scope | Where it is configured | Changes saved duration? |
| --- | --- | --- |
| Output start / until end / manual duration / dimensions | Preview Session or Process Video | Yes, according to the chosen range; dimensions affect resolution |
| One frame versus a continuous clip | Preview controls | Yes: one frame or the chosen range |
| Real pre-roll / initial repeated-frame warmup | History Settings | No; this context is excluded from visible output |
| Lazy residency / idle release / DLL and Proton choice | Runtime Configuration | No |
| Optical flow and scene-cut handling | Video Guides and flow provider | No |

The current implementation processes consecutive frames, not independent
2-second/30-second batches. It first prepares color/motion spools on disk, then
feeds them continuously to one NR stream, then encodes the visible result.
There are no newly inserted chunk boundaries, repeated chunk warmups, or
seams introduced by enabling full duration. NR frame payloads remain per-frame;
metadata lists scale with frame count. Concurrent chunks and overlapped
decode/NR/encode remain future mechanisms, not output settings.

Fixed 30-second and 2-GiB preparation limits have been removed. Before preparing,
the service estimates cache plus output storage, combines requirements when
both share a filesystem, and retains 512 MiB free space as a safety floor.
It checks disk space periodically while writing. An estimate is not a reservation:
other applications may still consume space. Insufficient space, invalid timing,
or more than the current worker's 1,000,000-frame protocol budget fails explicitly;
no shorter video is silently substituted. Inputs/controls currently allow up to
86,400 seconds per requested range. The worker's existing one-hour wall-clock
watchdog is execution time, not video duration; an exceptionally slow job can
still time out. Cache eviction remains manual, and long high-resolution videos
can consume substantial disk space. The execution report includes the resolved
output range and storage plan (a cache hit's plan describes its preparation).

The 65,536-frame resident stream is a reuse limit, not an output-length setting.
Larger tasks use one isolated worker and record `resident_fallback`; they are not
split into reset-prone chunks. Other compatibility restrictions remain unchanged.

Full-duration regression: 138 Python tests ran on Linux (4 opt-in tests skipped),
and 23 JavaScript tests passed. On the configured Linux/NVIDIA/Proton test host,
`scripts/check_full_duration.py` verified a 36-second / 864-frame preview with
manual duration still set to 2, a manual 4-second range, a single frame selected
at 35 seconds, and a separate Process → SaveVideo output containing all 36 seconds
and audio. Each isolated worker cleaned up fully. This is a tested >30-second
case, not a claim that arbitrary feature-length/high-resolution videos fit disk
or the worker watchdog. Disk admission above 2 GiB is covered by unit tests;
the small GPU fixture deliberately avoids writing several gigabytes for UI QA.

Both custom cards can shrink with the node. Their DOM roots have no fixed
minimum width; controls wrap, diagnostic values break long lines and excess
vertical content scrolls inside the widget. Do not reintroduce the old 520/380px
minimum widths or content-sized grid tracks: Comfy's Nodes 2.0 widget grid can
otherwise exceed the resized node. Tested by dragging both node corners wide
and narrow with a populated input report and a ready A/B preview; at internal
widths 269px (input) and 266px (preview), neither card had horizontal overflow.

## Control ownership

- **Video Guides**: connected DIS/NVIDIA or legacy zero motion, analysis resolution, scene-cut reset and
  consistency diagnostics. It configures input guidance, not the NR look.
- **NR Look**: NR enable, model intensity, readable style/preset choices, local tone,
  local/skin structure, automatic mask, original/result mix, and advanced UI
  correction/worker profile. The [parameter guide](nr-look.en.md) separates measured
  response from transport-only experimental fields. These settings feed both
  Preview and Process. Artistic presets are not SR Quality/Balanced modes.
- **Video Input Adapter**: media interpretation and optional [host FFmpeg/ffprobe paths](media-tools.en.md).
- **Runtime Configuration**: backend, component bindings, Proton and process
  policy; no artistic parameters.
- **Preview / Process**: execution range and preview/output scale, not a second
  copy of look settings. Future automatic preview needs bounded ranges,
  debouncing and stale-result handling; it is not implemented yet.

SR and frame generation are separate future backend capabilities, not NR look
sliders. The current D5V2 contract consumes one color+motion frame and returns
one equally sized color frame; it has neither an independent output-resolution
parameter nor an output frame multiplier.

All currently implemented D5V2 look fields are exposed. Mix is an 8-bit,
nonlinear-space blend, not a model parameter. Zero mix or
disabled NR bypasses the Worker. Optional History Settings controls warmup
(default 120 frames) and pre-roll (0.5 seconds, bounded by the upstream trim).
The first-frame warmup is separate from preceding video context.

## Processing and storage

Process Video's **Process to video end** toggle uses the actual remaining VIDEO;
legacy duration=0 also means the remainder. Scale=100 keeps resolution; smaller scales are downsampling,
not DLSS super resolution. Preview and Process share code and disk guide caches.
Use 100% preview to judge the final-size NR appearance.

`COMFY_DLSS_HOME` overrides the data root; otherwise it is under
`ComfyUI/user/default/comfy-dlss-experimental`:

- `prepared-clips/`: content-addressed color/motion spools and manifests.
- `runtime-snapshots/`: hash-verified copies of the three selected components.
- `prefixes/`: isolated Proton state, keyed by runtime/platform/Proton/display.
- `cache/`, `tmp/`: worker environment caches.

Comfy's temp folder stores `dlss-experimental/<session>/a`, `b` and job reports.
Process outputs are temporary VIDEO files until saved by Save Video. Completed
per-job raw output spools are removed; prepared guides and failed-job evidence
are retained. Cache eviction and a cleanup UI are not implemented: cumulative
disk use grows with prepared ranges and is guarded by available space, not automatic eviction. Clean only identified
inactive cache/job folders manually if needed; never remove active prefixes.

Changing DLLs changes the snapshot and prefix key. Snapshots are real copies,
not hardlinks. The default is one isolated worker per variant. Runtime
Configuration's optional **Lazy resident worker** toggle reuses one verified worker/model
instance only with compatible NR controls, dimensions and runtime. It starts on
demand, resets temporal history for each task, and releases after 300 idle seconds
by default (advanced input: 30–900 seconds). Turning the toggle off requests idle
release, or release after the current task. The runtime monitor also offers manual
release. Different Look A/B controls require replacement, not hot reconfiguration.
There is no hot DLL switching. See [details](preview-performance.en.md#opt-in-resident-worker).
Linux cleanup identifies owned processes using a unique
run marker and pidfds. No global wine/proton process-name kill is used.

## Tested outcomes

The provided 544×960, 24 fps, 8-second SDR clip produced:

- Node preview: 2 seconds, 272×480, 48 frames, about 5–6 seconds per run.
- Repeated preview: a new session with the guide cache reused.
- Mix=0: true bypass, no Worker started.
- Cancellation during frame processing: cancelled session, zero owned processes
  remaining. Comfy currently records this intentional cancellation as an error
  history entry; the card reports cancelled.
- Process Video → native Save Video: 544×960, 192 frames, exactly 8-second
  video and audio, about 19 seconds on this clip. BT.709 primaries/matrix and
  sRGB transfer were retained.
- Browser Render preview: successful session and both video sources loaded;
  single-frame stepping put both at the same timestamp.
- Automated tests cover preview isolation/branch selection, media report
  formatting and legacy NR option migration; optional media/GPU checks require
  the documented dependencies and explicit opt-in.
- NR Look: 24 half-second parameter/default/bypass cases, a two-look preview,
  and full-video export passed on Linux. Old defaults, explicit new defaults
  and a repeat render produced identical pre-encode RGBA hashes. See the
  [response matrix](nr-look.en.md#measured-response).

`scripts/check_comfy_video_nodes.py` is an **opt-in** integration test against
an already configured dedicated instance. It queues GPU work, tests
cancellation and saves a video. Supply `--prompt`, `--report` and optionally
`--url`; never run it against a busy production queue without coordination.

## Boundaries and migration

The [input adapter update](video-input-adapter.en.md) adds inline media diagnostics,
explicit missing-color interpretation and DIS/zero-vector selection. Existing
socket IDs remain compatible and optional controls default to strict/dis.

This is not a long-video streaming release. It accepts CFR, square pixels,
SDR BT.709 matrix/primaries with BT.709 or sRGB transfer. Missing color requires
an explicit interpretation; HDR, rotated/VFR sources and oversized ranges fail explicitly. No synthesized
depth, upscaling or frame generation is offered. Experimental NR controls are
exposed with bounded validation; some have no observed effect on this runtime.
Precise handling of unusual audio stream start offsets remains future work.
The direct Windows launch path exists but was not GPU-tested on Windows.

Old schema-1 look/temporal/render settings are rejected rather than silently
ignored. Recreate them from current templates. After backend updates restart
Comfy; after frontend updates force-refresh the browser to discard old assets.
Runtime descriptors have file fingerprints; stale or changed component bytes
are rejected before launch.

When launching a separate Comfy test instance, explicitly set `--database-url`
as well as `--base-directory` and `--user-directory`. This Comfy version can
otherwise migrate the main installation's legacy database into the new user
directory. Explicit database isolation avoids modifying the main instance.
