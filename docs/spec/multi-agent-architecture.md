# GAIA Multi-Agent Architecture

**Status:** Approved | **Date:** 2026-03-30 | **Milestones:** v0.19.0, v0.20.0

---

## 1. Problem

ChatAgent is a monolith: 35+ tools, 1,477-line system prompt, 35B model. Too slow for chat (<1s should be instant), too error-prone for tool calls (35-way classification), impossible to scale (every new tool degrades existing ones).

## 2. Solution

Split into **GaiaAgent** (personality + orchestration, 4B model) and **specialist agents** (focused tools, same 4B base with LoRA adapters). All agents share one model, one database, one UI.

| | Monolithic (today) | Multi-Agent |
|---|---|---|
| Chat speed | 2-5s (35B) | <1s (4B) |
| Tool accuracy | ~60% (35 tools) | ~90%+ (3-10 per specialist) |
| Memory | 20GB+ | ~8GB (shared 4B + LoRA) |
| Adding features | Bloats everything | New specialist, zero impact |

---

## 3. GaiaAgent

**Model:** Qwen3.5-4B | **Prompt:** ~100-200 lines | **Tools:** create_task, ask_agent, list_agents, spawn_agent

The user's conversational partner. Fun to talk to. Handles greetings, Q&A, follow-ups directly. Delegates tool-heavy work to specialists. Narrates progress as specialists work — not a loading spinner, a teammate giving updates.

GaiaAgent is NOT a router. It has personality, remembers the user, and maintains conversation continuity. The user talks to GaiaAgent, never directly to specialists.

### Live Progress Narration

```
User: "Prepare a summary of Q3 finances and check tomorrow's meetings"

GaiaAgent: "On it! Let me get that together for you."
  [DocAgent starts indexing Q3_report.pdf]
GaiaAgent: "I'm pulling up your Q3 report now..."
  [CalendarAgent queries tomorrow's agenda]
GaiaAgent: "Also checking your calendar for tomorrow."
  [DocAgent completes]
GaiaAgent: "Got the Q3 report. Revenue was $4.2M, up 12% from Q2..."
  [CalendarAgent completes]
GaiaAgent: "Your calendar tomorrow has 3 meetings including a Q3 review
at 2pm — perfect timing. Here's the full picture: [compiled summary]"
```

---

## 4. Specialist Agents

Each specialist: focused prompt (~100-200 lines), limited tools (3-10), no personality, same 4B base model with a domain-specific LoRA adapter.

### Platform Agents (ship with GAIA)

| Agent | Role |
|-------|------|
| **GaiaAgent** | Orchestrator, personality, user interaction |
| **CodeAgent** | Agent factory — builds new agents on demand |
| **DocAgent** | Document search, RAG Q&A, indexing |
| **FileAgent** | File system operations |
| **ShellAgent** | System commands, script execution |
| **WebAgent** | Web search, page fetching |

All share one Qwen3.5-4B base model loaded once (~4GB). LoRA adapters (~10MB each) swap in milliseconds.

### Use-Case Agents (built by CodeAgent)

Not pre-built. CodeAgent creates them when a user describes a need, using the full GAIA codebase as reference. A food truck in Austin gets different agents than a SaaS company in Delaware.

| Project | CodeAgent Builds |
|---------|-----------------|
| Start a business | FormationAgent, ComplianceAgent, FinanceAgent |
| Track investments | PortfolioAgent, NewsAgent, AlertAgent |
| Run a home | ThermostatAgent, SecurityAgent, EnergyAgent |

All use-case agents are fully autonomous — scheduled, self-directed, communicate freely.

---

## 5. Agent Communication

Agents communicate through **shared SQLite tables** — no protocol overhead. MCP stays for external tools and clients, not for agents talking to each other.

### Agent Tools

```python
create_task(title, assigned_agent, depends_on=[], context={})
    # Big work. Specialist owns it end-to-end. Async.
    # depends_on blocks until all listed tasks complete.

ask_agent(target_agent, question, timeout=60)
    # Quick question. Synchronous. "What's the cash balance?"

spawn_agent(agent_type, task, context={})
    # Start a new agent instance for a task.

list_agents()
    # What agents are available?
```

Two ways to work with agents: `create_task` for big jobs, `ask_agent` for quick questions. That's it.

### Task Lifecycle

```
created → blocked (waiting on depends_on) → pending → in_progress → completed / failed
```

All transitions emit SSE events — Agent UI updates in real time.

### Communication Patterns

1. **GaiaAgent → specialist** (most common): create task, specialist executes, GaiaAgent narrates result
2. **Specialist → specialist** (all-to-all): DocAgent asks FileAgent for a file directly. No bottleneck through GaiaAgent.
3. **Parallel fan-out**: GaiaAgent creates 3 tasks in parallel, 4th task depends on all three

---

## 6. Shared Memory

**One SQLite database** (`~/.gaia/memory.db`). Per-agent namespaces. Read any agent's memory. Write only to your own.

### Tables

```sql
agent_conversations (id, agent_id, role, content, context, created_at)
agent_tool_history  (id, agent_id, tool_name, tool_args, result, success, task_id, created_at)
agent_tasks         (id, agent_id, created_by, title, status, result, depends_on, created_at)
agent_insights      (id, agent_id, category, content, confidence, source, created_at)
user_profile        (key, value, source_agent, updated_at)  -- shared, any agent writes
```

### How Agents Use Memory

Agents don't carry conversation history in context. Everything important is stored as facts in memory. At task start, the agent loads:
1. System prompt
2. Current task description
3. Relevant memories (FTS5 search on task keywords)
4. Last few turns of active conversation

Context window doesn't grow with time — it grows with current task complexity. An agent running for 6 months uses the same context as one that started yesterday.

Nothing is summarized, compressed, or deleted. The database is unlimited. The context window is a search-driven view into it.

### Collective Intelligence

Agents build shared understanding by reading each other's insights:

```
DocAgent writes: "sales_report.pdf has poor OCR on pages 12-15"
  → GaiaAgent tells user "Heads up — OCR issues in that report"
  → FileAgent knows not to re-index that file

GaiaAgent writes: "User prefers concise responses"
  → All specialists read this and adjust output style
```

---

## 7. Agent UI

```
┌─────────────────────────────────────────────────────┐
│  GAIA Agent UI                              [+ New]  │
├───────────┬─────────────────────────────────────────┤
│           │                                         │
│  TASKS    │   ACTIVE TASK WINDOW                    │
│  ● Chat   │   [GaiaAgent] [DocAgent] [You]          │
│  ◐ Q3     │   Multi-participant conversation        │
│    Report  │   Tool calls inline, expandable         │
│  ◐ Calendar│   Inter-agent messages visible          │
│  ✓ Email  │                                         │
│           │                                         │
│  AGENTS   │                                         │
│  ● Gaia   │                                         │
│  ● Doc    │                                         │
│  ○ File   │                                         │
│  ○ Shell  │                                         │
│           │                                         │
└───────────┴─────────────────────────────────────────┘
```

The Agent UI becomes a multi-agent management platform:

- **Task sidebar** — all tasks with status (● active, ◐ working, ✓ done, ✗ failed), nested sub-tasks, which agent is assigned
- **Per-task conversation windows** — each task is its own thread. Multiple participants: user + GaiaAgent + specialists. Click between tasks like browser tabs.
- **Agent status panel** — all agents with current task, status, model info
- **User participation** — chat into any task, create tasks manually, pause/cancel/retry, provide corrections and feedback

### Observability — Everything Visible

Users must be able to trace exactly what agents are doing and how they're producing results:

- **Tool calls** — every tool call shown inline with expandable arguments and results. User sees "DocAgent called query_documents('Q3 revenue') → returned 3 chunks" not just the final answer.
- **Inter-agent messages** — when DocAgent asks FileAgent for a file, the request and response appear in the task thread. The user sees the collaboration happening.
- **Task dependencies** — visual indication of which tasks are blocked and what they're waiting for. "Set up bookkeeping" shows "waiting for: Get EIN ◐"
- **Agent reasoning** — when GaiaAgent decides to delegate, the user can see why: "This needs document analysis → sending to DocAgent"
- **Memory writes** — when an agent stores a new insight, it's visible: "ComplianceAgent learned: TX franchise tax due May 15"
- **Time and cost** — how long each task took, how many tokens used

**Why this matters:** Users won't trust agents they can't see. If GaiaAgent says "Revenue was $4.2M" but the user can't trace that back to a specific document query that returned a specific chunk, they'll assume it hallucinated. Full traceability builds trust.

### Approval Flow

Agents request approval through the task conversation naturally — not modal popups, but conversational asks:

```
ComplianceAgent: "Ready to file the LLC with Texas SOS. Filing fee: $300.
  Should I proceed?"
User: "Yes, go ahead"
ComplianceAgent: "Filing submitted. Confirmation #TX-2026-44821."
```

Every approval decision is logged in the task history for audit.

---

## 8. Error Handling

- **Validation before execution** — every tool call checked for valid name + args before running
- **Retry with feedback** — failed tool call returns error, agent retries with corrected args
- **Fallback to monolithic** — if multi-agent fails, fall back to existing ChatAgent (35B)
- **Kill criteria** — max 8 iterations per task, stuck detection after 3 identical retries
- **Deadlock detection** — circular task dependencies rejected at creation time
- **Semantic checkpointing** — scheduled agents persist state summaries, resume without replaying full history

---

## 9. Resources

All agents share one Qwen3.5-4B base model (~4GB). LoRA adapters (~10MB each) swap in milliseconds. CodeAgent may use a larger model (8B+) for code generation.

```
GaiaAgent (4B, NPU/GPU):     ~4GB  (always loaded)
LoRA adapters (6x ~10MB):    ~60MB
RAG embeddings:              ~500MB
Memory DB + overhead:        ~200MB
────────────────────────────────
Total:                       ~4.8GB (fits on any Ryzen AI PC)
```

Specialists load on demand. GaiaAgent masks cold start: "Let me bring in my document expert..."

---

## 10. Security

- **Safe** (no approval): read_file, search_file, query_documents, search_web
- **Needs user approval**: write_file, run_shell_command, send_email, install software, government filings, spending money
- **Domain guardrails**: baked into each agent's system prompt by CodeAgent ("Always include disclaimer that you're not a lawyer or CPA")

---

## 11. Dynamic Team Assembly

GaiaAgent interviews the user, reasons about what specialists are needed, checks the Agent Registry, and asks CodeAgent to build anything missing. No templates — the intelligence is in the agents.

Each specialist receives domain context at spawn time:
```python
spawn_agent("compliance_expert", task="Track tax obligations",
            context={"business": "Austin Bites LLC", "state": "TX", "entity": "llc"})
```

Teams share a workspace (`~/.gaia/teams/<name>/`) with per-agent owned files, RAG-indexed for cross-agent access.

### Adaptability

Context changes flow through memory. "I'm moving to California" → stored in user_profile → specialists read updated context on next task. Small changes = memory update. New capabilities = spawn new agent. Fundamental pivot = rebuild team.

---

## 12. Reliability

Small models have real limitations. Being honest:

| Risk | Mitigation |
|------|-----------|
| Hallucinated tool calls | Validate tool name + args before execution. Error feedback for retry. |
| Bad delegation | GaiaAgent has only 4 tools. Hard to pick wrong one. |
| Lost context | Memory-based, not history-based. Context doesn't grow with time. |
| Format errors | Constrained output: "respond EXACTLY like this: {json}" |
| Complete failure | Fall back to monolithic ChatAgent. Users always get a response. |

### Fallback Chain

```
Attempt 1: 4B specialist tries the task
  ↓ fails (wrong tool, bad args)
Attempt 2: Same model retries with error feedback
  ↓ fails again
Attempt 3: Escalate to larger model (8B+) for this task
  ↓ still fails (rare)
Fallback: GaiaAgent asks user for guidance in the task conversation
```

Most failures resolve at attempt 1-2. The user always gets a response — never a silent failure.

Fine-tuning (v0.19.0) closes the gap: research shows small fine-tuned models can match larger models on specific tool-calling tasks.

---

## 13. Known Limitations

| Limitation | Workaround |
|-----------|-----------|
| Single-user only | Per-user namespaces could be added later |
| No real-time event streams | Scheduled polling covers most cases |
| External auth is manual | User logs in via browser; OAuth could be added per-service |
| CodeAgent needs capable model | Runs on GPU, not NPU. Only GaiaAgent needs to be always-loaded. |
| Cold start for new specialists | GaiaAgent masks with conversational filler |

---

## 14. Issue Map

| Issue | Milestone | What |
|-------|-----------|------|
| #674 | v0.19.0 | GaiaAgent + specialist decomposition |
| #675 | v0.19.0 | Agent communication — shared state, tasks, spawning |
| #676 | v0.20.0 | Shared memory with per-agent namespaces |
| #677 | v0.20.0 | Agent UI management platform |
| #616 | v0.19.0 | System prompt compression for 4B model |
| #666 | v0.19.0 | Eval-to-training pipeline |
| #667 | v0.19.0 | Unsloth integration for LoRA fine-tuning |
| #668 | v0.19.0 | LoRA adapter library |
| #612 | v0.18.2 | Agent Registry |
| #542 | v0.20.0 | MemoryStore data layer |
| #543 | v0.20.0 | MemoryMixin agent integration |
