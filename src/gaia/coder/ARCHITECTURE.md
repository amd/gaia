# ARCHITECTURE.md — Composition map for gaia-coder

> How **she** is composed — mixin stack, tool registry, state machine, invariants.
> Injected into every system prompt alongside `GAIA.md` and `PROJECT_MAP.md`.
> Updated on every PR that changes composition. See `docs/plans/coder-agent.mdx`
> §6.5 for the contract.

## Surface — what runs and how to start it

* **Console script:** `gaia-coder` (registered in `setup.py` as
  `gaia.coder.cli:main`).
* **Default invocation:** `gaia-coder` with no subcommand drops into the
  interactive REPL — a Claude-Code-style coding session bound to the
  current repo.
* **Explicit subcommands** (one-shot): `repl`, `trust`, `promote`,
  `demote`, `ask`, `note`, `critical`, `inbox`, `daemon` (Phase-2 stub),
  `wait`, `stop`, `feedback`, `self-fix process`, `dev-mode`, `debug`,
  `rag`, `status`, `audit`, `spend`, `egress`, `introspect`, `skill`,
  `doctor`. Run `gaia-coder <cmd> -h` for any of them.
* **Logging:** top-level `-v` / `-q` / `--log-file` flags configure the
  root logger. The REPL uses `gaia.logger.get_logger`; tool calls log
  at INFO.

## Mixin stack — `gaia.coder.agent.Agent`

The interactive `Agent` (in `src/gaia/coder/agent.py`) composes:

| Mixin | Source | Tools registered |
|-------|--------|------------------|
| `SearchToolsMixin` | `src/gaia/coder/tools/search.py` | `grep`, `find_symbol`, `list_files`, `semantic_search` |
| ↳ inherits `FileToolsMixin` | `src/gaia/coder/tools/file.py` | `read_file`, `write_file`, `edit_file`, `search_code`, `glob`, `generate_diff` |
| `CLIToolsMixin` | `src/gaia/coder/tools/cli.py` | `run_cli_command`, `stop_process`, `list_processes`, `get_process_logs` |
| `GitHubToolsMixin` (opt-in) | `src/gaia/coder/tools/github.py` | 11 `gh_*` tools (PR / issue / run / release) |

All mixins use the shared `@tool` decorator from
`gaia.agents.base.tools`. The Anthropic tool-use payload is built by
`gaia.coder.tool_schema.build_anthropic_tools()` from the global
registry; dispatch goes through `gaia.coder.tool_schema.ToolDispatcher`
which gates every call on a permission policy.

## LLM seam

`gaia.coder.llm.CoderLLM` is the **single** Anthropic client wrapper. It
holds a `gaia.eval.claude.ClaudeClient` instance and exposes:

* `complete(prompt) -> str` — one-shot text completion (used by self-fix
  triage / critique / classify-failure / standup).
* `chat_with_tools(messages, tools, system) -> AssistantTurn` — multi-turn
  tool-use primitive (used by `Agent.send`).

Every call returns a `Usage` object (input/output tokens, USD cost from
`gaia.eval.config.MODEL_PRICING`). The `Agent` accumulates them into a
session running total surfaced by the REPL `/cost` command.

Default model: `claude-sonnet-4-6` (matches `DEFAULT_CLAUDE_MODEL`).
Review passes (§8) explicitly request `claude-opus-4-7-20251001`.

## Permission policy

`gaia.coder.agent.safe_default_policy` is the default REPL policy:

* `READ_TOOLS` (read_file, search_code, glob, …): auto-approve.
* `WRITE_TOOLS` (write_file, edit_file, run_cli_command, gh_pr_create, …):
  return `"prompt"` — the REPL's `InteractivePolicy` resolves with a
  y/n/yes-to-all/cancel question.
* Anything else: hard deny (must be classified first).

`--yes` / `/yes` flips the policy to auto-approve everything (trusted
environments and CI only).

## State machine — interactive REPL inner loop

Per turn:

1. User input → `Agent.send(message)`.
2. `_to_anthropic_messages()` renders history; `system_prompt()`
   assembles GAIA.md + ARCHITECTURE.md + PROJECT_MAP.md + repo
   `CLAUDE.md` / `AGENTS.md`.
3. `CoderLLM.chat_with_tools(...)` → `AssistantTurn`.
4. If `tool_uses` is non-empty: dispatch each via `ToolDispatcher`,
   append `tool_result` blocks as a single user message, **loop**.
5. If `tool_uses` is empty (`stop_reason == "end_turn"`): print final
   text, **return**.
6. Bound: `max_iterations` (default 30). Exceeding is a `RuntimeError`,
   not a silent truncation.

For self-fix workflows the state machine is the §5.1 ReAct graph
(`gaia.coder.loop.DEFAULT_LOOP`) driven by
`gaia.coder.self_fix.loop_driver.FeedbackLoopDriver` — declarative
today; the runner lands with the daemon (Phase 2 of the C-roadmap).

## Invariants

Rules I must preserve. Each cites the spec section that defines it.

* **Fail loudly.** No silent fallbacks (CLAUDE.md; §2 principle 3).
* **Never push to `main`.** Integration is on the `coder` branch (§4.2,
  §5.7). PR base is `coder`, not `main`.
* **Every change has a test.** The `declare_done` gate hard-fails
  without one (§2 principle 5).
* **Seven review passes before every PR.** Static / functional /
  architectural / security / prose / adversarial / feedback-binding
  (§8). Pass 2 currently inflates `**` globs into concrete paths so
  pytest collection works (fix landed in feat/coder-safety-and-pass2).
* **Self-edit is gated.** Tier ≥ 4 AND dev-mode ON AND repo-binding
  manifest allows the path (§7.1 + §6.5). Enforced at
  `gaia.coder.safety.enforce_action(ctx)` — wired into
  `self_fix/fixer.py:_edit_file_impl` and
  `self_fix/publisher.py:open_self_fix_pr`.
* **`gh pr create` is gated.** Tier ≥ 2 AND branch in
  `repo_binding.allowed_branches`. PR base defaults to `coder`,
  `--draft` is implicit.
* **GAIA.md grows by replacement, not accretion** (§4.6). Every PR
  that adds a rule removes / consolidates / demotes one.

## Open questions

Things I know I do not yet know about my own design.

* Long-lived autonomous daemon (Phase 2 of C-roadmap) — `loop.py`'s
  graph is declarative today; no runtime executor.
* GitHub-webhook EventBridge (§6.2) — not wired; events arrive only
  via CLI / inbox today.
* Self-edit (Phase 12) — `safety.enforce_action` blocks self-edits
  unconditionally until the dev-mode + Tier-4 contract is fully tested.
* Real RAG freshness watchdog (§6.9) — `gaia-coder rag status` reports
  via the noop provider; the freshness contract is wired but the
  reindex backend is opt-in via the `[rag]` extra.

## Change log

Append-only. One line per merged self-edit PR with PR link, fix-class,
and `loop_version` before/after.

* 2026-04-25 — `feat(coder): interactive REPL — daily-driver foundation`
  (`eac218e8`). Added `llm.py`, `tool_schema.py`, `agent.py`, `repl.py`;
  wired no-subcommand → REPL; loop_version unchanged. Fix-class:
  `tool` (new tools-bridge module) + `prompt` (system-prompt assembly).
