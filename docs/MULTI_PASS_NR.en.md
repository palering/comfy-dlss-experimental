# Multi-pass DLSS NR

English · [简体中文](MULTI_PASS_NR.zh-CN.md)

Audience: public

`DLSS NR Pass Stack` applies one to three NR Looks sequentially to the same clip.
A cascade can produce strong stylistic reinterpretation, but it is not an official
NVIDIA quality mode and does not guarantee restored truth, Super Resolution, or
Frame Generation.

## Wiring

```text
NR Look 1 ───────────────┐
NR Look 2 (optional) ────┼-> DLSS NR Pass Stack -> Preview Look A/B
NR Look 3 (optional) ────┘                       -> Process profile
```

- `NR passes` is 1–3 and defaults to 2.
- Pass 1 is required.
- An enabled but disconnected pass 2/3 inherits the previous Look.
- Connected Looks beyond the selected count are ignored, so temporarily reducing
  the count does not require rewiring.
- The output uses the same Comfy type as NR Look, preserving existing Preview/
  Process sockets and old single-pass workflows.
- A Pass Stack cannot be nested inside another stack; the ordered layers remain
  visible in one node.

## Independent variants

A Comfy output can connect to more than one downstream input, so branching does
not require a special execution node. For a tidier large graph, the optional
`DLSS Prepared Sequence Hub` exposes four identical routing outputs:

```text
Video Input Adapter -> Prepared Sequence Hub -+-> Look/Stack A -> Preview/Process
                                               +-> Look/Stack B -> Preview/Process
                                               +-> Look/Stack C -> Process
```

Every branch shares the same input configuration. Small retained prepared entries
can be reused; large streaming branches separately decode and compute guides.
The Hub itself does not copy pixels, compute optical flow or schedule GPU
concurrency: the current backend lock serializes rendering jobs. This keeps serial passes inside
Pass Stack and independent variants in the normal graph structure.

## Exact execution semantics

```text
Prepare once: video -> RGBA8 color + original motion guides + PTS/cut resets

Pass 1: original color + original motion -> NR -> raw RGBA
Pass 2: pass-1 RGBA   + original motion -> NR -> raw RGBA
Pass 3: pass-2 RGBA   + original motion -> NR -> final visible RGBA
                                              |
                                      encode once + original audio
```

The current policy is fixed to `reuse_input_guides`: every pass receives the same
motion field, timestamps, and scene-cut flags estimated from the source by Input
Adapter. Optical flow is not recomputed from generated intermediate imagery. This
is closer to common community cascades using previous-pass color with original
MV/depth and avoids treating changed texture as new physical motion.

Each pass has independent temporal history: it resets at pass start and retains
the source cut resets. Small cached ranges complete each pass sequentially and may
reuse a compatible resident process with explicit history resets. Larger ranges
stream each frame through independent Workers, one per enabled layer, then encode
it immediately. These multi-layer Workers are released after the task. A different
Look cannot hot-swap an already initialized incompatible instance.

Each pass's `mix` is relative to that pass input. Pass-2 mix=0.5 blends pass-1
color with pass-2 NR output, not with the original source. `nr_enabled=false` or
mix=0 bypasses that pass.

## Storage, cache, and reports

- Small color/motion entries remain content-cached; large streamed ranges recompute
  guides after Look/count edits. See [storage policy](STORAGE.en.md).
- Passes exchange uncompressed RGBA8, via bounded files for small cached ranges
  or frame buffers for large streamed ranges. Only the final result is encoded.
- Intermediate passes include pre-roll frames for complete next-pass history;
  the final output excludes pre-roll.
- In cached mode a prior raw spool is removed after the next pass succeeds; the
  final spool is removed after encoding. Streaming does not create these spools.
- NR intermediate output is not cached yet. Editing a later Look reruns earlier
  passes, avoiding unbounded large raw caches.
- Reports include every Look, inheritance, native settings, raw hashes, Worker/
  residency information, and per-pass timing. The top level explicitly reports
  `intermediate_video_encoding=false`.

## Cost and quality risks

Two passes normally approach twice the NR evaluation/transport cost and three
passes approach three times. Cached mode adds raw disk I/O; streaming retains
one model instance per enabled layer, increasing RAM/VRAM. Quality does not improve linearly.
Repeated generation may amplify:

- trails from wrong optical flow and slower settling after cuts;
- generated skin, material, lighting, and text drift;
- color/contrast drift, halos, oversharpening, or structural rewriting;
- inconsistent parameter response across community-modified runtimes.

Compare one versus two passes on a frame first, then inspect a short moving clip
with scene cuts before processing a full video. Three passes remain an advanced
experiment; this project does not expose 4–30 passes as a quality slider. A future
project-owned Worker may negotiate multiple feature handles and GPU-resident
intermediates as a separate backend without silently changing these host-orchestrated
raw semantics.

## Current Linux acceptance

On 2026-09-05, the unpublished staged source was deployed to the isolated ComfyUI
0.34.0 test instance on Linux, with an RTX 4070 Ti SUPER, NVIDIA driver 610.57.04,
GE-Proton11-6 and the previously validated external Worker/model pair. A single
50%-scale frame passed through Setup Helper, two separate Sequence Hub outputs,
and a two-pass Stack whose second Look inherited the first. Both NR passes
succeeded, the final report confirmed no intermediate video encoding, and each
pass left zero owned processes. The first run prepared guides; an identical second
run reported a guide-cache hit. This is one-frame integration acceptance, not a
long-clip quality, performance, scene-cut, Windows, or three-pass claim.
