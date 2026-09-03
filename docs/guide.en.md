# Documentation Guide

English · [简体中文](guide.zh-CN.md)

Audience: public

Purpose: let a future agent or teammate continue safely without relying on chat memory or leaking private work.

## Visibility Model

- `docs/` is public-safe project documentation.
- `.agent-docs/` is private agent working memory when `--visibility hybrid` is used.
- `docs/process/` is only for private repositories when `--visibility private` is used.
- If repository visibility is unknown, treat it as public and prefer `--visibility hybrid`.

Current visibility: `hybrid`.

## Write Boundary

- Work inside the current workspace by default.
- Do not write to `/tmp`, `/private/tmp`, `~/.codex`, `~/.local`, home-directory locations, or sibling workspaces unless the user explicitly approves that exact purpose and path.
- Tool permissions are not user intent. A path being writable does not make it acceptable to use.
- Put validation fixtures under an ignored workspace-local directory such as `tmp/agent-docs-tests/`.
- Ask before global skill synchronization or installation, and name both source and destination paths.

## Audience Labels

Put one audience line near the top of substantial docs:

- `Audience: public`
- `Audience: private-agent`
- `Audience: internal`

Only `Audience: public` material belongs in `docs/` for public or hybrid projects.

## Update Routing

| Change type | Update |
| --- | --- |
| Public user-facing behavior, stable requirements, public architecture | `docs/` |
| Current agent handoff, active risks, latest verification | `.agent-docs/process.md` and `.agent-docs/process/` |
| Accepted private direction, priority, or implementation strategy | `.agent-docs/process/decisions.md` |
| Exploratory idea, tradeoff, rejected option, raw research | `.agent-docs/process/discussions.md` or `.agent-docs/research/` |
| External references safe for publication | `docs/reference/` |

## Public-Safe Rules

Do not put these in `docs/` unless the owner explicitly wants them public:

- credentials, tokens, local machine paths, private URLs, customer data
- unreleased roadmap detail, private pricing or business strategy
- raw agent discussion, scratch notes, failed approaches, prompt fragments
- security weaknesses, exploit paths, vulnerability details, pending fixes
- private competitive research or source material with unclear redistribution rights

## Handoff Checklist

Before ending a substantial session:

- Update the current-state document for the chosen visibility.
- Promote only public-safe conclusions into `docs/`.
- Keep agent entry files thin.
- Run `agent-docs check` when feasible.

## Bilingual maintenance

Maintain substantive documents as matching `.en.md` and `.zh-CN.md` files in the
same change. Keep body links in the reader's language; only the top language
switch crosses languages. Unsuffixed README/guide files are navigation only,
not a third content copy. Preserve machine identifiers and authoritative legal
notices. Run `tests/test_documentation.py` to check pairing and local links.
