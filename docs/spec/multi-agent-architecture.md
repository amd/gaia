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

GAIA evolves to a multi-agent system orchestrated by **GaiaAgent** — a lightweight, personality-driven agent running on a 0.6B model at NPU speed. Specialist agents handle tool-heavy work. All agents communicate freely through a shared MCP bus with full observability.

### Why This Matters

| Problem | Monolithic (today) | Multi-Agent (proposed) | User Impact |
|---------|-------------------|----------------------|-------------|
| Chat response time | 2-5s (35B model) | <500ms (0.6B on NPU) | **Instant, natural conversation** |
| Tool accuracy | ~60% (35 tools) | ~90%+ (3-10 tools per specialist) | **Agent does the right thing** |
| Adding use cases | Bloats prompt, degrades all tasks | New specialist, zero impact on existing | **Platform gets better, never worse** |
| Memory usage | 35B model = 20GB+ | 0.6B + specialists on demand = <10GB | **Runs on any Ryzen AI PC** |
| Cloud API cost | $600-$3,600/yr for 35B quality | $0 — all local, NPU-accelerated | **Zero ongoing cost** |

### Design Principles

1. **GaiaAgent is the user's agent.** Fun to talk to. Handles conversation directly. Delegates tool-heavy work to specialists. Not a cold router — a genuine conversational partner that happens to have a team of experts behind it.
2. **All-to-all communication.** Any agent can talk to any agent. Any agent can spawn any agent. Any agent can create tasks for any agent. No hub-and-spoke bottleneck — agents collaborate directly.
3. **Observable by default.** Every task, message, tool call, inter-agent exchange, and memory write is visible in the Agent UI. The user sees exactly what's happening. No hidden state, no black boxes.
4. **Shared memory, isolated writes.** One database, per-agent namespaces. Any agent can read any agent's memory. Agents only write to their own namespace. This prevents corruption while enabling collective intelligence.
5. **Scalable via specialists.** New use cases = new specialist agents. GaiaAgent discovers them via the registry and learns to delegate. The architecture grows horizontally, not vertically. The platform gets better with every new agent — it never degrades.

---

## 2. GaiaAgent

**Model:** Qwen3-0.6B on NPU (instant inference, <500ms response)
**Role:** Orchestrator + chat + personality
**Prompt:** ~50-150 lines (vs 1,477 monolithic)
**Tools:** 4-5 delegation tools via Agent MCP Server

### Why a Dedicated Personality Agent?

Users don't want a tool-calling machine. They want an assistant that's **fun to talk to** and gets work done. By separating personality from tool execution:

- **Instant responses for 60-70% of interactions** — greetings, follow-ups, humor, and small talk don't need a 35B model or any tools. A 0.6B model on NPU handles them in milliseconds.
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
| **GaiaAgent** | 0.6B (NPU) | Orchestrator, personality, user interaction — the face of GAIA |
| **CodeAgent** | 4B+ (GPU) | Agent factory — builds new agents for any use case on demand |
| **DocAgent** | 1.7B (shared) | Document search, RAG Q&A, indexing — core capability used by all workflows |
| **FileAgent** | 1.7B (shared) | File system operations — agents need to read/write files |
| **ShellAgent** | 1.7B (shared) | System commands, script execution — agents need to interact with the OS |
| **WebAgent** | 1.7B (shared) | Web search, page fetching — agents need internet access |

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

## 4. Agent MCP Server — The Communication Bus

**File:** `src/gaia/mcp/servers/agent_ui_mcp.py` (extend existing)
**GitHub Issue:** #675

### Why MCP as the Communication Layer?

**Observability.** If agents communicated via direct Python function calls, their interactions would be invisible. By routing everything through MCP:
- Every message, task, and delegation is a structured MCP operation
- The Agent UI receives SSE events for every operation in real time
- Users see exactly what agents are doing — building trust
- The same protocol works for both local agents and future remote/cloud agents

**Interoperability.** MCP is the emerging standard for agent communication (Agentic AI Foundation, 10,000+ deployed servers). Building on MCP means GAIA agents can communicate with any MCP-compatible system — not just each other.

All inter-agent communication flows through the Agent MCP Server. Every operation is visible in the Agent UI.

### MCP Tools

#### Task Management
```python
create_task(title, assigned_agent, depends_on=[], context={})
    # Any agent can create tasks for any agent
    # depends_on: list of task_ids — task blocks until all dependencies complete

get_task_status(task_id)
    # Check: blocked → pending → in_progress → completed → failed

complete_task(task_id, result, artifacts=[])
    # Called by assigned agent when done

list_tasks(status=None, assigned_agent=None, parent_task_id=None)
    # Query tasks with filters
```

#### Agent Spawning
```python
spawn_agent(agent_type, task, context={}, parent_task_id=None)
    # Spawn a new agent instance to handle a task
    # Returns task_id immediately. Agent runs asynchronously.
```

**Why spawning matters:** Complex workflows need multiple agents working in parallel. GaiaAgent can spawn 3 specialists simultaneously, each working on a different aspect of the user's request. Without spawning, everything is sequential — 3x slower.

#### Inter-Agent Communication
```python
send_to_agent(target_agent, message, context={})
    # Fire-and-forget message to another agent

request_and_wait(target_agent, request, timeout_seconds=60)
    # Synchronous request — blocks until response or timeout
    # For quick queries where full task creation is overkill
```

#### Agent Discovery
```python
list_available_agents()
    # All registered agents with capabilities and status

get_agent_capabilities(agent_id)
    # Detailed info: tools, model, memory stats
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

---

## 9. Resource Management

### Why This Matters

Running multiple AI models simultaneously requires careful memory management. Users have 16-128GB of RAM. The architecture must work on the minimum (16GB Ryzen AI laptop) while scaling to the maximum (128GB Strix Halo).

### Model Loading Strategy

**GaiaAgent (0.6B):** Always loaded on NPU. Instant responses. ~500MB memory. This is the agent that's always ready — the user never waits for GaiaAgent.

**Specialist models (1.7-4B):** Loaded on demand, with LRU eviction:
- First request to a specialist → load model (2-5s cold start)
- GaiaAgent masks load time: "Let me bring in my document expert..."
- Frequently used specialists stay loaded (LRU cache)
- Memory pressure → evict least-recently-used specialist model
- Lemonade Server manages model loading/unloading via existing `/api/v1/load` and `/api/v1/unload`

### Memory Budget (Ryzen AI, 32GB system)
```
GaiaAgent (0.6B, NPU):          ~500MB
Specialist 1 (1.7B, GPU/NPU):   ~2GB
Specialist 2 (1.7B, GPU/NPU):   ~2GB
Specialist 3 (4B, GPU):         ~4GB
RAG embeddings (FAISS):         ~500MB
Memory DB + overhead:           ~200MB
─────────────────────────────────────
Total active:                   ~9.2GB (fits easily in 32GB)
```

With LoRA adapters, specialists share a base model and swap adapters (~10-100MB each) — dramatically reducing memory for multiple agents on the same model family. Four specialists using the same Qwen3-1.7B base + different LoRA adapters = ~2.4GB total instead of ~8GB.

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

The specialist agent's `__init__` method uses this context to construct its system prompt dynamically — not from a template file, but through the agent's own reasoning about what's relevant for this specific business.

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

Beyond the general security model (Section 10), domain teams can define additional guardrails:

```python
@dataclass
class DomainGuardrails:
    requires_disclaimer: bool = False       # "Not legal/financial advice"
    disclaimer_text: str = ""
    human_approval_actions: List[str] = []  # ["file_government", "spend_money", "send_external"]
    quality_checks: List[str] = []          # ["verify_state_code", "validate_ein_format"]
```

For the small business team:
- All legal/tax advice includes mandatory disclaimer
- Government filings, spending, and external communications require user approval
- State codes and EIN formats are validated before use

---

## 12. Scaling: Adding New Use Cases

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
