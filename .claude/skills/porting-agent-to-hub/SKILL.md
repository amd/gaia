---
name: "porting-agent-to-hub"
description: "Use when taking an existing in-repo GAIA agent to a published, day-one-usable hub package — porting a legacy agent under hub/agents/<id>/, deciding whether an agent should ship at all, or answering why an agent 'is not ready to publish'. Also use when an agent's manifest advertises capability its code does not deliver, or when an agent has a README but no SPEC/SKILL/CHANGELOG/SCORECARD."
---

# Porting an Agent to a Hub-Native Package

Take an agent that exists only as in-repo Python and make it a published package a
user can install from the Agent Hub and use in Agent UI v2 **immediately**.

The **email agent is the template** — `hub/agents/email/python/` +
`hub/agents/email/npm/` + `.github/workflows/release_agent_email.yml`. It is the
only agent in the repo that clears the bar; every phase below points at the file
in it you are mirroring.

**This skill is the porting flow.** For adjacent work use:
`gaia-build-agent` (a NEW agent) · `agent-hub-release` (cutting the release) ·
`adding-eval-scorecard` (the scorecard mechanics) · `integrate-hub-agent`
(consuming one from an app).

## The Iron Rule

**Generalize before you document.**

Docs, SPEC, SKILL and SCORECARD written against behavior that is about to change
are wasted work — and a scorecard is meaningless until the capability is stable.
Phases run in order. Do not jump to the parity kit because it looks mechanical.

## Phase 0 — Decide whether it should ship at all

**Not every agent should be ported.** Before any work, get a verdict:
**PORT / MERGE INTO `<target>` / DISCARD (keep as in-repo example) / DEFER**.

Check for a more general agent that already covers the use case — in
`docs/plans/agent-hub-22-agents-spec.md`, in the other hub agents, and in
ChatAgent's profiles. Duplicating a capability into the catalog is worse than
not shipping.

Signals it is **not** a catalog agent: `category: examples` /
`security_tier: experimental`; absent from `setup.py`'s `AGENT_WHEEL_PACKAGES`;
no `gaia.agent` entry point; a module docstring saying it exists to validate
some other feature; a named successor already in flight.

## Phase 1 — Capability-truth audit

Read the manifest's `description`, `tags`, `tools_count` and `interfaces:`, then
prove each claim against the code. **Assume the manifest is lying until checked** —
in the 2026-07 fleet audit it was wrong for most agents, in both directions.

- Does every advertised verb have a reachable tool? (An agent advertising CSV
  analysis that composes no file mixin cannot open a file.)
- Does `tools_count` match the real `@tool` surface plus composed mixins?
- Do the agent's **own defaults** work end to end? (A `language="python"` default
  that fails a TypeScript-only validator is a mis-scope, not a bug.)
- Does `interfaces:` claim a mode nothing serves?

Record every gap. This list is the port's actual scope.

## Phase 2 — Generalize and harden

- Close every Phase 1 gap: implement the advertised capability, or narrow the
  manifest to the truth. Both are valid; shipping the mismatch is not.
- Remove single-instance / single-machine assumptions — an author's own service
  schema baked into a system prompt is the canonical case.
- Inject configuration instead of hardcoding paths, URLs, models, ports.
- **Fail loudly** (CLAUDE.md): no `except …: pass`, no default-to-empty, no
  swallowed retry. Errors name what failed, what to do, and where to look.
- Declare and preflight external dependencies (a daemon, a binary, credentials,
  VRAM). Check the *service*, not just the binary on PATH.

## Phase 3 — Behavioral tests

The fleet-wide failure mode: tests import the agent, construct it, and assert a
tool **name** appears in the registry. They never call a tool.

Every `@tool` needs a test that **invokes** it, over a fixture harness (temp FS,
mocked network, temp scratchpad DB) with `_TOOL_REGISTRY` isolation. Cover the
cold state a new user is in — empty index, empty DB, first run.

## Phase 4 — Eval: corpus first, then scorecard

Two halves, and the expensive one is not the code:

- **The oracle (not automatable):** a labelled, human-curated ground-truth corpus
  plus a deterministic fixture harness so the eval needs no live service and no
  LLM judge. Email's is `tests/fixtures/email/ground_truth.json` +
  `FakeGmailBackend`.
- **The mechanism:** the adapter, `SCORECARD.md`, the `scorecard_gate.py` wiring
  and a refresh workflow → **use `adding-eval-scorecard`**.

Pick the metric **before** starting, and prefer deterministic exact-match over a
judge. Where no honest metric exists, say so in the scorecard rather than
reporting a number that means nothing.

Beware the measurement trap: an `agent_type` in the scenario corpus may name a
ChatAgent **prompt profile**, not your package. A green scenario can be measuring
something else entirely.

## Phase 5 — The parity kit (what email has)

| Surface | Mirror from email | Consumed by |
|---|---|---|
| `README` `SPEC` `SKILL` `CHANGELOG` `EVALUATION` | `hub/agents/email/npm/` | hub page + Agent UI (the Worker reads all of them) |
| `CAPABILITY_MATRIX.md` | `packaging/capability_matrix.py` | the "what can it do" surface |
| `SCORECARD.md` | `packaging/gen_scorecard.py` | release gate |
| `server.py`, `api_routes.py`, `query_routes.py` | `/health`, `/version`, `POST /v1/<id>/query` (SSE) | the daemon + UI chat |
| `openapi.<id>.json`, `specification.html` | `export_openapi.py`, `spec_html.py` | the contract |
| playground + `playground_url` | `playground_html.py` | "try before install" |
| `packaging/` | freeze, stamp_version, smoke_test, lock, publish | the release |
| npm client | `hub/agents/email/npm/src/` | integrators |

**Generate these; do not hand-write them per agent.** Anything derivable from the
manifest and the tool registry should be — `tools_count` especially. A number a
human types is a number that drifts.

**Doc-root gotcha:** email's canonical docs live in its **npm** package, not the
Python one. Do not assume `python/README.md` is the source of truth.

## Phase 6 — Versioning and CI/CD

Each agent versions and ships **independently**.

- One source of version truth (`version.py`), propagated by a stamp script, with
  a test asserting `pyproject.toml` ≡ `gaia-agent.yaml` ≡ `version.py`.
- Tag namespace `agent-pkg-<id>-v*` — **never** `v*`, which fires the core release.
- Its own test / eval / scorecard-refresh / release workflows, generated from the
  manifest. → **`agent-hub-release`** for the release lane itself.
- After publishing, assert the live catalog entry matches the repo manifest for
  that version. Published-vs-repo drift is real and silent.

## Phase 7 — Day-one usability gate

Publishing is not the finish line — **usable on install** is. Script it:

catalog index → install → daemon spawns the sidecar → `/health` passes →
`/query` returns a rendered SSE stream in the UI → the playground URL loads.

**If any step needs a human, the agent is not day-one usable.** Also confirm an
`AgentSidecarSpec` exists (or the spec table is manifest-driven), every declared
`renderTypes[]` has a renderer with a fallback, and `conversation_starters` are
present.

## Red flags — stop and go back a phase

- Writing SPEC/SKILL/SCORECARD while the capability is still being changed
- Typing a `tools_count` by hand
- A scorecard produced without a corpus, or with hand-authored numbers
- "The tests pass" when no test calls a tool
- Copy-pasting another agent's workflow instead of generating it
- Porting an agent nobody gave a Phase 0 verdict for
- Treating `interfaces: api_server: true` as satisfied because the manifest says so

## Reference

- Template: `hub/agents/email/python/`, `hub/agents/email/npm/`
- Generic 5-interface server (TUI/CLI/pipe/API/MCP, manifest-gated):
  `src/gaia/agents/base/server.py` — `run_agent_cli()`
- Sidecar registration: `src/gaia/daemon/sidecars/spec.py`
- Manifest parsing: `src/gaia/hub/manifest.py`
- Catalog readers: `workers/agent-hub/src/storage.ts`
- Scorecard format: `docs/reference/eval-scorecard.mdx`
- v2 contract + manifest schema: `docs/plans/agent-ui-agent-capabilities-plan.md` §0.1, §0.2, §0.28
