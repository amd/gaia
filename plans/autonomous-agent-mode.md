# Autonomous Agent Mode — Design Specification

**Branch:** `feature/agent-always-running` (branch off `feature/agent-memory`)  
**Status:** Draft  
**Date:** 2026-04-01

---

## 1. Overview

GAIA agents are fully autonomous agentic systems. By default they run
continuously, proactively working on goals, processing memory, and taking
initiative — not merely waiting for the user to send a message.

The agent stops (or pauses) only when it:
- Has nothing left to do right now (`IDLE`)
- Is waiting for user input (`WAITING_INPUT`)
- Has self-scheduled a future wake-up (`SCHEDULED`)
- Has been explicitly paused by the user or a setting (`PAUSED`)

A single setting `always_running` (default `true`) controls whether this
behavior is active. Disabling it switches the agent to manual (request/
response) mode.

---

## 2. Goals

| # | Goal |
|---|------|
| G1 | Agent loop is ON by default; no user action required to start it |
| G2 | Agent proactively checks memory, goals, and task queue each tick |
| G3 | Agent can ask the user questions without halting all work |
| G4 | Agent can self-schedule future wake-ups (e.g., "remind me in 10 min") |
| G5 | User can disable autonomous mode from the Settings UI |
| G6 | All autonomous activity is visible in the active chat session |
| G7 | System is safe: agent never runs destructive tools without confirmation |

---

## 3. Agent Loop State Machine

```
                    ┌───────────────────────────────────────────┐
                    │              RUNNING                       │
                    │  (default — agent is actively working)    │
                    └──────────┬─────────────┬──────────────────┘
                               │             │
               nothing to do   │             │ agent calls
               this tick       │             │ request_user_input()
                               ▼             ▼
                    ┌──────────────┐   ┌──────────────────┐
                    │    IDLE      │   │  WAITING_INPUT   │
                    │ (tick again  │   │  (blocking on    │
                    │  next cycle) │   │   user reply)    │
                    └──────────────┘   └────────┬─────────┘
                                                │
                              user replies OR   │
                              timeout + continue│
                                                ▼
                                       back to RUNNING

                    ┌──────────────────────────────────────┐
                    │           SCHEDULED                  │
                    │  agent returns "SCHEDULE:<secs>"     │
                    │  loop sleeps until that time         │
                    └──────────────────────────────────────┘

                    ┌──────────────────────────────────────┐
                    │            PAUSED                    │
                    │  always_running=false in settings    │
                    │  or user explicitly paused           │
                    └──────────────────────────────────────┘
```

### State Transitions

| From | Event | To |
|------|-------|----|
| `RUNNING` | agent answers "IDLE" on tick | `IDLE` → sleep → `RUNNING` |
| `RUNNING` | agent calls `request_user_input()` | `WAITING_INPUT` |
| `RUNNING` | agent returns `SCHEDULE:<N>` | `SCHEDULED` → sleep N secs → `RUNNING` |
| `RUNNING` | `always_running` set to `false` | `PAUSED` |
| `WAITING_INPUT` | user responds | `RUNNING` |
| `WAITING_INPUT` | timeout + `continue_if_no_response=true` | `RUNNING` |
| `WAITING_INPUT` | timeout + `continue_if_no_response=false` | `PAUSED` |
| `IDLE` | next tick interval | `RUNNING` |
| `SCHEDULED` | scheduled time reached | `RUNNING` |
| `PAUSED` | `always_running` set to `true` | `RUNNING` |

---

## 4. Key Components

### 4.1 `AgentLoop` (`src/gaia/ui/agent_loop.py`)

Background `asyncio.Task` launched on server startup.

```
AgentLoop
├── start()                 — launch background task
├── stop()                  — graceful shutdown
├── schedule_tick(delay)    — accelerate next wake-up
├── _loop()                 — main coroutine: sleep → check settings → tick
└── _tick()                 — find active session, run background prompt
```

**Tick interval:** 60 seconds (configurable via `GAIA_AGENT_TICK_INTERVAL` env var).

**Active session selection:** Most recently updated session in the DB.
Future: allow per-session loop enable/disable.

**Background prompt** (injected as user message):
```
You are running autonomously. Review your memory, goals, and pending tasks.
• If there is work to do: do it now using your tools.
• If you need to ask the user something: use the request_user_input tool.
• If there is a future task scheduled: respond with exactly SCHEDULE:<seconds>.
• If there is nothing to do right now: respond with exactly IDLE.
Do not explain — just act or signal.
```

The agent's response is streamed through the normal SSE pipeline so all
activity appears in the chat UI in real time.

### 4.2 `request_user_input` Tool

The most important new agent capability. Allows the agent to pause its
current work, present a question to the user, and wait for a response —
all without breaking the SSE stream.

**Signature:**
```python
def request_user_input(
    message: str,
    timeout_seconds: int = 300,     # 5 min default
    continue_if_no_response: bool = True,
) -> Optional[str]
```

**Flow:**
```
Agent thread                    SSE stream                 Browser
─────────────────────────────────────────────────────────────────
call request_user_input()   →   emit user_input_request   →  show input UI
    block (threading.Event)                                   user types...
                                                              POST /api/chat/user-input
                            ←   resolve_user_input(resp)
    unblock, return resp    →   emit status "continuing"   →  hide input UI
continue working...
```

**Timeout behavior:**
- If `continue_if_no_response=True` (default): agent unblocks after timeout
  and continues with `None` response. Agent should handle `None` gracefully
  (e.g., use a default value or skip the action).
- If `continue_if_no_response=False`: agent pauses loop after timeout;
  user must re-engage to restart.

**Async variant (future):** Agent fires off the request (without blocking)
and continues other work. The response is delivered to the agent's inbox
and processed at the next opportunity.

### 4.3 `SSEOutputHandler` Extensions (`src/gaia/ui/sse_handler.py`)

New methods mirroring the existing `confirm_tool_execution` pattern:

```python
request_user_input(message, timeout, continue_if_no_response) -> Optional[str]
resolve_user_input(response: str) -> bool
```

New SSE event emitted:
```json
{
  "type": "user_input_request",
  "request_id": "uuid4",
  "message": "Which directory should I save the report to?",
  "timeout_seconds": 300,
  "continue_if_no_response": true
}
```

### 4.4 HTTP Endpoint (`POST /api/chat/user-input`)

Mirrors the existing `/api/chat/confirm-tool` pattern.

```
POST /api/chat/user-input
{
  "session_id": "sess_abc",
  "request_id":  "uuid4-from-sse-event",
  "response":    "~/Documents/Reports"
}
→ 200 { "status": "ok", "request_id": "..." }
→ 404 if no active handler for session
→ 409 if request_id mismatch
```

### 4.5 Settings (`always_running`)

| Key | Type | Default | Storage |
|-----|------|---------|---------|
| `always_running` | bool | `true` | SQLite settings table |

**API:**
```
GET  /api/settings  → { "always_running": true, "custom_model": null, ... }
PUT  /api/settings  { "always_running": false }
```

**UI:** "Agent Behavior" section in SettingsModal with an ON/OFF toggle.
Toggle defaults to ON. Toggling calls `PUT /api/settings` immediately.

---

## 5. Frontend Changes

### 5.1 Settings Modal — Agent Behavior Section

```
┌─────────────────────────────────────────────────────┐
│  Agent Behavior                                     │
│  ─────────────────────────────────────────────────  │
│  Always Running                          [ ON ]     │
│  Agent runs continuously and autonomously.          │
│  Disable to respond only when you send a message.  │
└─────────────────────────────────────────────────────┘
```

### 5.2 User Input Request UI

When the agent emits `user_input_request`, the chat shows a special card:

```
┌─────────────────────────────────────────────────────┐
│  🤖 Agent needs your input                          │
│                                                     │
│  "Which directory should I save the report to?"    │
│                                                     │
│  [___________________________]  [ Send ]            │
│                                                     │
│  ⏱ 4:32 remaining  (or continue without response) │
└─────────────────────────────────────────────────────┘
```

- Shows countdown timer based on `timeout_seconds`
- On submit: calls `POST /api/chat/user-input`
- If timer expires and `continue_if_no_response=true`: card collapses with
  note "Agent continued without response"
- Keyboard: Enter submits, Escape dismisses (only if continue_if_no_response)

### 5.3 New Types

```typescript
// StreamEventType additions
| 'user_input_request'   // agent needs user input
| 'agent_loop_status'    // loop state changed (running/idle/scheduled/paused)

// StreamEvent additions
request_id?: string;              // for user_input_request
continue_if_no_response?: boolean;

// New top-level interface
interface UserInputRequest {
    requestId: string;
    message: string;
    timeoutSeconds: number;
    continueIfNoResponse: boolean;
    expiresAt: number;  // Date.now() + timeoutSeconds * 1000
}
```

### 5.4 Chat Store Updates

```typescript
// useChatStore additions
pendingUserInputRequests: UserInputRequest[];
addUserInputRequest: (req: UserInputRequest) => void;
removeUserInputRequest: (requestId: string) => void;
```

When the stream delivers a `user_input_request` event, add it to the store.
The `UserInputRequestCard` component subscribes and renders it.

---

## 6. Agent Tool Registration

The `request_user_input` tool is registered on the base `Agent` class and
available to all agent types. It is listed in the system prompt tool
description so the LLM knows it can use it.

```python
@tool
def request_user_input(
    self,
    message: str,
    timeout_seconds: int = 300,
    continue_if_no_response: bool = True,
) -> str:
    """Ask the user a question and wait for their response.

    Use this when you need information from the user to proceed.
    The agent will pause and wait for the user's reply.

    Args:
        message: The question to ask the user.
        timeout_seconds: How long to wait for a response (default: 300 = 5 min).
        continue_if_no_response: If True, continue working after timeout
            even without a response. If False, pause the agent loop.

    Returns:
        The user's response text, or empty string if no response received.
    """
    result = self._request_user_input(message, timeout_seconds, continue_if_no_response)
    return result or ""
```

---

## 7. AgentLoop + Chat Semaphore Integration

The `AgentLoop` background tick must respect the existing concurrency
controls to avoid corrupting session state:

```python
async def _run_background_tick(self, session):
    # Acquire the same semaphore as user-initiated messages
    # (but with a shorter timeout — skip if busy, don't queue)
    try:
        await asyncio.wait_for(chat_semaphore.acquire(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.debug("AgentLoop: server busy, skipping tick")
        return
    try:
        # Also acquire per-session lock
        session_lock = session_locks.setdefault(sid, asyncio.Lock())
        if not await asyncio.wait_for(session_lock.acquire(), timeout=2.0):
            return
        try:
            await self._stream_background_prompt(session)
        finally:
            session_lock.release()
    finally:
        chat_semaphore.release()
```

This ensures:
- Background ticks never interrupt user messages
- User messages never interrupt a background tick
- Skip (not queue) if system is busy — next tick will catch up

---

## 8. Memory Integration

The autonomous loop is most powerful when combined with the Memory system
(`feature/agent-memory`). On each tick, the agent can:

1. Query `memory_search("pending tasks")` to find open goals
2. Check `memory_search("scheduled")` for time-based reminders
3. Act on what it finds, storing results back in memory
4. Use `request_user_input` if it needs clarification

Example flow:
```
Tick fires →
  Agent: memory_search("pending tasks") → "Summarize Q1 report"
  Agent: read_file("q1_report.pdf") → [content]
  Agent: write_file("q1_summary.md") → done
  Agent: remember("Completed Q1 summary", category="completed_task")
  Agent: answer("I finished summarizing the Q1 report → q1_summary.md")
```

---

## 9. Implementation Checklist

### Phase 1 — Foundation (this branch: `feature/agent-always-running`)

- [x] `always_running` setting in backend (models.py, system.py router)
- [x] `always_running` toggle in SettingsModal
- [x] `loop_state` field on base `Agent` class
- [x] `SSEOutputHandler.request_user_input()` + `resolve_user_input()`
- [x] `POST /api/chat/user-input` endpoint
- [x] `submitUserInput()` in api.ts
- [x] `user_input_request` StreamEventType + fields in types/index.ts
- [ ] `AgentLoop` background task (`agent_loop.py`) — wired into server lifespan
- [ ] `request_user_input` registered as `@tool` on base Agent class
- [ ] `UserInputRequestCard` frontend component (shows question + timer + input)
- [ ] Chat store: `pendingUserInputRequests` state
- [ ] Stream handler: dispatch `user_input_request` events to store
- [ ] `agent_loop_status` SSE event + frontend indicator

### Phase 2 — Polish

- [ ] Per-session always_running override (session settings)
- [ ] `SCHEDULE:<seconds>` response parsing in AgentLoop
- [ ] Async `request_user_input` (non-blocking variant)
- [ ] Agent inbox for async responses
- [ ] Loop activity indicator in the sidebar/header
- [ ] "Agent paused" banner when `always_running=false`
- [ ] Configurable tick interval via env var / settings

### Phase 3 — Advanced Autonomy

- [ ] Goal/task queue in memory: agent adds tasks, loop processes them
- [ ] Multi-session loop (run across all active sessions in priority order)
- [ ] Agent-to-agent delegation (spawn sub-agents for parallel work)
- [ ] Scheduled tasks with cron-like syntax in memory
- [ ] Push notifications when agent completes something significant
- [ ] Loop history / audit trail in the UI

---

## 10. Security Considerations

- All tools that modify files/system still require `TOOLS_REQUIRING_CONFIRMATION`
  confirmation even in autonomous mode
- Background tick prompt is injected as a user message — it goes through
  the same safety pipeline as real user messages
- `request_user_input` timeout prevents the agent from holding up the
  server thread indefinitely
- `always_running=false` is a complete kill switch; all autonomous activity
  stops immediately
- The AgentLoop only operates on the most recently active session by default
  — it does not create new sessions or touch sessions the user hasn't opened

---

## 11. Open Questions

1. **Tick interval**: 60s default — is this too frequent? Too slow?
   Consider making it adaptive (faster when there's pending work, slower when idle).

2. **Background prompt injection**: Should the background tick show in the
   chat history as a "system" message, or be hidden from the user? Currently
   it would appear as a user message, which is confusing.
   Proposal: tag it with `role="system"` and hide it in the UI, but log it.

3. **Session selection**: What if the user has 5 sessions? Should the loop
   run on ALL of them, or just the most recently active one?
   Proposal: run on all sessions that have `autorun=true` (per-session flag,
   phase 2 feature).

4. **Async user input**: The blocking `request_user_input` is simple but
   prevents the agent from doing other work while waiting. A non-blocking
   "fire and forget" variant that delivers the response to the agent's inbox
   is more powerful but requires a task queue mechanism.

5. **Model compatibility**: The background tick prompt assumes the LLM
   understands "respond with exactly IDLE". Smaller models may not follow
   this reliably. We may need a structured output approach (JSON) instead.
