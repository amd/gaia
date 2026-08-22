# GAIA Flagship — Local TUI Validation Report (live)

**Transport:** Go TUI (`gaia-drive.exe run gaia`) driven via the loopback control API (port 8817), agent on **claude-haiku-4-5** (`--use-claude`). **Lemonade is never started** — this box crashes under it; Lemonade process count is asserted 0 before and after every run below.

**Environment:** `GAIA_MEMORY_DISABLED=1` and `GAIA_DYNAMIC_TOOLS=0` (both features need the Lemonade embedder), private `GAIA_TUI_HOME` + `GAIA_AGENT_LOG`, `.venv/Scripts` on PATH so the TUI spawns this branch's `gaia-agent`.

**Scope note (honest):** Haiku results validate the harness + scenarios, not the shipped local-model quality — the product baseline is Gemma-4-E4B on the self-hosted runner. Scenarios tagged `local_blocked_no_embedder` (42) cannot run without the embedder and are listed as **BLOCKED**, not skipped silently.

## Status

_Last updated: 2026-08-22 ~08:55 PT_

| Phase | Status |
|---|---|
| Phase 3a smoke (`gaia eval agent --agent-type gaia`, UI-backend transport) | ✅ PASS 10.0/10 (`core_arithmetic_direct`, scorecard `agent_type: gaia`, 0 Lemonade procs) |
| TUI up in control mode on Haiku | ✅ chat view, agent=gaia, control API on :8817, `claude` chip active |
| TUI scenario runner (`util/tui_eval.py`) | ✅ working — launch → `/clear` isolation → turns → deterministic checks → per-scenario JSON |
| Fixture environment staged | ✅ `~/gaia-eval/` (csv, mini_repo); signed fixture hub prepared (ephemeral key, trust-added); fixture server on :8765 (hub 200, atlas.html 200); github-triage pre-seeded; fake `gh` on next-launch PATH |
| `gaia_core` sweep | 🔄 running |
| Remaining local categories (files/data/web/shell/skills/honesty) | ⏳ queued behind core |
| BLOCKED categories (need embedder → runner) | `gaia_memory` (15), `gaia_rag` (8), `gaia_code` (4), `gaia_tool_selection` (5), + memory/RAG-leaning skill tasks |

## Product findings from this validation session

1. **🐛→✅ FIXED: `/clear` did not clear the agent's conversation history on the flagship's subprocess transport.** The TUI view emptied while `conversation_history` kept riding into every later prompt — including anything sensitive the user believed cleared. `subprocess.go` had no `TranscriptResetter`; only the SSE transport implemented it. Fixed on this branch (clear_history control verb + queue-sentinel routing so a mid-turn `/clear` lands after that turn), with Go + Python tests. Found because scenario isolation *required* a real clear.
2. **Harness lessons (control API contract):** the `text` endpoint does not submit (a separate `enter` keypress does); `screen` returns its text under `"screen"`; `status` nests everything under `"state"`. All three produced silent stalls in the first runner version — each now pinned by the working runner.

## Per-scenario results

_(populated as runs complete; every PASS carries the captured answer evidence)_

| Scenario | Tier/Tags | Result | Evidence |
|---|---|---|---|
| core_arithmetic_direct (TUI) | t1_basic, tui | ✅ PASS | `391` — 4.5s, ttft 4.5s, 1 step, no tools |
