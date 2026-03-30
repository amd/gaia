# GAIA Multi-Agent Architecture

**Status:** Approved
**Date:** 2026-03-30
**Milestones:** v0.19.0 (Agent Decomposition), v0.20.0 (Memory + UI Platform)

---

## 1. Overview

### The Problem

The current ChatAgent is a monolith: 35+ tools, a 1,477-line system prompt, and a 35B model requirement. This creates three critical problems:

1. **Too slow for conversation.** "Hi, how are you?" goes through a 35B model with 35 tools in context. Response takes 2-5 seconds. Users expect instant chat.
2. **Too error-prone for tool use.** 35-tool classification is hard even for large models — tool selection accuracy is ~60%. Users lose trust when the agent picks the wrong tool.
3. **Impossible to scale.** Adding email, calendar, and home automation means 50+ tools in one prompt. Every new use case makes every existing use case worse.

### The Solution

GAIA evolves to a multi-agent system orchestrated by **GaiaAgent** — a lightweight, personality-driven agent running on a 4B model at NPU speed. Specialist agents handle tool-heavy work. All agents communicate freely through a shared MCP bus with full observability.

### Why This Matters

| Problem | Monolithic (today) | Multi-Agent (proposed) | User Impact |
|---------|-------------------|----------------------|-------------|
| Chat response time | 2-5s (35B model) | <1s (4B on NPU/GPU) | **Fast, natural conversation** |
| Tool accuracy | ~60% (35 tools) | ~90%+ (3-10 tools per specialist) | **Agent does the right thing** |
| Adding use cases | Bloats prompt, degrades all tasks | New specialist, zero impact on existing | **Platform gets better, never worse** |
| Memory usage | 35B model = 20GB+ | 4B shared base + LoRA adapters = ~8GB | **Runs on any Ryzen AI PC** |
| Cloud API cost | $600-$3,600/yr for 35B quality | $0 — all local, NPU-accelerated | **Zero ongoing cost** |

### Design Principles

1. **GaiaAgent is the user's agent.** Fun to talk to. Handles conversation directly. Delegates tool-heavy work to specialists. Not a cold router — a genuine conversational partner that happens to have a team of experts behind it.
2. **All-to-all communication.** Any agent can talk to any agent. Any agent can spawn any agent. Any agent can create tasks for any agent. No hub-and-spoke bottleneck — agents collaborate directly.
3. **Observable by default.** Every task, message, tool call, inter-agent exchange, and memory write is visible in the Agent UI. The user sees exactly what's happening. No hidden state, no black boxes.
4. **Shared memory, isolated writes.** One database, per-agent namespaces. Any agent can read any agent's memory. Agents only write to their own namespace. This prevents corruption while enabling collective intelligence.
5. **Scalable via specialists.** New use cases = new specialist agents. GaiaAgent discovers them via the registry and learns to delegate. The architecture grows horizontally, not vertically. The platform gets better with every new agent — it never degrades.

---

## 2. GaiaAgent

**Model:** Qwen3.5-4B on NPU/GPU (fast inference, <1s response)
**Role:** Orchestrator + chat + personality
**Prompt:** ~50-150 lines (vs 1,477 monolithic)
**Tools:** 4-5 delegation tools via Agent MCP Server

### Why a Dedicated Personality Agent?

Users don't want a tool-calling machine. They want an assistant that's **fun to talk to** and gets work done. By separating personality from tool execution:

- **Instant responses for 60-70% of interactions** — greetings, follow-ups, humor, and small talk don't need a 35B model or any tools. A 4B model on NPU handles them in milliseconds.
- **Consistent personality** — one model owns the voice. It doesn't change when switching between document analysis and calendar management.
- **Better tool accuracy** — specialists focus on their domain with 3-10 tools, not 35. Smaller decision space = fewer mistakes.
- **The user never sees the plumbing** — GaiaAgent weaves specialist results into natural conversation. The user talks to one agent, not a committee.

### What GaiaAgent IS

- The user's primary conversational partner — warm, fun, helpful
- A generalist that handles most interactions directly (greetings, Q&A, follow-ups, humor, small talk)
- A coordinator that gets work done by delegating to the right experts
- Where memory and personality live — knows user preferences, patterns, history
- A live narrator that reports progress as specialists work

### What GaiaAgent is NOT

- A cold intent classifier
- A mechanical dispatcher
- A thin wrapper around specialist calls

### Live Progress Narration

**Why:** Long-running tasks (document analysis, web research, multi-step workflows) take 10-60 seconds. Users stare at a loading spinner and wonder if something broke. GaiaAgent eliminates this by narrating progress in real-time — like a colleague keeping you posted.

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
GaiaAgent: "Your calendar tomorrow has 3 meetings including a Q3 review at 2pm — perfect timing."
GaiaAgent: "Here's the full picture: [compiled summary]"
```

Not a loading spinner — a teammate giving a play-by-play.

---

## 3. Specialist Agents

### Why Specialists Instead of One Big Agent?

**The monolithic agent fails for the same reason monolithic software fails** — coupling. When one system prompt handles RAG, file I/O, shell commands, web search, AND personality, every change affects everything. A prompt fix for RAG accuracy breaks shell command formatting. Adding web tools degrades file search accuracy.

Specialists solve this:
- **Isolation** — fixing DocAgent's RAG accuracy has zero impact on FileAgent
- **Right-sized models** — DocAgent needs good comprehension (1.7-4B). ShellAgent needs precise command formatting (1.7B). Neither needs personality (that's GaiaAgent's job).
- **Independent fine-tuning** — each specialist gets a domain-specific LoRA adapter. DocAgent is fine-tuned on document Q&A. FileAgent is fine-tuned on file operations. No cross-contamination.
- **Testable in isolation** — eval scenarios can target individual specialists without testing the whole system

Each specialist has:
- **Focused prompt** (~100-200 lines, only its domain)
- **Limited tool set** (3-10 tools, only its specialty)
- **No personality** — returns structured results to GaiaAgent
- **Runs on 1.7-4B model** — optimized for tool calling accuracy
- **Domain-specific LoRA adapter** — fine-tuned for its vertical
- **Own memory namespace** — tracks its conversations, tool calls, tasks, and insights

### Platform Agents (ship with GAIA)

These foundational agents enable everything else. They ship with GAIA and are always available:

| Agent | Model | Role |
|-------|-------|------|
| **GaiaAgent** | 4B (NPU/GPU) | Orchestrator, personality, user interaction — the face of GAIA |
| **CodeAgent** | 4B+ (GPU) | Agent factory — builds new agents for any use case on demand |
| **DocAgent** | 4B (shared) | Document search, RAG Q&A, indexing — core capability used by all workflows |
| **FileAgent** | 4B (shared) | File system operations — agents need to read/write files |
| **ShellAgent** | 4B (shared) | System commands, script execution — agents need to interact with the OS |
| **WebAgent** | 4B (shared) | Web search, page fetching — agents need internet access |

All platform agents share the same Qwen3.5-4B base model with different LoRA adapters. One model loaded, all agents served.

### Use-Case Agents (built by CodeAgent on demand)

These are NOT pre-built. CodeAgent creates them when a user describes a project or use case, tailored to the specific situation. All use-case agents are **fully autonomous** — they run on schedules, communicate freely with other agents, and work independently.

| Project | Agents CodeAgent Builds | Each Agent Is Autonomous |
|---------|------------------------|------------------------|
| **Start a business** | FormationAgent, ComplianceAgent, FinanceAgent, PermitAgent | ComplianceAgent checks deadlines weekly. FinanceAgent reconciles daily. |
| **Manage investments** | PortfolioAgent, NewsAgent, AlertAgent | NewsAgent monitors feeds continuously. AlertAgent fires on price targets. |
| **Run a home** | ThermostatAgent, SecurityAgent, EnergyAgent | SecurityAgent monitors 24/7. EnergyAgent optimizes hourly. |
| **Dev team** | CIAgent, ReviewAgent, DeployAgent | CIAgent watches for failures. DeployAgent handles rollouts. |
| **Research project** | LitReviewAgent, DataAgent, WritingAgent | LitReviewAgent crawls papers. DataAgent processes results. |

**Every team is unique.** Different users get different agents with different tools, different schedules, and different context. The platform grows with the user's needs.

### CodeAgent — The Agent Factory

| Agent | Focus | Why It's Critical |
|-------|-------|-------------------|
| **CodeAgent** | Build purpose-built agents for any use case | CodeAgent is the **agent factory**. When a user describes what they need, GaiaAgent reasons about the problem and CodeAgent builds an entire team of autonomous agents tailored to that specific situation. No pre-built specialists needed — CodeAgent creates them fresh. |

**Why this changes everything:** Traditional platforms ship with a fixed set of agents (email agent, calendar agent, etc.). GAIA ships with GaiaAgent + CodeAgent. Everything else is built on demand, tailored to the specific user's situation. A food truck in Austin gets different agents than a SaaS company in Delaware — not because different templates exist, but because CodeAgent builds different agents.

CodeAgent has access to:
- **Full GAIA codebase** — `src/gaia/` as RAG-indexed context. Understands the agent base class, tool decorator, MCP integration, and all existing patterns.
- **GAIA documentation** — `docs/` including SDK reference, playbooks, and this architecture spec.
- **All existing agents** — reads their code as reference patterns for building new ones.
- **Test framework** — writes and runs tests for new agents.
- **Agent MCP Server** — registers new agents, creates initial tasks, wires up communication.

**Example — Small Business Team:**
```
User: "I want to start a food truck in Austin"

GaiaAgent interviews → understands the need → asks CodeAgent to build the team

CodeAgent builds:
  1. FormationAgent — tailored for Texas LLC filing, Austin-specific requirements
  2. ComplianceAgent — tailored for TX franchise tax, food service permits
  3. FinanceAgent — tailored for food truck economics, Square POS integration
  4. PermitAgent — tailored for Austin health department, food handler's cert

Each agent:
  - Has its own focused system prompt with business context baked in
  - Has only the tools relevant to its job
  - Registers in Agent Registry
  - Gets initial tasks with dependencies
  - Runs autonomously on a schedule
  - Communicates freely with all other agents via MCP
```

**Example — Stock Portfolio:**
```
User: "I need help tracking my investments"

CodeAgent builds:
  1. PortfolioAgent — fetches prices, tracks positions, calculates P&L
  2. NewsAgent — monitors financial news for held positions
  3. AlertAgent — sends notifications on price targets, earnings dates
```

**Every team is unique.** Different users get different agents with different tools, different schedules, and different context. The platform grows with the user's needs, not with templates maintained by developers.

### Model Sharing Across Specialists

**Specialists reuse LLMs — they don't each need their own model.** Multiple specialists can run on the same base model with different LoRA adapters:

```
Lemonade Server
  └── Qwen3-1.7B-GGUF (loaded once, ~2GB)
       ├── DocAgent adapter    (10MB)  — document Q&A patterns
       ├── FileAgent adapter   (10MB)  — file operation patterns
       ├── ShellAgent adapter  (10MB)  — command formatting
       └── WebAgent adapter    (10MB)  — search query patterns

  └── Qwen3-4B-GGUF (loaded once, ~4GB)
       ├── CodeAgent adapter   (10MB)  — code generation patterns
       └── EmailAgent adapter  (10MB)  — email triage patterns
```

**Why this matters:**
- **Memory efficiency:** 4 specialists sharing one 1.7B base = ~2.04GB. Without sharing = ~8GB.
- **Faster switching:** LoRA adapter swap takes milliseconds. Full model load takes seconds.
- **Not all specialists need the same model:** CodeAgent needs a stronger model (4B) for code generation. DocAgent works fine on 1.7B for document Q&A. The right model for the right job.
- **User's choice:** Power users with 128GB Strix Halo can load larger models per specialist. Laptop users with 16GB share a single base model across all specialists.

---

## 4. Agent Communication — Shared State, Not Protocols

**GitHub Issue:** #675

### Why Not MCP for Agent-to-Agent?

MCP is designed for agent-to-tool communication, not agent-to-agent. Google's A2A protocol exists for cross-vendor agent interop, but it's designed for agents across organizational boundaries with OAuth and discovery — massive overkill for agents in the same process.

**What we actually need is simpler:** agents share state through a common database and the system emits events for the UI. No protocol overhead. No serialization. Just shared SQLite tables + SSE for observability.

**MCP stays for external communication** — connecting GAIA to third-party tools (Playwright, Home Assistant), exposing GAIA to external clients (Claude Code, IDE extensions, webapp embeds). But agents talking to each other inside GAIA? Shared state is simpler, faster, and works naturally with small models.

### Agent Tools (Python functions, not MCP)

Agents get two tools for working with other agents. These are regular Python `@tool` functions that read/write the shared task table — no protocol overhead:

```python
@tool
def create_task(title, assigned_agent, depends_on=[], context={})
    # Writes a row to the tasks table. Any agent can assign work to any agent.
    # depends_on: list of task_ids — task stays blocked until all complete.
    # Returns task_id. Assigned agent picks it up on next run or is woken immediately.

@tool
def ask_agent(target_agent, question, timeout_seconds=60)
    # Quick synchronous question. Wakes the target agent, waits for answer.
    # "What's the cash balance?" "Is the EIN ready?"
    # For fast answers where full task creation is overkill.
```

**Two tools. Small models can easily distinguish "big job" from "quick question."** No `send_to_agent`, no `request_and_wait`, no `use_agent_as_tool` — those are the same concept with different names. Simplify.

### How It Works Under the Hood

```
Shared SQLite (tasks table):
  Agent A calls create_task(assigned=Agent B) → row inserted
  Agent B checks for pending tasks → picks it up → status = in_progress
  Agent B completes → status = completed, result filled
  Agent A reads result (or GaiaAgent narrates it to user)

  Dependencies:
  Task C depends_on [Task A, Task B] → stays blocked
  Task A completes → system checks → Task B still pending → Task C stays blocked
  Task B completes → system checks → all deps met → Task C → pending

  SSE events emitted on every state change → Agent UI updates in real time
```

No MCP serialization. No protocol negotiation. Just database writes and reads. Fast, debuggable, works with any model size.

### Agent Discovery

Agents register in the Agent Registry (#612) on startup. GaiaAgent queries the registry to know what's available:

```python
@tool
def list_agents()
    # Returns all registered agents with capabilities and status

@tool
def spawn_agent(agent_type, task, context={})
    # Start a new agent instance. Creates a task and wakes the agent.
    # Returns task_id. Agent runs asynchronously.
```

### Task Lifecycle

```
created → blocked (waiting on depends_on)
       → pending (dependencies met, waiting for agent pickup)
       → in_progress (agent working)
       → completed (result available)
       → failed (error, available for retry)
```

All transitions emit SSE events → visible in Agent UI in real time.

### Task Dependencies

**Why dependencies matter:** Real workflows have ordering constraints. "Summarize Q3 report with org chart context" requires the report AND the org chart to be loaded first. Without dependency management, agents would race, fail, and retry — wasting time and tokens.

Tasks can declare dependencies. Blocked tasks auto-transition to pending when dependencies complete:

```python
task_1 = create_task("Index Q3 report", agent="doc_expert")
task_2 = create_task("Read org chart", agent="file_expert")
task_3 = create_task("Summarize with org context", agent="doc_expert",
                      depends_on=[task_1, task_2])
# task_3 waits for BOTH task_1 and task_2 before starting
```

### Communication Patterns

#### 1. GaiaAgent delegates to specialist
User asks → GaiaAgent creates task → specialist executes → GaiaAgent narrates result.
**Most common pattern.** 70% of delegations follow this path.

#### 2. Specialist delegates to specialist (all-to-all)
DocAgent needs file content → sends request_and_wait to FileAgent → gets result → continues.
**Why all-to-all is critical:** Without it, every cross-domain request must bounce through GaiaAgent, adding latency and losing context. DocAgent knows exactly what file it needs — it should ask FileAgent directly.

#### 3. Parallel fan-out with dependency join
GaiaAgent creates 3 tasks in parallel → creates 4th task with depends_on all three → result compiled after all complete.
**Key performance optimization.** "Prepare morning brief" touches email + calendar + news. Sequential = 30s. Parallel = 10s.

#### 4. Agent spawning
Any agent can spawn a new agent instance for a task. The spawned agent runs independently and posts results back via MCP.

---

## 5. Shared Memory — Per-Agent Namespaces

**Database:** `~/.gaia/memory.db` (single SQLite, WAL mode)
**GitHub Issue:** #676

### Why Shared Memory?

**Agents that don't share memory are a team that doesn't communicate.** If DocAgent learns that a PDF has poor OCR quality, but FileAgent doesn't know this, FileAgent will try to re-index it and fail. If GaiaAgent learns the user prefers concise responses, but specialists don't know, they'll produce verbose output that GaiaAgent has to truncate.

Shared memory enables **collective intelligence** — each agent's experience benefits every other agent.

### Why Isolated Writes?

**Preventing corruption.** If any agent could write to any namespace, a misbehaving specialist could overwrite GaiaAgent's user preferences or another specialist's learned insights. Isolated writes + shared reads gives the best of both worlds: collaboration without corruption.

### Access Rules

- **Read:** Any agent can read any agent's memory
- **Write:** Agents can ONLY write to their own namespace
- **Shared tables:** user_profile, agent_directory (read/write by any agent, source tracked)

### Per-Agent Tables

```sql
-- Each agent's conversations
agent_conversations (id, agent_id, session_id, role, content, context, created_at)

-- Each agent's tool usage
agent_tool_history (id, agent_id, tool_name, tool_args, result, success, duration_ms, task_id, created_at)

-- Each agent's task tracking
agent_tasks (id, agent_id, created_by, title, status, context, result, depends_on, created_at, completed_at)

-- Each agent's learned insights
agent_insights (id, agent_id, category, content, confidence, source, context, created_at, last_accessed_at)
```

### Shared Tables

```sql
-- User profile (any agent writes, source tracked)
user_profile (key, value, source_agent, confidence, updated_at)

-- Agent directory (syncs with Agent Registry)
agent_directory (agent_id, display_name, capabilities, model, status, last_active_at)
```

### Memory Updates

When an agent writes an important fact (EIN obtained, deadline discovered, task completed), GaiaAgent sees it on the next interaction and can narrate it to the user. Agents read relevant memories at the start of each task — no complex subscription system needed.

Critical facts are stored as simple key-value pairs in the agent's memory namespace:
```
FormationAgent stores: {category: "fact", key: "ein", value: "12-3456789"}
ComplianceAgent reads it on next run: recall(agent_id="formation", category="fact")
```

Simple, queryable, no parsing required.

### Scaling Memory Over Time

As agents work over weeks and months, memory grows. An agent that's processed hundreds of tasks has thousands of insights, tool calls, and conversation turns. This is a feature — not a problem.

**Memory IS the context. No summarization needed.**

The key insight: agents don't carry conversation history in their context window. Everything important is already stored as discrete facts in memory. The context window holds:

1. **System prompt** (~200 lines)
2. **Current task** (what am I doing right now)
3. **Retrieved memories** (searched via FTS5, relevant to THIS task)
4. **Current conversation** (last few turns of the active task)

This means the context window doesn't grow with time — it grows with the *current task's complexity*. An agent that's been running for 6 months uses the same amount of context as one that started yesterday, because it retrieves only what's relevant.

**How memory stays useful as it scales:**

- **FTS5 search** — agents search memory by keywords and categories, not scan linearly. "What do I know about tax deadlines?" returns relevant insights instantly, even from thousands of entries.
- **Recency weighting** — recent memories are ranked higher in search results. An EIN obtained yesterday surfaces before a filing date researched 6 months ago.
- **No information loss** — nothing is summarized, compressed, or deleted. Every fact, every tool call, every conversation turn is preserved in full. The database grows, not the context window.
- **Per-task retrieval** — at the start of each task, the agent searches memory for relevant context: `recall(query="tax deadlines Texas LLC")`. Only matching results enter the context window. Irrelevant history stays in the database, retrievable if needed later.

This is how a 16K context window handles months of accumulated knowledge without summarization — the database is unlimited, the context window is a focused view into it.

### Collective Intelligence in Practice

**Why this is the killer feature for local AI:** Cloud agents can't share behavioral data without transmitting it to third-party servers. GAIA's memory is entirely local — agents build a deeply personalized, private understanding of the user that improves with every interaction.

```
DocAgent writes: "sales_report.pdf has poor OCR quality on pages 12-15"
  → GaiaAgent reads this → tells user "Heads up — some pages in that report had OCR issues"
  → FileAgent reads this → skips re-indexing that file

FileAgent writes: "User frequently accesses ~/Work/gaia4/"
  → GaiaAgent reads this → suggests indexing that directory
  → ShellAgent reads this → uses as default working directory

GaiaAgent writes: "User prefers concise responses"
  → All specialists read this → adjust their output style

EmailAgent writes: "User's most urgent sender is boss@company.com"
  → GaiaAgent reads this → prioritizes notifications from that sender
  → CalendarAgent reads this → highlights meetings with that person
```

The longer the system runs, the more valuable it becomes. This creates hardware retention that cloud subscriptions cannot match.

---

## 6. Agent UI — Multi-Agent Management Platform

**GitHub Issue:** #677

### Why Evolve the UI?

The current Agent UI is a single-agent chat window. With multiple agents working on multiple tasks, users need:
- **Visibility** into what every agent is doing (not just GaiaAgent)
- **Control** to create, pause, cancel, and retry tasks
- **Context** to understand how agents collaborate (inter-agent messages, dependencies)

Without this, the multi-agent system is a black box. Users won't trust agents they can't see.

### Layout

```
┌─────────────────────────────────────────────────────┐
│  GAIA Agent UI                              [+ New]  │
├───────────┬─────────────────────────────────────────┤
│           │                                         │
│  TASKS    │   ACTIVE TASK WINDOW                    │
│           │                                         │
│  ● Chat   │   Multi-participant conversation        │
│    with    │   [GaiaAgent] [DocAgent] [You]          │
│    Kalin   │   Tool calls inline, expandable         │
│           │   Inter-agent messages visible           │
│  ◐ Q3     │                                         │
│    Report  │                                         │
│    (Doc)   │                                         │
│           │                                         │
│  ◐ Calendar│                                         │
│    (Cal)   │                                         │
│           │                                         │
│  ✓ Email  │                                         │
│    Digest  │                                         │
│    (Email) │                                         │
│           │                                         │
│  AGENTS   │                                         │
│  ● Gaia   │                                         │
│  ● Doc    │                                         │
│  ○ File   │                                         │
│  ○ Shell  │                                         │
│  ○ Web    │                                         │
│           │                                         │
└───────────┴─────────────────────────────────────────┘
```

### Key Features

1. **Task sidebar** — All tasks with status (● active, ◐ in progress, ✓ done, ✗ failed). Sub-tasks nested under parents. Each task shows which agent is assigned.
2. **Per-task conversation windows** — Each task is its own conversation thread. Multiple participants: user + GaiaAgent + specialist agents. Click between tasks like browser tabs.
3. **Agent status panel** — All agents with current task, status (active/idle/unavailable), memory stats, model info, device (NPU/GPU).
4. **Full observability** — Tool calls with expandable args/results, inter-agent messages, memory writes, task dependency visualization. Nothing hidden.
5. **User participation and feedback** — The Agent UI is not a read-only dashboard. It is the primary way users interact with the multi-agent system:
   - **Chat into any task** — send messages, corrections, additional context to any agent working on any task
   - **Provide feedback** — "That's wrong, the deadline is April 15 not March 15" → agent updates its memory
   - **Approve actions** — agents that need human input (filing, spending, sending) request approval through the task conversation. User approves or denies in-line.
   - **Create tasks** — user can directly assign work to any agent
   - **Pause/cancel/retry** — full control over agent execution
   - **Course correct** — "Stop working on marketing, focus on compliance first" → GaiaAgent reprioritizes the team

### Human-in-the-Loop

**Agents are autonomous but not unsupervised.** The Agent UI ensures the user is always in control:

- **Agents request input when they need it.** "I found two possible filing addresses — which one should I use?" appears in the task conversation. The agent waits for the user's response before proceeding.
- **User feedback improves agents.** When the user corrects an agent, the correction is stored in memory. The agent learns and doesn't repeat the mistake.
- **Approval gates are conversational.** Instead of a modal popup, the agent asks naturally in the task thread: "Ready to file the LLC with Texas SOS for $300. Should I proceed?" The user responds in the conversation.
- **GaiaAgent mediates when needed.** If the user provides conflicting instructions to two agents, GaiaAgent resolves the conflict and communicates the decision.
- **Preference learning.** When the user corrects an agent ("No, the deadline is April 15, not March 15"), the correction is stored in that agent's memory. The agent reads its past corrections at the start of each task and doesn't repeat mistakes.

### SSE Events

```
task_created       — new task in sidebar
task_updated       — status change (blocked → pending → in_progress → completed)
task_message       — new message in task conversation
agent_status       — agent active/idle/unavailable
agent_insight      — agent stored new insight (observable)
inter_agent_msg    — agent-to-agent message (visible in task window)
```

---

## 7. Issue Map

| Issue | Milestone | Layer | Value |
|-------|-----------|-------|-------|
| #674 | v0.19.0 | GaiaAgent + specialist decomposition | Instant chat + 90%+ tool accuracy + scalable architecture |
| #675 | v0.19.0 | Agent MCP Server — tasks, spawning, all-to-all, dependencies | Agents collaborate freely, everything observable |
| #676 | v0.20.0 | Shared memory — per-agent namespaces | Collective intelligence, deepening personalization |
| #677 | v0.20.0 | Agent UI management platform | Users see and control everything, trust the system |
| #616 | v0.19.0 | System prompt compression | Enable 0.6B GaiaAgent on NPU |
| #666 | v0.19.0 | Eval-to-training pipeline | Continuous quality improvement |
| #667 | v0.19.0 | Unsloth integration for LoRA fine-tuning | Train on AMD consumer GPUs |
| #668 | v0.19.0 | LoRA adapter library | Per-agent fine-tuning, hot-swap at inference |
| #612 | v0.18.2 | Agent Registry — discovery, capabilities | Agents find each other dynamically |
| #542 | v0.20.0 | MemoryStore data layer | Persistent SQLite + FTS5 |
| #543 | v0.20.0 | MemoryMixin agent integration | Auto-logging, memory tools |

---

## 8. Error Handling & Resilience

### Why Resilience Matters

Agents fail. Models hallucinate. Network requests timeout. A multi-agent system that crashes on any single failure is worse than a monolith. Resilience means the system degrades gracefully — the user gets a helpful response even when something goes wrong.

### Specialist Failure
When a specialist agent fails or times out:
1. Task status → `failed` with error details
2. GaiaAgent notified via SSE event
3. GaiaAgent tells user naturally: "I had trouble getting that file — let me try another way"
4. GaiaAgent can retry, delegate to a different agent, or ask user for guidance

### Cascading Failure Prevention
- Tasks with `depends_on` a failed task auto-transition to `failed` (with reason: "dependency failed")
- GaiaAgent catches cascading failures and re-plans: creates new tasks without the failed dependency
- Circuit breaker: if an agent fails 3 times in a row, mark as `unavailable` in agent_directory. GaiaAgent stops routing to it and tells user.

### Timeout Defaults
- `request_and_wait`: 60s default, configurable per call
- `create_task`: no timeout (async), but GaiaAgent can poll and report "still working on it..."
- Individual tool calls within specialists: inherit agent-level timeout (default 30s)

### Kill Criteria and Iteration Limits

**Why:** Unbounded agent loops are the #1 production failure mode in multi-agent systems. Anthropic's own systems spawned 50 subagents for simple queries before adding hard caps. (Source: Anthropic engineering blog, GitHub multi-agent reliability research)

- **Max iterations per task:** 8 (default). Agent forced to reflect and either complete or escalate after 8 tool-call loops.
- **Stuck detection:** If an agent retries the same tool call 3 times with the same arguments, mark task as `stuck` and notify GaiaAgent.
- **Per-agent context budget:** Each agent has a token budget per task (default: 16K for 4B GaiaAgent/specialists, 32K for 8B+ CodeAgent). Exceeding budget triggers summarization of older context.
- **Deadlock detection:** Before executing `depends_on`, check for circular dependencies in the task graph. Reject cycles at creation time.

### Semantic Checkpointing

**Why:** Specialists that run on schedules (weekly ComplianceAgent, daily FinanceAgent) lose their working context between runs. Without checkpoints, they must reconstruct understanding from scratch every time — wasting tokens and missing continuity.

At key milestones during task execution, agents persist a **context summary** to their memory namespace:
```python
checkpoint(task_id, summary="Filed LLC with TX SOS. Awaiting confirmation. EIN application next.",
           state={"filed": True, "confirmation_pending": True, "ein_applied": False})
```

On next scheduled run, the agent loads its last checkpoint instead of replaying the full conversation. This enables:
- Resumption after crashes or restarts
- Continuity across scheduled runs (weekly ComplianceAgent picks up where it left off)
- Efficient context usage (checkpoint summary vs full conversation replay)

---

## 9. Resource Management

### Why This Matters

Running multiple AI models simultaneously requires careful memory management. Users have 16-128GB of RAM. The architecture must work on the minimum (16GB Ryzen AI laptop) while scaling to the maximum (128GB Strix Halo).

### Model Loading Strategy

**GaiaAgent (4B):** Always loaded on NPU/GPU. Fast responses (<1s). ~4GB memory. This is the agent that's always ready — capable enough for real orchestration and conversation, small enough to stay loaded alongside specialists.

**Specialist models (1.7-4B):** Loaded on demand, with LRU eviction:
- First request to a specialist → load model (2-5s cold start)
- GaiaAgent masks load time: "Let me bring in my document expert..."
- Frequently used specialists stay loaded (LRU cache)
- Memory pressure → evict least-recently-used specialist model
- Lemonade Server manages model loading/unloading via existing `/api/v1/load` and `/api/v1/unload`

### Memory Budget (Ryzen AI, 32GB system)
```
GaiaAgent (4B, NPU/GPU):        ~4GB
Specialist base (4B, shared):   ~4GB  (shared across all specialists via LoRA)
LoRA adapters (4x 10MB):        ~40MB
RAG embeddings (FAISS):         ~500MB
Memory DB + overhead:           ~200MB
─────────────────────────────────────
Total active:                   ~8.7GB (fits easily in 32GB)
```

Key insight: GaiaAgent and specialists can share the SAME base model (Qwen3.5-4B). GaiaAgent uses a personality/orchestration LoRA, specialists use domain-specific LoRAs. One model loaded = ~4GB. All agents served. LoRA swap takes milliseconds.

---

## 10. Security Boundaries

### Why Security Boundaries in a Local System?

Even though GAIA runs locally, agents have real power: shell commands, file writes, email sending. A misbehaving specialist (hallucinating a dangerous command) must be caught before execution. Security isn't about protecting from external attackers — it's about protecting the user from agent mistakes.

### Authorization Model

1. **Safe operations (no approval needed):** read_file, search_file, query_documents, search_web, get_system_info
2. **Sensitive operations (GaiaAgent approval):** write_file, run_shell_command, send_email, delete_file
3. **Dangerous operations (user approval required):** install software, modify system settings, send messages on behalf of user

### How It Works
- Specialist requests sensitive operation → Agent MCP Server checks authorization policy
- If GaiaAgent-level: GaiaAgent auto-approves based on context (e.g., user asked for it)
- If user-level: GaiaAgent asks user in conversation: "ShellAgent wants to run `rm -rf ~/temp/` — should I allow that?"
- User responds → GaiaAgent relays approval/denial via MCP
- All authorization decisions logged in memory for audit trail

### Isolation
- Specialists run in the same process but with tool-level access control
- ShellAgent's commands can be sandboxed (blocked directories, command allowlist)
- FileAgent respects existing PathValidator security (blocked system dirs, sensitive files)
- WebAgent respects SSRF prevention (blocked schemes, private IPs)

---

## 11. Dynamic Team Assembly

### Why Not Templates?

Templates are brittle. They require maintenance for every new scenario, break on edge cases, and don't scale to use cases the designer didn't anticipate. A Jinja2 template for "small business in Texas" doesn't help when a user says "I'm starting a nonprofit music school in Berlin."

**The intelligence should be in the agents, not in static templates.**

### How Teams Are Built: GaiaAgent + CodeAgent

1. **GaiaAgent interviews the user** — understands the need through natural conversation
2. **GaiaAgent reasons about what specialists are needed** — based on conversation context, not a template lookup
3. **GaiaAgent checks the Agent Registry** — are the needed specialists already available?
4. **For existing specialists:** GaiaAgent spawns them with context from the interview
5. **For new specialists:** GaiaAgent asks CodeAgent to build them — CodeAgent has the full GAIA codebase and docs as context, knows the agent patterns, and creates purpose-built specialists
6. **Specialists register themselves** in the Agent Registry and start working
7. **GaiaAgent narrates progress** to the user

```
User: "I want to start a food truck in Austin"

GaiaAgent (reasoning):
  - This needs entity formation help → FormationAgent (exists in registry)
  - This needs tax/compliance help → ComplianceAgent (exists)
  - This needs financial help → FinanceAgent (exists)
  - Food truck in Texas → needs health permit specialist → doesn't exist
  → Ask CodeAgent to build a PermitAgent for Texas food service

GaiaAgent → CodeAgent: "Build a specialist agent for food service
  permits in Texas. It needs tools for web search and document
  generation. Use the GAIA agent base class pattern."

CodeAgent:
  - Reads src/gaia/agents/base/agent.py (understands the pattern)
  - Reads existing specialists as examples
  - Creates src/gaia/agents/permits/agent.py with focused prompt + tools
  - Registers in Agent Registry
  - Reports back to GaiaAgent

GaiaAgent: "I've assembled your team: FormationAgent for LLC filing,
  ComplianceAgent for tax deadlines, FinanceAgent for bookkeeping,
  and a new PermitAgent specifically for Texas food service permits."
```

### Why This Scales

| Template Approach | Dynamic Assembly |
|-------------------|-----------------|
| Need a template for every industry × state × entity type | GaiaAgent reasons about what's needed from conversation context |
| New use case = someone writes a new template | New use case = CodeAgent builds the right agents on the fly |
| Edge cases break templates (nonprofit in Berlin?) | GaiaAgent + CodeAgent handle anything through reasoning |
| Scaling linearly with templates maintained | Scaling with the capability of the models |

### Domain Context Injection

Instead of Jinja2 templates, each specialist receives context through its **spawn configuration** — a structured object passed at creation time:

```python
spawn_agent(
    agent_type="compliance_expert",
    task="Track tax obligations and compliance deadlines",
    context={
        "business_name": "Austin Bites LLC",
        "industry": "food_service",
        "entity_type": "llc",
        "state": "TX",
        "city": "Austin",
    }
)
```

The specialist agent's `__init__` method uses this context to construct its system prompt dynamically — not from a template file, but through the agent's own reasoning about what's relevant for this specific situation.

### Memory Slicing on Spawn

**Why:** Dumping the full interview transcript and all context to every spawned agent wastes context window tokens (especially critical for 1.7B specialists with 8K context). Research shows relevance-filtered context reduces memory overhead by 42% while maintaining task accuracy. (Source: AgentSpawn, arxiv 2602.07072)

When GaiaAgent spawns a specialist, it creates a **focused context slice** — only the information relevant to that agent's task:

```python
spawn_agent(
    agent_type="compliance_expert",
    task="Track tax obligations",
    context={
        # Only compliance-relevant fields from the interview:
        "business_name": "Austin Bites LLC",
        "entity_type": "llc",
        "state": "TX",
        "industry": "food_service",
        "has_employees": False,
        # NOT included: monthly_budget, tech_comfort, existing_tools
        # (irrelevant to compliance work)
    }
)
```

GaiaAgent reasons about what each specialist needs and excludes irrelevant context. This keeps specialist context windows lean and focused.

### Shared Workspace

Teams share a working directory (`~/.gaia/teams/<team_name>/`) with per-agent owned files:

```
~/.gaia/teams/austin_bites/
├── README.md              # Team overview (owned by GaiaAgent)
├── formation.md           # Entity & Legal (owned by FormationAgent)
├── compliance.md          # Tax & Compliance (owned by ComplianceAgent)
├── finance.md             # Finances (owned by FinanceAgent)
└── profile.json           # Domain context (read-only, set during interview)
```

Each agent writes only its own file. All agents can read all files via RAG. GaiaAgent maintains the README as the team status dashboard.

### Scheduled Recurring Execution

Some specialists need to run on a schedule, not just on-demand:
- ComplianceAgent: "Every Monday, check for upcoming tax deadlines"
- FinanceAgent: "Daily, reconcile transactions"
- InfraAgent: "Every 5 minutes, run health checks"

Schedules are defined at spawn time and registered with the scheduler (v0.23.0). Each scheduled run:
1. Agent loads its memory context
2. Checks for pending tasks
3. Executes highest-priority task
4. Updates its workspace file
5. Notifies GaiaAgent of progress

### Domain-Specific Guardrails

Domain guardrails are system prompt instructions, not code abstractions. CodeAgent bakes them into each agent's prompt when building it:

- Business agents: "Always include a disclaimer that you're not a lawyer or CPA"
- Financial agents: "Never execute a transaction without user confirmation"
- Home automation agents: "Never unlock doors or disable alarms without explicit user approval"

Simple, readable, no framework overhead. The guardrails are in the prompt where the model can follow them.

---

## 12. Adaptability — Handling Change

### Why This Matters

Real use cases don't stay static. A user starts a Texas LLC, then moves to California. A portfolio agent tracks 5 stocks, then the user adds crypto. A home automation agent handles lights, then the user installs a security system. Agents must adapt without requiring the user to start over.

### How Agents Adapt

**Context changes flow through memory.** When the user says "I'm moving to California," GaiaAgent stores this in shared memory. On their next task, specialists read the updated context and adjust:

```
User: "Actually, I'm relocating to California."
GaiaAgent stores: {key: "state", value: "CA", previous: "TX"} in user_profile
ComplianceAgent (next run): reads user_profile → "State changed from TX to CA"
  → Creates tasks: "Research CA franchise tax", "Check CA-specific permits"
  → Updates compliance.md with new state requirements
```

No rebuild needed. Agents read fresh context at the start of each task. If the change is large enough (entirely different industry, for example), GaiaAgent can ask CodeAgent to build additional specialists.

### When to Rebuild vs. Adapt

| Change | Action | Why |
|--------|--------|-----|
| State changes (TX → CA) | Adapt — agents read new context | Same domain, different parameters |
| New capability needed (add marketing) | Spawn — CodeAgent builds MarketingAgent | New domain, existing agents can't cover it |
| Fundamental pivot (food truck → SaaS) | Rebuild — new team from scratch | Everything changes, old agents are irrelevant |
| User preferences change (concise → detailed) | Adapt — stored in memory, all agents read it | Behavioral change, not structural |

GaiaAgent decides which action to take based on the magnitude of the change. Small changes = memory update. Medium = spawn new agent. Large = interview again and rebuild team.

### Growing With the User

As the business evolves (idea → forming → operating → scaling), GaiaAgent proactively suggests new capabilities:

```
GaiaAgent: "Your LLC is formed, EIN is set, and bookkeeping is running.
Now that you're operating, would you like help with marketing or hiring?"
User: "Yeah, I need help with social media"
GaiaAgent → CodeAgent: build a MarketingAgent for food truck social media
```

The system grows with the user's needs. It doesn't wait to be asked — GaiaAgent reads the current state and suggests next steps.

---

## 13. Reliability — Honest Constraints of Small LLMs

### The Core Challenge

A 4B model orchestrating specialists is ambitious. Small models have real limitations:

| Limitation | Impact | How Common |
|-----------|--------|------------|
| **Hallucinated tool calls** | Agent calls a tool that doesn't exist or with wrong arguments | 20-40% on untrained models |
| **Bad delegation decisions** | GaiaAgent sends a file task to DocAgent instead of FileAgent | 10-20% of delegations |
| **Lost context mid-conversation** | Agent forgets what it was doing after 3-4 turns | Common at 4K context |
| **Instruction non-compliance** | Agent ignores system prompt rules (e.g., adds planning text) | 15-30% on small models |
| **Format errors** | Agent outputs malformed JSON for tool calls | 10-25% on untrained models |

**This is not a showstopper — it's an engineering problem.** Every limitation has a mitigation.

### Mitigation: Validate Before Executing

Every tool call is validated before execution:

```python
def execute_tool_call(agent, tool_name, tool_args):
    # 1. Does the tool exist in this agent's registry?
    if tool_name not in agent.tools:
        return error(f"Unknown tool: {tool_name}. Available: {agent.tools.keys()}")

    # 2. Are the required arguments present and correctly typed?
    tool = agent.tools[tool_name]
    validation_error = tool.validate_args(tool_args)
    if validation_error:
        return error(f"Invalid args for {tool_name}: {validation_error}")

    # 3. Execute with timeout
    result = tool.execute(tool_args, timeout=30)
    return result
```

The validation layer catches hallucinated tools and wrong arguments **before** anything runs. The model gets an error message and can retry with correct arguments. This is already implemented in GAIA's base Agent class.

### Mitigation: Retry With Fallback

When a small model fails, the system has a fallback chain:

```
Attempt 1: Small model (0.6B/1.7B) tries the task
  ↓ fails (wrong tool, bad args, format error)
Attempt 2: Same model retries with error feedback
  ↓ fails again
Attempt 3: Escalate to larger model (4B) for this specific task
  ↓ fails again (rare at this point)
Fallback: Ask user for guidance via Agent UI
```

Most failures are caught by attempt 1-2. The error message from validation gives the model enough context to self-correct. Escalation to a larger model is a last resort — it's slower but more reliable.

### Mitigation: Constrained Output Format

Instead of asking the model to output arbitrary JSON, constrain its output to a strict format:

```
You have these tools: [query_documents, read_file, search_web]

To use a tool, respond EXACTLY like this:
{"tool": "query_documents", "tool_args": {"query": "your question"}}

Do NOT add any text before or after the JSON.
```

Smaller prompts with explicit format instructions reduce hallucination. The fewer choices the model has (3 tools vs 35), the more reliable it is.

### Mitigation: Graceful Degradation

If the multi-agent system fails entirely (GaiaAgent can't delegate, specialists crash), the system falls back to the existing monolithic ChatAgent:

```
Multi-agent mode:  GaiaAgent → specialist → result → narrate
Fallback mode:     ChatAgent (35B, monolithic) → direct response
```

Users always get a response. The multi-agent architecture is an optimization — the monolith is the safety net. As small models improve through fine-tuning (v0.19.0), the fallback triggers less and less.

### Mitigation: Fine-Tuning Closes the Gap

The reliability problems above are **pre-fine-tuning numbers.** After training on GAIA-specific tool calling patterns (v0.19.0 #666-668):

| Metric | Before Fine-Tuning | After Fine-Tuning (projected) |
|--------|-------------------|------------------------------|
| Tool call format accuracy | 60-75% | 90-95% |
| Tool selection accuracy | 60-80% | 85-95% |
| Argument accuracy | 70-85% | 90-95% |
| Instruction compliance | 70-85% | 85-95% |

Research shows a 350M fine-tuned model achieved 77% tool calling accuracy vs 26% for GPT-class models on specific tasks. The gap between small and large models is primarily a training gap, not a capability gap — for focused, well-defined tasks.

### What This Means Practically

**Day 1 (before fine-tuning):** Multi-agent works for simple tasks (single delegation, known tools). Complex multi-step workflows may need fallback to monolithic mode. Users will see occasional errors that self-correct on retry.

**After fine-tuning (v0.19.0):** Multi-agent handles most workflows reliably. Fallback to monolithic is rare. Tool calling accuracy matches or exceeds the monolithic 35B model on GAIA-specific tasks because each specialist is focused on fewer tools.

**Long-term (v0.20.0+):** Memory and preference learning make agents more reliable over time. Agents learn from past mistakes. The system gets better with use — the opposite of cloud agents that reset every session.

---

## 14. Known Limitations

Being honest about what this architecture does NOT solve:

| Limitation | Impact | Workaround |
|-----------|--------|------------|
| **Single-user only** | No multi-user or team collaboration | Could be extended later with per-user memory namespaces |
| **No real-time event streams** | Can't watch stock prices continuously or monitor live feeds | Scheduled polling (every N minutes) covers most cases |
| **External auth is manual** | User must log into Gmail/Calendar themselves (via Playwright browser session) | OAuth flows could be added per-service later |
| **CodeAgent needs a capable model** | Building new agents requires 4B+ model — can't run on NPU alone | CodeAgent runs on GPU. Only GaiaAgent needs NPU. |
| **Cold start for new specialists** | First request to a new specialist = 2-5s model load time | GaiaAgent masks with "Let me bring in my expert..." |
| **Context window pressure** | 4B GaiaAgent with 16K context can track many tasks but long conversations still need management | Semantic checkpointing + summarization keeps context lean |

These are real constraints, not aspirational goals. The architecture is designed to work well within them, not pretend they don't exist.

---

## 15. Scaling: Adding New Use Cases

### Why This Architecture Scales

Traditional AI assistants grow by adding features to a single agent. Every feature makes the system larger, slower, and more fragile. GAIA grows by adding specialist agents — each focused, testable, and independently improvable.

Adding a new use case (e.g., EmailAgent) requires:

1. **Create specialist agent** — `src/gaia/agents/email/agent.py` with focused prompt + tools
2. **Register in Agent Registry** (#612) — GaiaAgent discovers it automatically
3. **Create LoRA adapter** (#668) — fine-tune on email-specific training data
4. **GaiaAgent learns to delegate** — auto-discovers via registry, no code changes needed
5. **Memory namespace auto-created** — EmailAgent gets its own tables on first use

**No changes to GaiaAgent's core code. No prompt bloat. No degradation of existing agents.** The platform gets better with every new specialist — it never gets worse.

This is how GAIA becomes the Agent Computer OS: a growing ecosystem of specialist agents, orchestrated by GaiaAgent, running entirely on local hardware, with zero cloud cost and complete privacy.
