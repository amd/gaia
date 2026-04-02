# Autonomous Agent Mode — Design Specification

**Branch:** `feature/agent-always-running` (branch off `feature/agent-memory`)
**Status:** Draft v2 — security-reviewed
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
| G2 | Agent proactively works on explicitly approved goals from memory |
| G3 | Agent can ask the user questions without halting all other activity |
| G4 | Agent can self-schedule future wake-ups ("remind me in 10 min") |
| G5 | User can disable autonomous mode from the Settings UI |
| G6 | All autonomous activity is visible, traceable, and auditable |
| G7 | System is safe: no destructive action without live user approval |
| G8 | Memory cannot be used as a prompt injection / command injection vector |
| G9 | Private sessions are never touched by the autonomous loop |

---

## 3. Agent Loop State Machine

```
  ┌─────────────────────────────────────────────────────────────────────┐
  │                           RUNNING                                    │
  │  (agent is actively executing goals / working on tasks)             │
  └──────────┬──────────────────────┬─────────────────────┬────────────┘
             │                      │                      │
     no approved     agent calls    agent calls      always_running
     goals remain    request_user   set_loop_state   → false
             │       _input()       ("scheduled",N)       │
             ▼             ▼              ▼                ▼
      ┌──────────┐  ┌─────────────┐  ┌───────────┐  ┌─────────┐
      │   IDLE   │  │WAITING_INPUT│  │ SCHEDULED │  │ PAUSED  │
      │ (sleeps  │  │ (blocks on  │  │ (sleeps   │  │ (no     │
      │ until    │  │  user reply │  │  until    │  │  ticks) │
      │ next     │  │  or timeout)│  │  target   │  │         │
      │ trigger) │  └──────┬──────┘  │  time)    │  └────┬────┘
      └────┬─────┘         │         └─────┬─────┘       │
           │     reply OR  │               │        always_running
     next  │     timeout + │         time  │         → true
    trigger│     continue  │         reached         │
           │               │               │              │
           └───────────────┴───────────────┴──────────────┘
                                   │
                                   ▼
                               RUNNING
```

### State Transitions

| From | Event | To |
|------|-------|----|
| `RUNNING` | `set_loop_state("idle")` | `IDLE` → sleep until trigger → `RUNNING` |
| `RUNNING` | `request_user_input()` called | `WAITING_INPUT` |
| `RUNNING` | `set_loop_state("scheduled", N)` | `SCHEDULED` → sleep N secs → `RUNNING` |
| `RUNNING` | `always_running` toggled off | `PAUSED` |
| `RUNNING` | step budget exhausted (see §7.3) | `IDLE` (forced, logged) |
| `WAITING_INPUT` | user responds | `RUNNING` |
| `WAITING_INPUT` | timeout + `continue_if_no_response=true` | `RUNNING` |
| `WAITING_INPUT` | timeout + `continue_if_no_response=false` | `PAUSED` |
| `IDLE` | user sends message | `RUNNING` |
| `IDLE` | tick interval fires | `RUNNING` (only if approved goals exist) |
| `SCHEDULED` | scheduled time reached | `RUNNING` |
| `PAUSED` | `always_running` toggled on | `RUNNING` |

**Important:** `IDLE` does not loop unconditionally on every tick. The loop
only fires a new run if there are approved, pending goals in memory
(category `goal` or `task`, `status=pending`, `approved_for_auto=true`).
If none exist, the tick checks once and immediately sleeps again. This
prevents idle LLM calls when the agent truly has nothing to do.

---

## 4. Event-Driven Trigger Queue (Not a Polling Timer)

The loop is **event-driven**, not tick-based. Triggers wake the agent:

```
Trigger sources:
  ├── user_message       — user sends a chat message (always fires)
  ├── scheduled          — agent called set_loop_state("scheduled", N)
  ├── idle_tick          — periodic check (only if pending goals exist)
  └── external_event     — Phase 2: file change, webhook, etc.
```

After each agent run completes:
1. Check if the run produced more approved work (agent created new goals)
2. If yes → immediately re-trigger (no sleep)
3. If no → go IDLE, sleep until next trigger

```python
async def _process_trigger(self, trigger: AgentTrigger):
    while True:
        result = await self._run_step(trigger)
        match result.directive:
            case "idle":
                break                         # nothing more to do
            case "scheduled":
                self.schedule_at(result.wake_at); break
            case "waiting_input":
                break                         # loop resumes via resolve_user_input
            case "continue":
                trigger = AgentTrigger("continuation", result)
                # immediately loop — no sleep needed
```

**Tick interval** (for idle_tick): 60 seconds (configurable via
`GAIA_AGENT_TICK_INTERVAL` env var). The tick only fires a run if
`db.count_pending_autonomous_goals() > 0`.

---

## 5. Key Components

### 5.1 `AgentLoop` (`src/gaia/ui/agent_loop.py`)

```
AgentLoop
├── start(db, app_state)     — launch background task
├── stop()                   — graceful shutdown
├── notify_user_message(sid) — called by chat router on every user msg
├── schedule_tick(delay)     — accelerate next idle-tick
├── submit_user_input(rid, r)— called by /api/chat/user-input endpoint
├── _trigger_queue           — asyncio.Queue of AgentTrigger
├── _loop()                  — main coroutine: drain queue → _process_trigger
└── _run_step(trigger)       — find session, acquire locks, run agent tick
```

**Active session selection:**
- Select most recently updated non-private session
- Skip sessions where `session.private = true` — **always, unconditionally**
- Skip sessions with no approved pending goals (avoids pointless LLM calls)

**Startup gate:** The loop does not process triggers until after the
initialized marker exists (`~/.gaia/chat/initialized`). On first launch,
no autonomous ticks fire until onboarding completes.

**Tunnel gate:** If `app.state.tunnel.active` is True, the loop is
**suspended** — no background ticks fire. The user may re-enable via an
explicit override (`GAIA_AUTONOMOUS_ALLOW_TUNNEL=1`). This prevents remote
code injection through the tunnel API.

### 5.2 `set_loop_state` Tool (replaces "respond with IDLE")

The agent signals its intent explicitly via a registered tool rather than
returning a magic string. This is reliable regardless of model size.

```python
@tool
def set_loop_state(
    self,
    state: str,          # "idle" | "scheduled" | "paused"
    reason: str = "",
    wake_in_seconds: int = 0,   # required when state="scheduled"
) -> str:
    """Signal the autonomous loop what to do next.

    Call this when you have finished all current work or need to pause.

    Args:
        state: "idle"      — nothing more to do right now; loop will check
                             again at the next tick interval.
               "scheduled" — wake me up in wake_in_seconds seconds.
               "paused"    — stop the autonomous loop (requires user action
                             to restart).
        reason: Human-readable explanation (shown in activity log).
        wake_in_seconds: Seconds until next wake-up (only for "scheduled").
                         Minimum: 30. Maximum: 86400 (24 hours).
    """
```

**Constraints enforced in the tool implementation:**
- `wake_in_seconds` floor: 30 seconds (prevents infinite loops)
- `wake_in_seconds` ceiling: 86400 seconds (prevents absurdly far scheduling)
- If `wake_in_seconds` is below floor, clamp to 30 and log a warning

### 5.3 `request_user_input` Tool

Allows the agent to pause and ask the user a question via the SSE stream.

```python
@tool
def request_user_input(
    self,
    message: str,
    choices: Optional[List[str]] = None,      # show buttons instead of text field
    default_if_no_response: Optional[str] = None,  # returned on timeout
    timeout_seconds: int = 300,
    continue_if_no_response: bool = True,
) -> str:
    """Ask the user a question and wait for their response.

    Args:
        message: The question to present to the user.
        choices: Optional list of choices (renders as buttons in the UI).
        default_if_no_response: Value to use if no response received before
            timeout. If not set and continue_if_no_response=True, returns
            the sentinel string "__NO_RESPONSE__". Callers must check for
            this value and handle it explicitly — never proceed blindly.
        timeout_seconds: How long to wait (min 10, default 300).
        continue_if_no_response: If True, continue working after timeout.
            If False, the loop pauses until user re-engages.

    Returns:
        User's response string, chosen option, or "__NO_RESPONSE__" if
        timed out and no default was provided. ALWAYS check the return value.
    """
```

**Return value contract:**
- User responded: returns their text or chosen option
- Timeout + `default_if_no_response` set: returns the default
- Timeout + no default + `continue_if_no_response=True`: returns `"__NO_RESPONSE__"`
- Never returns empty string `""` — this prevents silent blind-proceed bugs

**Callers must handle `"__NO_RESPONSE__"` explicitly:**
```python
response = self.request_user_input("Which folder?", default_if_no_response="~/Documents")
# response is always a real value here — default used on timeout
```
```python
response = self.request_user_input("Confirm deletion?", continue_if_no_response=True)
if response == "__NO_RESPONSE__":
    self.set_loop_state("idle", reason="Awaiting user confirmation before delete")
    return  # do NOT proceed
```

**Input request queue:** Multiple concurrent requests are supported.
`SSEOutputHandler` maintains an ordered `deque` of pending requests rather
than a single slot. Each request has a unique `request_id`. The frontend
renders all pending requests; each is resolved independently by `request_id`.

### 5.4 Background Mode: Destructive Tool Handling

When a tick runs in the background (no user actively watching), any tool
in `TOOLS_REQUIRING_CONFIRMATION` behaves differently:

| Context | Behavior |
|---------|----------|
| User-initiated (SSE open) | Show overlay, wait 60s, deny on timeout |
| Background tick (no active SSE) | **Immediately deny** — no waiting |

On immediate deny, the agent receives:
```
"Tool '[tool_name]' requires live user approval and cannot run unattended.
 Use request_user_input() to notify the user and ask them to approve,
 then retry in a subsequent turn."
```

This prevents the background loop from holding the semaphore for 60-second
stretches and prevents silent retry storms.

**How to detect background context:** `SSEOutputHandler` gains a
`background_mode: bool` flag, set to `True` by `AgentLoop._run_step()`.
`confirm_tool_execution()` checks this flag and short-circuits.

### 5.5 `SSEOutputHandler` Changes (`src/gaia/ui/sse_handler.py`)

```python
class SSEOutputHandler(OutputHandler):
    def __init__(self, background_mode: bool = False):
        ...
        self.background_mode = background_mode
        # User input request queue (ordered, multi-slot)
        self._user_input_queue: deque = deque()
        # Per-request events: request_id -> threading.Event
        self._user_input_events: dict = {}
        self._user_input_results: dict = {}

    def confirm_tool_execution(self, tool_name, tool_args, timeout=60) -> bool:
        if self.background_mode:
            # Immediate deny — no waiting, no semaphore hold
            self._emit({
                "type": "tool_confirm_denied",
                "tool": tool_name,
                "reason": "unattended",
                "message": f"'{tool_name}' requires live user approval.",
            })
            return False
        # ... existing blocking logic unchanged ...

    def request_user_input(self, message, choices=None,
                           default_if_no_response=None,
                           timeout=300, continue_if_no_response=True) -> str:
        ...  # uses _user_input_queue, not single-slot fields

    def resolve_user_input(self, request_id: str, response: str) -> bool:
        ...  # looks up by request_id in _user_input_events
```

### 5.6 Path Access in Background Mode

`PathValidator.is_path_allowed()` accepts a `prompt_user: bool` parameter.
`AgentLoop` **always calls with `prompt_user=False`**. This means:

- Out-of-scope path access is **denied silently** in background mode
- The agent receives `"Access denied: path outside allowed directories"`
- The agent can use `request_user_input` to ask the user to expand permissions

`PathValidator` also enforces: background mode (non-interactive) calls
**never** invoke `_prompt_user_for_access()`, which calls `input()` and
would deadlock the server thread (no stdin in background).

Additionally: background ticks **do not expand `allowed_paths`** at runtime.
The `[always]` option in the path prompt persists to disk —  this is too
powerful for unattended use. Path expansion is only permitted in interactive
(user-present) sessions.

---

## 6. Memory & Goals — Safe Autonomous Execution Model

### 6.1 New Memory Categories

```python
VALID_CATEGORIES: frozenset = frozenset({
    "fact", "preference", "error", "skill", "note",
    "reminder", "system", "profile",
    "goal",    # NEW — an objective the agent should work toward
    "task",    # NEW — a discrete, completable action item
})
```

### 6.2 Goal/Task Schema

Goals and tasks stored in memory carry additional metadata:

| Field | Type | Description |
|-------|------|-------------|
| `approved_for_auto` | bool | **Must be True** for autonomous execution. Default: False. |
| `status` | str | `pending` \| `in_progress` \| `done` \| `blocked` \| `cancelled` |
| `created_by` | str | `user` \| `agent` — who created this goal |
| `due_at` | ISO str | Optional deadline |

**The autonomous loop only executes goals where:**
1. `category in ("goal", "task")`
2. `approved_for_auto = True`
3. `status = "pending"`

Goals created by the agent itself (`created_by="agent"`) default to
`approved_for_auto=False`. They appear in the UI as suggestions the user
must approve before the agent will act on them. **An agent can never
autonomously execute a goal it created itself on the same tick.**

Goals created by the user via the memory API or chat (`created_by="user"`)
can be created with `approved_for_auto=True` directly.

### 6.3 Memory Injection Mitigation

The background tick prompt **does not say "act on whatever is in memory."**
It says:

```
You are running autonomously. Check for approved pending goals:
  memory_search(category="goal", status="pending", approved_for_auto=True)
  memory_search(category="task", status="pending", approved_for_auto=True)

Work on whatever you find. If there are no approved goals, call
set_loop_state("idle", reason="No approved pending goals").

For any destructive operation (delete, overwrite, shell command), you MUST
use request_user_input() to ask first — never proceed without confirmation.
```

This means:
- Injected `reminder` / `note` / `fact` entries are **never** treated as
  executable commands
- Only entries explicitly categorized as `goal` or `task` with
  `approved_for_auto=True` are acted upon
- Any `goal`/`task` created via the tunnel API is created with
  `approved_for_auto=False` by default and requires in-app approval

---

## 7. Session History — Background Ticks Are Not User Messages

### 7.1 New Message Role: `autonomous`

The database gains a fourth message role alongside `user`, `assistant`,
`system`:

```sql
-- messages.role CHECK constraint updated to include 'autonomous'
role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system', 'autonomous'))
```

Background tick prompts are stored with `role="autonomous"`:
- Included in conversation history sent to the LLM (the agent needs context)
- **Excluded** from the UI message list (user never sees raw tick prompts)
- Excluded from session exports
- Visible only in the Activity Log (§9)

The assistant's response to an autonomous tick is stored as `role="assistant"`
with an `activity_id` field linking it to the audit log entry. The UI can
optionally show these with a visual tag ("Agent acted autonomously").

### 7.2 Conversation History Injection

When building history pairs for the LLM, `_build_history_pairs()` includes
autonomous turns like this:

```python
# autonomous turns appear as [SYSTEM] prefixed user messages in context
{"role": "user", "content": "[AUTONOMOUS TICK] " + tick_prompt}
{"role": "assistant", "content": agent_response}
```

A rolling window cap applies: at most 5 recent autonomous turns are included
in history, preventing context window saturation from repeated ticks.

### 7.3 Step Budget and Rate Limiting

| Limit | Default | Config key |
|-------|---------|------------|
| Max steps per autonomous tick | 20 | `GAIA_AUTO_MAX_STEPS` |
| Max autonomous ticks per hour | 30 | `GAIA_AUTO_MAX_TICKS_PER_HOUR` |
| Max agent-created goals per day | 10 | `GAIA_AUTO_MAX_GOALS_PER_DAY` |
| Min schedule interval | 30s | hardcoded |
| Max schedule lookahead | 86400s | hardcoded |

When `max_steps` is reached mid-tick, the agent is forced to `IDLE` and a
warning is added to the activity log: "Tick exceeded step budget — partially
completed work may need review."

When the hourly rate limit is hit, all further ticks are suppressed and a
notification is shown in the UI: "Autonomous loop rate-limited — check
activity log."

---

## 8. Audit Log & Traceability (Phase 1 Requirement)

Every autonomous action must be traceable. This is **not** a Phase 3 nice-
to-have — it is a Phase 1 requirement. Users must be able to see exactly
what the agent did without asking, especially for anything affecting files
or memory.

### 8.1 Activity Log — SQLite Table

```sql
CREATE TABLE IF NOT EXISTS autonomous_activity (
    id          TEXT PRIMARY KEY,       -- UUID
    session_id  TEXT NOT NULL,
    trigger     TEXT NOT NULL,          -- "user_message"|"scheduled"|"idle_tick"
    trigger_goal_id TEXT,               -- memory entry ID that triggered this run
    started_at  TEXT NOT NULL,          -- ISO 8601
    ended_at    TEXT,
    outcome     TEXT,                   -- "completed"|"idle"|"scheduled"|"error"|"rate_limited"
    steps_taken INTEGER DEFAULT 0,
    tools_called TEXT,                  -- JSON array of tool names used
    files_read  TEXT,                   -- JSON array of file paths
    files_written TEXT,                 -- JSON array of file paths
    memory_read TEXT,                   -- JSON array of memory IDs queried
    memory_written TEXT,                -- JSON array of memory IDs created/updated
    user_input_requests TEXT,           -- JSON array of request_user_input calls
    error_message TEXT,
    message_id  INTEGER,                -- FK to messages table (agent's response)
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX idx_activity_session ON autonomous_activity(session_id);
CREATE INDEX idx_activity_started ON autonomous_activity(started_at DESC);
```

### 8.2 Activity REST API

```
GET  /api/agent/activity?session_id=&limit=20&offset=0
     → { activities: [...], total: N }

GET  /api/agent/activity/{activity_id}
     → full activity record

DELETE /api/agent/activity/{activity_id}
     → 204 (admin cleanup only)
```

### 8.3 Activity UI

The chat sidebar gains an "Activity" tab (or footer link). It shows:

```
┌─────────────────────────────────────────────────────┐
│  Agent Activity                                     │
│  ─────────────────────────────────────────────────  │
│  ✓ 14:32  Summarised Q1 report (3 tools, 8 steps)  │
│  ✓ 14:01  Set reminder for weekly review            │
│  ⏸ 13:55  Awaiting approval: delete ~/tmp/*.log    │
│  ✗ 13:20  Rate limited (30 ticks/hr reached)        │
│  ─────────────────────────────────────────────────  │
│  [View full log]                                    │
└─────────────────────────────────────────────────────┘
```

Expanding an entry shows: trigger, goal that was worked on, each tool call
with args and result summary, files touched, memory entries created.

---

## 9. HTTP Endpoint: User Input Response

```
POST /api/chat/user-input
{
  "session_id": "sess_abc",
  "request_id": "uuid4-from-sse-event",
  "response":   "~/Documents/Reports"
}
→ 200 { "status": "ok", "request_id": "..." }
→ 404 if no active SSE handler for session
→ 409 if request_id not found in the pending queue
```

---

## 10. Settings

| Key | Type | Default | Storage |
|-----|------|---------|---------|
| `always_running` | bool | `true` | SQLite settings table |

```
GET  /api/settings  → { "always_running": true, "custom_model": null, ... }
PUT  /api/settings  { "always_running": false }
```

Toggling `always_running` to `false` immediately transitions the loop to
`PAUSED` — any in-flight tick is allowed to complete its current step, then
stops.

---

## 11. Private Sessions

Sessions with `private=True` are **never touched by the autonomous loop**,
unconditionally. This applies to:
- Active session selection in `_get_active_session()` — skipped entirely
- The `notify_user_message()` trigger — if the session is private, no
  autonomous continuation fires after the agent responds
- The activity log — no entries for private sessions

This is enforced at the `AgentLoop` level, not just the settings level.
Even with `always_running=True`, private sessions are never autonomously
processed.

---

## 12. Tunnel Safety

When `app.state.tunnel.active` is `True`:

1. The `AgentLoop` suspends all idle-tick firing
2. User-message triggers still fire (the user is actively interacting)
3. Goals created via the tunnel API are created with `approved_for_auto=False`
   regardless of the request payload
4. To re-enable autonomous idle-ticks over the tunnel, the user must set
   `GAIA_AUTONOMOUS_ALLOW_TUNNEL=1` in their environment

---

## 13. User-Message → Autonomous Continuation

When a user sends a message and the agent responds, if `always_running=True`:

1. Chat router calls `agent_loop.notify_user_message(session_id)` after response
2. `AgentLoop` enqueues a `AgentTrigger("user_message_followup", session_id)`
3. On the next loop iteration, agent checks for approved pending goals
4. If goals exist → continues working autonomously
5. If no goals → immediately goes `IDLE` (no LLM call)

This means "always running" applies to the entire agent lifecycle, not just
background ticks. A user saying "summarise all my Q1 docs" gets a response,
then the agent immediately continues on the next goal without the user having
to send another message.

---

## 14. Time Awareness

The agent needs current time for scheduling. `system_context.py` (already
on the `feature/agent-memory` branch) injects a `system_context` block into
every autonomous tick's context:

```python
# Prepended to the autonomous tick prompt:
f"Current date/time: {datetime.now().isoformat()}\n"
f"Timezone: {tzlocal.get_localzone_name()}\n"
```

This allows the agent to reason: "the user asked me to check back at 3pm —
that's 2 hours 14 minutes from now → SCHEDULE:8040".

---

## 15. Visual Distinction: Autonomous vs User-Prompted Messages

Messages produced by autonomous ticks have an `activity_id` field in the DB.
The UI renders these with:
- A small "⚡ Autonomous" badge beneath the assistant message
- A link to the activity log entry ("3 tools · 8 steps")
- Slightly different background shade (configurable via CSS variable)

This ensures users always know whether the agent was responding to them or
acting on its own initiative.

---

## 16. Frontend Changes

### 16.1 Settings Modal — Agent Behavior Section

```
┌─────────────────────────────────────────────────────┐
│  Agent Behavior                                     │
│  ─────────────────────────────────────────────────  │
│  Always Running                          [ ON ]     │
│  Agent works on approved goals automatically.       │
│  Disable to respond only when you send a message.  │
└─────────────────────────────────────────────────────┘
```

### 16.2 User Input Request Card

```
┌─────────────────────────────────────────────────────┐
│  ⚡ Agent needs your input                          │
│  (Working on: "Summarise Q1 reports")               │
│                                                     │
│  "Which directory should I save the output?"       │
│                                                     │
│  [~/Documents]  [~/Desktop]  [Other...]            │  ← choices buttons
│   ── or ──                                         │
│  [___________________________]  [ Send ]            │
│                                                     │
│  ⏱ 4:32 remaining · will use default if no reply  │
└─────────────────────────────────────────────────────┘
```

- `choices` renders as buttons (fast selection, no typing needed)
- Timer countdown from `timeout_seconds`
- "will use default if no reply" only shown when `default_if_no_response` set
- Multiple cards stack if agent asks several questions in sequence
- Each resolves independently; resolved cards collapse with the user's answer

### 16.3 Permission Request Overlay (Autonomous Context)

When a destructive tool fires during autonomous execution, the overlay shows:

```
┌─────────────────────────────────────────────────────┐
│  ⚡ Agent Action — Autonomous                       │
│                                                     │
│  Working on: "Clean up ~/tmp directory"            │  ← goal context
│  Wants to run: run_shell_command                    │
│  Command: rm -rf ~/tmp/*.log                        │
│                                                     │
│  [ Allow ]    [ Deny ]    [ View Activity Log ]    │
│                                                     │
│  ⏱ 58s remaining                                   │
└─────────────────────────────────────────────────────┘
```

The `permission_request` SSE event gains two new fields:
- `triggered_by: str` — goal/task ID or "user_message"
- `task_context: str` — human-readable description of what the agent was doing

### 16.4 New Types

```typescript
// StreamEventType additions
| 'user_input_request'   // agent needs user input
| 'agent_loop_status'    // loop state changed (running/idle/scheduled/paused)
| 'tool_confirm_denied'  // tool denied immediately (background mode)

// StreamEvent additions
request_id?: string;
continue_if_no_response?: boolean;
choices?: string[];
default_if_no_response?: string;
// permission_request additions:
triggered_by?: string;
task_context?: string;

// UserInputRequest interface
interface UserInputRequest {
    requestId: string;
    message: string;
    choices?: string[];
    defaultIfNoResponse?: string;
    timeoutSeconds: number;
    continueIfNoResponse: boolean;
    expiresAt: number;   // Date.now() + timeoutSeconds * 1000
}
```

### 16.5 Chat Store Updates

```typescript
pendingUserInputRequests: UserInputRequest[];
addUserInputRequest:    (req: UserInputRequest) => void;
removeUserInputRequest: (requestId: string)     => void;
```

---

## 17. AgentLoop + Concurrency Controls

```python
async def _run_step(self, trigger: AgentTrigger, session: dict):
    semaphore = self._app_state.chat_semaphore
    session_locks = self._app_state.session_locks
    sid = session["id"]

    # Background ticks: skip if busy (don't queue)
    # User-message followup: wait up to 10s (user is actively present)
    timeout = 10.0 if trigger.source == "user_message_followup" else 3.0
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.debug("AgentLoop: server busy, skipping trigger %s", trigger.source)
        return

    try:
        session_lock = session_locks.setdefault(sid, asyncio.Lock())
        try:
            await asyncio.wait_for(session_lock.acquire(), timeout=2.0)
        except asyncio.TimeoutError:
            semaphore.release()
            return
        try:
            sse_handler = SSEOutputHandler(background_mode=True)
            await self._stream_autonomous_prompt(session, sse_handler)
        finally:
            session_lock.release()
    finally:
        semaphore.release()
```

---

## 18. Implementation Checklist

### Phase 1 — Foundation (branch: `feature/agent-always-running`)

**Memory / Data Layer:**
- [ ] Add `goal`, `task` to `VALID_CATEGORIES` in `memory_store.py`
- [ ] Add `approved_for_auto`, `status`, `created_by` fields to knowledge store
- [ ] Add `autonomous_activity` table to `ChatDatabase`
- [ ] Add `autonomous` to the message `role` constraint
- [ ] `_build_history_pairs()` rolling window cap for autonomous turns (max 5)

**Agent Loop:**
- [ ] `AgentLoop` class (`agent_loop.py`) — trigger queue, startup gate, tunnel gate
- [ ] `set_loop_state` tool registered on base `Agent`
- [ ] `request_user_input` tool registered on base `Agent` (with `choices`, `default_if_no_response`, `"__NO_RESPONSE__"` sentinel)
- [ ] `SSEOutputHandler.background_mode` flag + immediate deny for destructive tools
- [ ] `SSEOutputHandler` user input request queue (replace single slot with `deque`)
- [ ] `PathValidator`: enforce `prompt_user=False` in background mode (no `input()` calls)
- [ ] `AgentLoop._get_active_session()` skips private sessions unconditionally
- [ ] `set_loop_state` enforces `wake_in_seconds` floor (30s) and ceiling (86400s)
- [ ] Per-tick step budget (default 20), hourly rate limit (default 30)
- [ ] Daily agent-created goal limit (default 10)

**Server Integration:**
- [ ] `AgentLoop` started in `server.py` lifespan (after init gate check)
- [ ] `chat_router.py` calls `agent_loop.notify_user_message(sid)` after each response
- [ ] `POST /api/chat/user-input` endpoint (resolves by `request_id` in queue)

**Settings:**
- [ ] `always_running` in `SettingsResponse` + `SettingsUpdateRequest` (default True)
- [ ] GET/PUT `/api/settings` handles `always_running`

**Audit Log:**
- [ ] `GET /api/agent/activity` + `GET /api/agent/activity/{id}` endpoints
- [ ] Activity log written on every autonomous tick (start, tools, files, outcome)
- [ ] Messages from autonomous ticks store `activity_id`

**Frontend:**
- [ ] `always_running` toggle in SettingsModal ("Agent Behavior" section)
- [ ] `user_input_request` StreamEventType + fields (`choices`, `default_if_no_response`)
- [ ] `UserInputRequestCard` component (choices buttons, countdown, stacking)
- [ ] Chat store `pendingUserInputRequests` + add/remove actions
- [ ] Stream handler dispatches `user_input_request` to store
- [ ] `permission_request` overlay: show `triggered_by` + `task_context`
- [ ] Activity feed UI (sidebar tab or footer)
- [ ] "⚡ Autonomous" badge on assistant messages with `activity_id`
- [ ] `submitUserInput(sessionId, requestId, response)` in `api.ts`
- [ ] `agent_loop_status` event updates a loop-state indicator in the header

### Phase 2 — Polish

- [ ] Per-session `always_running` override
- [ ] Adaptive tick interval (faster when goals pending, slower when idle)
- [ ] Async `request_user_input` (non-blocking, inbox-based)
- [ ] Goal approval UI (approve/reject agent-suggested goals inline)
- [ ] "Agent paused" banner when `always_running=false`
- [ ] Configurable budgets via Settings UI

### Phase 3 — Advanced Autonomy

- [ ] External event triggers (file change, webhook)
- [ ] Multi-session loop (priority ordering)
- [ ] Push notifications for significant completions
- [ ] Cron-like scheduled tasks in memory

---

## 19. Security Summary

| Risk | Mitigation |
|------|-----------|
| `input()` deadlock in background | `prompt_user=False` enforced in all AgentLoop path checks |
| Tool confirm timeout (60s semaphore hold) | `background_mode=True` → immediate deny, no wait |
| Memory injection / command injection | Goals require `approved_for_auto=True`; injected reminders/notes never executed |
| Session history pollution | Tick prompts stored as `role="autonomous"`, hidden from UI |
| Unbounded LLM calls | Per-tick step budget + hourly rate limit + daily goal limit |
| No audit trail | `autonomous_activity` table is a Phase 1 requirement |
| PathValidator `[always]` expansion | Background mode never calls `_prompt_user_for_access()` |
| SCHEDULE:1 infinite loop | `wake_in_seconds` clamped to [30, 86400] |
| Tunnel → remote code injection | Loop suspended when tunnel active; tunnel goal API always creates with `approved_for_auto=False` |
| Multi-slot user input race | `deque`-based queue, resolved by `request_id` |
| No goal/task memory category | `"goal"`, `"task"` added to `VALID_CATEGORIES` |
| `allowed_paths` defaults to `~` | Background ticks enforce read-only unless explicit write scope |
| Empty-string blind proceed | Returns `"__NO_RESPONSE__"` sentinel; callers must check |
| Confirm overlay lacks context | `triggered_by` + `task_context` fields on `permission_request` event |
| Private session exposure | Private sessions unconditionally excluded from all loop operations |

---

## 20. Open Questions

1. **Autonomous turn history cap**: 5 recent turns seems right but needs
   empirical testing. Too few → agent loses context of what it just did.
   Too many → context window saturation for long-running autonomous sessions.

2. **Goal approval UX**: Where should unapproved agent-created goals appear?
   Options: inline in chat ("I'd like to also do X — approve?"), a dedicated
   "Pending Goals" panel, or a notification badge. Inline feels most natural
   but could be annoying if the agent suggests many goals.

3. **`always_running` default for new installations**: Starting with `true`
   on a clean install (no goals in memory) means the first few ticks fire,
   find nothing, go idle, and stop. That's low-cost and correct — but may
   surprise users who haven't opted in to autonomous mode. Consider:
   `always_running` defaults to `false` until after first-boot onboarding
   is complete, then auto-enables.

4. **Autonomous history in exports**: Should session exports include
   autonomous turns? They're part of the conversation context but were never
   visible in the UI. Proposal: include them in a separate appendix section
   of the export, clearly labelled.
