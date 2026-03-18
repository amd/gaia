# C++ Framework ↔ WebUI Integration Plan

**Status:** Proposed
**Date:** 2026-03-16
**Branch:** `kalin/chat-ui`
**Depends on:** PR #518 (C++ SSE Streaming Support)

## Overview

This plan describes how to make the GAIA WebUI work with both the Python and C++ agent frameworks. The goal is a **single reusable frontend** with clean abstractions so both frameworks are first-class citizens with minimal technical debt.

The C++ framework will support **MCP tools only** (no RAG, no file tools), with full agent reasoning loop, custom system prompts, and streaming — matching the experience of agents like the WiFi Troubleshooter, Health Agent, and Process Analyst.

## Architecture

### Current State

```
React Frontend → SSE → FastAPI Backend → ChatAgent (Python) → Lemonade LLM
                              ↓
                     SQLite DB, RAG SDK, SSE Handler
```

The WebUI is tightly coupled to Python's `ChatAgent`. There is no abstraction layer — the chat helpers directly instantiate `ChatAgent` and wire up `SSEOutputHandler`.

### Target State

```
┌──────────────────────────────────────────────────────────────┐
│                   React Frontend (UNCHANGED)                  │
│    ChatView · AgentActivity · MessageBubble · Settings        │
│                                                               │
│    Consumes: SSE events (13 types) via POST /api/chat/send    │
│    Adapts:   Capability-based feature toggling                │
└──────────────────────────┬───────────────────────────────────┘
                           │ SSE (text/event-stream)
┌──────────────────────────┴───────────────────────────────────┐
│              FastAPI Backend — SHARED LAYER                    │
│                                                               │
│   Sessions · Messages · DB · Files · System · Auth/CORS       │
│                                                               │
│   ┌───────────────────────────────────────────────────────┐   │
│   │           AgentBackend (Abstract Interface)            │   │
│   │   run(query, history) → AsyncIterator[AgentEvent]      │   │
│   │   get_capabilities() → { rag, streaming, mcp, ... }   │   │
│   │   get_available_agents() → [{ id, name, desc }]       │   │
│   └──────────┬──────────────────────────┬─────────────────┘   │
│              │                          │                      │
│   ┌──────────┴──────────┐   ┌──────────┴──────────────────┐   │
│   │ PythonAgentBackend  │   │    CppAgentBackend          │   │
│   │                     │   │                             │   │
│   │ ChatAgent+SSEHandler│   │ Subprocess: C++ agent.exe   │   │
│   │ In-process thread   │   │ --json-events --query "..."  │   │
│   │ Queue → SSE         │   │ stdout → JSON lines → SSE   │   │
│   │                     │   │                             │   │
│   │ Capabilities:       │   │ Capabilities:               │   │
│   │  rag ✅             │   │  rag ❌                     │   │
│   │  streaming ✅       │   │  streaming ✅ (PR #518)     │   │
│   │  file_tools ✅      │   │  mcp ✅                     │   │
│   │  shell_tools ✅     │   │  custom_agents ✅           │   │
│   └─────────────────────┘   └─────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Single frontend, capability-driven** — The React app is identical for both backends. Features like Document Library hide automatically when the backend reports `rag: false`.
2. **AgentBackend abstraction** — One interface with one critical method (`run()` yielding events). Both backends implement it.
3. **Subprocess bridge for C++** — The Python FastAPI server launches C++ agent binaries as subprocesses and reads JSON-line events from stdout. This avoids duplicating the HTTP/DB/session layer in C++.
4. **Shared SSE event protocol** — Both backends emit the same 13 event types the frontend already handles.

## Framework Comparison

| Aspect | Python Agent | C++ Agent |
|--------|-------------|-----------|
| Agent loop | `process_query()` in-process | `processQuery()` via subprocess |
| LLM access | AgentSDK → Lemonade | LemonadeClient → Lemonade |
| Streaming | ✅ OpenAI client wrapper | ✅ PR #518 (SseParser + callbacks) |
| Output | `AgentConsole` → `SSEOutputHandler` → Queue | `OutputHandler` → `JsonEventOutputHandler` → stdout |
| Tools | `@tool` decorator, `_TOOL_REGISTRY` | `ToolInfo` struct, `ToolRegistry` |
| MCP | `MCPClientMixin` (not wired to ChatAgent) | `connectMcpServer()` (built-in) |
| RAG | Full (FAISS + embeddings) | ❌ Not available |
| State machine | 5 states (PLANNING → EXECUTING_PLAN → ...) | 5 states (same pattern) |
| Response format | `{ thought, goal, tool, plan, answer }` | `{ thought, goal, tool, plan, answer }` (identical) |
| Plans | Auto-execute with `$PREV` substitution | Advisory (displayed, not auto-executed) |

## SSE Event Protocol

Both backends must emit these events (the frontend already handles all of them):

### Content Events

| Event Type | Fields | Source |
|-----------|--------|--------|
| `chunk` | `{ content }` | Streaming LLM tokens |
| `answer` | `{ content, elapsed, steps, tools_used }` | Final response |

### Wrapper Events (emitted by Python orchestration layer, not by agent)

| Event Type | Fields | Source |
|-----------|--------|--------|
| `done` | `{ message_id, content }` | Stream complete — emitted after DB save |
| `error` | `{ content }` | HTTP/transport error — not agent-level |

### Agent Activity Events

| Event Type | Fields | Source |
|-----------|--------|--------|
| `thinking` | `{ content }` | Agent reasoning / thought |
| `status` | `{ status, message }` | Goal updates, warnings, info, completion |
| `step` | `{ step, total, status }` | Step N of M |
| `plan` | `{ steps[], current_step }` | Multi-step plan |
| `tool_start` | `{ tool, detail }` | Tool invocation started |
| `tool_args` | `{ tool, args, detail }` | Tool arguments |
| `tool_result` | `{ title, summary, success, command_output?, result_data? }` | Tool result |
| `tool_end` | `{ success }` | Tool invocation ended |
| `agent_error` | `{ content }` | Agent-level error |

### C++ OutputHandler → SSE Event Mapping

| C++ OutputHandler Method | SSE Event Type | SSE Fields |
|---|---|---|
| `printProcessingStart(query, steps, model)` | *(no-op)* | Suppressed (Python SSEOutputHandler also no-ops this) |
| `printStepHeader(n, total)` | `step` | `{ step: n, total, status: "started" }` |
| `printStreamToken(token)` | `chunk` | `{ content: token }` (disabled in v1, see Streaming Caveat) |
| `printStreamEnd()` | *(no-op)* | Internal state only |
| `printThought(text)` | `thinking` | `{ content: text }` |
| `printGoal(text)` | `status` | `{ status: "working", message: text }` |
| `printPlan(plan, current)` | `plan` | `{ steps: [...], current_step: current }` |
| `printToolUsage(name)` | `tool_start` | `{ tool: name }` |
| `prettyPrintJson(args, "Tool Args")` | `tool_args` | `{ tool: name, args: {...}, detail: "..." }` |
| `printToolComplete()` | `tool_end` | `{ success: true }` |
| `prettyPrintJson(result, "Tool Result")` | `tool_result` | `{ title: "...", summary: "...", success: true, command_output?: {...} }` |
| `printFinalAnswer(text)` | `answer` | `{ content: text, steps, tools_used }` |
| `printError(msg)` | `agent_error` | `{ content: msg }` |
| `printWarning(msg)` | `status` | `{ status: "warning", message: msg }` |
| `printInfo(msg)` | `status` | `{ status: "info", message: msg }` |
| `printCompletion(steps, limit)` | `status` | `{ status: "complete", steps }` |
| `printStateInfo("ERROR RECOVERY")` | `status` | `{ status: "warning", message: "..." }` |
| `printDecisionMenu(decisions)` | *(no-op)* | Interactive-only (process agent); not supported in WebUI v1 |
| `printPrompt(text)` | *(no-op)* | Debug output; not sent to frontend |
| `printResponse(text)` | *(no-op)* | Debug output; not sent to frontend |
| `printHeader(text)` | *(no-op)* | Cosmetic terminal header |
| `printSeparator(len)` | *(no-op)* | Cosmetic terminal separator |
| `printToolInfo(name, params, desc)` | *(no-op)* | Tool listing (startup only) |

## Streaming + Structured Events (PR #518 Consideration)

PR #518 adds streaming to C++ but **suppresses structured output** (thought, goal, final answer) during streaming to avoid duplication on the terminal. The WebUI needs **both**: live token chunks AND structured agent events.

### Solution: `structuredEvents` Config Flag

```cpp
struct AgentConfig {
    bool streaming = defaultStreaming();
    bool structuredEvents = false;  // NEW: always emit structured events
};
```

When `structuredEvents = true` (set by `--json-events` flag), `processQuery()` always calls `printThought()`, `printGoal()`, `printFinalAnswer()` even in streaming mode. This gives the `JsonEventOutputHandler` both:
- `chunk` events from `printStreamToken()` (live tokens)
- `thinking` / `status` / `answer` events from structured methods (agent activity)

The existing `CleanConsole` and `TerminalConsole` behavior is unchanged — they never set `structuredEvents`.

### Streaming Caveat: Raw JSON Tokens

**Important:** C++ agents produce JSON-formatted LLM responses (`{"thought": "...", "tool": "...", "tool_args": {...}}`). When streaming is enabled, `printStreamToken()` receives **raw JSON tokens** from the LLM, not natural language. Sending these as `chunk` events would display raw JSON in the WebUI chat bubble.

**Solution for v1:** In `--json-events` mode, set `streaming = false`. The agent makes blocking LLM calls, then emits structured events (`thinking`, `tool_start`, `answer`) after parsing. The user still gets **real-time step-by-step feedback** because each tool call generates events as it happens — just not token-by-token LLM output.

```
Step 1: [thinking: "Checking adapter status..."]
         [tool_start: check_adapter]
         [tool_result: "Connected, signal 85%"]
Step 2: [thinking: "DNS looks good, testing internet..."]
         [tool_start: test_internet]
         [tool_result: "Connection to 8.8.8.8 successful"]
Step 3: [answer: "Your WiFi is working correctly. Signal strength is good at 85%."]
```

This is good UX — the user sees step progress without raw JSON. Token-level streaming for the final answer can be added in a follow-up by having the agent make a second streaming LLM call specifically for the answer generation.

## Critical Implementation Details

### `done` Event Ownership

The `done` SSE event (`{"type": "done", "message_id": N, "content": "..."}`) is **not emitted by the agent itself**. It is emitted by the Python FastAPI wrapper **after** the agent finishes and the assistant message is saved to the database (to include the `message_id`). This applies to both backends:

- **PythonAgentBackend:** The wrapper reads `None` sentinel from `SSEOutputHandler.signal_done()`, saves message to DB, then emits `done` with the DB-assigned `message_id`.
- **CppAgentBackend:** The wrapper detects subprocess exit (stdout EOF), saves message to DB, then emits `done`.

**The C++ `JsonEventOutputHandler` must NOT emit a `done` event.** The Python orchestration layer handles this.

### Conversation History Constraints

The WebUI passes a limited conversation history to agents:

- **Max pairs:** 2 (last 2 user↔assistant exchanges)
- **Max chars per message:** 500 (truncated with `... (truncated)` suffix)
- **Format:** `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`

For the C++ backend, this history is serialized to JSON and passed via `--history` CLI arg.

### ChatAgent Config Fields Used by WebUI

The `_chat_helpers.py` currently passes these fields when creating `ChatAgent`:

| Field | Type | Description | C++ Equivalent |
|-------|------|-------------|----------------|
| `model_id` | `str` | LLM model | `AgentConfig.modelId` |
| `max_steps` | `int` (default 10) | Step limit | `AgentConfig.maxSteps` |
| `streaming` | `bool` | Enable streaming | `AgentConfig.streaming` |
| `silent_mode` | `bool` | Suppress console | N/A (always JSON mode) |
| `rag_documents` | `List[str]` | Session docs | N/A (no RAG) |
| `library_documents` | `List[str]` | Library docs | N/A (no RAG) |
| `allowed_paths` | `List[str]` | Security paths | N/A |

The `CppAgentBackend` only needs to forward `model_id`, `max_steps`, and `agent_id` to the subprocess.

## Component Specifications

### 1. AgentBackend Interface (Python)

**File:** `src/gaia/ui/backends/base.py`

```python
from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict, List, Any
from dataclasses import dataclass

@dataclass
class AgentEvent:
    type: str    # Matches SSE event types: "chunk", "thinking", "tool_start", etc.
    data: dict   # Event payload (same shape frontend already expects)

class AgentBackend(ABC):
    @abstractmethod
    async def run(self, query: str, history: List[dict], config: dict) -> AsyncIterator[AgentEvent]:
        """Execute a query and yield events."""
        ...

    @abstractmethod
    def get_capabilities(self) -> dict:
        """Return backend capabilities: { rag, streaming, mcp, file_tools, shell_tools }"""
        ...

    @abstractmethod
    def get_available_agents(self) -> List[dict]:
        """List available agent types: [{ id, name, description }]"""
        ...
```

### 2. PythonAgentBackend (Python)

**File:** `src/gaia/ui/backends/python_backend.py`

Wraps the existing `ChatAgent` + `SSEOutputHandler` with minimal changes to current code. The existing `_chat_helpers.py` logic moves behind this interface.

### 3. CppAgentBackend (Python)

**File:** `src/gaia/ui/backends/cpp_backend.py`

Launches C++ agent binaries as subprocesses with `--json-events` flag. Reads JSON-line events from stdout, converts to `AgentEvent`, yields as SSE.

```python
class CppAgentBackend(AgentBackend):
    async def run(self, query, history, config):
        agent_binary = self.resolve_agent(config.get("agent_id", "wifi"))
        proc = await asyncio.create_subprocess_exec(
            agent_binary, "--json-events", "--query", query,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        async for line in proc.stdout:
            event = json.loads(line.strip())
            yield AgentEvent(type=event["type"], data=event)
```

### 4. JsonEventOutputHandler (C++)

**File:** `cpp/include/gaia/json_event_handler.h` + `cpp/src/json_event_handler.cpp`

A new `OutputHandler` subclass (~150 lines) that emits JSON lines to stdout matching the SSE event protocol. Each `OutputHandler` method maps to one JSON event (see mapping table above).

```cpp
class JsonEventOutputHandler : public OutputHandler {
public:
    void printThought(const std::string& thought) override {
        emit({{"type", "thinking"}, {"content", thought}});
    }
    void printStreamToken(const std::string& token) override {
        emit({{"type", "chunk"}, {"content", token}});
    }
    void printToolUsage(const std::string& toolName) override {
        currentTool_ = toolName;
        emit({{"type", "tool_start"}, {"tool", toolName}});
    }
    void printFinalAnswer(const std::string& answer) override {
        emit({{"type", "answer"}, {"content", answer},
              {"steps", stepsTaken_}, {"tools_used", toolsUsed_}});
    }
    // ... all 19 OutputHandler methods mapped (see table above)
private:
    void emit(const json& event) {
        std::cout << event.dump() << "\n" << std::flush;
    }
};
```

### 5. CLI Flags for C++ Agents

Each C++ agent binary gains two new flags:

- `--json-events` — Use `JsonEventOutputHandler` instead of `CleanConsole`
- `--query "text"` — Run single query from argument (non-interactive mode)

**Note:** `AgentConfig config_` is currently private in `Agent` with no public accessor. A public `AgentConfig& config()` accessor must be added to `agent.h` (one line) to support runtime configuration from CLI helpers.

```cpp
// In agent.h — add public accessor:
AgentConfig& config() { return config_; }

// In main() of each agent:
int main(int argc, char* argv[]) {
    WifiTroubleshooterAgent agent;
    if (hasFlag(argc, argv, "--json-events")) {
        agent.setOutputHandler(std::make_unique<JsonEventOutputHandler>());
        agent.config().structuredEvents = true;
        agent.config().streaming = false;  // Avoid raw JSON tokens in WebUI
    }
    if (hasFlag(argc, argv, "--query")) {
        std::string query = getArgValue(argc, argv, "--query");
        agent.processQuery(query);
    } else {
        // Existing interactive loop
    }
}
```

### 6. Capability-Based Frontend Adaptation

The frontend queries backend capabilities and conditionally shows/hides features:

```typescript
const caps = await api.getCapabilities();

// Document Library only shown for backends with RAG
{caps.rag && <DocumentLibrary />}

// Agent selector shown when multiple agents available
{caps.agents.length > 1 && <AgentSelector agents={caps.agents} />}

// Tool activity panel works identically (same events)
<AgentActivity steps={agentSteps} />
```

### 7. Backend Selection API

**New endpoint:** `GET /api/system/capabilities`

```json
{
  "backend": "python",          // or "cpp"
  "rag": true,
  "streaming": true,
  "mcp": false,
  "file_tools": true,
  "shell_tools": true,
  "agents": [
    { "id": "chat", "name": "Chat Agent", "description": "General chat with RAG" }
  ]
}
```

For C++ backend:
```json
{
  "backend": "cpp",
  "rag": false,
  "streaming": true,
  "mcp": true,
  "file_tools": false,
  "shell_tools": false,
  "agents": [
    { "id": "wifi", "name": "WiFi Troubleshooter", "description": "Diagnose and fix WiFi issues" },
    { "id": "health", "name": "System Health", "description": "System health monitoring" },
    { "id": "process", "name": "Process Analyst", "description": "Process analysis and monitoring" }
  ]
}
```

## Shared vs. Framework-Specific Code

| Component | Shared (both) | Python Only | C++ Only |
|-----------|--------------|-------------|----------|
| React frontend | ✅ 100% | | |
| SSE event format | ✅ 100% | | |
| Session management | ✅ 100% | | |
| Message persistence | ✅ 100% | | |
| AgentActivity UI | ✅ 100% | | |
| File browser | ✅ 100% | | |
| AgentBackend interface | ✅ 100% | | |
| Document Library | | ✅ (RAG) | |
| RAG indexing/query | | ✅ | |
| File/Shell tools | | ✅ | |
| JsonEventOutputHandler | | | ✅ |
| structuredEvents flag | | | ✅ |
| --json-events CLI | | | ✅ |

**Estimated shared code: 90%+**

## Implementation Order

### Phase 1: Backend Abstraction (Python)
1. Create `AgentBackend` abstract interface (#520)
2. Implement `PythonAgentBackend` wrapping existing code (#521)
3. Refactor `_chat_helpers.py` to use the backend interface (#526)

### Phase 2: C++ JSON Events (can run in parallel with Phase 1)
4. Add `structuredEvents` config flag to AgentConfig (#523)
5. Implement `JsonEventOutputHandler` in C++ (#522)
6. Add `--json-events` and `--query` CLI flags to agent binaries (#524)

### Phase 3: C++ Backend Bridge (Python — after Phase 1 + 2)
7. Implement `CppAgentBackend` subprocess bridge (#525)

### Phase 4: Frontend Adaptation (after Phase 3)
8. Add capabilities endpoint + frontend adaptation (#527)
9. Tool metadata for C++ agent tools (#528)
10. End-to-end integration testing (#529)

## Effort Estimate

| Task | Where | Effort | Difficulty |
|---|---|---|---|
| AgentBackend interface | Python | 1 day | 2/10 |
| PythonAgentBackend | Python | 1-2 days | 3/10 |
| CppAgentBackend | Python | 2-3 days | 4/10 |
| JsonEventOutputHandler | C++ | 2-3 days | 4/10 |
| structuredEvents flag | C++ | 0.5 day | 2/10 |
| --json-events + --query CLI | C++ | 1 day | 2/10 |
| Backend selection + config | Python | 1 day | 2/10 |
| Capability-based UI | React | 2-3 days | 3/10 |
| Agent selector | React | 1-2 days | 3/10 |
| command_output extraction | C++ | 1-2 days | 3/10 |
| Testing | Both | 2-3 days | 4/10 |
| **Total** | | **~2.5-3 weeks** | **3-4/10** |

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Two backends to maintain | AgentBackend interface is tiny (3 methods) — minimal contract surface |
| C++ event format drift | JSON event schema validated in unit tests on both sides |
| Feature gaps (RAG in C++ missing) | Capability system hides unsupported features; no broken UI states |
| Session history sync | Python backend owns history; passes to C++ via --history arg |
| C++ agent crashes | CppAgentBackend monitors subprocess, emits agent_error on crash |
| Cross-platform subprocess | C++ agents are Windows-only today; Linux support via CMake |

## Dependencies

- **PR #518** (C++ SSE Streaming) — Must be merged first. Provides `printStreamToken()` / `printStreamEnd()` and `StreamCallback` that `JsonEventOutputHandler` builds on.
- **WebUI base** (current `kalin/chat-ui` branch) — SSE event handling, AgentActivity component, chat store.
