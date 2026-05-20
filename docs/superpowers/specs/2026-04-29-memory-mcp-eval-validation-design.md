# Memory MCP for Eval Validation — Design

**Date:** 2026-04-29
**Branch:** `feature/agent-memory`
**Status:** Approved (verbal: "perfect, implement it all")

## Problem

The agent-memory eval is purely behavioral. The judge LLM reads the agent's
natural-language reply and grades it against `success_criteria` text. That misses
two whole classes of regression:

1. **Silent storage failures.** Agent says "got it, saved" but never called
   `remember`, or stored the wrong category, or wrote with `sensitive=true`
   when it should have been false.
2. **Cross-scenario contamination.** All scenarios run against the live shared
   `MemoryStore` — a fact stored in scenario N can leak into scenario N+1's
   recall results, causing flaky passes/fails.

A second smaller gap: pytest CI has near-zero coverage of the
memory-MCP-server tool surface itself. The 3 tools that exist
(`memory_stats`, `memory_list`, `memory_recall` in `agent_ui_mcp.py`) are
gated behind a UI toggle and have no integration tests, so a router-vs-MCP
schema drift would only surface during a manual eval run.

## Goal

Give the eval (and CI) **direct, structural read access to the memory store
via MCP**, plus the lifecycle hooks needed to make scenarios reproducible.
Then update memory scenarios + judge prompts to actually use that access to
verify ground-truth state, not just response text.

## Non-Goals

- Exposing memory write tools that bypass the agent (e.g. `remember` via
  MCP). Eval validates the *agent's* memory behavior — letting MCP write
  facts directly would defeat the test. The only writes are `memory_clear`
  (teardown) and `memory_seed` (controlled setup).
- Changing the existing 5 agent-side memory tools (`remember`, `recall`,
  `update_memory`, `forget`, `search_past_conversations`). Those stay as the
  agent's interface; MCP is a *parallel inspection* surface.
- Building a query DSL. The new MCP tools are thin wrappers over the
  existing REST endpoints (and add only the missing ones).

## Architecture

```
┌──────────────────────────┐
│  Eval simulator (Claude) │  ← reads scenario YAML, drives turns
└────────────┬─────────────┘
             │ MCP (streamable-http)
             ▼
┌──────────────────────────┐
│  agent_ui_mcp server     │  ← existing; extends with memory_* tools
│   (FastMCP)              │
└────────────┬─────────────┘
             │ HTTP REST
             ▼
┌──────────────────────────┐
│  Agent UI backend        │
│  /api/memory/* router    │  ← existing; adds /knowledge/{id},
│                          │     /clear, /seed (eval-only)
└────────────┬─────────────┘
             │ Python
             ▼
┌──────────────────────────┐
│  MemoryStore (SQLite)    │  ← unchanged
└──────────────────────────┘
```

Three layers add code:

1. **Memory router (`src/gaia/ui/routers/memory.py`)** — three new endpoints:
   - `GET /api/memory/knowledge/{knowledge_id}` — single-item lookup
     (currently you can only list/search; for adversarial verification we
     need to read e.g. `superseded_by` on a specific row).
   - `POST /api/memory/admin/clear` — body `{scope: "all" | "knowledge" | "conversations"}`.
     Reuses `MemoryStore.clear_all()` and adds `clear_knowledge()` /
     `clear_conversations()` partial variants. Returns counts.
   - `POST /api/memory/admin/seed` — body `{items: [...]}` — bulk insert.
     Validates schema, calls `MemoryStore.store()` per item, returns
     `{ids: [...]}`.
   - Both `admin/*` endpoints are gated server-side by env
     `GAIA_MEMORY_ADMIN=1`. Disabled in production by default; the eval
     runner sets it before launching the simulator.

2. **MCP server (`src/gaia/mcp/servers/agent_ui_mcp.py`)** — replace the
   `mcp_memory_enabled` UI-settings gate with a layered enable check, and
   add the missing tools:

   | Tool | Status | What it returns |
   |------|--------|----------------|
   | `memory_stats` | exists | `{knowledge: {...}, conversations: {...}}` |
   | `memory_list` | extend with `entity`, `domain`, `sensitive`, `since` | knowledge list |
   | `memory_recall` | exists | hybrid-search hits |
   | `memory_get` | NEW | one knowledge row including `superseded_by`, `sensitive`, `entity`, `confidence` |
   | `memory_get_by_entity` | NEW | rows where `entity == ?` |
   | `memory_get_conversation_turns` | NEW | rows from `conversations` table |
   | `memory_clear` | NEW (eval-only) | `{cleared: {...counts...}}` |
   | `memory_seed` | NEW (eval-only) | `{ids: [...]}` |

   Enable rule (replaces today's single-flag check):
   ```
   enabled = (
       env GAIA_MEMORY_MCP_ALWAYS == "1"          # eval runner sets this
       or settings.mcp_memory_enabled is True     # UI toggle still works
   )
   ```
   Admin tools (`memory_clear`, `memory_seed`) ALSO require
   `GAIA_MEMORY_ADMIN=1` — same gate the REST endpoint uses, so the toggle
   is one env var, not two.

3. **Eval runner (`src/gaia/eval/runner.py`)** — sets
   `GAIA_MEMORY_MCP_ALWAYS=1` and `GAIA_MEMORY_ADMIN=1` in the simulator
   subprocess env when the scenario category is `memory`. No effect on
   non-memory categories.

## Data flow per memory scenario

```
Phase 0  (runner)                  Phase 1  (simulator)
─────────────────                  ────────────────────
spawn claude -p with               1. system_status()
  GAIA_MEMORY_MCP_ALWAYS=1   ───►  2. create_session(...)
  GAIA_MEMORY_ADMIN=1              3. memory_clear(scope="all")        ← NEW
                                   4. (optional) memory_seed(items)    ← NEW
                                   5. for each turn:
                                        a. send_message(...)
                                        b. judge response (existing)
                                        c. memory_recall / memory_get  ← NEW
                                           to verify side-effects
                                   6. delete_session(...)
                                   7. Return JSON result
```

The `success_criteria` strings in scenario YAML grow a `VERIFY VIA MCP:`
clause when structural verification matters (adversarial poisoning,
contradictions, sensitive flag, supersession, false-memory injection).
Scenarios where behavior is the only thing that matters (greeting,
personality) keep their existing format.

## Components & responsibilities

### `src/gaia/ui/routers/memory.py`
- Owns: REST endpoints, request validation, error translation.
- New endpoints listed above. Each fails loudly on missing
  `GAIA_MEMORY_ADMIN` (`raise HTTPException(403, "memory admin disabled")`).
- No silent fallback — if `seed` gets a malformed item, the whole batch
  rejects with a structured error.

### `src/gaia/agents/base/memory_store.py`
- Owns: SQLite ops.
- New: `clear_knowledge()`, `clear_conversations()` (partial counterparts
  of existing `clear_all()`). One-line each, just `DELETE FROM ...; rebuild
  FTS;`. Plus `get_item(knowledge_id)` if it doesn't already exist (it does
  — confirmed at line 462). Plus a small `seed_bulk(items)` helper that
  short-circuits dedup so eval can plant exact rows.

### `src/gaia/mcp/servers/agent_ui_mcp.py`
- Owns: MCP tool wrappers + enable check.
- All new tools are thin: validate args, call REST, return dict.
- The gate function moves into a single helper
  `_memory_mcp_enabled(backend_url) -> tuple[bool, bool]` returning
  `(read_ok, admin_ok)`.

### `tests/integration/test_memory_mcp_surface.py` (NEW)
- Owns: deterministic CI coverage of the MCP↔REST↔store path.
- Spins up FastAPI test client + FastMCP in-memory + isolated SQLite.
- Covers each new tool: schema, happy path, gate-rejection, error path.
- Runs in seconds. No LLM. No Lemonade.

### `eval/scenarios/memory/*.yaml` (UPDATE)
- 8 scenarios get `VERIFY VIA MCP:` clauses on the success criteria where
  structural verification adds signal:
  - `memory_store_and_recall` — verify item exists with right category
  - `memory_stress_adversarial_poisoning` — verify `superseded_by`,
    verify `hunter2` absent, verify final state
  - `memory_conflict_resolution` — verify supersession chain
  - `memory_stress_forget_semantics` — verify deletion
  - `memory_stress_retrieval_under_noise` — pre-seed clutter, verify recall
  - `memory_proactive_surfacing` — verify what the agent saw
  - `memory_email_sender_priorities` — verify entity-tagged storage
  - `memory_journaling` — verify conversation-turn capture
- Other 18 scenarios stay text-only.

### `eval/prompts/judge_turn.md` (UPDATE)
- One paragraph: "If the success_criteria contains a `VERIFY VIA MCP:`
  clause, you MUST call the named memory_* MCP tool(s) and use the result
  in your scoring. Failure to verify when asked = correctness=0."

### `eval/prompts/simulator.md` (UPDATE)
- One paragraph documenting the new memory_* tools so the simulator knows
  it can call them between turns and during setup/teardown.

## Error handling

Every layer fails loudly per CLAUDE.md "no silent fallbacks":

- REST endpoints raise `HTTPException` with actionable messages
  ("memory admin disabled — set GAIA_MEMORY_ADMIN=1").
- MCP wrappers translate REST errors into `{error: "...", success: false}`.
- `memory_seed` validates the whole batch *before* writing — partial
  inserts are not tolerated.
- `memory_clear` is idempotent but logs at INFO with the count cleared,
  so a misconfigured eval that accidentally clears prod memory is loud
  in the logs.

## Test plan

| Layer | Test file | What it proves |
|-------|-----------|----------------|
| Store | `tests/unit/test_memory_store.py` (extend) | `clear_knowledge`, `clear_conversations`, `seed_bulk` round-trip cleanly |
| Router | `tests/unit/test_memory_router.py` (extend) | Admin endpoints 403 without env, 200 with env, schema validation |
| MCP↔REST | `tests/integration/test_memory_mcp_surface.py` (NEW) | Each new MCP tool calls the right REST endpoint with the right shape and returns the right dict |
| Eval (offline) | `tests/integration/test_memory_eval.py` (extend) | Existing tests keep passing; add 3 tests that simulate the verification flow without an LLM |
| Eval (live) | `gaia eval agent --scenario memory_stress_adversarial_poisoning` | End-to-end on the rebuilt judge prompt |

The pytest layers run in CI on every PR. The live eval runs on demand; it
needs Lemonade and is ~3 min/scenario.

## Rollout

Single PR on `feature/agent-memory` (already the active branch). No
feature flag in user code paths — the env vars are entirely server-side.
The UI `mcp_memory_enabled` toggle keeps working unchanged.

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Eval runs forget to clear → false PASS from leftover state | `memory_clear` is in the simulator's mandatory Phase-1 checklist; absence is logged |
| Admin tools accidentally exposed in prod | Gated by env `GAIA_MEMORY_ADMIN=1`; not set by any user-facing entrypoint |
| Seeded data with wrong dedup behavior triggers stale-fact bugs that don't reproduce in real use | `seed_bulk` documents the bypass; only stress scenarios use it |
| Schema drift between MCP tool docstring and REST response | Integration test `test_memory_mcp_surface.py` asserts exact response shape |

## Open questions

None at design time. Spec is locked.
