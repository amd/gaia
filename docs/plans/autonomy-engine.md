# Autonomy Engine -- Background Service, Heartbeat Scheduler & Event Hooks

> **Date:** 2026-04-01
>
> **Status:** Planning (0% implemented)
>
> **Milestone:** v0.23.0 -- Autonomous Agent Infrastructure
>
> **Prerequisites:** v0.20.0 Memory & Bootstrap must ship first (MemoryStore, MemoryMixin)
>
> **Related plans:**
> - [Agent UI](agent-ui.md) -- Phase C (autonomous infra)
> - [Messaging Integrations](messaging-integrations-plan.md) -- Messaging adapters depend on this service
> - [Security Model](security-model.md) -- Tool guardrails and dangerous mode

---

## 1. What We're Building

The Autonomy Engine is GAIA's always-on background service that turns the agent from a
reactive chat tool into a proactive assistant. It runs as a separate process from the
Electron UI or CLI, executing scheduled checks, responding to file system events, and
delivering desktop notifications -- all powered by local inference at zero API cost.

The engine has three pillars:

1. **Heartbeat Scheduler** -- cron-based recurring tasks that run cheap deterministic checks
   first and escalate to the LLM only when something interesting is found.
2. **Event Hooks** -- file system watchers, webhook receivers, and system event triggers
   that fire agent actions in response to real-world changes.
3. **Autonomous Loop** -- a think-act-schedule cycle where the agent can plan follow-up
   actions and schedule its own future executions.

**Design principle:** Cheap-first. Deterministic checks (file stat, git status, disk space)
cost zero LLM tokens. Only escalate to the 0.6B model for triage, and only invoke the full
30B model when the agent needs to take meaningful action.

---

## 2. GitHub Issue Cross-References

| Issue | Title | Component |
|-------|-------|-----------|
| #634 | Always-on agent: heartbeat, scheduler, and event hooks | Core engine |
| #550 | Scheduled Autonomy: recurring task scheduler for Agent UI | Scheduler |
| #555 | Autonomous mode: agent schedules follow-up messages | Autonomous loop |
| #557 | Autonomous loop: think-act-schedule cycle | Autonomous loop |
| #558 | Agent Activity feed: sidebar timeline | Activity feed |
| #559 | Dangerous mode: opt-in guardrail bypass | Guardrails |
| #560 | One-shot delayed execution: single-fire tasks | Scheduler |
| #643 | System tray app + background agent service | Background service |
| #415--#424 | Tray app: core agent, manager, marketplace, permissions, dashboard, terminal, notifications, interactive interface, auto-start, testing/CI | System tray |

---

## 3. Architecture Overview

```
+--------------------------------------------------+
|           GAIA Autonomy Engine (background proc)  |
|                                                   |
|  +---------------+   +------------------------+  |
|  | Heartbeat     |   | Event Hooks            |  |
|  | Scheduler     |   | (file system, webhooks)|  |
|  +-------+-------+   +------------+-----------+  |
|          +-------------+-----------+              |
|                        v                          |
|          +----------------------------+           |
|          | Task Executor              |           |
|          | Tier 0: deterministic      |           |
|          | Tier 1: triage (0.6B)      |           |
|          | Tier 2: action (30B)       |           |
|          +-------------+--------------+           |
|                        v                          |
|          +----------------------------+           |
|          | Session Manager            |           |
|          | (new sessions, never       |           |
|          |  interrupts active ones)   |           |
|          +-------------+--------------+           |
+------------------------+-------------------------+
                         |
        +----------------+----------------+
        v                v                v
  Notifications    Activity Log     Electron UI
  (desktop)        (SQLite)         (activity feed)
```

The engine runs as a **separate OS-level process** so the UI can be closed without stopping
background tasks, it can auto-start at boot, and crashes are isolated. Communication with
the UI uses a local HTTP API (same pattern as the MCP bridge on `:8765`).

**File layout:** `~/.gaia/heartbeat.yaml` (config), `~/.gaia/autonomy/` (PID file,
rotating log, SQLite activity DB, pending/results task directories).

---

## 4. Heartbeat Scheduler

### Configuration

The scheduler reads `~/.gaia/heartbeat.yaml`. Created with sensible defaults on first
enable, editable by hand or through the Settings UI.

```yaml
# ~/.gaia/heartbeat.yaml
enabled: false                    # Off by default -- user must opt in

triage_model: "Qwen3-0.6B-GGUF"
action_model: "Qwen3-Coder-30B-A3B-Instruct-GGUF"

notifications: true
max_concurrent_tasks: 2
quiet_hours:
  enabled: false
  start: "22:00"
  end: "07:00"

tasks:
  - name: "Project watcher"
    schedule: "*/30 * * * *"       # Every 30 minutes (cron syntax)
    enabled: true
    checks:
      - check_git_changes
      - check_new_files
      - summarize_if_needed        # Escalates to LLM only if changes found
    watch_paths:
      - "~/projects"

  - name: "System health"
    schedule: "0 9 * * 1-5"       # 9 AM weekdays
    enabled: true
    checks:
      - check_disk_space
      - check_watched_dirs
      - daily_summary

resource_limits:
  max_cpu_percent: 30
  defer_if_user_active: true
```

Cron expressions use standard five-field syntax, parsed by `croniter`.

### Built-in Checks

Phase 1 ships with deterministic (zero-token) checks: `check_git_changes` (git status/log
on watched repos), `check_new_files` (directory diff against last snapshot),
`check_disk_space` (partition usage), `check_watched_dirs` (file stat deltas). MCP-based
checks (`check_calendar`, `check_email`) are also zero-cost. LLM-backed checks
(`summarize_if_needed`, `daily_summary`, `generate_daily_brief`) only run when prior checks
find changes or on explicit schedule.

### Cheap-First Execution Pattern

Every task runs through a three-tier escalation:

- **Tier 0 -- Deterministic** (zero cost): Run checks. If nothing found, log "all clear"
  and stop. No LLM is loaded.
- **Tier 1 -- Triage** (Qwen3-0.6B, ~600MB, 50-200 tokens): Ask the cheap model "Is this
  worth notifying the user? YES/NO with a one-line reason." If NO, log and stop.
- **Tier 2 -- Action** (Qwen3-Coder-30B, ~4GB MoE/3B active, 500-5000 tokens): Create a
  new chat session with full context. Agent uses tools, generates summaries, drafts
  responses. Notify the user via desktop notification and activity feed.

On a quiet day with no changes, the heartbeat costs zero LLM tokens.

---

## 5. Autonomous Loop (Think-Act-Schedule)

The autonomous loop allows an agent to plan multi-step work across time. After completing
an action, the agent can schedule a follow-up check. Example workflows:

- "Monitor this PR and notify me when CI passes" (schedule check every 5 minutes)
- "Remind me about this in 2 hours"
- "Run this analysis overnight and have results ready by morning"

### Cycle: THINK -> ACT -> SCHEDULE -> COMPLETE

1. **Think:** Analyze trigger and findings, plan next action.
2. **Act:** Execute tools, write files, call APIs.
3. **Schedule:** Optionally create a follow-up task via `schedule_followup` tool.
4. **Complete:** Log result, notify if needed, yield.

The agent receives a structured planning prompt with the trigger type, findings summary,
and available tools. It responds with an assessment, actions to take, an optional
follow-up schedule, and whether to notify the user.

### Self-Scheduling Constraints

- Maximum 24-hour delay (longer recurrence belongs in `heartbeat.yaml`).
- Maximum 10 pending follow-ups at any time (prevents runaway scheduling).
- Each follow-up runs at most once (one-shot, not recurring).
- Follow-ups inherit the dangerous-mode setting of the creating session.

One-shot tasks ("do X at time Y") are persisted as JSON files in
`~/.gaia/autonomy/tasks/pending/` so they survive engine restarts.

---

## 6. Event Hooks

### File System Triggers

The engine uses `watchdog` (cross-platform) to monitor directories and fire agent actions.
Example: watch `~/Downloads` for new PDFs and auto-index into RAG, or watch `~/.ssh` for
any change and send an alert. Configured as `event_hooks` entries in `heartbeat.yaml`.

### Webhook Receiver

A localhost-only HTTP endpoint accepts inbound webhooks from external services (GitHub,
Slack, etc.). Payloads are validated (HMAC/token) before being passed to the agent.

### System Event Triggers

Platform-specific triggers (user login, network connected, USB attach, screen unlock)
using native APIs (Task Scheduler / WMI on Windows, systemd / D-Bus on Linux, launchd /
IOKit on macOS). Phase 1 supports user login and network connected; others are stretch
goals.

---

## 7. System Tray App

The system tray app is the user-facing control surface for all background processes,
integrated into the existing Electron app (`src/gaia/electron/`). It provides:

- **Status indicator** -- icon color reflects engine state (green = running, yellow =
  service error, red = stopped, blue pulsing = new findings, gray = quiet hours).
- **Quick actions** -- open GAIA Chat, pause/resume heartbeat, view activity log.
- **Running services** -- at-a-glance list of Lemonade Server, API Server, Autonomy
  Engine, and MCP Bridge with their status.
- **Settings and quit** -- access to autonomy configuration and clean shutdown.

The tray app uses Electron's `Tray` and `Menu` APIs for native context menus on all
platforms.

---

## 8. Desktop Notifications

Notifications use Electron's `Notification` API when the app is running, with a fallback
to platform-native notifications (`plyer` / `win10toast`) in headless/CLI mode. Types:
heartbeat findings, reminders, event hook alerts, follow-up results, and errors. Each
notification links back to the relevant activity entry or chat session.

During quiet hours, notifications are suppressed but tasks still execute and log.

---

## 9. Dangerous Mode and Safety

### Safe Mode (Default)

- Heartbeat checks run freely (they are read-only).
- Any action that modifies files, runs shell commands, or sends messages requires
  user confirmation via a desktop notification with Accept/Reject/Review buttons.
- The agent cannot self-schedule tasks that bypass confirmation.

### Dangerous Mode (Opt-In)

Dangerous mode removes the confirmation step. It is off by default, per-task configurable,
session-scoped, and all actions are prominently logged with a warning indicator.

```yaml
tasks:
  - name: "Auto-commit watcher"
    schedule: "*/15 * * * *"
    dangerous: true              # This task can modify files without confirmation
    checks:
      - check_git_changes
      - auto_commit_if_ready
```

For comprehensive security analysis, see [security-model.md](security-model.md).

---

## 10. Session Management

When a heartbeat escalates to Tier 2 (full agent action), it creates a **new chat session**
rather than injecting into an existing conversation. Key guarantees:

- **Non-interruption:** If the user has an active session, heartbeat results are queued
  and the user is notified. Messages are never injected into active conversations.
- **Session origin metadata:** Autonomous sessions carry metadata (`origin: "autonomy"`,
  trigger type, task name) so the UI can distinguish them.
- **Activity feed:** All autonomous activity is logged to the SQLite activity database
  and surfaced in the Electron UI sidebar as a timeline of cards.

---

## 11. Cross-Platform Background Service

| Platform | Auto-Start Mechanism | Service Manager |
|----------|---------------------|-----------------|
| Windows | Registry Run key or Task Scheduler "At log on" trigger | System tray process |
| Linux | systemd user service (`~/.config/systemd/user/gaia-autonomy.service`) | systemctl --user |
| macOS | launchd plist (`~/Library/LaunchAgents/ai.amd.gaia.autonomy.plist`) | launchctl |

The engine abstracts platform differences behind a `PlatformService` interface with
`install()`, `uninstall()`, `start()`, `stop()`, and `is_running()` methods, with concrete
implementations for each OS.

---

## 12. CLI Commands

```bash
# Engine lifecycle
gaia autonomy start / stop / status / restart

# Heartbeat management
gaia autonomy heartbeat list / run / pause / resume

# Activity log
gaia autonomy log [--all] [--json]

# One-shot tasks
gaia autonomy schedule "remind me to check PR" --delay 30m
gaia autonomy schedule "run disk check" --at "17:00"
gaia autonomy tasks
gaia autonomy cancel <task_id>
```

---

## 13. Key Design Decisions

1. **Separate process, not a thread.** The engine runs as its own OS process so the UI
   can be closed without stopping background work, and crashes are isolated.

2. **Cheap-first, not LLM-first.** Deterministic checks run before any model is loaded.
   Most heartbeats on quiet days cost zero tokens.

3. **New sessions, not injection.** Autonomous actions create new chat sessions rather
   than injecting into active conversations. Users are never surprised by messages
   appearing mid-conversation.

4. **Safe by default.** All write actions require user confirmation unless dangerous
   mode is explicitly enabled per-task.

5. **YAML config, not database.** Scheduler configuration lives in a human-editable
   YAML file, not in a database. This makes it version-controllable and inspectable.

6. **Local HTTP API for IPC.** The engine communicates with the UI via a local HTTP
   API, following the same pattern as the MCP bridge.

---

## 14. Open Questions

1. **Model preloading:** Should the triage model (0.6B) be kept warm in Lemonade at all
   times, or loaded on demand? Warm = faster heartbeats but ~600MB always used.

2. **Multi-agent heartbeats:** Should different heartbeat tasks be able to invoke different
   agents (e.g., CodeAgent for repo watching, GaiaAgent for email triage)?

3. **Memory integration:** How deeply should the heartbeat integrate with the v0.20.0
   Memory system? Options: (a) heartbeat findings written to daily memory automatically,
   (b) heartbeat can read memory for context, (c) both.

4. **Messaging adapter delivery:** Should heartbeat notifications be deliverable through
   messaging adapters (Telegram, Discord) in addition to desktop notifications?

5. **Rate limiting for external MCP calls:** If heartbeat checks invoke MCP servers that
   call external APIs (calendar, email), how do we rate-limit to avoid quota exhaustion?
