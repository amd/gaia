# GAIA Flagship — Local TUI Validation Report (live)

**Transport:** Go TUI (`gaia-drive.exe run gaia`) driven via the loopback control API (port 8817), agent on **claude-haiku-4-5** (`--use-claude`). **Lemonade is never started** — this box crashes under it; Lemonade process count is asserted 0 before and after every run below.

**Environment:** `GAIA_MEMORY_DISABLED=1` and `GAIA_DYNAMIC_TOOLS=0` (both features need the Lemonade embedder), private `GAIA_TUI_HOME` + `GAIA_AGENT_LOG`, `.venv/Scripts` on PATH so the TUI spawns this branch's `gaia-agent`.

**Scope note (honest):** Haiku results validate the harness + scenarios, not the shipped local-model quality — the product baseline is Gemma-4-E4B on the self-hosted runner. Scenarios tagged `local_blocked_no_embedder` (42) cannot run without the embedder and are listed as **BLOCKED**, not skipped silently.

## Status

_Last updated: 2026-08-21 ~16:20 PT_

| Phase | Status |
|---|---|
| Phase 3a smoke (`gaia eval agent --agent-type gaia`, UI-backend transport) | ✅ PASS 10.0/10 (`core_arithmetic_direct`, scorecard `agent_type: gaia`, 0 Lemonade procs) |
| TUI up in control mode on Haiku | ✅ chat view, agent=gaia, control API on :8817, `claude` chip active |
| Capability ladder (tui-tagged scenarios) | 🔄 L1 ✅ — rest running |
| Category sweep (locally runnable) | ⏳ pending |
| BLOCKED categories (need embedder → runner) | `gaia_memory` (15), `gaia_rag` (8), `gaia_code` (4), `gaia_tool_selection` (5), + memory/RAG-leaning skill tasks |

## Per-scenario results

_(populated as runs complete; every PASS carries the captured answer evidence)_

| Scenario | Tier/Tags | Result | Evidence |
|---|---|---|---|
| core_arithmetic_direct (TUI) | t1_basic, tui | ✅ PASS | `391` — 4.5s, ttft 4.5s, 1 step, no tools |
