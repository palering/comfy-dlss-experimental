# comfy-dlss-experimental Codex Entry

This file is a thin Codex-specific entrypoint. Shared project knowledge lives in `docs/` and private agent memory lives in `.agent-docs/`.

## Start Here

1. `docs/README.md`
2. `docs/guide.md`
3. `.agent-docs/process.md` when present

## Working Rules

- Do not rely on chat memory as project state.
- Keep `docs/` public-safe.
- Read `.agent-docs/process.md` before implementation work when it exists.
- Update `.agent-docs/process.md` after substantial implementation work.
- Put accepted private direction changes in `.agent-docs/process/decisions.md`.
- Put exploratory ideas in `.agent-docs/process/discussions.md`.
- Only promote public-safe commitments into `docs/`.
- Keep this file short; reusable guidance belongs in `docs/`.

## Write Boundary

- Work inside the current workspace by default.
- Do not write to `/tmp`, `/private/tmp`, `~/.codex`, `~/.local`, home-directory locations, or sibling workspaces unless the user explicitly approves that exact purpose and path.
- Tool permissions are not user intent. A path being writable does not make it acceptable to use.


## Local Conventions

- Use `rg` for search.
- Use `eza` for readable directory trees when available.
- Use Conventional Commits / Commitizen-style commit messages.
