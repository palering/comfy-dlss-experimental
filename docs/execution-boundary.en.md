# Host and execution boundary

English · [简体中文](execution-boundary.zh-CN.md)

Audience: public

Full-video NR and SR orchestration has a host-neutral Python entry. ComfyUI
remains the user-facing integration; this is an internal API, not an installed
CLI, a stable external RPC schema or a new runtime backend.

```text
Comfy VIDEO -> ComfyVideoSource ─┐
                              ├-> shared Python execution -> native Worker
file path  -> FileVideoSource ──┘        -> output Path + report
                                               -> Comfy wraps VIDEO
```

## Responsibilities

| Layer | Owns |
| --- | --- |
| `comfy_adapter.py` and node endpoints | Public VIDEO trim/crop materialization, Comfy paths, cancellation/progress adapters, wrapping the result as VIDEO. |
| `video_source.py` | Source duration and binding a requested temporal view. File input never silently discards a spatial crop. |
| `execution_context.py` | Explicit absolute data/temp roots and caller-supplied synchronous progress/cancellation callbacks. |
| `nr_execution.py` / `sr_execution.py` | Input validation, preparation, orchestration, reports and output-file results. |
| Media, guide and process modules | Decode/encode, source hashes/PTS, numerical guides, cache limits, authenticated binary transport and owned-process cleanup. |
| C++ feature engines | Device/resources/fences and vendor feature lifecycle. They do not decode media or depend on ComfyUI. |

`render_nr_video(source=..., context=..., sequence=..., runtime=..., contract=...,
profile=...)` accepts a `VideoSource` and detached version-2 sequence metadata.
The sequence must not contain `video`; guide settings retain their node payload
schema (including `schema_version: 2` and percentage `analysis_scale`), not the
internal `GuideSettings` dataclass representation. Runtime input must already be
validated and bound to exact files. Existing range/cache policies still apply.

For SR, `render_sr_video` takes `SRVideoInput`: detached sequence metadata and a
numerical depth provider. `load_guide_manifest` can open that provider without a
VIDEO object. The Comfy adapter verifies graph source identity before detaching;
the executor still verifies actual source hash, view, grid and PTS. Detachment
does not make a relative depth estimate into calibrated device depth. See
[SR requirements](super-resolution.en.md).

`ExecutionContext` scopes paths to a task; quotas are shared by jobs using the
same temporary root, and resident compatibility also includes the data root.
This does not add cross-process scheduling or permit concurrent GPU calls into
one native session. Callers must retain a single owner per resident session.

## Preserved boundaries

- No vendor DLL is loaded into Python. Legacy D5V2/CLR1 and owned CNR1/CSR1
  remain distinct protocols; stdout is not a frame transport.
- Comfy endpoints preserve their node IDs and VIDEO outputs. A/B preview and
  server/UI monitoring remain Comfy integrations, not a standalone application.
- Cancellation stops host submission and performs owned-process cleanup; it
  does not interrupt an already executing GPU kernel.
- Media dependencies, numerical validation, disk limits and backend acceptance
  remain necessary outside Comfy. Import independence is not GPU acceptance.
- The separate [Streamline CXR1 development backend](streamline-reconstruction.en.md)
  handles explicit camera/RR planes, not ordinary VIDEO input. It does not change
  `owned_sr` or its capabilities. Its host-neutral `render_reconstruction` file
  executor and thin [Comfy reconstruction nodes](reconstruction-nodes.en.md)
  share the same input, runtime snapshot, encoding and cancellation boundaries.
