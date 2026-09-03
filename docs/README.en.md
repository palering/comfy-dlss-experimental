# comfy-dlss-experimental Documentation

English · [简体中文](README.zh-CN.md)

Audience: public

User setup, implemented behavior and engineering contracts live here.

## Reading Order

1. [Installation and platforms](distribution.en.md): clone, helpers, runtime selection and validation limits.
2. [Host FFmpeg/PyAV](media-tools.en.md) and [external runtime files](DLL_PREPARATION.en.md): what to obtain separately and how to bind versions.
3. [Node quickstart](comfy-video-nodes.en.md): preview, process and save a video.
4. [Interface localization](i18n.en.md) and [example workflows](../example_workflows/README.en.md).

For agent handoff and private process state, read `.agent-docs/process.md` locally when it exists. `.agent-docs/` should stay out of git.

## Documentation Areas

- [Input adapter](video-input-adapter.en.md), [motion/input contract](nr-input-reconstruction.en.md), [NVIDIA optical flow](nvidia-optical-flow.en.md).
- [NR Look controls](nr-look.en.md), [preview, performance and worker lifecycle](preview-performance.en.md).
- [Architecture](ARCHITECTURE.en.md), [direct NR protocol](direct-nr-relay.en.md), [runtime boundaries](dlss5-runtime-research-2026-09.en.md).
- [Development and publication](DEVELOPMENT.en.md), [C++ quality](sidecar-cpp-quality.en.md), [video diagnostic](video-nr-validation.en.md).
- [guide.en.md](guide.en.md): documentation visibility and update routing.

- [Sidecar](../sidecar/README.en.md), [diagnostic frame protocol](../sidecar/protocol/frame-stream-v1.en.md), [NVOF header provenance](../sidecar/vendor/nvof/README.en.md).

## Bilingual maintenance

Substantive documents use paired `.en.md` / `.zh-CN.md` names. Update both in the
same change; body links stay in the current language. Unsuffixed entries are
navigation only. Authoritative legal originals are exempt from translation.

## Public Safety Rule

Keep raw planning, unreleased roadmap detail, private research, security-sensitive notes, credentials, local paths, and agent scratch work out of `docs/`.
