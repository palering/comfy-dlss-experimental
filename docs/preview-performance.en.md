# Preview, execution records, and performance

English · [简体中文](preview-performance.zh-CN.md)

Audience: public

## Implemented behavior

- **Render current frame** outputs exactly one decoded source frame at or after the selected cursor. The cursor is relative to `start_time`, inside `duration`. Real preceding frames (`pre_roll`) are still evaluated. A cold instance performs the configured NR warmup; a compatible resident instance reuses its initialized feature and resets temporal history instead. This is a look-development preview, not proof of temporal quality or pixel equivalence to a frame reached through an arbitrarily long continuous history.
- **Render range** evaluates the selected duration, for judging motion, flicker, and scene changes. The native Comfy queue follows the `preview_mode` input; the two card buttons override only the submitted preview branch, without changing the saved workflow or triggering Save Video.
- Before any rendered media exists, the cursor is a time selector (24 fps UI stepping fallback). After a render it uses the source frame rate. It does not decode/show an unrendered source image on every seek. A single-frame result remains at its recorded timestamp until another render finishes.
- Controls are grouped into render actions, four equal-width comparison modes, comparison split, and playback/timeline. Mode buttons expose pressed state; controls have focus outlines. The narrow-card render actions wrap explicitly, not by accidental intrinsic sizing.
- Prepared color/flow remain cached independently of Look. An encoded adapted-original A is now also cached under `prepared-clips/<key>/original-preview-v1/`, with length and SHA256 checks. It is copied into each Comfy temp job so `/view` still uses approved media roots. Pinned Look A is a separate NR render and is **not** covered by this original-only cache.
- No per-frame source reconstruction, NR history, color conversion, or quality settings were removed to achieve the measured improvement.

## Where diagnostics live

The Preview card displays total node-execution wall time and a collapsible stage breakdown. Expanded details share extra height with the viewer as the node grows; the previous 680-pixel card and 180-pixel timing caps are removed. Runtime Configuration has separate **Live worker status** and **Execution history snapshots** foldouts. Visible expanded cards refresh every second during execution or while a matching resident exists, every five seconds with no worker, and on Comfy execution changes. Collapsed/hidden cards stop polling. Device-wide GPU figures are in a separate reference foldout. Owned process-group RSS includes Proton, with individual process details; worker VRAM is matched by Linux PID. Old samples are labelled historical, never current idle-worker usage. Idle residents receive their own current resource samples even when no task is running.

**Cancel and release current worker** requires confirmation because it aborts the current task. The endpoint targets an exact active execution ID and requests cancellation through the owning job; it cannot target arbitrary PIDs or another execution. An idle resident instead exposes **Release idle worker**, with no task to abort. Resident release targets the exact worker ID and, when busy, its current execution ID so stale controls cannot cancel a later task. Existing owner-scoped cleanup remains responsible for terminating processes. An accepted request is not proof of release: worker state progresses through starting/running/idle/releasing/released or cleanup_failed. No live worker means the button is disabled. Input caches are retained; cancelled tasks are not automatically restarted.

Expand **Task details** in the Preview card and select **Copy task details** to copy a JSON diagnostic snapshot of the recorded run: parameters, input/working dimensions, cache flags, stage times, backend/Proton, component hashes, errors and sampled resources when available. It does not substitute unsubmitted widget changes for executed settings. On plain HTTP, selected-text copy is attempted if the Clipboard API is unavailable or denied; if automatic copy is blocked, a selected read-only report remains in the card for Cmd/Ctrl+C. Reports contain local paths and environment information; review before public sharing. This frontend-only addition needs a browser refresh, not a Comfy restart.

Each actual Preview/Process invocation writes `executions/<execution-id>.json` beneath the configured `COMFY_DLSS_HOME`. Records include parameters, effective backend, Proton/display/policy, component paths and hashes, selected source/hash, color/guide choices, guide backend evidence, cache hits, stage times, success/failure/cancellation, and sampled resources. Full result data stays on disk; the service returns the latest 15 records (the card shows up to 10 completed records matching its preset). Active executions appear only in the live section. Records survive restart.

Important limits:

- This measures execution inside the DLSS node, not queue wait or all upstream Comfy nodes. If an upstream node fails before DLSS runs, this DLSS recorder cannot record that execution; Comfy's history owns that error. Whole Comfy cache hits are not new executions.
- `first_frame_and_warmup` and `nr_frames_and_transport` are host wall time including worker calls, transfer, and synchronization, **not isolated GPU kernel timings**.
- `color_conversion`, `optical_flow`, and `cache_write` are subcomponents of `input_preparation`; do not add the parent and its children together.
- New records sample resources every second, with the GPU query shared between monitoring and sampling. Older records used two seconds. Short runs may have no samples, sampled peaks may miss real peaks. Only marked, owned Linux relay/Proton/worker processes are attributed. Their summed RSS can double-count shared memory. CPU is accumulated CPU seconds, not an instantaneous CPU percentage.
- GPU utilization and total memory are **device-wide**. Per-worker VRAM is matched by owned Linux PID against NVIDIA's graphics/compute process report; missing/unsupported values remain unavailable, never fake zero. Windows currently has device GPU metrics but no equivalent owned-process RSS collector.
- Component SHA256 is the exact version identity; product/FileVersion strings are not parsed yet. A hash is not a human-readable vendor release number.
- UI history is bounded; disk records/caches currently have no automatic retention/eviction. Interrupted partial cache directories can remain for diagnosis.
- Default worker policy remains isolated. Residency is opt-in. Opening a monitor or enabling the toggle does not start a worker, probe an NR feature, or render a video.
- `worker_startup` ends at the relay's child-created handshake, not an NR-ready signal. Settings are then written once; the first cold frame call waits for initialization, transfer, warmup and its output. That combined timing is not a measurement of the 120 evaluations alone. The D5V2 interface has no separate initialization/warmup timestamps. Reused tasks instead report `history_reset_first_frame`; they retain the live GPU feature, not merely disk prefixes/caches. Cold `worker_startup` is nested inside resident `worker_acquire`, so do not sum both.

## Opt-in resident worker

Runtime Configuration's **Lazy resident worker** toggle defaults off. Off means launch on
demand and release after the NR work; on means launch on first actual render,
retain one compatible instance, and release after **300 idle seconds** by default
(advanced `idle_timeout_seconds`, 30–900 seconds). Turning it off requests release
of the matching idle instance immediately, or after its current task finishes.
Loading a workflow does not itself release another workflow's instance; use the
manual release button when switching to an upstream VRAM-heavy workflow.

The external D5V2 worker reads controls only once. This implementation keeps one
bounded stream open (65,536-frame capacity, maximum 1,800-second age), leasing
successive frames to tasks. It sends an explicit history reset at each task's
first frame and preserves real pre-roll. It does **not** repeat the initial 120
warmup evaluations on a reused instance. This behavior is enabled only for the
worker/model SHA256 pair validated in `resident_worker.py`; other pairs must use
isolated mode until independently verified. No new DLL or native build is needed.

Changing NR controls, dimensions, selected component hashes, Proton or display
environment rebuilds the instance. Changing only Python-side output mix does not.
Consequently cursor seeking and repeated same-Look previews benefit; editing NR
Look parameters or comparing different pinned/working Looks still incurs cold
initialization. Live control reconfiguration and multiple parallel workers are
not implemented. All NR leases and cleanup remain serialized; there is no hidden
extra GPU worker. EOF is not claimed between resident tasks: each frame is
validated, with `exit: null` and an explicit resident lifecycle report.

Failed, partial, cancelled or unhealthy streams are discarded. Idle timeout,
manual release, stream capacity/age limits, incompatible requests and server
shutdown close owned resources. Cleanup failure stays visible and retains the
prefix lock; replacement is blocked until successful cleanup. Historical task
records can say the worker was returned idle then; only the live section describes
whether it remains idle now, together with instance ID, component hashes, lease
count, remaining idle timeout, owned process RSS and attributed VRAM when available.

Verified on the dedicated Linux/Proton instance, NVIDIA flow, 24 fps, 0.5-second
pre-roll, cold warmup 120 and prepared-input caches:

| Single-frame preview | Isolated/cold | Resident reuse |
| --- | ---: | ---: |
| 280×376, same seek position | 2.84 s isolated | 0.51 s |
| 560×752 | 3.43 s after size change | 0.63 s |

Raw NR output hashes matched for tested cold/reused and seek-reused/isolated
comparisons. A direct 12-frame stream probe also matched byte-for-byte after
reset. This is evidence for these clips/settings, not universal temporal-quality
equivalence. Look and size changes correctly rebuilt the instance; manual idle
release, actual 30-second idle expiry and cancellation during a reused task all
released the owned worker. Reproduce with `scripts/check_resident_worker.py` and
`scripts/check_worker_controls.py --resident` on an idle dedicated instance.

## Confirmed hotspot and fix (2026-09-03)

The relay sent small protocol headers and payloads separately over synchronous loopback TCP. Nagle buffering plus delayed acknowledgements introduced a repeatable extra wait per frame. `TCP_NODELAY` is now set on **both** the Python socket and our C++ relay socket. Changing only Python did not remove the measured wait. This is an application socket option, not a system TCP setting. Microsoft's [Winsock guidance](https://learn.microsoft.com/en-us/windows/win32/winsock/tcp-ip-characteristics-2) describes this interaction.

Measured on RTX 4070 Ti SUPER, GE-Proton11-6, direct Feature 18, NVIDIA flow, intensity 0.4, warmup 120, identical prepared input and look settings:

| Workload | Before | TCP fix | TCP fix + A encode cache |
| --- | ---: | ---: | ---: |
| 2-second range, 336×504, prepared cache hit | 5.31 s | 3.37 s | not separately benchmarked |
| 10-second range, 672×1008, 220 output frames, prepared cache hit | 26.77 s | 12.89 s | 10.18 s |
| Ordinary-frame median, 336×504 | 47.7 ms | 7.0 ms | — |
| Ordinary-frame median, 672×1008 | 91.4 ms | 19.5 ms | 18.1 ms |

All compared raw NR output SHA256 values were identical, including the 220-frame native-resolution run. These are repeated local measurements, not a throughput promise for other resolutions, clips, hardware, or presets. First input preparation at native resolution was ~5.85 s (optical flow ~3.77 s, pixel conversion ~0.17 s); cache reuse was ~0.24 s. Native-resolution result encoding was ~2.35 s. Original A export fell from ~2.49 s to ~0.005 s on its cache hit.

Current-frame preview at 336×504 and cursor 0.5 s took ~2.8 s with preparation cached. Most of this is ~0.74 s startup plus ~1.18 s first-frame/warmup. One output frame is not one total NR evaluation. Sampled native-resolution owned-process RSS was ~1.35 GiB; attributed worker VRAM ~749 MiB. Those samples are not safe upper bounds for a concurrency scheduler.

Reproduce against an **idle dedicated test instance** with `scripts/check_preview_performance.py`; use `--runtime-preset` to compare an old/new relay without replacing external DLLs. `--with-frame` checks one-frame output and a Look change. Do not run multiple competing benchmarks or GPU tests at once.

## What to optimize next, and why concurrency stays off

1. **Measure remaining CPU/transport work.** Per-frame integrity reads, pipe/socket copies, GPU upload/readback, and synchronization are still serial. The new `cache_read_verify` timer separates cache checking; direct GPU timestamps would require worker support. Remove unnecessary copies only with byte-equality and cancellation regressions. Do not drop integrity checks without a replacement trust model.
2. **Encoding and pipeline overlap.** The original A encode is now reusable. For long renders, explore bounded decode/flow → NR → encode streaming queues and configurable output encoding. Encoder changes require separate quality/color/audio verification; do not silently switch to NVENC or change CRF. The later full-duration update removes the 30-second/2-GiB cutoffs using disk-backed preparation and available-space admission; this does not yet overlap the stages. See [output range versus mechanisms](comfy-video-nodes.en.md#output-range-and-processing-mechanisms).
3. **Short-preview fixed costs.** Compatible residency now avoids startup/model setup using the bounded reset-capable D5V2 stream described above. NR Look changes still need a new instance because the selected worker cannot update its initial controls. Removing that cost needs a verified reconfigure/session protocol or another worker adapter, not merely keeping Python alive.
4. **Segment concurrency: experimental future work, not enabled.** Existing `_GPU_LOCK` and per-prefix lock enforce one NR job at a time. Multiple workers need separate owned Proton prefixes/sessions, bounded admission, and measured per-resolution peak VRAM/RAM plus safety reserve for Comfy and other apps. A momentary low utilization or free-VRAM sample alone is not safe admission logic.
5. **Segment quality.** Splitting inside a shot discards history. Each segment needs real preceding frames, independent warmup, overlap discard, ordered frame/PTS/audio assembly, and comparison against continuous rendering. No guarantee that a fixed overlap reproduces long-lived NR history. Prefer verified scene cuts first. Only enable 2-worker trials if they improve end-to-end time without memory pressure or visible joins; otherwise fall back to 1. No dynamic chunking or multi-worker speedup is claimed by this release.

Official [NVIDIA SMI documentation](https://docs.nvidia.com/deploy/nvidia-smi/index.html) explains the distinction between device utilization, framebuffer memory, and process reporting. The implementation invokes installed host tools and introduces no NVML/psutil dependency.

## Verification

118 Python tests passed on Linux, including native NVIDIA flow/media checks; 9 JavaScript tests passed. All 9 relay mock cases passed (echo, log flood, exit, truncated result, rejected result, cancellation, disconnect, timeout, blocked writer cancellation). Actual frame preview, range preview, Look-change cache reuse, color normalization and Process→SaveVideo were exercised. Source media and external vendor/worker DLLs were not modified.

UI capture: the original controls showed the fourth mode wrapping beneath the first three; the revised controls use explicit rows. A full revised card was inspected. This is a bounded component review, not a full accessibility audit or a complete drag-resize matrix.

Resident-mode follow-up: 131 Python tests ran successfully on Linux (4 opt-in
tests skipped); 21 JavaScript tests passed. The added lifecycle tests cover lazy
launch, immutable compatibility keys, reuse, timeout/capacity, stale release
requests, cancellation, partial streams and cleanup failure. Real resident
preview/release regressions are described above; prior unrelated GPU tests were
not all repeated for this follow-up.

Browser-skill UI check: a newly created Runtime node defaulted to residency off.
Enabling it did not launch a process; an independent frame render created an idle
instance whose live card showed owned RSS/VRAM and timeout. Turning the UI toggle
off released that exact instance, confirmed by the service with zero remaining
owned processes.
