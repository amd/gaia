# PR #606 Integration Analysis — feature/pipeline-orchestration-v1

**PR:** amd/gaia#606 — feat(memory): agent memory v2 — second brain with hybrid search, LLM extraction, and observability dashboard
**Author:** kovtcharov (Kalin Ovtcharov)
**Branch analyzed:** feature/pipeline-orchestration-v1
**PR branch:** feature/agent-memory
**Analysis date:** 2026-04-08
**Analyst:** planning-analysis-strategist (Dr. Sarah Kim)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [PR #606 Architecture Overview](#2-pr-606-architecture-overview)
3. [Conflict Matrix](#3-conflict-matrix)
4. [HIGH Severity Conflicts — Detailed Analysis](#4-high-severity-conflicts--detailed-analysis)
5. [MEDIUM Severity Conflicts](#5-medium-severity-conflicts)
6. [Non-Conflicts: Clean Files](#6-non-conflicts-clean-files)
7. [Build-Upon Opportunities](#7-build-upon-opportunities)
8. [Risk Register](#8-risk-register)
9. [Recommended Action Plan](#9-recommended-action-plan)
10. [Questions for kovtcharov](#10-questions-for-kovtcharov)
11. [Open Items Added to Branch Change Matrix](#11-open-items-added-to-branch-change-matrix)

---

## 1. Executive Summary

PR #606 is the most architecturally significant open PR in the GAIA repository. At 37,040 additions across 75 files, it is larger than any single delivery phase on our branch. Its core deliverable is a persistent agent memory system — a production-grade "second brain" for GAIA agents — built on a hybrid retrieval stack (FAISS vector search + BM25 FTS5 full-text + cross-encoder reranking with RRF fusion), an LLM-in-the-loop extraction pipeline that classifies every user interaction as ADD/UPDATE/DELETE/NOOP, and an autonomous background execution loop (`AgentLoop`) driven by a goal state machine. Alongside the memory subsystem, PR #606 also ships a system discovery engine (`SystemDiscovery`, 2,543 lines) that profiles the user's hardware, software, and filesystem context; a goal tracking store (`GoalStore`, 572 lines) with scheduling and priority sorting; a six-section Memory Dashboard in React (4,227 lines of TSX and CSS); 14 eval scenarios; and two major specification documents.

The conflict picture with our branch is manageable but non-trivial. Of the files modified by PR #606, eleven exist in modified or created form on our branch. Four of these rise to HIGH severity: `_chat_helpers.py`, `database.py`, `sse_handler.py`, and `routers/mcp.py` — in each case because our branch created a comprehensive new module where PR #606 made targeted additions against a much smaller upstream version. The resolution strategy for all four HIGH cases is the same: absorb PR #606's additions into our larger module rather than losing them. The remaining seven conflicts are MEDIUM or LOW and are resolvable by standard three-way merge with focused manual review.

The strategic recommendation is to merge PR #606 to main first. It establishes foundational infrastructure — `MemoryStore`, `MemoryMixin`, `AgentLoop`, `GoalStore`, `SystemDiscovery`, and the memory REST API — that our pipeline stages can build upon immediately. The ChatSDK→AgentSDK rename PR #606 performs is identical to the same rename already on our branch, which resolves one conflict automatically. After PR #606 lands on main, we rebase `feature/pipeline-orchestration-v1` onto the updated main, absorb the four HIGH conflicts, and open a coordination session with kovtcharov about the `PipelineExecutor`↔`AgentLoop` convergence opportunity and a Phase 6 design discussion. Six concrete build-upon opportunities are identified in Section 7 that would make the pipeline orchestration system significantly more capable when layered on PR #606's memory infrastructure.

---

## 2. PR #606 Architecture Overview

The following table summarizes every major new component introduced by PR #606, ordered by line count.

| Component | File | Lines | Purpose |
|---|---|---|---|
| `SystemDiscovery` | `src/gaia/agents/base/discovery.py` | 2,543 | Scans hardware (CPU, GPU, NPU), installed software, filesystem layout, and Windows registry (guarded). Produces a profile used to bootstrap `MemoryStore` on Day 0. |
| `MemoryMixin` | `src/gaia/agents/base/memory.py` | 2,271 | LLM-in-the-loop ADD/UPDATE/DELETE/NOOP extraction. FAISS embedding pipeline. Consolidation and reconciliation. Adaptive top_k recall. Five public agent tools: `remember`, `recall`, `update_memory`, `forget`, `search_past_conversations`. |
| `MemoryDashboard.tsx` + CSS | `src/gaia/apps/webui/src/components/MemoryDashboard.tsx` + `.css` | 2,423 + 1,804 | Six-section React UI: memory browser, knowledge graph, goal tracker, system context viewer, session history, and observability panels. |
| `MemoryStore` | `src/gaia/agents/base/memory_store.py` | 2,478 | SQLite WAL-mode storage with FAISS vector index and BM25 FTS5 full-text search. Hybrid retrieval using Reciprocal Rank Fusion (RRF). Cross-encoder reranking. Schema v2: `knowledge.embedding BLOB`, `knowledge.superseded_by TEXT`, `conversations.consolidated_at TEXT`. |
| `memory.py` REST API router | `src/gaia/ui/routers/memory.py` | 1,329 | Full CRUD REST API for memory operations: list, search, add, update, delete, bulk-export, consolidate, and observability stats endpoints. |
| `AgentLoop` | `src/gaia/ui/agent_loop.py` | 442 | Autonomous background execution engine. Event-driven state machine with four states: IDLE, RUNNING, SCHEDULED, PAUSED. Reads pending goals from `GoalStore`, executes agent steps, emits SSE state-change events. |
| `system_context.py` | `src/gaia/agents/base/system_context.py` | 368 | Day-0 OS/hardware/software context record. Produced by `SystemDiscovery`, stored in `MemoryStore`, serialized to `AgentLoop` for hardware-aware reasoning. |
| `GoalStore` | `src/gaia/agents/base/goal_store.py` | 572 | Goal state machine (PENDING / ACTIVE / COMPLETED / FAILED / CANCELLED). Task decomposition under goals. Priority sorting with urgency decay. Scheduling with cron-like recurrence. |
| `goals.py` REST API router | `src/gaia/ui/routers/goals.py` | 283 | CRUD REST API for goal management: create, list, update state, add tasks, complete tasks, delete. |
| Eval scenarios | `eval/scenarios/memory/` | 14 files | Scenario definitions covering ADD/UPDATE/DELETE/NOOP classification, hybrid retrieval accuracy, cross-encoder reranking quality, and consolidation correctness. |
| Spec: agent-memory-architecture | `docs/spec/agent-memory-architecture.md` | 2,238 | Architecture specification covering the full memory lifecycle, hybrid retrieval design, schema evolution, and observability requirements. |
| Spec: autonomous-agent-mode | `docs/spec/autonomous-agent-mode.md` | 1,117 | Specification for the `AgentLoop` autonomous background mode: goal lifecycle, scheduling model, safety constraints, and UI integration contracts. |

**Modified files (not listed above):** `src/gaia/agents/base/agent.py` (+44/-18), `src/gaia/chat/sdk.py` (+16/-14), `src/gaia/agents/chat/agent.py` (+140/-19), `src/gaia/ui/_chat_helpers.py` (+38/0), `src/gaia/ui/server.py` (+15/0), `src/gaia/ui/database.py` (+31/-3), `src/gaia/ui/sse_handler.py` (+115/-1), `src/gaia/ui/routers/mcp.py` (+206/-1), `src/gaia/cli.py` (+1,017/0), `src/gaia/apps/webui/src/components/ChatView.tsx` (+42/-21), `src/gaia/mcp/servers/agent_ui_mcp.py` (+54/0).

---

## 3. Conflict Matrix

Files modified by both PR #606 and our branch, ordered by severity.

| ID | File | PR #606 Change | Our Branch Change | Severity | Resolution Path |
|---|---|---|---|---|---|
| C-1 | `src/gaia/ui/_chat_helpers.py` | +38 lines: adds `_register_agent_memory_ops()` function wiring live `ChatAgent` LLM and FAISS into memory router | Created as a comprehensive 1,144-line module with full agent cache, SSE coordination, and session management | **HIGH** | Absorb PR #606's 38 lines into our module during rebase |
| C-2 | `src/gaia/ui/database.py` | +31/-3: adds three memory schema columns (`embedding BLOB`, `superseded_by TEXT`, `consolidated_at TEXT`) to `knowledge` and `conversations` tables | Created as a full 787-line `ChatDatabase` class with complete schema SQL | **HIGH** | Absorb memory schema columns into our `SCHEMA_SQL` constant during rebase |
| C-3 | `src/gaia/ui/sse_handler.py` | +115/-1: adds `AgentLoop` state-change event types and streaming handlers | Created as a full 950-line SSE handler module | **HIGH** | Absorb AgentLoop event handlers and type definitions into our handler during rebase |
| C-4 | `src/gaia/ui/routers/mcp.py` | +206/-1: adds MCP connection health endpoint, tool-list endpoint, and control endpoints (enable/disable per tool) | Created as a 425-line MCP catalog router with tiered catalog browsing and install configuration | **HIGH** | Merge both routers into one file — catalog endpoints + health/tool/control endpoints are complementary |
| C-5 | `src/gaia/cli.py` | +1,017 lines: adds `gaia memory status/clear/export/context`, `gaia goal list/add/complete`, `gaia agent-mode enable/disable` subcommands | +865 lines: adds ngrok URL configuration, MCP process management subcommands, and `gaia pipeline` stub | **MEDIUM** | Different CLI sections; manual three-way review required — no logical overlap but line ranges may collide |
| C-6 | `src/gaia/agents/base/agent.py` | +44/-18: adds `_get_mixin_prompts()` auto-discovery, memory prompt injection into system prompt, curly-brace escaping fix | Adds `TOOLS_REQUIRING_CONFIRMATION` set, `ComponentLoader` initialization in `__init__`, `allowed_tools` parameter | **MEDIUM** | Different sections; likely auto-merge with manual spot-check of `__init__` block |
| C-7 | `src/gaia/agents/chat/agent.py` | +140/-19: memory prompt injection, `ChatAgent._get_mixin_prompts()` override fix, memory-aware greeting generation | +140/-19 on our branch (exact methods modified not enumerated here — confirm with `git diff main...HEAD -- src/gaia/agents/chat/agent.py` before resolving) | **MEDIUM** | Different concerns; manual three-way review for `__init__` and `_get_mixin_prompts` |
| C-8 | `src/gaia/ui/server.py` | +15: memory router import, `lifespan` hook calls `close_store()` | Pipeline router import additions | **LOW** | Different import additions; router mount list ordering conflict only |
| C-9 | `src/gaia/apps/webui/src/components/ChatView.tsx` | +42/-21: type-while-streaming input unlock (disables input lock while response streams) | Pipeline dashboard changes | **LOW-MEDIUM** | Different UI concerns; ChatView JSX tree touch requires manual diff |
| C-10 | `src/gaia/mcp/servers/agent_ui_mcp.py` | +54: memory access toggle — adds `enable_memory`/`disable_memory` control via MCP protocol | Modified for pipeline MCP registration | **MEDIUM** | Additive on both sides; manual merge of `__init__` and tool list |

**Files with no conflict (our branch only):**

| File | Our Purpose |
|---|---|
| `src/gaia/pipeline/*` (17 files) | Core pipeline orchestration engine |
| `src/gaia/pipeline/stages/*` (5 files) | Phase 5 autonomous pipeline stages |
| `src/gaia/pipeline/orchestrator.py` | Phase 5 `PipelineOrchestrator` |
| `component-framework/*` (47 files) | Meta-template library |
| `src/gaia/utils/frontmatter_parser.py` | MD frontmatter parser |
| `src/gaia/agents/configurable.py` | YAML-driven `ConfigurableAgent` |
| `src/gaia/agents/registry.py` | Pipeline-oriented `AgentRegistry` |
| `src/gaia/quality/`, `src/gaia/state/`, `src/gaia/resilience/`, etc. | All enterprise infrastructure |
| `docs/reference/branch-change-matrix.md`, `docs/reference/pr720-integration-analysis.md` | Our program documentation |
| `docs/spec/agent-ecosystem-design-spec.md`, `docs/spec/agent-ecosystem-action-plan.md` | Phase 5 specifications |

**Files with no conflict (PR #606 only):**

| File | PR #606 Purpose |
|---|---|
| `src/gaia/agents/base/memory_store.py` | Core `MemoryStore` — no touch on our branch |
| `src/gaia/agents/base/memory.py` | `MemoryMixin` — no touch on our branch |
| `src/gaia/agents/base/discovery.py` | `SystemDiscovery` — no touch on our branch |
| `src/gaia/agents/base/goal_store.py` | `GoalStore` — no touch on our branch |
| `src/gaia/agents/base/system_context.py` | `SystemContext` record — no touch on our branch |
| `src/gaia/ui/agent_loop.py` | `AgentLoop` — new file — no conflict |
| `src/gaia/ui/routers/memory.py` | Memory REST API — new file — no conflict |
| `src/gaia/ui/routers/goals.py` | Goals REST API — new file — no conflict |
| `src/gaia/apps/webui/src/components/MemoryDashboard.tsx` + `.css` | Dashboard UI — no touch on our branch |
| `eval/scenarios/memory/` (14 files) | Memory eval scenarios — no touch on our branch |
| `docs/spec/agent-memory-architecture.md` | Memory spec — no touch on our branch |
| `docs/spec/autonomous-agent-mode.md` | AgentLoop spec — no touch on our branch |

---

## 4. HIGH Severity Conflicts — Detailed Analysis

### C-1: `src/gaia/ui/_chat_helpers.py` — Targeted Addition Against a Much Larger Module

**What collides.** PR #606 adds 38 lines to the upstream `_chat_helpers.py` — specifically a new function `_register_agent_memory_ops()` that wires the live `ChatAgent` LLM instance and FAISS embedding model from the agent cache into the memory router, enabling the router's LLM-extraction endpoints to use the same model already loaded in memory rather than spinning up a second inference session. On upstream main, `_chat_helpers.py` is a relatively small module. Our branch created it as a 1,144-line comprehensive module (confirmed by `wc -l`) with full agent cache management, per-session locking, SSE coordination primitives, confirmation-pending state, and session document indexing logic.

**Why it conflicts.** When we rebase onto main after PR #606 merges, git will see our 1,144-line version of `_chat_helpers.py` differing from the upstream version (which now includes PR #606's `_register_agent_memory_ops()`) in nearly every line. Git cannot auto-merge this because the common ancestor is a much smaller file. The rebase will halt with a conflict on this file.

**Exact resolution steps.**

1. During rebase conflict resolution for `_chat_helpers.py`, accept our 1,144-line version as the base.
2. Identify the `_register_agent_memory_ops()` function from PR #606. It takes the in-memory `ChatAgent` instance (accessible via our agent cache dict, keyed by `session_id`) and injects it into the memory router module via `memory_router.set_live_agent(agent)` or equivalent. The exact signature must be read from the merged main after PR #606 lands.
3. Add `_register_agent_memory_ops()` at the end of our module, after the existing cache management functions, before the module's closing comments.
4. Verify that any import `_register_agent_memory_ops()` requires (likely `from .routers.memory import set_live_agent` or similar) is present in our import block at the top of the file.
5. Run `python -m pytest tests/unit/test_chat_helpers.py -xvs` to confirm no regressions.
6. Verify that `server.py` calls `_register_agent_memory_ops()` at the correct point in the startup lifespan (after agent cache is warm, before memory router handles requests).

**Coordination note.** Notify kovtcharov that `_chat_helpers.py` on our branch is a 1,144-line module, not the smaller upstream version. His 38-line addition should be reviewed against our version's agent cache structure to confirm `_register_agent_memory_ops()` accesses the cache correctly. The cache on our branch is `_agent_cache: dict[str, dict]` keyed by `session_id`, where each value is a dict with keys `"agent"`, `"model_id"`, and `"document_ids"` — not a direct `ChatAgent` reference. The function must use `_agent_cache[session_id]["agent"]` to retrieve the live agent instance.

---

### C-2: `src/gaia/ui/database.py` — Memory Schema Columns Into Our Full ChatDatabase

**What collides.** PR #606 adds three schema columns to the upstream `database.py`: `knowledge.embedding BLOB` (stores FAISS embedding vectors as raw bytes), `knowledge.superseded_by TEXT` (links superseded knowledge entries for lineage tracking), and `conversations.consolidated_at TEXT` (marks when a conversation was absorbed into memory). It also adds a `+31/-3` migration path that alters existing tables using `ALTER TABLE IF NOT EXISTS`. Our branch created `database.py` as a full 787-line `ChatDatabase` class with a comprehensive `SCHEMA_SQL` constant that defines all tables from scratch.

**Why it conflicts.** Our `SCHEMA_SQL` defines the `sessions`, `messages`, `documents`, `session_documents`, and `settings` tables. It does not define the `knowledge` or `conversations` tables because those are part of the `MemoryStore` schema, which PR #606 introduces in `memory_store.py`. The `+31/-3` that PR #606 makes to the upstream `database.py` is an alteration to the database initialization path (adding column migrations), not a table creation. When we rebase, git will see our 787-line `database.py` diverging from the upstream 787-line-equivalent file.

**Exact resolution steps.**

1. Confirm after PR #606 merges: does `memory_store.py` manage its own SQLite database file (separate from `gaia_chat.db`) or does it share `gaia_chat.db`? If it shares the file, the `knowledge` and `conversations` tables must be created in `SCHEMA_SQL` or in the `MemoryStore.__init__` schema bootstrap. If it uses a separate file (e.g., `~/.gaia/memory/memory.db`), no schema changes are needed in our `database.py`.
2. If shared database: add the PR #606 column definitions and migration statements to our `SCHEMA_SQL` constant at the bottom of the table definitions block. Do not add them mid-table; append after the `settings` table.
3. If PR #606 uses `ALTER TABLE` for schema migration (likely, given the +31/-3 pattern), add corresponding `ALTER TABLE IF NOT EXISTS` guards to our `_migrate()` method in `ChatDatabase`. Our migration method is named `_migrate()` (not `_migrate_schema()`), called from `_init_schema()` at database open time.
4. The three column additions are: `knowledge.embedding BLOB`, `knowledge.superseded_by TEXT`, `conversations.consolidated_at TEXT`. Confirm exact SQL from the merged upstream.
5. Run `python -m pytest tests/unit/test_database.py -xvs` after the merge.

**Coordination note.** Ask kovtcharov (see Q1 in Section 10) whether `MemoryStore` and `ChatDatabase` share a SQLite file. This is an architectural decision with significant implications for schema ownership.

---

### C-3: `src/gaia/ui/sse_handler.py` — AgentLoop Event Types Into Our Full SSE Handler

**What collides.** PR #606 adds 115 lines to the upstream `sse_handler.py` — primarily new event type constants and streaming handler methods for `AgentLoop` state transitions: specifically, events for IDLE→RUNNING, RUNNING→SCHEDULED, RUNNING→PAUSED, and the completion/failure terminal states. It also adds the SSE serialization for goal-completion progress events emitted when a goal transitions from ACTIVE to COMPLETED. Our branch created `sse_handler.py` as a full 950-line module with comprehensive SSE output handling, the `SSEOutputHandler` class, thought/tool-call/answer event parsing, and the regex constants `_THOUGHT_JSON_SUB_RE`, `_TOOL_CALL_JSON_SUB_RE`, `_ANSWER_JSON_SUB_RE`, `_RAG_RESULT_JSON_SUB_RE`.

**Why it conflicts.** Our module is self-contained and already imports the regex constants into `_chat_helpers.py` (visible in the confirmed import block at lines 28–35 of `_chat_helpers.py`: `_ANSWER_JSON_SUB_RE`, `_RAG_RESULT_JSON_SUB_RE`, `_THOUGHT_JSON_SUB_RE`, `_TOOL_CALL_JSON_SUB_RE`, `_clean_answer_json`, `_fix_double_escaped` — all defined in our `sse_handler.py`). PR #606's additions reference `AgentLoop` state enum values that do not exist on our branch. The rebase will produce a conflict because git sees two divergent versions of a file that was small on main and large on both sides.

**Exact resolution steps.**

1. Accept our 950-line version as the base during rebase conflict resolution.
2. Identify the AgentLoop event type constants PR #606 adds (likely string constants like `EVENT_AGENT_STATE_CHANGE`, `EVENT_GOAL_PROGRESS`).
3. Add these constants after the existing event type constants in our module, maintaining alphabetical order or logical grouping with our existing constants.
4. Identify the AgentLoop SSE handler methods PR #606 adds. These methods emit state-transition events to the active SSE connection keyed by session ID. Add them as methods on `SSEOutputHandler` or as module-level functions matching the pattern already established in our handler.
5. Verify that `src/gaia/ui/agent_loop.py` (PR #606's new file) imports from `sse_handler` using names that exist in our merged version.
6. Import `AgentLoop` from `src/gaia/ui/agent_loop.py` in `sse_handler.py` only if necessary; prefer the handler accepting raw state strings to avoid a circular import risk (agent_loop.py likely imports from sse_handler.py already).

---

### C-4: `src/gaia/ui/routers/mcp.py` — Two Routers Serving Different MCP Concerns

**What collides.** PR #606 adds 206 lines to the upstream `routers/mcp.py` — a small file on main — introducing MCP connection health endpoints (`GET /api/mcp/health`), a tool-list endpoint (`GET /api/mcp/tools`), and per-tool control endpoints (`POST /api/mcp/tools/{tool_id}/enable`, `POST /api/mcp/tools/{tool_id}/disable`). Our branch created `routers/mcp.py` as a 425-line module providing a curated MCP server catalog (`GET /api/mcp/catalog`, `GET /api/mcp/catalog/{name}`) with tier-based browsing, install configuration generation, and environment variable templating.

**Why it conflicts.** Both versions of `routers/mcp.py` define a `router = APIRouter(tags=["mcp"])` and mount complementary endpoint sets. Neither set conflicts logically — catalog browsing and connection health/tool control are distinct concerns that belong in the same router file. The merge conflict is structural: two complete router modules must be combined into one without duplicating the `router` declaration or the imports.

**Exact resolution steps.**

1. Accept our 425-line version as the base during rebase conflict resolution.
2. Inspect the PR #606 additions for imports not already present in our file. Likely additions: `MCPClientManager` or similar runtime connection manager, `MCPToolRegistry` or equivalent for tool state tracking.
3. Add those imports to our import block.
4. Add PR #606's health/tool/control endpoints after our catalog endpoints, maintaining the router grouping order: catalog first (discovery), health/tool/control second (runtime management). Use a comment section separator to document the grouping:

```python
# ---------------------------------------------------------------------------
# Runtime health and tool control endpoints (from agent-memory integration)
# ---------------------------------------------------------------------------
```

5. Verify that `server.py` mounts the `mcp` router only once. After the merge, `server.py` should have a single `app.include_router(mcp.router, prefix="/api/mcp")` call — not two.
6. Run `python -m pytest tests/mcp/ -xvs` and `python -m pytest tests/unit/test_mcp_router.py -xvs` to confirm both catalog and health endpoints respond correctly.

**Architectural note.** After the merge, the unified `routers/mcp.py` will cover three concerns: catalog discovery, runtime connection health, and per-tool control. This is appropriate for a single file — all three serve the Agent UI's MCP management panel. If the file grows beyond 700 lines after absorption, consider splitting into `routers/mcp_catalog.py` and `routers/mcp_control.py` with a shared `routers/mcp/__init__.py` re-exporting both routers.

---

## 5. MEDIUM Severity Conflicts

### C-5: `src/gaia/cli.py` — Two Large Additive Blocks in Different Subcommand Sections

PR #606 adds 1,017 lines to `cli.py` implementing three new subcommand groups: `gaia memory` (status, clear, export, context), `gaia goal` (list, add, complete), and `gaia agent-mode` (enable, disable). Our branch adds 865 lines implementing ngrok URL configuration under `gaia mcp`, MCP process management (`gaia mcp start`, `gaia mcp stop`, `gaia mcp status`, `gaia mcp test`), and the `gaia pipeline` informational stub. Our `cli.py` is confirmed at 6,748 lines — already an extremely large file.

The logical concern areas are non-overlapping: memory/goal/agent-mode (PR #606) versus mcp-process/pipeline (our branch). However, both branches touch the `subparsers.add_parser()` registration block and the main `if/elif` dispatch chain near the end of the file. These areas will produce line-range conflicts during rebase.

**Resolution:** Manual three-way merge of the argument parser registration block and the dispatch chain. Accept both sets of subcommands. Preserve ordering: add PR #606's `memory`, `goal`, `agent-mode` parsers after our `pipeline` and `mcp` parsers. In the dispatch chain, add PR #606's `elif args.action in ("memory", "goal", "agent-mode")` branches adjacent to our `pipeline` and `mcp` branches. Verify with `gaia --help` that all subcommands appear in the help output.

---

### C-6: `src/gaia/agents/base/agent.py` — Two Additive Enhancements to `__init__`

PR #606 adds `_get_mixin_prompts()` auto-discovery logic that walks the MRO to collect prompt snippets from all active mixins, injects memory context into the system prompt, and fixes a curly-brace escaping bug in prompt template interpolation. Our branch adds `TOOLS_REQUIRING_CONFIRMATION` (a module-level set at line 43), `allowed_tools` parameter to `__init__`, and `ComponentLoader` initialization early in `__init__` (confirmed at lines 105, 153, 219, 234–237 of our version).

These changes affect different regions of the file: the `TOOLS_REQUIRING_CONFIRMATION` set and `allowed_tools` parameter are at the top of the class and in the constructor signature; `_get_mixin_prompts()` is a new method appended to the class body. The primary overlap risk is in the `__init__` method body, where both branches add initialization lines. The curly-brace escaping fix from PR #606 is a genuine correctness fix — it prevents format-string injection when memory content contains curly braces — and must be preserved.

**Resolution:** Accept the three-way merge. Manually verify the `__init__` block to ensure: (1) PR #606's curly-brace escaping fix is present, (2) our `ComponentLoader` initialization occurs after the existing attribute setup, (3) `allowed_tools` parameter is in the constructor signature. Run `python -m pytest tests/unit/test_agent.py -xvs`.

---

### C-7: `src/gaia/agents/chat/agent.py` — Memory Prompt vs. Pipeline Hooks

PR #606 adds memory prompt injection to `ChatAgent` (adding memory context to the system prompt at session start), a `_get_mixin_prompts()` override to fix the mixin prompt collection ordering, and memory-aware greeting generation (the greeting message includes what the agent already knows about the user). Our branch has also modified `chat/agent.py` (confirmed at +140/-19 on our branch relative to upstream); the exact nature of those modifications is not enumerated here and should be confirmed by reviewing the branch diff before resolving this conflict.

These changes may affect overlapping regions: PR #606 touches `__init__`, `_get_mixin_prompts`, and the greeting generation path; our branch's +140/-19 change set should be inspected to identify which methods were modified before resolving. Auto-merge may succeed for non-overlapping regions, but the `__init__` method is a likely conflict site because both branches add initialization calls.

**Resolution:** Manual three-way review of `chat/agent.py`. Before beginning conflict resolution, run `git diff main...HEAD -- src/gaia/agents/chat/agent.py` to enumerate the exact methods modified on our branch. Preserve all of: memory prompt injection (PR #606), `_get_mixin_prompts` override (PR #606), and all additions from our branch. Run `python -m pytest tests/unit/test_chat_agent.py -xvs` and confirm that memory-aware greetings appear in a test chat session.

---

### C-8 through C-10 (LOW to LOW-MEDIUM)

**C-8 `server.py`:** PR #606 adds a memory router import and a `lifespan` hook that calls `close_store()` on shutdown. Our branch adds pipeline router imports. Both are additive to the import block and the router mount list. Standard rebase — accept both additions. Verify `server.py` import order (alphabetical by router name) and that `close_store()` is called in the correct lifespan position.

**C-9 `ChatView.tsx`:** PR #606 unlocks the chat input while a response is streaming (the type-while-streaming feature). Our branch adds pipeline dashboard elements. These are different UI regions. Perform a visual three-way merge in the JSX return tree. Test both features in the browser: (1) confirm input unlocks mid-stream, (2) confirm our pipeline dashboard elements render correctly.

**C-10 `mcp/servers/agent_ui_mcp.py`:** PR #606 adds `enable_memory` and `disable_memory` tool handlers to the MCP server, allowing external MCP clients to toggle memory on and off. Our branch modified this file for pipeline MCP registration. Both are additive tool registrations. Merge both sets of tool handlers; they occupy different sections of the class body.

---

## 6. Non-Conflicts: Clean Files

### Files exclusively on our branch (no PR #606 touch)

| File | Our System | Safe to Merge |
|---|---|---|
| `src/gaia/pipeline/` (17 files) | Pipeline engine | Yes |
| `src/gaia/pipeline/stages/` (5 files) | Phase 5 autonomous stages | Yes |
| `src/gaia/pipeline/orchestrator.py` | `PipelineOrchestrator` | Yes |
| `src/gaia/quality/` (15 files) | Quality gate system | Yes |
| `src/gaia/state/` (4 files) | Context and Nexus state | Yes |
| `src/gaia/resilience/` (3 files) | Circuit breaker / bulkhead / retry | Yes |
| `src/gaia/health/` (3 files) | Health monitoring | Yes |
| `src/gaia/cache/`, `src/gaia/config/`, `src/gaia/observability/` | Enterprise infrastructure | Yes |
| `src/gaia/agents/configurable.py` | `ConfigurableAgent` | Yes |
| `src/gaia/agents/registry.py` | Pipeline `AgentRegistry` | Yes — but naming collision with PR #720 remains open (see branch-change-matrix Open Item 1) |
| `component-framework/` (47 files) | Meta-template library | Yes |
| `src/gaia/utils/frontmatter_parser.py` | MD frontmatter parser | Yes |
| `docs/spec/agent-ecosystem-design-spec.md` | Phase 5 design spec | Yes |
| `docs/spec/agent-ecosystem-action-plan.md` | Phase 5 action plan | Yes |
| `docs/reference/branch-change-matrix.md` | This branch's change matrix | Yes |
| `docs/reference/pr720-integration-analysis.md` | PR #720 analysis | Yes |

### Files exclusively in PR #606 (no branch touch)

| File | PR #606 System | Impact on Our Branch |
|---|---|---|
| `src/gaia/agents/base/memory_store.py` | `MemoryStore` | None at merge. Build-upon target post-merge. |
| `src/gaia/agents/base/memory.py` | `MemoryMixin` | None at merge. `MemoryMixin` can be added to pipeline stage agents post-merge. |
| `src/gaia/agents/base/discovery.py` | `SystemDiscovery` | None at merge. `DomainAnalyzer` can consume discovery output post-merge. |
| `src/gaia/agents/base/goal_store.py` | `GoalStore` | None at merge. `PipelineExecutor` can write goals to `GoalStore` post-merge. |
| `src/gaia/agents/base/system_context.py` | `SystemContext` | None at merge. |
| `src/gaia/ui/agent_loop.py` | `AgentLoop` | None at merge. Phase 6 convergence candidate with `PipelineExecutor`. |
| `src/gaia/ui/routers/memory.py` | Memory REST API | None at merge. |
| `src/gaia/ui/routers/goals.py` | Goals REST API | None at merge. |
| `src/gaia/apps/webui/src/components/MemoryDashboard.tsx` + `.css` | Memory Dashboard UI | None at merge. |
| `eval/scenarios/memory/` (14 files) | Memory eval scenarios | Additive to eval framework. |
| `docs/spec/agent-memory-architecture.md` | Memory architecture spec | None at merge. |
| `docs/spec/autonomous-agent-mode.md` | AgentLoop spec | None at merge. |

### Identical change (auto-resolves)

| File | Change |
|---|---|
| `src/gaia/chat/sdk.py` | ChatSDK→AgentSDK, ChatConfig→AgentConfig, ChatResponse→AgentResponse rename — identical on both branches. Git will auto-resolve this on rebase with no conflict. |

---

## 7. Build-Upon Opportunities

These six opportunities become available to our branch immediately after PR #606 merges. They are ordered by implementation readiness.

### BU-1: MemoryMixin for Pipeline Stage Agents

**Opportunity.** `DomainAnalyzer`, `WorkflowModeler`, `LoomBuilder`, `GapDetector`, and `PipelineExecutor` currently produce analysis results that exist only in memory for the duration of a pipeline run. After PR #606 merges, these stage agents can inherit `MemoryMixin` and persist their analysis results across sessions. A subsequent pipeline run on the same domain would immediately recall prior domain analysis, component gap findings, and generated workflow models — dramatically reducing redundant LLM calls.

**How to implement.** In each stage agent class in `src/gaia/pipeline/stages/`, add `MemoryMixin` to the inheritance chain. Add `super().__init__()` call to `MemoryMixin.__init__()` in each stage constructor. At the end of each stage's `execute()` method, call `self.remember(key=f"{self.__class__.__name__}:{domain}:{timestamp}", value=result_summary)`. Use `self.recall(query=f"prior analysis for domain {domain}")` at the start of `execute()` to check for cached results before running the full LLM analysis pipeline.

**Phase.** Phase 6 — after both PRs merge to main.

---

### BU-2: GoalStore as Unified Goal Tracking for PipelineExecutor

**Opportunity.** PR #606's `GoalStore` implements a goal state machine (PENDING / ACTIVE / COMPLETED / FAILED / CANCELLED) with task decomposition, priority sorting, and scheduling. Our `PipelineExecutor` tracks pipeline phase completion through an internal state machine with an analogous set of states. Writing pipeline execution state to `GoalStore` would make pipeline runs visible in the Memory Dashboard's goal tracker panel — providing users a unified view of all autonomous activity, whether initiated via chat or pipeline.

**How to implement.** In `PipelineOrchestrator` (`src/gaia/pipeline/orchestrator.py`), inject a `GoalStore` instance (available from `src/gaia/agents/base/goal_store.py` after PR #606 merges). At orchestrator startup, create a top-level goal: `goal_store.create_goal(title="Pipeline run: {domain}", priority=HIGH)`. For each stage, create a task under that goal and transition its state as phases complete or fail. Map pipeline states to `GoalStore` states: PLANNING→PENDING, EXECUTION→ACTIVE, REVIEW→ACTIVE, COMPLETED→COMPLETED, FAILED→FAILED.

**Phase.** Phase 6 — after both PRs merge to main.

---

### BU-3: AgentLoop and PipelineExecutor Convergence

**Opportunity.** PR #606's `AgentLoop` (442 lines, `src/gaia/ui/agent_loop.py`) and our `PipelineExecutor` share a fundamental pattern: both are event-driven state machines that execute agent steps autonomously in the background, both support user interruption (PAUSED state in AgentLoop, iteration budget exhaustion in PipelineExecutor), and both emit progress events via SSE. Maintaining two independent implementations of this pattern creates divergence risk. The Phase 6 convergence opportunity is to unify into a single runtime that the Agent UI, the pipeline engine, and the goal scheduler all use as their execution substrate.

**How to implement.** This requires a design session with kovtcharov before implementation. The candidate unification design: extract the shared state machine and step-budget logic into a new `src/gaia/ui/autonomous_runtime.py` base class. `AgentLoop` becomes a subclass specialized for chat-goal execution. `PipelineExecutor` becomes a subclass specialized for multi-phase pipeline execution. Both share the SSE event emission infrastructure already present in our `sse_handler.py`.

**Phase.** Phase 6 — requires coordination with kovtcharov. Propose design session before either PR merges to main.

---

### BU-4: SystemDiscovery Bootstrapping DomainAnalyzer

**Opportunity.** PR #606's `SystemDiscovery` (2,543 lines) collects hardware specifications (CPU family, GPU model, NPU availability and driver version), installed software inventory, filesystem layout, and project type detection. Our `DomainAnalyzer` stage currently derives its domain recommendations from the user-provided goal text alone. `SystemDiscovery` output would allow `DomainAnalyzer` to calibrate its recommendations based on actual hardware — for example, recommending the NPU-optimized agent tier when Ryzen AI NPU is detected and its driver version meets the minimum threshold.

**How to implement.** In `DomainAnalyzer.execute()` in `src/gaia/pipeline/stages/domain_analyzer.py`, import `SystemDiscovery` from `gaia.agents.base.discovery` after PR #606 merges. Call `discovery = SystemDiscovery(); context = discovery.get_cached_context()` (the discovery caches results after the first run). Pass `context.hardware` and `context.software_inventory` as additional parameters to the domain analysis LLM prompt. Add a section to the prompt: "Available hardware: {hardware_summary}. Calibrate agent tier recommendations accordingly."

**Phase.** Phase 6 — after PR #606 merges to main. Low implementation cost, high value.

---

### BU-5: MemoryStore Caching GapDetector Results

**Opportunity.** `GapDetector` (`src/gaia/pipeline/stages/`) currently scans the filesystem and component registry on every invocation to detect gaps in the agent ecosystem. For large workspaces with many components, this scan is expensive. PR #606's `MemoryStore` supports knowledge supersession via the `superseded_by` lineage field — when a gap is filled, the old gap record can be marked as superseded by the new component rather than deleted, preserving audit history.

**How to implement.** After `GapDetector.execute()` identifies gaps, call `self.remember(key=f"gap:{component_type}:{capability}", value=gap_description, tags=["gap-detection"])`. At the start of the next `GapDetector` invocation, call `self.recall(query="unfilled gaps in component registry")` before running the filesystem scan. If memory returns recent gap records (within a configurable TTL, e.g., 4 hours), skip the scan and return the cached results. When `GapDetector` determines a gap has been filled, call `self.update_memory(key=f"gap:{component_type}:{capability}", value="FILLED", supersedes=prior_key)`.

**Phase.** Phase 6 — after PR #606 merges. Requires `MemoryMixin` to be added to `GapDetector` first (see BU-1).

---

### BU-6: Declarative Memory Tool Invocations in component-framework Templates

**Opportunity.** The `component-framework` meta-template library (47 files) uses an explicit fenced-block syntax for tool invocations that the `ComponentLoader` renders at template instantiation time. PR #606 introduces five memory tools (`remember`, `recall`, `update_memory`, `forget`, `search_past_conversations`) as registered `@tool`-decorated functions. These tools can be expressed declaratively in component templates, making memory use a first-class capability that template authors specify without writing Python.

**How to implement.** Extend the `component-framework` tool-call fenced block syntax to recognize the memory tool namespace. Add five template fragments to `component-framework/templates/tools/`:

    ```tool:remember
    key: "{{component_name}}:analysis:{{timestamp}}"
    value: "{{analysis_summary}}"
    tags: ["pipeline", "{{stage_name}}"]
    ```

Update `ComponentLoader.render()` to emit the correct `MemoryMixin` method call when a `tool:remember` block is encountered. Add validation in `ComponentLoader._validate_tool_reference()` that the memory tools are available in the agent's tool registry before rendering. Document in `component-framework/README.md`.

**Phase.** Phase 6 — after PR #606 merges. Design discussion with kovtcharov recommended to align tool-call block syntax with the broader tool-call architecture.

---

## 8. Risk Register

The following risks are explicitly tracked. Each risk describes what breaks if the identified condition is not satisfied before or immediately after PR #606 lands on main.

| ID | Risk | Trigger Condition | Impact | Likelihood | Mitigation |
|---|---|---|---|---|---|
| R-1 | `_chat_helpers.py` rebase produces a corrupted module | PR #606 merges without coordination on C-1; git three-way merge is accepted without manual absorption of `_register_agent_memory_ops()` | Memory operations fail silently at runtime because the live agent instance is never injected into the memory router | Medium | P0 Step 1: notify kovtcharov before merge; confirm cache key convention |
| R-2 | Memory schema columns land in wrong database file | Q1 (shared vs. separate SQLite file) is unanswered when C-2 is resolved during rebase | Schema mismatch causes `OperationalError: no such column: knowledge.embedding` on any memory recall operation after first run | High | P0 Step 2: get Q1 answer from kovtcharov before PR #606 merges; gate C-2 resolution on Q1 answer |
| R-3 | `AgentLoop` SSE events are dropped by our handler | C-3 is resolved without full absorption of event type constants; frontend receives unrecognized event type strings | Goal completion events are silently dropped; Memory Dashboard goal tracker never updates during autonomous execution | Medium | P0 Step 3: request Q3 event JSON schema from kovtcharov before rebase; P1 Step 10: verify during absorption |
| R-4 | Duplicate router mount in `server.py` | C-8 rebase produces two `app.include_router(mcp.router, ...)` calls — one from our additions and one from PR #606's import additions | FastAPI raises a router mount conflict or silently serves duplicate routes; MCP health endpoints respond on two prefixes | Low | P1 Step 11: after merging C-4, grep `include_router` in `server.py` and confirm single mount per router |
| R-5 | `PipelineExecutor` and `AgentLoop` diverge permanently | Phase 6 begins implementation before the convergence design session (BU-3) is held | Two independent autonomous execution runtimes require separate maintenance; SSE event schemas diverge; future unification cost doubles | Medium | P0 Step 5: schedule convergence design session before Phase 6 kickoff |
| R-6 | MCP tool control endpoints land unauthenticated | Q5 (authentication posture) is unanswered; C-4 merge combines catalog endpoints (unauthenticated, local-only) with tool enable/disable endpoints without aligning security posture | External processes on the same machine can toggle agent memory on/off without authorization | Medium | P0 Step 4: get Q5 answer from kovtcharov; align security posture in C-4 resolution |
| R-7 | Memory eval scenarios conflict with pipeline eval runner | Q6 (shared vs. custom runner) is unanswered; 14 eval scenarios use an incompatible runner that overwrites our runner configuration | `gaia eval` crashes or produces invalid results when run against combined scenario directory | Low | P1 Step 15: confirm Q6 runner compatibility before running `gaia eval` post-rebase |
| R-8 | BU-5 and BU-6 are deferred indefinitely without open items | No tracked work item exists for `GapDetector` memory caching (BU-5) or component-framework memory tool blocks (BU-6) | Two build-upon opportunities are lost when the post-merge window closes and Phase 6 scope is locked | Low | Open Items 14 and 15 (added in Section 11) track BU-5 and BU-6 explicitly |

---

## 9. Recommended Action Plan

The following table is the authoritative, ownable action plan for integrating PR #606 with our branch. All steps are ordered by priority tier. Ownership tags: **[us]** = our branch team acts unilaterally; **[joint]** = requires synchronous coordination with kovtcharov; **[kovtcharov]** = requires kovtcharov's action or answer.

Priority tiers: **P0** = before PR #606 merges to main (act now); **P1** = immediately after PR #606 merges, before submitting our branch for review; **P2** = within one sprint of PR #606 merge.

### P0 — Before PR #606 merges (act now)

| Step | Owner | Action | Blocks | Ref |
|---|---|---|---|---|
| 1 | [us] | Post a comment on PR #606 notifying kovtcharov that `_chat_helpers.py` on our branch is a 1,144-line module (not the upstream version). Request confirmation that `_register_agent_memory_ops()` accesses the agent cache correctly: our cache is `_agent_cache: dict[str, dict]` keyed by `session_id`, where the live agent is at `_agent_cache[session_id]["agent"]` — not a direct `ChatAgent` value. | C-1 resolution; R-1 mitigation | Q2, C-1 |
| 2 | [us] | Post a comment on PR #606 notifying kovtcharov that `database.py` on our branch is a full 787-line `ChatDatabase` class. Request the Q1 answer: does `MemoryStore` share `gaia_chat.db` or use a separate SQLite file? This answer gates the C-2 resolution strategy entirely. | C-2 resolution; R-2 mitigation | Q1, C-2 |
| 3 | [us] | Post a comment on PR #606 requesting the AgentLoop SSE event JSON schema (Q3). The 115 lines added to `sse_handler.py` introduce new event types; we need the payload structure before absorbing them to ensure our frontend's SSE client handles them correctly. | C-3 resolution; R-3 mitigation | Q3, C-3 |
| 4 | [us] | Post a comment on PR #606 requesting the authentication posture of the MCP tool control endpoints (Q5). Catalog endpoints on our branch are unauthenticated; if the new control endpoints require authentication, we must align security posture during C-4 resolution. | C-4 resolution; R-6 mitigation | Q5, C-4 |
| 5 | [joint] | Propose and schedule the `AgentLoop`↔`PipelineExecutor` convergence design session (BU-3). This is a Phase 6 architectural decision; the session does not block either PR's merge but must be scheduled before Phase 6 kickoff. Reference Section 7 BU-3 and risk R-5. | R-5 mitigation; Open Item 12 | BU-3 |
| 6 | [us] | Add Open Items 9–15 to the branch-change-matrix document. Section 11 of this document defines the exact text for each item. | Branch change matrix current | Section 11 |

### P1 — Immediately after PR #606 merges to main

| Step | Owner | Action | Blocks | Ref |
|---|---|---|---|---|
| 7 | [us] | Rebase `feature/pipeline-orchestration-v1` onto updated main. Resolve all ten conflict sites in order: C-2 first (gate on Q1 answer), then C-1, C-3, C-4 (HIGH), then C-5 through C-10 (MEDIUM/LOW). Estimated total effort: 3.5 engineer-hours. See conflict effort table below. | All subsequent P1 steps | Section 3, 4, 5 |
| 8 | [us] | After rebase, absorb `_register_agent_memory_ops()` into `_chat_helpers.py`. Verify: function is present at end of module, import from `routers.memory` is in the import block, `server.py` lifespan calls it after agent cache is warm. | R-1 mitigation | C-1 |
| 9 | [us] | Absorb memory schema columns (`embedding BLOB`, `superseded_by TEXT`, `consolidated_at TEXT`) into `database.py` per Q1 answer. If shared database: add to `SCHEMA_SQL` constant and add `ALTER TABLE IF NOT EXISTS` guards to `_migrate()`. If separate database: no change needed but confirm `MemoryStore.__init__` handles its own schema bootstrap. | R-2 mitigation | C-2, Q1 |
| 10 | [us] | Absorb `AgentLoop` event type constants and streaming handler methods into `sse_handler.py`. Verify event constants match the JSON schema confirmed via Q3. Verify `agent_loop.py` imports only names that now exist in our merged version. | R-3 mitigation | C-3, Q3 |
| 11 | [us] | Merge MCP health/tool/control endpoints into `routers/mcp.py`. Add section separator comment. Verify `server.py` has a single `app.include_router(mcp.router, ...)` call after merge. | R-4, R-6 mitigation | C-4, Q5 |
| 12 | [us] | Resolve C-5 through C-10 (MEDIUM/LOW conflicts). Merge CLI subcommand sections (C-5), verify `agent.py` `__init__` block including curly-brace escaping fix (C-6), verify `chat/agent.py` method-level merge (C-7), verify `server.py` import ordering and single router mounts (C-8), perform three-way JSX diff on `ChatView.tsx` (C-9), merge MCP tool handler additions (C-10). | Rebase completeness | C-5 through C-10 |
| 13 | [us] | Run the full test suite post-rebase. Pay particular attention to: `test_chat_helpers.py`, `test_database.py`, `test_sse_handler.py`, `test_mcp_router.py`. | PR submission | — |
| 14 | [us] | Smoke test the merged Agent UI. Verify: (1) Memory Dashboard loads, (2) chat input unlocks mid-stream, (3) `GET /api/mcp/catalog` responds, (4) `GET /api/mcp/health` responds, (5) memory tools appear in the agent tool list. | PR submission | — |
| 15 | [us] | Confirm Q6 (memory eval runner compatibility): run `gaia eval` against the combined scenario directory. Verify that the 14 memory eval scenarios and the pipeline eval scenarios run together without conflict. | R-7 mitigation | Q6 |
| 16 | [us] | Verify CLI help output shows all subcommands from both branches: `gaia memory`, `gaia goal`, `gaia agent-mode`, `gaia mcp`, `gaia pipeline`. | PR submission | C-5 |

**Rebase conflict effort table:**

| File | Conflict | Estimated Effort |
|---|---|---|
| `src/gaia/ui/_chat_helpers.py` | Full module divergence (C-1) | 45 min |
| `src/gaia/ui/database.py` | Schema addition absorption (C-2) | 20 min |
| `src/gaia/ui/sse_handler.py` | Event type/handler absorption (C-3) | 30 min |
| `src/gaia/ui/routers/mcp.py` | Two-router merge (C-4) | 30 min |
| `src/gaia/cli.py` | Subcommand section merge (C-5) | 45 min |
| `src/gaia/agents/base/agent.py` | `__init__` block spot-check (C-6) | 20 min |
| `src/gaia/agents/chat/agent.py` | Method-level three-way review (C-7) | 20 min |
| `src/gaia/ui/server.py` | Import block ordering (C-8) | 10 min |
| `src/gaia/apps/webui/src/components/ChatView.tsx` | JSX tree diff (C-9) | 20 min |
| `src/gaia/mcp/servers/agent_ui_mcp.py` | Tool handler addition (C-10) | 15 min |
| **Total estimated effort** | | **~3.5 engineer-hours** |

**Test suite commands:**

```bash
python -m pytest tests/unit/ -xvs
python -m pytest tests/mcp/ -xvs
python -m pytest tests/integration/ -xvs
```

**CLI verification commands:**

```bash
gaia --help
gaia memory --help
gaia goal --help
gaia agent-mode --help
gaia mcp --help
gaia pipeline --help
```

### P2 — Within one sprint of PR #606 merge

| Step | Owner | Action | Phase | Ref |
|---|---|---|---|---|
| 17 | [us] | Implement BU-1: add `MemoryMixin` to pipeline stage agents. Start with `DomainAnalyzer` as the proof of concept. Write a unit test verifying `DomainAnalyzer` recalls a prior analysis result from memory rather than re-running the LLM pipeline. | Phase 6 | BU-1, Open Item 10 |
| 18 | [us] | Implement BU-2: wire `GoalStore` into `PipelineOrchestrator`. Create goals at orchestrator startup, transition task states as phases complete. Verify that the Memory Dashboard goal tracker panel shows pipeline runs. | Phase 6 | BU-2, Open Item 11 |
| 19 | [us] | Implement BU-4: wire `SystemDiscovery` cached hardware context into `DomainAnalyzer`. Pass `context.hardware` and `context.software_inventory` to the domain analysis LLM prompt. Verify NPU detection improves agent tier recommendations on AMD hardware. Low implementation cost, high value. | Phase 6 | BU-4, Open Item 13 |
| 20 | [joint] | Hold the Phase 6 convergence design session for BU-3 (`AgentLoop`↔`PipelineExecutor`). Produce a design document for the unified autonomous runtime before any implementation begins. This step is the prerequisite gate for Open Item 12. | Phase 6 | BU-3, Open Item 12 |
| 21 | [us] | Implement BU-5: add `MemoryMixin` to `GapDetector` and wire memory caching for gap scan results with a configurable TTL (recommended: 4 hours). Wire supersession when a gap is filled. Requires BU-1 (`MemoryMixin` added to stage agents) as prerequisite. | Phase 6 | BU-5, Open Item 14 |
| 22 | [joint] | Design session with kovtcharov on BU-6: declarative memory tool invocations in `component-framework` templates. Align tool-call block syntax with the broader tool-call architecture before implementation. | Phase 6 / Phase 7 | BU-6, Open Item 15 |

### P1 Gate — Verification Checklist

This checklist is the P1 completion gate. The rebased branch must not be submitted for review until all items are checked.

- [ ] All ten conflict sites (C-1 through C-10) resolved and smoke-tested
- [ ] `_register_agent_memory_ops()` present in `_chat_helpers.py` and called from `server.py` lifespan (Step 8)
- [ ] Q1 answered; memory schema columns absorbed into `database.py` `SCHEMA_SQL` if shared database (Step 9)
- [ ] `AgentLoop` event type constants present in `sse_handler.py`, payload structure confirmed via Q3 (Step 10)
- [ ] Both MCP catalog endpoints and health/tool/control endpoints present in unified `routers/mcp.py` (Step 11)
- [ ] All CLI subcommands from both branches present and working: `gaia memory`, `gaia goal`, `gaia agent-mode`, `gaia mcp`, `gaia pipeline` (Step 16)
- [ ] Full unit test suite passes with no new failures (Step 13)
- [ ] Memory Dashboard loads in Agent UI (Step 14)
- [ ] Pipeline engine runs a complete cycle end-to-end (Step 14)
- [ ] Memory eval scenarios confirmed compatible with pipeline eval runner (Step 15)
- [ ] Branch change matrix updated with Open Items 9–15 and post-merge status (Step 6)

---

## 10. Questions for kovtcharov

The following questions require kovtcharov's input before or during the merge sequence. All are resolvable via a PR #606 comment or a short async discussion. Each question is cross-referenced to the Action Plan step that is gated on the answer.

**Q1 — MemoryStore database file sharing.** (Gates Action Plan Step 9; see also R-2)
Does `MemoryStore` manage its own separate SQLite file (e.g., `~/.gaia/memory/memory.db`) or does it share `~/.gaia/chat/gaia_chat.db` with `ChatDatabase`? This determines whether the `knowledge` and `conversations` table definitions and the three memory schema columns (`embedding BLOB`, `superseded_by TEXT`, `consolidated_at TEXT`) must be absorbed into our `SCHEMA_SQL` constant, or whether they are isolated in `MemoryStore.__init__`. This is the highest-priority question as it gates conflict resolution for C-2. Requested via P0 Step 2.

**Q2 — `_register_agent_memory_ops()` cache key convention.** (Gates Action Plan Step 8; see also R-1)
The function added to `_chat_helpers.py` (+38 lines) injects a live `ChatAgent` instance into the memory router. Our `_chat_helpers.py` cache (1,144 lines) uses `session_id` as the primary cache key, with the cache defined as `_agent_cache: dict[str, dict]` where each value is a dict with keys `"agent"`, `"model_id"`, and `"document_ids"`. The live agent instance is retrieved via `_agent_cache[session_id]["agent"]`, not via a direct value lookup. Does `_register_agent_memory_ops()` access the agent via this dict structure, or does it assume a different cache shape (e.g., direct `dict[str, ChatAgent]` mapping)? Confirming the access pattern before the function is absorbed prevents a silent `KeyError` or type error at runtime. Requested via P0 Step 1.

**Q3 — AgentLoop SSE event schema.** (Gates Action Plan Step 10; see also R-3)
The 115 lines added to `sse_handler.py` introduce new event types for `AgentLoop` state transitions. What is the JSON schema for these events (event name, payload fields)? Our frontend's SSE client (in `src/gaia/apps/webui/`) will need to handle these events. Sharing the event schema now lets us verify that our SSE handler absorption preserves the correct payload structure. Requested via P0 Step 3.

**Q4 — AgentLoop and PipelineExecutor convergence interest.** (Gates Open Item 12; see also R-5)
PR #606's `AgentLoop` and our `PipelineExecutor` share a fundamental pattern: autonomous background execution with an event-driven state machine, step budgets, and SSE streaming. Is kovtcharov open to a Phase 6 design session exploring a unified autonomous runtime that both the memory goal executor and the pipeline orchestrator use as their execution substrate? If yes, we would draft a design proposal before implementation begins. Proposed via P0 Step 5.

**Q5 — MCP health/tool/control endpoint authentication.** (Gates Action Plan Step 11; see also R-6)
The +206 lines in `routers/mcp.py` add runtime control endpoints (`POST /api/mcp/tools/{tool_id}/enable`, `POST /api/mcp/tools/{tool_id}/disable`). These endpoints change the active tool set of the running agent, which is a privileged operation. Are these endpoints protected by any authentication middleware in PR #606, or are they assumed to be local-only (no authentication required)? Our catalog endpoints are unauthenticated (local-only assumption). Aligning the security posture of both sets before merge is important. Requested via P0 Step 4.

**Q6 — Memory eval scenarios and our eval framework integration.** (Gates Action Plan Step 15; see also R-7)
PR #606 adds 14 eval scenarios under `eval/scenarios/memory/`. Our branch extended the eval framework (`src/gaia/eval/`) with `eval_metrics.py` and `scorecard.py` for pipeline performance measurement. Do the 14 memory eval scenarios use the same runner infrastructure (`src/gaia/eval/runner.py`) that our pipeline eval scenarios use? If yes, they can be run together with `gaia eval --scenario-dir eval/scenarios/` with no changes. If they use a custom runner, we need to ensure the two runners do not conflict. Confirmed in P1 Step 15.

---

## 11. Open Items Added to Branch Change Matrix

The following items should be added to the `feature/pipeline-orchestration-v1` branch change matrix open items list (currently items 1–8) after this document is reviewed and accepted. Items 9 through 15 continue the existing numbering sequence. Add all seven items in a single update to the matrix.

**Open Item 9 — Absorb C-1 through C-4 during rebase onto main (post-PR #606 merge).**
Four HIGH severity conflict resolution tasks must be completed when rebasing our branch onto main after PR #606 merges: (C-1) absorb `_register_agent_memory_ops()` into `_chat_helpers.py`; (C-2) absorb memory schema columns into `database.py` `SCHEMA_SQL` (gated on Q1 answer); (C-3) absorb `AgentLoop` event types into `sse_handler.py` (gated on Q3 event schema); (C-4) merge MCP health/tool/control endpoints into `routers/mcp.py` (gated on Q5 authentication posture). Estimated total effort: 3.5 engineer-hours. Owner: [us]. Status: open, blocked on PR #606 merge to main. Risks mitigated: R-1, R-2, R-3, R-4, R-6.

**Open Item 10 — BU-1: Add MemoryMixin to pipeline stage agents (post-PR #606).**
`DomainAnalyzer`, `WorkflowModeler`, `LoomBuilder`, `GapDetector`, and `PipelineExecutor` should inherit `MemoryMixin` to persist analysis results across sessions. Start with `DomainAnalyzer` as the proof of concept. Write a unit test verifying recall of prior analysis results before re-running the full LLM pipeline. Requires PR #606 on main. Owner: [us]. Status: post-merge work, Phase 6. Action Plan: Step 17.

**Open Item 11 — BU-2: Wire GoalStore into PipelineOrchestrator (post-PR #606).**
`PipelineOrchestrator` should write pipeline execution state to `GoalStore` using the PENDING/ACTIVE/COMPLETED/FAILED state mapping defined in Section 7. This makes pipeline runs visible in the Memory Dashboard goal tracker panel without any additional UI work. Requires PR #606 on main. Owner: [us]. Status: post-merge work, Phase 6. Action Plan: Step 18.

**Open Item 12 — BU-3: Schedule AgentLoop/PipelineExecutor convergence design session (coordination prerequisite).**
PR #606's `AgentLoop` and our `PipelineExecutor` share an autonomous background execution pattern that warrants a shared runtime abstraction evaluation in Phase 6. A design session with kovtcharov must be scheduled and the resulting design document produced before any Phase 6 convergence implementation begins. This item has no code dependency — it is a coordination prerequisite and a Phase 6 gate. Owner: [joint]. Status: open, schedule before Phase 6 start. Risk mitigated: R-5. Action Plan: Steps 5, 20.

**Open Item 13 — BU-4: SystemDiscovery → DomainAnalyzer hardware calibration (post-PR #606).**
`DomainAnalyzer` should import `SystemDiscovery` from `gaia.agents.base.discovery` and use the cached hardware context (NPU availability, GPU model, driver version) to calibrate domain agent tier recommendations. This is the lowest implementation cost, highest value build-upon: NPU detection immediately improves recommendation quality for AMD hardware users without requiring any new LLM infrastructure. Requires PR #606 on main. Owner: [us]. Status: post-merge work, Phase 6. Action Plan: Step 19.

**Open Item 14 — BU-5: GapDetector memory caching with MemoryStore supersession (post-PR #606).**
`GapDetector` should inherit `MemoryMixin` (prerequisite: Open Item 10) and cache gap scan results in `MemoryStore` with a configurable TTL (recommended: 4 hours). When a gap is subsequently filled, call `self.update_memory(...)` with the `supersedes` parameter to mark the gap record as resolved while preserving audit history via the `knowledge.superseded_by` lineage field introduced by PR #606. This eliminates redundant filesystem scans in large workspaces. Requires PR #606 on main and Open Item 10 complete. Owner: [us]. Status: post-merge work, Phase 6. Action Plan: Step 21.

**Open Item 15 — BU-6: Declarative memory tool invocations in component-framework templates (design session required).**
The `component-framework` tool-call fenced block syntax should be extended to recognize the five memory tools introduced by PR #606 (`remember`, `recall`, `update_memory`, `forget`, `search_past_conversations`). Template authors would specify memory operations declaratively without writing Python. Requires a joint design session with kovtcharov to align tool-call block syntax with the broader tool-call architecture before any implementation. Requires PR #606 on main. Owner: [joint]. Status: design session required before implementation, Phase 6 / Phase 7. Action Plan: Step 22.

---

*File: `docs/reference/pr606-integration-analysis.md`. All file references are relative to the repository root `C:\Users\amikinka\gaia`. Confirmed file sizes: `_chat_helpers.py` 1,144 lines, `database.py` 787 lines, `sse_handler.py` 950 lines, `routers/mcp.py` 425 lines, `cli.py` 6,748 lines (all measured 2026-04-08 on branch feature/pipeline-orchestration-v1).*

---

## Document History

| Stage | Role | Contribution | Date |
|---|---|---|---|
| 1 | planning-analysis-strategist (Dr. Sarah Kim) | Initial document: PR #606 architecture overview (Section 2), conflict matrix (Section 3), HIGH/MEDIUM/LOW conflict detailed analysis (Sections 4–5), non-conflict clean file tables (Section 6), six build-upon opportunities (Section 7), questions for kovtcharov (Section 10) | 2026-04-08 |
| 2 | software-program-manager | Added risk register (Section 8), structured P0/P1/P2 action plan with ownership tags and rebase effort table (Section 9), and Open Items 9–15 for the branch change matrix (Section 11) | 2026-04-08 |
| 3 | quality-reviewer | Fixed five technical accuracy issues: corrected cache annotation to `dict[str, dict]`, corrected migration method name to `_migrate()`, corrected `_chat_helpers.py` import line range, removed unverified `NexusService` claim, resolved conflict matrix severity inconsistency for C-9 | 2026-04-08 |
| 4 | technical-writer-expert | Final polish pass: added Table of Contents, verified sequential section numbering, fixed BU-6 nested code block, normalized table formatting, corrected production note reference, added Document History block | 2026-04-08 |
