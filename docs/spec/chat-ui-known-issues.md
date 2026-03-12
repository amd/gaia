# Chat UI — Known Issues (Complex / High-Risk)

These issues were identified during code review but are too complex or risky
to fix as part of the current PR. They should be addressed in dedicated
follow-up tickets.

---

## 1. Global Tool Registry Race Condition

**File:** `src/gaia/agents/base/tools.py`

The `@tool` decorator uses a module-level `_TOOL_REGISTRY` dict.  When
multiple agent instances run concurrently (e.g. two chat sessions), tool
registrations from one agent can leak into another.

**Impact:** Low probability in the Chat UI (single-agent), but a latent bug
for multi-agent scenarios.

**Suggested fix:** Scope the registry per-agent instance (e.g. store tools
on the agent class itself rather than in a global dict).

---

## 2. Zombie Agent Threads on Client Disconnect

**File:** `src/gaia/ui/routers/chat.py`, `src/gaia/ui/sse_handler.py`

When a client disconnects mid-stream (e.g. closes the browser tab), the
SSE response generator detects the disconnect, but the underlying agent
thread may continue executing tool calls (file reads, shell commands, RAG
queries) until the current turn completes.

**Impact:** Wasted CPU/memory, potential file locks or partial writes if
the agent was in the middle of a write_file tool call.

**Suggested fix:** Implement a cancellation token that the agent checks
between tool calls.  On disconnect, set the token and have the agent
abort at the next checkpoint.

---

## 3. `@tool` Decorator Ignores `description` / `parameters` kwargs

**File:** `src/gaia/agents/base/tools.py`

The `@tool` decorator accepts `description` and `parameters` keyword
arguments, but the current implementation extracts tool metadata from the
function's docstring and type hints instead.  Explicitly passed kwargs are
silently ignored.

**Impact:** Tool descriptions shown to the LLM may be inaccurate if a
developer passes explicit kwargs expecting them to override the docstring.

**Suggested fix:** Check for explicit kwargs first and prefer them over
auto-extracted metadata.

---

## 4. Blocking I/O in Async File Router Endpoints

**File:** `src/gaia/ui/routers/files.py`

Several async endpoints perform synchronous filesystem operations that block
the event loop:

- **`browse_files`** — `Path.iterdir()`, `stat()`, `is_dir()` (lines 223-260)
- **`preview_file`** — `open()` + read loop (lines 564-601)
- **`upload_file`** — `write_bytes()` (line 119)

`search_files` was already fixed to use `run_in_executor`.

**Impact:** Under load, these synchronous calls block the single asyncio
event loop thread, adding latency to all concurrent requests (including SSE
streams).

**Suggested fix:** Wrap the blocking sections in
`asyncio.get_running_loop().run_in_executor(None, ...)` using the same
pattern as `search_files`.

---

## 5. Store Files Using `console.*` Instead of Logger

**Files:** `src/gaia/apps/webui/src/stores/agentStore.ts`,
`auditStore.ts`, `agentChatStore.ts`, `systemStore.ts`, `notificationStore.ts`

These stores (19 calls total) use raw `console.log`/`console.error`/
`console.warn` instead of the structured `log` utility from
`utils/logger.ts`.

**Impact:** Inconsistent logging output; no log level filtering in
production.  These stores are part of the multi-agent desktop UI, not the
current Chat UI PR scope.

**Suggested fix:** Import and use `log` from `../utils/logger` in each
store, matching the pattern used in components and services.

---

## 6. Hardcoded Colors in Tool Metadata (AgentActivity.tsx)

**File:** `src/gaia/apps/webui/src/components/AgentActivity.tsx`

The `TOOL_META` table (lines 41-74) and inline `style={{ color: ... }}`
attributes use hardcoded hex colors (e.g. `#3b82f6`, `#22c55e`, `#ef4444`)
that don't respond to theme changes.

**Impact:** Colors may have poor contrast in some themes. Not a functional
bug but a design consistency issue.

**Suggested fix:** Replace with CSS custom properties (e.g.
`var(--tool-color-search)`) defined in the theme.

---

## 7. Chat SDK Integration Test Flaky on LLM Memory Recall

**File:** `tests/test_chat_sdk.py` (line 340)

`test_convenience_functions_integration` asserts the LLM recalls "Max"
from a prior message in the same conversation. The small local model
(Qwen3-0.6B) intermittently fails to recall context, causing non-
deterministic test failures.

Also fails on `main` (e.g. Release v0.16.0 run 22786442028).

**Impact:** Flaky CI — the "Chat SDK Tests (Windows)" workflow fails
roughly 1 in 3 runs. Not a code bug.

**Suggested fix:** Make the assertion more tolerant (retry once, or
accept partial recall), or pin to a larger model for this test.
