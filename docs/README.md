# comfy-dlss-experimental Documentation

Audience: public

User setup, implemented behavior and engineering contracts live here.

## Reading Order

1. [Installation and platforms](distribution.md): clone, helpers, runtime selection and validation limits.
2. [External runtime files](DLL_PREPARATION.md): what to obtain separately and how to bind versions.
3. [Node quickstart](comfy-video-nodes.md): preview, process and save a video.

For agent handoff and private process state, read `.agent-docs/process.md` locally when it exists. `.agent-docs/` should stay out of git.

## Documentation Areas

- [Input adapter](video-input-adapter.md), [motion/input contract](nr-input-reconstruction.md), [NVIDIA optical flow](nvidia-optical-flow.md).
- [NR Look controls](nr-look.md), [preview, performance and worker lifecycle](preview-performance.md).
- [Architecture](ARCHITECTURE.md), [direct NR protocol](direct-nr-relay.md), [runtime boundaries](dlss5-runtime-research-2026-09.md).
- [Development and publication](DEVELOPMENT.md), [C++ quality](sidecar-cpp-quality.md), [video diagnostic](video-nr-validation.md).
- [guide.md](guide.md): documentation visibility and update routing.

## Public Safety Rule

Keep raw planning, unreleased roadmap detail, private research, security-sensitive notes, credentials, local paths, and agent scratch work out of `docs/`.
