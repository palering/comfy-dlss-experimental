# Storage and streaming

English · [简体中文](STORAGE.zh-CN.md)

Audience: public

Preview and Process automatically select the storage strategy. Existing workflows
keep their connections and output ranges; no additional configuration node is required.

## Default limits

| Setting | Default | Behavior |
| --- | ---: | --- |
| Prepared cache total | 2 GiB | Evict least-recently-used inactive entries when preparing/reusing input. |
| Prepared entry threshold | 128 MiB | Larger raw color/motion estimates select streaming, even with retention enabled. |
| Temporary jobs total | 8 GiB | Stop admitting new jobs when the total plus reservations exceeds the limit. |
| Temporary budget per job | 2 GiB | Includes encoded previews/output and job logs. |
| Minimum filesystem free space | 2 GiB | Checked during processing; other applications can still consume disk. |

Limits are shared by this Comfy instance's node data root and stored in
`storage-policy.json`. They are not output-duration or quality controls.
Changing a widget does not apply the limits until **Apply limits above** is clicked.
Reservations are process-local; do not share one data/temp root between independent
Comfy servers. Filesystem checks are not operating-system disk reservations.

## Processing

Small entries retain the existing content-addressed cache. Look-only changes can
reuse color and optical flow. **Retain prepared input cache** controls whether a
completed small entry remains after success, subject to the shared quota. Failed
preparation removes incomplete raw files; completed entries can remain for retry.

Large ranges first scan source timestamps to obtain the exact frame count required
by the external Worker. This decodes the source twice, but only small timing
descriptors survive the scan. The render pass then decodes/converts color, computes
flow, evaluates NR and sends visible results to FFmpeg one frame at a time. There
are no whole-range color, motion or raw output files. Encoder backpressure limits
the producer; memory still includes decoder, optical-flow, model and encoder workspaces.
Timing metadata scales with frame count, within the protocol limit.

Each streaming Stack layer has an independent continuously initialized Worker.
Original motion/cut flags are shared across layers, while colors pass from one
layer to the next. This preserves independent temporal histories without disk
intermediates and requires more VRAM than sequential whole-clip passes. Multi-layer
workers are released after each task. One-layer streaming can reuse a compatible
resident; requests beyond the resident stream capacity use isolated execution.
On Linux the per-layer Proton prefixes are reused across subsequent tasks.

Encoded previews and outputs still grow with duration and content. FFmpeg receives
a file-size cap and jobs check the free-space reserve and total job size. Truncated
outputs fail frame-count validation and are not published. Tiny packet/checking
overruns are possible; the reserve is additional headroom, not a byte-exact filesystem quota.
Large cropped/tensor-backed VIDEO inputs currently require saving and reloading
as file-backed VIDEO first, because public `VIDEO.save_to` cannot enforce a bounded sink.

## Storage Manager node

Add **DLSS Storage Manager** anywhere; it needs no connections. It is an in-node
card. Running the node or clicking Refresh reads the list without deleting anything.

- Lists `prepared-clips`, DLSS jobs under Comfy temp, and `executions` records.
- Shows sizes, paths, modification times and files within each entry.
- Explicit selection and confirmation remove whole entries permanently.
- Removing temporary jobs invalidates their previews and unsaved VIDEO outputs;
  save wanted results using Save Video first.
- Active rendering disables deletion; stale selections require a refresh.
- Source videos, saved outputs, DLLs, runtime snapshots and Proton prefixes are
  outside its deletion scope. Other instance/legacy roots are not scanned.
- Execution records retain the latest 1,000 files automatically. Temporary preview
  jobs are not auto-evicted; reaching their quota asks the user to clean them.

Task details report `storage_plan`, `storage_usage` and
`prepared_cache_retention`. A streamed job reports `raw_disk_bytes: 0` and
`streaming_no_prepared_cache`; this does not mean the encoded video or logs take no space.
