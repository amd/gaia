# PROJECT_MAP.md — Map of amd/gaia (the project gaia-coder is building)

> Phase 1 placeholder. Per §6.5 of `docs/plans/coder-agent.mdx` this file
> describes the **project she is building** — the subsystem map of
> `amd/gaia`, its public-API surface, in-flight initiatives, architectural
> decisions, known gotchas. It is maintained continuously from her RAG index
> once that index is online (Phase 3+). Phase 1 ships the skeleton so
> downstream tasks can rely on the file's existence.

## Subsystem map

One-line summaries of the top-level modules under `src/gaia/` (agents,
api, apps, audio, chat, cli, electron, eval, llm, mcp, rag, sd, shell,
talk, ui, vlm, coder). Auto-populated from the RAG index and the repo
root `CLAUDE.md` in Phase 3.

## Public-API surface

CLI commands (`gaia <subcommand>`), console scripts (`gaia`, `gaia-mcp`,
`gaia-emr`, `gaia-code`, `gaia-coder`), REST API routes, the
`KNOWN_TOOLS` registry. What external contracts a change might break.

## In-flight initiatives

Two-line entries summarising active `docs/plans/*.mdx` work with
milestone and owner. Auto-populated in Phase 3+.

## Architectural decisions (ADRs)

Things the project has decided and the rationale. Drawn from the
companion analysis, `docs/plans/`, and significant merged PRs.

## Known gotchas

Things learned while working in the project ("pypdf must be tried before
PyPDF2 per #495", "Lemonade needs `--ctx-size 32768` for the code
agent"). Sourced from her `failure_patterns` memory and audit log as
Phase 3+ comes online.

## Open questions about the project

Areas where her understanding is thin. She flags these in the weekly
standup so the EM can fill them in.
