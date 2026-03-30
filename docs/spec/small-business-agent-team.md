# Small Business Agent Team — MVP Specification

**Date**: March 26, 2026
**Branch**: `optimize/agent-response-quality`
**Foundation**: gaia3 `MemoryMixin` + gaia6 `Scheduler` + `SharedAgentState` + Agent UI MCP Server

---

## 1. Vision

A system where a user describes their small business through a guided interview in the Agent UI, and GAIA automatically assembles a team of specialized agents that work together — autonomously and on schedule — to help the user start and run their business.

The system has three layers:
1. **Interview & Business Profile** — collect what the user needs
2. **Agent Builder** — generate the team of agents from the profile
3. **Autonomous Team** — agents execute tasks, communicate, and coordinate

**Design principle**: Simplicity and elegance. Use what GAIA already has. Don't build infrastructure for hypothetical future requirements.

---

## 2. Business Profile

The seed for everything. A structured object that all agents share.

### Schema

```python
@dataclass
class BusinessProfile:
    """Shared context for all agents in a small business team."""

    # Identity
    owner_name: str
    business_name: str
    business_description: str         # 1-3 sentences

    # Classification
    entity_type: Optional[str]        # "llc", "s_corp", "sole_prop", "c_corp", None (undecided)
    industry: str                     # "food_service", "saas", "retail", "consulting", etc.
    business_model: str               # "product", "service", "marketplace", "subscription"

    # Location
    state: str                        # US state code ("TX", "DE", "CA")
    city: Optional[str]
    operates_online: bool
    ships_physical_goods: bool

    # Stage
    stage: str                        # "idea", "forming", "operating", "scaling"
    founded_date: Optional[str]       # ISO date, None if not yet formed
    has_employees: bool
    employee_count: int = 0

    # Existing tools & budget
    existing_tools: List[str]         # ["quickbooks", "shopify", "stripe"]
    monthly_budget_range: Optional[str]  # "0-500", "500-2000", "2000+"
    tech_comfort: str                 # "beginner", "intermediate", "advanced"

    # Timestamps
    created_at: str
    updated_at: str
```

### Storage

Stored in KnowledgeDB as a single `category="business_profile"` insight with the full profile as `metadata` JSON. All agents recall it via `recall(category="business_profile")`.

This is deliberate: one row, one source of truth. No separate database, no profile table. The existing KnowledgeDB infrastructure handles persistence, search, and cross-agent visibility.

### Collection: Interview Flow

The interview runs as a guided conversation in the Agent UI, driven by a dedicated **InterviewAgent** (a ChatAgent with a specialized system prompt, no custom code needed).

The InterviewAgent's system prompt instructs it to:
1. Ask questions conversationally, one topic at a time
2. Validate answers (e.g., state must be a valid US state)
3. Infer when possible ("You mentioned selling handmade soap on Etsy" → `industry=retail`, `operates_online=true`, `ships_physical_goods=true`)
4. Confirm the final profile with the user before saving
5. Call `remember(category="business_profile", ...)` to persist

**UI component**: No special wizard UI. The chat interface IS the wizard. The InterviewAgent guides the conversation. This avoids building a custom UI component and lets the LLM handle edge cases (ambiguous answers, follow-up questions, corrections) naturally.

**Why not a form?** Forms are rigid. A user might say "I'm thinking about starting a food truck in Austin but I'm not sure if I should be an LLC or sole prop." A form can't handle that. A conversational agent can say "Let's figure that out together — here's the tradeoff..."

### Validation Step: Confirm Before Building

Before the agent team is created, the InterviewAgent presents the extracted BusinessProfile as a structured summary for the user to review:

```
Here's what I've gathered about your business:

  Business: Austin Bites Food Truck LLC
  Owner: Maria Santos
  Industry: Food Service
  Entity Type: LLC (recommended — we discussed the liability protection)
  State: Texas
  City: Austin
  Online: No (local only)
  Stage: Forming (not yet filed)
  Employees: None yet
  Tools: Square (POS), personal checking account
  Budget: $500-2000/month

Does this look right? I can change anything before we set up your team.
```

The user can correct any field conversationally ("Actually, I'm also going to sell sauces online"). The InterviewAgent updates the profile and re-confirms.

Only after explicit user confirmation ("Looks good", "Yes", "Let's go") does the system call `build_agent_team()`.

**Why this matters**: If the LLM misinterprets the user's state ("Austin" → "TX" is easy, but "Portland" could be OR or ME), the entire team gets configured for the wrong jurisdiction. The confirmation step catches these errors before they propagate.

---

## 3. Agent Team Architecture

### The Agents

Each agent is a ChatAgent with:
- A specialized system prompt (the "expertise")
- A curated RAG corpus (the "reference library")
- A set of tasks (the "to-do list")
- Access to shared memory (the "team brain")

**MVP roster (3 agents + 1 orchestrator):**

| Agent | Role | RAG Corpus | Key Tools |
|-------|------|------------|-----------|
| **BusinessAdvisor** (orchestrator) | Routes questions, delegates tasks, maintains the business plan | Business plan document | All memory tools, task delegation |
| **FormationAgent** | Entity selection, state filing, EIN, operating agreements | IRS Pub 334, state filing guides | Memory, web search |
| **ComplianceAgent** | Tax obligations, permits, licenses, deadlines | IRS Pub 535, state tax guides | Memory, scheduler (for deadline reminders) |
| **FinanceAgent** | Bookkeeping, invoicing, cash flow, expense tracking | IRS Pub 583, bookkeeping guides | Memory, service integrations (QuickBooks, Wave) |

### Why Not More Agents?

More agents = more coordination overhead, more context switching for the user, more things to break. Three specialists + one orchestrator covers the "I just started a business, now what?" journey. Additional agents (Marketing, HR, Operations) are added when the user's stage progresses to "operating" or "scaling".

### Agent Configuration (Not Code Generation)

Each agent is an instance of `ChatAgent` with different configuration — not a generated Python class. The Agent Builder doesn't write code; it writes configuration.

```python
# What the Agent Builder produces per agent:
@dataclass
class AgentTeamConfig:
    """Configuration for a small business agent team."""
    business_profile: BusinessProfile
    agents: List[AgentConfig]

@dataclass
class AgentConfig:
    """Configuration for a single agent in the team."""
    name: str                          # "FormationAgent"
    role: str                          # "formation"
    system_prompt: str                 # Full system prompt with business context baked in
    rag_documents: List[str]           # Paths to RAG corpus files
    initial_tasks: List[TaskConfig]    # Tasks assigned at creation
    schedule: Optional[str]            # Natural language schedule, e.g., "weekly on monday"
    mcp_servers: List[str]             # MCP server names this agent needs

@dataclass
class TaskConfig:
    """A task assigned to an agent."""
    name: str
    description: str
    priority: str                      # "high", "medium", "low"
    depends_on: Optional[str]          # Task name this depends on
    due_date: Optional[str]            # ISO date
```

**Why configuration, not code generation?**
- Code generation is fragile — generated Python can have bugs, import errors, security issues
- Configuration is deterministic — ChatAgent already handles all the mechanics
- Configuration is versionable — easy to diff, review, update
- Configuration is shareable — export/import team configs between users
- Claude Code can still be used to ADD new tools to GAIA itself when the existing tool set is insufficient, but that's a separate contribution workflow, not the default path

---

## 4. Agent Communication

### The Problem

Agents need to:
1. Share information ("I filed the LLC → here's the EIN")
2. Delegate tasks ("ComplianceAgent, set up quarterly tax reminders")
3. Request input ("FinanceAgent, what's the current cash position?")

### The Solution: Agent UI MCP Server as Message Bus

All agents communicate through the **Agent UI MCP server**. This is the simplest approach that uses existing infrastructure.

```
┌──────────────────────────────────────────────────────────┐
│                     Agent UI MCP Server                   │
│                                                          │
│  Sessions:    [Formation] [Compliance] [Finance] [Advisor]│
│  Messages:    Per-session conversation history             │
│  Tasks:       scheduled_tasks table                       │
│  Documents:   Shared document library                     │
│                                                          │
│  MCP Tools:                                              │
│    send_message(session_id, message)                     │
│    get_messages(session_id)                               │
│    create_session(title)                                 │
│    list_sessions()                                       │
│    index_document(filepath)                              │
│    schedule_task(name, interval, prompt)                  │
│                                                          │
└──────────────────────────────────────────────────────────┘
        ▲           ▲           ▲           ▲
        │           │           │           │
   Formation   Compliance   Finance    BusinessAdvisor
    Agent        Agent       Agent     (Orchestrator)
```

**How communication works:**

1. **Agent-to-agent via shared sessions**: Each agent has its own session. To communicate, an agent sends a message to another agent's session. The receiving agent picks it up on its next execution (scheduled or triggered).

2. **Orchestrator delegation**: The BusinessAdvisor can send a message to any agent's session with a task request. The receiving agent sees it as the next message in its conversation.

3. **Shared knowledge via KnowledgeDB**: For persistent facts (EIN number, entity type, filing dates), agents use `store_insight()` in the shared KnowledgeDB. Any agent can `recall()` these facts.

**Why this approach?**

- **No new infrastructure**: Agent UI MCP server already exists with `send_message`, `get_messages`, `create_session`
- **Full audit trail**: Every inter-agent message is a regular message in the database, visible in the Agent UI
- **User can observe**: The user can open any agent's session and see the full conversation, including delegated tasks
- **User can intervene**: The user can send a message to any agent's session to correct course or provide input

### Communication Protocol

Messages between agents use a **structured envelope + natural language body**. The envelope is a small JSON block that the system can parse programmatically (for triggering, logging, and UI display), followed by a freeform message the receiving agent reads naturally.

```
---agent-message---
{"from": "BusinessAdvisor", "to": "ComplianceAgent", "priority": "high", "task_id": "abc-123"}
---

Please set up recurring reminders for federal quarterly estimated tax payments.
The business is an LLC in Texas, EIN: 12-3456789, formed 2026-03-15.
The deadlines are April 15, June 15, September 15, January 15.
```

**The envelope is machine-parsed, the body is LLM-parsed.**

- The `---agent-message---` delimiters let the Agent UI MCP `send_message` implementation extract the envelope reliably (simple string split, not LLM parsing)
- The system uses `priority: "high"` to decide whether to trigger immediate execution
- The system uses `task_id` to link the message to a task in the `agent_tasks` table
- The body is natural language — the receiving agent reads it as a regular message
- If an agent sends a message without the envelope (malformed), it's still delivered as a regular message — graceful degradation, not silent failure

**Why this hybrid?** Pure convention (text headers) is fragile — if the LLM formats it wrong, the system can't parse it. Pure JSON schema is overkill — the actual request is natural language anyway. The envelope handles the machine-readable parts (routing, priority, task linking) while the body stays natural.

### Direct vs. Orchestrated Communication

Both patterns are supported:

**Orchestrated (via BusinessAdvisor):**
- User asks a question → BusinessAdvisor routes to the right agent
- BusinessAdvisor breaks a complex goal into tasks and delegates
- BusinessAdvisor monitors progress and follows up

**Direct (agent-to-agent):**
- FormationAgent completes LLC filing → sends EIN to ComplianceAgent and FinanceAgent directly
- ComplianceAgent discovers a tax deadline → sends alert to FinanceAgent
- Any agent can send a message to any other agent's session

The BusinessAdvisor doesn't need to mediate every interaction. Just like a real team — the manager delegates and coordinates, but team members can talk to each other directly when it makes sense.

---

## 5. Task System

### Dedicated Tasks Table

Tasks are structurally different from knowledge — they have lifecycle, status transitions, dependencies, and ownership. They get their own table in the Agent UI database (`gaia_chat.db`), not KnowledgeDB.

```sql
CREATE TABLE agent_tasks (
    id TEXT PRIMARY KEY,                        -- UUID
    team_id TEXT NOT NULL,                      -- Links to a business/team
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    agent_role TEXT NOT NULL,                   -- "formation", "compliance", "finance", "advisor"
    status TEXT DEFAULT 'pending',              -- pending → active → blocked → complete → cancelled
    priority TEXT DEFAULT 'medium',             -- high, medium, low
    assigned_by TEXT,                           -- Agent role that created this task
    depends_on TEXT,                            -- Task ID of dependency
    due_date TEXT,                              -- ISO date
    subtasks TEXT,                              -- JSON array of subtask strings
    completion_notes TEXT,                      -- Filled on completion
    blocked_reason TEXT,                        -- Filled when blocked
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE INDEX idx_tasks_team ON agent_tasks(team_id);
CREATE INDEX idx_tasks_agent ON agent_tasks(agent_role);
CREATE INDEX idx_tasks_status ON agent_tasks(status);
CREATE INDEX idx_tasks_due ON agent_tasks(due_date) WHERE due_date IS NOT NULL;
```

**Why a dedicated table instead of KnowledgeDB insights?**
- Tasks need indexed queries by status, agent, and due date — `WHERE status='pending' AND agent_role='compliance' ORDER BY priority, due_date` is a natural SQL query, not an FTS5 search
- Status transitions need atomicity (no partial updates)
- Dependencies need foreign key references between tasks
- The Agent UI can display task boards directly from this table without parsing insight metadata

### Task Tools (Agent UI MCP)

The Agent UI MCP server exposes task management tools:

```python
# Available to all agents via MCP
create_task(team_id, name, description, agent_role, priority, due_date, depends_on, subtasks)
update_task(task_id, status, completion_notes, blocked_reason)
list_tasks(team_id, agent_role, status, limit)
get_task(task_id)
```

### Task Execution

Tasks execute in four ways:

1. **Scheduled**: Agent runs on a schedule (e.g., ComplianceAgent runs weekly). On each run, it calls `list_tasks(agent_role="compliance", status="pending")` and works on the highest-priority one.

2. **Triggered immediately**: When an agent delegates a task with `priority=high`, the system triggers the receiving agent's execution immediately via the Scheduler's `execute_now(task_name)` method — no waiting for the next scheduled run.

3. **Event-driven**: When a task is marked `complete`, the system checks for tasks with `depends_on` pointing to it and marks them as `pending` (unblocked). If the unblocked task's agent has a schedule, it triggers immediately.

4. **User-initiated**: User opens an agent's session and asks it to work on a specific task.

### Autonomy Boundaries

**Agents CAN autonomously:**
- Research (web search, RAG queries)
- Generate documents and checklists
- Organize information and create summaries
- Update task status
- Send messages to other agents
- Set up reminders and schedules

**Agents MUST get human approval for:**
- Filing anything with a government agency
- Spending money (filing fees, subscriptions)
- Sending external communications (emails, messages)
- Making legal or tax elections
- Connecting to new third-party services (OAuth flows)

This is enforced via the existing `TOOLS_REQUIRING_CONFIRMATION` mechanism in the Agent base class. The Agent UI already supports tool confirmation popups.

---

## 6. Shared Business Plan

The business plan is the team's shared working document — a collection of Markdown files, each owned by one agent, indexed via RAG and accessible to all agents.

### Structure: Per-Agent Section Files

Each agent owns its section exclusively. No concurrent writes, no locking needed.

```
~/.gaia/business/
├── README.md                  # Executive summary (owned by BusinessAdvisor)
├── formation.md               # Entity & Legal (owned by FormationAgent)
├── compliance.md              # Tax & Compliance (owned by ComplianceAgent)
├── finance.md                 # Finances (owned by FinanceAgent)
└── profile.json               # BusinessProfile (read-only, set by InterviewAgent)
```

**Example: `formation.md`**
```markdown
# Entity & Legal — {business_name}
*Last updated: 2026-03-16 by FormationAgent*

## Status
- Entity Type: LLC ✅
- State: Texas ✅
- EIN: 12-3456789 ✅ (applied 2026-03-15)
- Operating Agreement: Draft complete ✅

## Completed Steps
1. Reserved business name with Texas SOS (Mar 10)
2. Filed Articles of Organization (Mar 12, confirmation #TX-2026-44821)
3. Applied for EIN online at IRS.gov (Mar 15)
4. Drafted single-member operating agreement (Mar 16)

## Notes
- Texas LLC annual franchise tax report due May 15
- Registered agent: owner (self) at business address
- Forwarded EIN to ComplianceAgent and FinanceAgent
```

**Example: `README.md`** (the index)
```markdown
# Business Plan: {business_name}
*{business_description}*

| Area | Status | Agent | Last Updated |
|------|--------|-------|-------------|
| Entity & Legal | ✅ Complete | FormationAgent | Mar 16 |
| Tax & Compliance | ⏳ In Progress | ComplianceAgent | Mar 20 |
| Finances | ⏳ In Progress | FinanceAgent | Mar 18 |

See individual files for details.
```

### Why Per-Agent Files?

1. **No concurrency hazard**: Each agent writes only to its own file. Two agents running simultaneously can't overwrite each other.
2. **Clear ownership**: If `compliance.md` has bad advice, you know exactly which agent produced it.
3. **Selective RAG**: An agent can `query_documents()` across all files, or `query_specific_file()` for just one section.
4. **User-editable**: User can open and edit any file. Agent sees the changes on next run.
5. **Git-friendly**: Per-file diffs are cleaner than section-level edits in a monolith.

### How Agents Use It

- **RAG indexed**: The `~/.gaia/business/` folder is indexed as a RAG corpus for all agents
- **Write own section**: Each agent updates only its own file via file tools
- **Read all sections**: Any agent can query the full corpus to understand the bigger picture
- **BusinessAdvisor maintains README.md**: The orchestrator updates the index when agents report progress

---

## 7. Agent Builder

The Agent Builder is **not** a separate agent. It's a function that runs after the interview completes. It takes the BusinessProfile and produces an AgentTeamConfig.

### Logic

```python
def build_agent_team(profile: BusinessProfile) -> AgentTeamConfig:
    """
    Determine which agents are needed and configure them.

    This is deterministic logic, not an LLM call.
    The LLM was already used during the interview to collect the profile.
    """
    agents = []

    # Always include the orchestrator
    agents.append(build_advisor_config(profile))

    # Always include for pre-operating businesses
    if profile.stage in ("idea", "forming"):
        agents.append(build_formation_config(profile))

    # Always include (every business has tax obligations)
    agents.append(build_compliance_config(profile))

    # Include if past idea stage
    if profile.stage != "idea":
        agents.append(build_finance_config(profile))

    # Future: marketing, hr, operations agents
    # Added when profile.stage progresses

    return AgentTeamConfig(
        business_profile=profile,
        agents=agents,
    )
```

### System Prompt Generation

Each agent's system prompt is templated with business context:

```python
def build_compliance_config(profile: BusinessProfile) -> AgentConfig:
    return AgentConfig(
        name="ComplianceAgent",
        role="compliance",
        system_prompt=f"""You are a compliance advisor for {profile.business_name},
a {profile.industry} {profile.entity_type or 'business'} in {profile.state}.

Your responsibilities:
- Track federal and state tax obligations
- Monitor compliance deadlines and send reminders
- Advise on permits and licenses needed for {profile.industry} in {profile.state}
- Help with employment compliance when the business hires

IMPORTANT: You are not a lawyer or CPA. Always include a disclaimer that your
guidance is informational only and the user should consult a qualified professional
for legal or tax decisions.

Business context:
{json.dumps(asdict(profile), indent=2)}

You have access to the shared business plan. Update the Compliance & Tax section
when you complete tasks.

When you receive a [FROM: ...] message from another agent, treat it as a delegated
task and execute it.""",
        rag_documents=get_compliance_corpus(profile.state),
        initial_tasks=get_compliance_tasks(profile),
        schedule="weekly on monday at 9am",
        mcp_servers=[],
    )
```

### RAG Corpus Selection

The corpus is selected based on the profile:

```python
def get_compliance_corpus(state: str) -> List[str]:
    """Select RAG documents based on business state."""
    corpus = [
        "corpus/irs-pub-535-business-expenses.pdf",
        "corpus/irs-pub-583-starting-a-business.pdf",
    ]
    state_guide = f"corpus/states/{state.lower()}-business-guide.pdf"
    if os.path.exists(state_guide):
        corpus.append(state_guide)
    return corpus
```

### Team Instantiation

After `build_agent_team()` returns the config, the system:

1. Creates a session per agent in the Agent UI (via MCP)
2. Indexes each agent's RAG documents
3. Creates initial tasks in the `agent_tasks` table via MCP
4. Sets up scheduled runs in the Scheduler
5. Generates the initial business plan files (`~/.gaia/business/`)
6. Sends a welcome message to each agent's session with its role and first task

---

## 8. Scheduling & Execution

Leverages the gaia6 `Scheduler` infrastructure.

### Agent Execution Schedule

| Agent | Default Schedule | What It Does Each Run |
|-------|-----------------|----------------------|
| BusinessAdvisor | On user message only | Routes questions, reviews team progress |
| FormationAgent | Daily at 9am (while forming) | Checks task list, works on next pending task |
| ComplianceAgent | Weekly on Monday at 9am | Reviews deadlines, sends reminders, checks regulatory updates |
| FinanceAgent | Weekly on Friday at 5pm | Weekly financial summary, upcoming payments |

Schedules are configurable by the user via the Agent UI scheduler.

### Immediate Execution Trigger

Scheduled execution alone is too slow for inter-agent delegation. When an agent sends a message with `"priority": "high"` in the envelope, the system triggers the receiving agent's execution immediately.

**Flow:**
```
FormationAgent completes "File LLC" task
  → Sends message to ComplianceAgent: priority=high, "LLC filed, set up tax registrations"
  → Agent UI MCP send_message() detects priority=high in envelope
  → Calls Scheduler.execute_now("ComplianceAgent")
  → ComplianceAgent runs immediately (not waiting for Monday 9am)
  → ComplianceAgent reads the message, creates tax registration tasks
```

**Implementation**: The Agent UI MCP `send_message` tool checks for the `---agent-message---` envelope. If `priority` is `"high"`, it calls the Scheduler's `execute_now()` method for the target agent's scheduled task. This is a ~10-line addition to the existing `send_message` implementation.

**Guardrail**: Immediate triggers still respect the global chat semaphore (one agent executing at a time). If another agent is already running, the triggered execution queues behind it. This prevents resource contention.

### Execution Flow

```
Scheduler triggers ComplianceAgent (scheduled or immediate)
  → Creates/resumes ComplianceAgent's session
  → Injects: "You are running your weekly check. Review your tasks and deadlines."
  → Agent:
    1. Recalls business profile from KnowledgeDB
    2. Calls list_tasks(agent_role="compliance", status="pending")
    3. Checks for upcoming deadlines (due_date within 14 days)
    4. Works on highest-priority pending task
    5. Calls update_task(task_id, status="complete", completion_notes="...")
    6. If something needs user attention: sends message to BusinessAdvisor session
    7. Updates its section of the business plan (compliance.md)
```

---

## 9. Third-Party Service Integration

Leverages the gaia6 `ServiceIntegrationMixin` pattern.

### How It Works

When a user says "I use QuickBooks" during the interview (or later), the agent:

1. **Discovers the API**: Web search for "QuickBooks API" → finds docs, auth type
2. **Guides credential setup**: Walks user through OAuth flow or API key generation
3. **Stores credentials**: Encrypted in KnowledgeDB `credentials` table
4. **Registers as MCP server**: The integration becomes an MCP tool available to relevant agents

### MVP Integrations (Assist, Don't Automate)

For MVP, service integrations are **advisory** — they help the user set up and use services, they don't manage them autonomously.

| Service | Agent | What It Does |
|---------|-------|-------------|
| IRS (web) | FormationAgent | Guides EIN application (user completes the form) |
| State SOS (web) | FormationAgent | Guides LLC filing (user completes the form) |
| QuickBooks / Wave | FinanceAgent | Explains setup, categorization, reconciliation |
| Calendar | ComplianceAgent | Reminds about deadlines (user adds to their calendar) |

Full autonomous service integration (agent files forms, makes payments, sends emails) is a post-MVP capability gated on the human-in-the-loop approval system.

---

## 10. Quality & Evaluation

### The Problem

Small business advice has real consequences. If the ComplianceAgent tells a Texas LLC owner they don't need to file a franchise tax report, and they actually do, that's a real penalty. We need a way to validate agent advice quality before shipping and continuously after.

### Eval Strategy

**Pre-launch: Ground truth scenarios**

Build 5 eval scenarios covering the most common business types:

| Scenario | Profile | Key Validations |
|----------|---------|----------------|
| Solo LLC in Texas | Food truck, single member, forming | Correct TX filing steps, franchise tax awareness, EIN process |
| S-Corp in Delaware | SaaS startup, 2 founders, forming | DE franchise tax vs. home state taxes, S-election timing |
| Sole Prop in California | Freelance consultant, operating | Self-employment tax, CA LLC fee trap (if they convert), Schedule C |
| LLC in New York | Retail, online + physical, scaling | NY publication requirement, sales tax nexus, hiring obligations |
| Partnership in Florida | Service business, 2 partners, idea | No state income tax, partnership agreement, pass-through taxation |

Each scenario has:
- A scripted interview transcript
- Expected BusinessProfile output
- Expected initial task list per agent
- 10-15 factual assertions the agents should get right (ground truth)
- 5 common mistakes the agents should NOT make (anti-patterns)

**Runtime: User feedback**

After each agent interaction, the user can rate the response (thumbs up/down). This is stored in the messages table as `feedback` metadata. Low-rated responses are flagged for review.

**Runtime: Disclaimer compliance**

A post-processing check verifies that agent responses in legal/tax/financial domains include the "not professional advice" disclaimer. If missing, the system appends it automatically. This is a simple regex check on the response content, not an LLM call.

### Disclaimer Enforcement

All agents in the small business team include this in their system prompt:

```
MANDATORY DISCLAIMER: Every response that includes legal, tax, or financial guidance
MUST end with: "This is general information only, not legal or tax advice. Consult a
qualified professional (attorney, CPA, or tax advisor) before making decisions."

Never omit this disclaimer, even if the user says to skip it.
```

Additionally, the Agent UI MCP `send_message` implementation for team sessions appends the disclaimer automatically if the response doesn't already contain it. Belt and suspenders.

---

## 11. Implementation Milestones

### Milestone Overview

| # | Milestone | What | Depends On | Risk Level |
|---|-----------|------|------------|------------|
| M1 | Single agent proves the approach | One agent + RAG answers business questions competently | — | Low |
| M2 | Interview extracts profile | Conversational interview → structured BusinessProfile | M1 | **High** (LLM extraction reliability) |
| M3 | Builder assembles team | Profile → team config → sessions + business plan files | M2 | Low (deterministic logic) |
| M4 | Task system | `agent_tasks` table, MCP tools, agents read/update tasks | — | Medium |
| M5 | Inter-agent communication | Agents send messages to each other, delegation works | M3, M4 | **High** (protocol + prompt engineering) |
| M6 | Scheduled autonomy | Agents run on schedule, execute tasks, update business plan | M5 | **High** (agent quality at scale) |
| M7 | End-to-end validation | Eval scenarios, disclaimers, guardrails, documentation | M6 | Medium |

**Critical path:** M1 → M2 → M3 → M5 → M6 → M7
**Parallel track:** M4 can run alongside M1-M3

---

### Required Capabilities

Several capabilities needed by these milestones are work-in-progress on other branches. These are listed here as requirements — not as merge tasks.

| Capability | Required By | Status |
|-----------|-------------|--------|
| Persistent memory (MemoryMixin, KnowledgeDB) | M2, M3, M5 | WIP — MemoryMixin on gaia3, SharedAgentState on gaia6 |
| Agent UI MCP Server (programmatic session/message control) | M3, M5 | WIP — agent_ui_mcp.py on gaia6 |
| Scheduler (natural language schedules, async execution) | M6 | WIP — scheduler.py on gaia6 |
| Task scheduling DB tables (scheduled_tasks, schedule_results) | M6 | WIP — database migration on gaia6 |
| Agent-to-agent message delivery via MCP | M5 | WIP — send_message on gaia6 |

These do not need to be the exact implementations from those branches. They are requirements that can be built fresh, ported, or adapted — whatever makes sense at the time.

---

### M1: Single Agent Proves the Approach

**Goal:** One ChatAgent with a custom system prompt and RAG corpus can answer small business questions competently.

This is the cheapest way to validate the core approach before building the multi-agent system. If a single agent with a good prompt and IRS publications can't give useful advice, adding 3 more agents won't fix it.

**Requirements:**
- `BusinessProfile` dataclass with validation and JSON serialization
- One specialist system prompt (ComplianceAgent) with business context interpolation
- RAG corpus: 3-5 IRS publications (Pub 334, 535, 583) downloaded and indexed
- A ChatAgent instance configured with the prompt + corpus
- CLI or Agent UI entry point to run the agent interactively

**Validation (manual, 10 questions):**
- "What are my tax obligations as an LLC in Texas?" → Should cite franchise tax, federal income tax, self-employment tax
- "Do I need a sales tax permit?" → Should ask clarifying questions about what's being sold
- "When are quarterly estimated taxes due?" → Should give correct dates
- "What's the difference between an LLC and S-Corp?" → Should give a competent comparison
- Grade: **7/10 correct, contextually appropriate answers = pass**

**Risk:** Low. ChatAgent + prompt + RAG is a well-understood pattern. The risk is RAG corpus quality, which is exactly why we validate here first.

**Go/no-go decision:** If the single agent can't pass the 10-question test, stop and fix the prompt + corpus before proceeding. Adding more agents won't compensate for a bad foundation.

---

### M2: Interview Extracts Profile

**Goal:** The InterviewAgent can reliably extract a structured `BusinessProfile` from a multi-turn conversation.

This is the highest-risk early milestone. We're asking an LLM to:
1. Guide a conversation across 10+ fields
2. Infer values from natural language ("food truck in Austin" → TX, food_service)
3. Validate constraints (valid US state, valid entity type)
4. Produce a structured JSON output
5. Confirm with the user before committing

If this doesn't work reliably, the entire downstream system gets configured wrong.

**Requirements:**
- InterviewAgent system prompt (full prompt engineering effort, not a first draft)
- Persistent storage for the profile (requires persistent memory — WIP)
- Validation step: agent presents summary, user confirms before proceeding
- CLI entry point for running the interview

**Validation (5 scripted scenarios, each run 3 times):**

| Scenario | User Style | Key Challenge |
|----------|-----------|--------------|
| Clear and specific | "I'm starting an LLC food truck in Austin, TX" | Extract everything from one sentence |
| Vague and exploratory | "I have an idea for selling stuff online" | Needs many follow-up questions |
| Already operating | "I run a consulting firm in SF, 3 employees, S-Corp" | Collect operational details |
| Undecided on entity | "I don't know if I should be an LLC or sole prop" | Guide without deciding for them |
| Ambiguous location | "I'm in Portland" | Must ask OR or ME, not guess |

**Pass criteria:** 4/5 scenarios produce correct profiles on at least 2/3 runs.

**Risk:** **High.** The LLM may hallucinate state codes, pick wrong entity types, or fail to ask crucial follow-ups. Mitigations:
- `BusinessProfile.validate()` catches invalid state codes, bad entity types
- Confirmation step catches errors before they propagate
- May need few-shot examples in the prompt or structured output mode (function calling)

---

### M3: Builder Assembles Team

**Goal:** A deterministic builder takes a BusinessProfile and produces a working team — sessions, business plan files, and initial tasks for each agent.

**Requirements:**
- Agent Builder function: profile → team config (which agents, what prompts, which RAG docs, what tasks)
- System prompt templates for all 4 agents (Advisor, Formation, Compliance, Finance) with business context interpolation
- Business plan file generator: per-agent Markdown files in `~/.gaia/business/`
- Team instantiation: creates sessions in Agent UI, indexes RAG docs, sends welcome messages (requires Agent UI MCP — WIP)
- CLI entry point: build from a profile JSON file, with dry-run option

**Validation:**
- Unit tests: team composition varies correctly by stage (idea → 3 agents, forming → 4, operating → 3)
- Unit tests: task dependency chains are valid (no dangling references)
- Integration test: build → sessions exist in Agent UI → agents answer contextual questions
- Manual test: open each agent's session, ask a domain question, get a relevant answer

**Risk:** Low. Deterministic code — data transformation, file generation, API calls. Prompt quality for each specialist is important but iteratable.

---

### M4: Task System

**Goal:** A structured task management system that agents can read from and write to, with dependency tracking and status lifecycle.

Runs in parallel with M1-M3 — it's infrastructure, not dependent on the business logic.

**Requirements:**
- `agent_tasks` table in the database with proper schema:

```sql
CREATE TABLE agent_tasks (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    agent_role TEXT NOT NULL,
    status TEXT DEFAULT 'pending',    -- pending → active → blocked → complete → cancelled
    priority TEXT DEFAULT 'medium',   -- high, medium, low
    assigned_by TEXT,
    depends_on TEXT,                  -- FK to another task ID
    due_date TEXT,
    subtasks TEXT,                    -- JSON array
    completion_notes TEXT,
    blocked_reason TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
```

- MCP tools exposed to agents: `create_task`, `update_task`, `list_tasks`, `get_task`
- Dependency resolution: completing task A automatically unblocks tasks that depend on it
- Agent UI visibility: tasks queryable and displayable (at minimum via MCP; stretch: dedicated UI view)

**Validation:**
- Unit tests: CRUD operations, dependency unblocking
- Integration test: agent calls `list_tasks` via MCP, gets correct results
- Integration test: agent calls `update_task(status="complete")`, dependent task becomes pending

**Risk:** Medium. DB + MCP plumbing is straightforward. The risk is whether agents reliably call the task tools (prompt engineering challenge — agents tend to ignore tools they don't understand the purpose of).

---

### M5: Inter-Agent Communication

**Goal:** Agents can send messages to each other, the system parses routing metadata, triggers execution when needed, and every interaction is auditable.

This is where the system becomes genuinely multi-agent. Everything before M5 is just multiple independent agents that share a business profile.

**Requirements:**
- Message delivery between agent sessions (requires Agent UI MCP — WIP)
- Structured envelope protocol (`---agent-message---` + JSON metadata + natural language body)
- Envelope parsing in the message delivery layer (extract `from`, `to`, `priority`, `task_id`)
- Immediate execution trigger: `priority: "high"` causes receiving agent to run now, not on next schedule
- Graceful degradation: malformed messages (no envelope) still delivered as regular text
- Audit trail: all inter-agent messages visible in Agent UI sessions
- Shared knowledge: agents store critical facts (EIN, entity type, filing dates) in persistent memory; any agent can recall them

**Validation scenarios:**

| Scenario | Flow | Pass Criteria |
|----------|------|--------------|
| User asks tax question | User → Advisor → delegates to Compliance → Compliance responds | User sees Compliance answer in Advisor session |
| LLC filed triggers compliance | Formation marks task complete → high-priority message to Compliance | Compliance runs immediately, creates tax registration tasks |
| Cross-agent knowledge sharing | Formation stores EIN in memory → Finance recalls it | Finance references the EIN without being told directly |
| Malformed envelope | Agent sends message without `---agent-message---` block | Message still delivered (graceful degradation) |

**Staged approach:**
1. First: Advisor → Specialist one-way delegation only (simpler, predictable)
2. Then: Direct agent-to-agent (Formation → Compliance, Compliance → Finance)
3. Last: Bidirectional exchanges (agent requests info from another agent and waits)

**Risk:** **High.** This combines prompt engineering (agents must format envelopes), system engineering (parsing, triggering), coordination logic (who sends what when), and agent quality (receiver must understand and act correctly). Mitigations:
- Stage the rollout (one-way first)
- Log every envelope parse for debugging
- Fall back to shared memory for critical facts — don't rely solely on message passing

---

### M6: Scheduled Autonomy

**Goal:** Agents run on schedules, review their task lists, execute pending tasks, update their business plan section, and notify other agents — without user intervention.

This is where the system goes from "agents you can chat with" to "agents that work for you."

**Requirements:**
- Schedule configuration per agent (requires Scheduler — WIP)
- Natural language schedule parsing ("daily at 9am", "weekly on monday")
- Autonomous execution loop per agent run:
  1. Recall business profile
  2. Query pending tasks (`list_tasks(agent_role=..., status="pending")`)
  3. Work on highest-priority task (research, generate documents, update status)
  4. Update own business plan section file
  5. Notify other agents if their tasks are unblocked
- Approval gates: tool confirmation for external/destructive actions
- Team-level pause/resume: single control to pause all agent schedules
- Session strategy for scheduled runs (avoid prompt drift over many runs)
- Max-steps limit per run to prevent runaway execution

**Validation:**
- FormationAgent runs daily, picks up "File LLC in TX" task, produces a filing checklist
- ComplianceAgent runs weekly, identifies upcoming quarterly tax deadline, sends reminder
- FinanceAgent runs weekly, produces financial setup status summary
- Approval gate fires when agent attempts shell command or file write
- Pause team → no agents execute → resume → agents catch up

**Risk:** **High.** This is the hardest problem in the spec:
- **Quality:** Can agents produce useful output without human guidance each turn?
- **Reliability:** Do agents get stuck in loops, produce garbage, or silently fail?
- **Cost:** Each run burns LLM tokens. 4 agents × daily/weekly = meaningful local LLM usage
- **Drift:** Over many runs, do agents stay on-task or drift into repetition/irrelevance?

**Descoped fallback:** If full autonomy doesn't work reliably, descope to **proactive reminders** only — agents surface deadline alerts and task suggestions on schedule, but the user initiates all substantive work. This is still valuable and dramatically more achievable.

---

### M7: End-to-End Validation

**Goal:** The full pipeline works reliably for the 5 most common business types, with proper guardrails, disclaimers, and documentation.

**Requirements:**
- 5 eval scenarios with ground truth (see §10):
  - Solo LLC in Texas (food truck)
  - S-Corp in Delaware (SaaS startup)
  - Sole Prop in California (freelance consultant)
  - LLC in New York (online retail)
  - Partnership in Florida (service business)
- Each scenario has 10-15 factual assertions agents must get right, plus 5 anti-patterns they must avoid
- Disclaimer enforcement: prompt-level + automated post-processing append
- User feedback mechanism: thumbs up/down on agent responses
- User guide documentation (MDX in `docs/guides/`)
- Edge case handling: profile updates, team reconfiguration, error recovery
- Performance baseline: token cost per agent per week, execution time per run

**Pass criteria:**
- 80% of factual assertions correct across all 5 scenarios
- Disclaimer present in 100% of advice-containing responses
- No security issues (path traversal, credential leaks, prompt injection via business name)

**Risk:** Medium. By this point the system works — this is about quality, correctness, and polish. The main risk is discovering agent advice quality is too low, which sends you back to M1 (prompts + RAG corpus).

---

### Milestone Dependencies

```
M1: Single Agent Proves Approach
 └─→ M2: Interview Extracts Profile
      └─→ M3: Builder Assembles Team ─────┐
                                           │
M4: Task System (parallel) ───────────────┤
                                           │
                           M5: Inter-Agent Communication
                                           │
                           M6: Scheduled Autonomy
                                           │
                           M7: End-to-End Validation
```

M4 runs in parallel with M1-M3. Everything else is sequential on the critical path.

---

### Risk Summary

| Milestone | Risk | Why | Go/No-Go Signal |
|-----------|------|-----|-----------------|
| M1 | Low | Well-understood pattern | 10-question manual test |
| M2 | **High** | LLM structured extraction is unreliable | 5-scenario extraction test |
| M3 | Low | Deterministic code | Unit tests |
| M4 | Medium | DB + MCP plumbing | Integration tests |
| M5 | **High** | Multi-agent coordination is novel | First delegation round-trip |
| M6 | **High** | Autonomous quality without human guidance | First scheduled run output quality |
| M7 | Medium | Quality bar, not architecture | Eval pass rate |

**Three high-risk milestones (M2, M5, M6)** should each be prototyped with throwaway spikes before committing to production implementation. If any fundamentally doesn't work, the approach needs rethinking before proceeding.

### Recommended Ship Boundary

**Ship M1-M4 as v1.** That gives users:
- Conversational interview → business profile
- Team of specialist agents in the Agent UI
- Shared business plan
- Task tracking
- User drives all interactions (picks which agent to talk to)

**M5-M6 are the stretch goal.** Prototype M5 (one delegation round-trip) early to gauge feasibility. If it works, proceed. If not, the system is still useful as a team of independent specialist agents.

---

## 12. Required Capabilities

| Capability | Required By | Status |
|-----------|-------------|--------|
| ChatAgent + custom system prompts | M1, M2, M3 | Ready |
| RAG SDK (document indexing + query) | M1, M3 | Ready |
| Tool confirmation (human-in-the-loop) | M5, M6 | Ready |
| Persistent agent memory (cross-session storage + recall) | M2, M3, M5 | WIP |
| Agent UI MCP Server (programmatic session/message control) | M3, M5 | WIP |
| Task scheduling (cron-like, natural language schedules) | M6 | WIP |
| Service integration (API discovery, credential management) | Post-MVP | WIP |
| Computer use (browser automation, workflow replay) | Post-MVP | WIP |

---

## 13. What This Does NOT Include (Explicit Non-Goals)

1. **Code generation**: The Agent Builder does NOT generate Python agent classes. It produces configuration for ChatAgent instances.
2. **PR contribution workflow**: Optional future feature. Not MVP.
3. **Custom UI components**: No interview wizard, no team dashboard widget. Everything runs in the existing chat interface and session list.
4. **Autonomous external actions**: Agents don't file forms, send emails, or spend money without user approval.
5. **Multi-user support**: One user, one business, one team. Multi-tenant is not in scope.
6. **Real-time agent-to-agent streaming**: Agents communicate asynchronously via messages. No WebSocket agent-to-agent protocol.
7. **Agent marketplace / sharing**: No registry for sharing agent configurations. Just export/import JSON.

---

## 14. Open Questions

1. **RAG corpus sourcing**: IRS publications are public. State-specific guides vary wildly in format and availability. Do we curate a starter corpus (expensive to maintain) or rely on web search (less reliable, needs internet)?

2. **Agent session UX**: How does the user navigate between agent sessions? Options: a "Team" sidebar group, session tags/badges, or a dedicated team dashboard page. Needs design input.

3. **Profile evolution mechanism**: When the user's business evolves (hired first employee, changed entity type), how does the team adapt? Options: (a) BusinessAdvisor detects milestone events and updates profile + spawns new tasks, (b) user re-runs a lighter "update interview", (c) agents flag when their assumptions no longer match reality.

4. **Team pause/resume**: How does the user pause all agents at once? A team-level control in the scheduler, or individually pausing each agent's schedule?

5. **Session strategy for scheduled runs**: Should each scheduled execution reuse the agent's existing session (long history, risk of prompt drift) or create a new session per run (clean context, but loses conversational continuity)? Hybrid: summarize and rotate after N runs?

6. **Concurrent agent execution**: The current chat semaphore (1) serializes all execution. With 4 agents, this creates a bottleneck. Options: increase semaphore, run agents in separate worker processes, or accept sequential execution for MVP.

---

## 15. Self-Critique (Revised)

### What's Good

- **Builds on existing infrastructure**: Memory, scheduling, MCP, RAG, ChatAgent — all exist. No new frameworks.
- **Configuration over code generation**: Dramatically simpler, safer, and more maintainable than having an AI write Python agent classes.
- **Agent UI MCP as message bus**: Reuses the existing MCP server for inter-agent communication instead of building a new pub/sub system. Every message is visible and auditable in the UI.
- **Clear autonomy boundaries**: Agents can research and organize but can't take external actions without approval.
- **Minimal agent count**: 3+1 agents covers the core journey without coordination explosion.
- **Structured envelope + natural language body**: Machine-parseable routing metadata with graceful degradation if malformed.
- **Per-agent section files**: Eliminates concurrency hazard while keeping everything RAG-indexed and user-editable.
- **Dedicated tasks table**: Proper relational schema for task lifecycle instead of overloading KnowledgeDB.
- **Interview validation step**: User confirms extracted profile before team creation, catching LLM interpretation errors.
- **Eval scenarios + disclaimer enforcement**: Quality gates for a domain where bad advice has real consequences.

### Issues Addressed (from first draft)

| Original Concern | Resolution |
|---|---|
| Convention-based messaging is fragile | Structured JSON envelope with `---agent-message---` delimiters (§4) |
| Scheduler-driven execution is slow | `execute_now()` trigger for high-priority inter-agent delegation (§8) |
| Business plan concurrency hazard | Per-agent section files, each agent owns one file exclusively (§6) |
| Tasks don't belong in KnowledgeDB | Dedicated `agent_tasks` table with proper indexes (§5) |
| No quality feedback loop | Eval scenarios, user ratings, disclaimer enforcement (§10) |
| LLM might extract wrong profile data | Structured confirmation step before team creation (§2) |

### Remaining Concerns

1. **RAG corpus quality is still the ceiling.** IRS publications are dense. We may need curated "plain English" guides or lean more heavily on web search for state-specific questions.

2. **The chat semaphore (1) serializes all agent execution.** If the FormationAgent is running, a triggered ComplianceAgent execution queues behind it. For a 4-agent team, this could cause noticeable delays during busy periods. May need to increase the semaphore or run agents in separate processes.

3. **Agent prompt drift over long sessions.** As an agent accumulates conversation history (20+ messages of task reports), the system prompt's instructions may get diluted by context. Periodic session summarization or session rotation (new session per scheduled run) might be needed.

4. **No mechanism for the user to "pause the team."** If the user goes on vacation or pauses their business plans, all agents keep running on schedule. Need a team-level pause/resume.

5. **Profile evolution is underspecified.** When the user hires their first employee, the team should adapt (add HR tasks, update compliance requirements). The spec says agents "adapt" but doesn't specify the mechanism — does the BusinessAdvisor detect this and update the profile? Does the user re-run the interview?

---

*Small Business Agent Team MVP Specification*

---

<small style="color: #666;">

**License**

Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.

SPDX-License-Identifier: MIT

</small>
