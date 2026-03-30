# Small Business Agent Team

**Date:** 2026-03-30
**Foundation:** [Multi-Agent Architecture](multi-agent-architecture.md)
**Milestones:** v0.19.0 (Architecture), v0.20.0 (Memory), v0.23.0 (Autonomous)

---

## 1. Vision

A user tells GaiaAgent "I want to start a business." GaiaAgent conducts a friendly, conversational interview, then assembles a team of specialist agents that work together — autonomously and on schedule — to help the user form, comply, and operate their business. All running locally, zero cloud cost, complete privacy.

**This is the first application built on GAIA's multi-agent architecture** — proof that the platform works for real, complex, multi-agent workflows.

### Why Small Business?

| Reason | Detail |
|--------|--------|
| **High value** | Starting a business is stressful, expensive, and mistake-prone. An AI team that handles formation, compliance, and finance is immediately useful. |
| **Always-on + sensitive data** | Tax deadlines, financial records, legal documents — the exact intersection where local AI has the strongest advantage over cloud (strategy doc §9.5 Tier 1-2). |
| **Multi-agent showcase** | Requires orchestration, inter-agent communication, task dependencies, shared memory, scheduled execution, and human approval gates — exercises every layer of the architecture. |
| **Scalable template** | The same blueprint pattern works for dev teams, research teams, home automation teams, healthcare teams. Small business is the first of many. |

---

## 2. Architecture: Built on Multi-Agent Platform

This team runs entirely on the multi-agent architecture defined in [multi-agent-architecture.md](multi-agent-architecture.md). No custom infrastructure.

```
GaiaAgent (0.6B NPU — conducts interview, narrates progress, coordinates team)
  │
  ├── Agent MCP Server (all-to-all communication bus)
  │     ├── create_task() — delegate work between agents
  │     ├── send_to_agent() — direct agent-to-agent messages
  │     └── request_and_wait() — synchronous queries
  │
  ├── Shared Memory (per-agent namespaces)
  │     ├── GaiaAgent: user preferences, interview profile
  │     ├── FormationAgent: entity status, filings, EIN
  │     ├── ComplianceAgent: deadlines, permits, tax obligations
  │     ├── FinanceAgent: transactions, invoices, cash flow
  │     └── Shared: BusinessProfile, user_profile
  │
  ├── Shared Workspace (~/.gaia/teams/austin_bites/)
  │     ├── README.md (GaiaAgent)
  │     ├── formation.md (FormationAgent)
  │     ├── compliance.md (ComplianceAgent)
  │     └── finance.md (FinanceAgent)
  │
  └── Specialist Agents
       ├── FormationAgent — entity selection, state filing, EIN, operating agreements
       ├── ComplianceAgent — tax obligations, permits, licenses, deadlines
       └── FinanceAgent — bookkeeping, invoicing, cash flow, expense tracking
```

### What the Multi-Agent Architecture Provides

| Capability | How Small Business Uses It |
|-----------|--------------------------|
| **GaiaAgent personality** | Conducts the interview conversationally, narrates team progress, is the user's single point of contact |
| **All-to-all comms** | FormationAgent gets EIN → sends directly to ComplianceAgent AND FinanceAgent. No bottleneck through GaiaAgent. |
| **Task dependencies** | "Set up payroll" depends_on ["Get EIN", "Open business bank account"]. Auto-starts when both complete. |
| **Shared memory** | BusinessProfile readable by all agents. Each agent's insights visible to all others. |
| **Agent spawning** | GaiaAgent spawns the team after interview. Can add MarketingAgent later when business reaches "operating" stage. |
| **Agent Registry** | Business agents register at spawn. GaiaAgent discovers them. User sees them in Agent UI. |
| **Per-task windows** | User sees "Form LLC" task, "Get EIN" task, "Set Up QuickBooks" task — each with its own conversation thread. |
| **Security boundaries** | Government filings require user approval. Financial transactions require user approval. Research is autonomous. |
| **Scheduled execution** | ComplianceAgent runs weekly to check deadlines. FinanceAgent runs daily to reconcile. |
| **CodeAgent** | User says "I need help with marketing" → CodeAgent builds a MarketingAgent with social media + content tools. |

---

## 3. The Interview

GaiaAgent conducts the interview — not a separate InterviewAgent. This is GaiaAgent being its natural self: friendly, curious, helpful.

### Why GaiaAgent, Not a Separate InterviewAgent?

The interview IS a conversation. GaiaAgent is the conversational partner. Creating a separate InterviewAgent would mean:
- User talks to InterviewAgent for 5 minutes, then gets handed off to GaiaAgent — jarring
- Two agents with personality fighting for voice — confusing
- Extra complexity for zero benefit

GaiaAgent handles the interview using the small business blueprint's interview questions as guidance. After confirmation, it spawns the team.

### Flow

```
GaiaAgent: "Hey! I can help you start a business. Tell me about what you're planning."

User: "I want to start a food truck in Austin"

GaiaAgent: "A food truck in Austin — great market for that! Let me ask a few questions
so I can set up the right team for you.

What's the name of your business?"

User: "Austin Bites"

GaiaAgent: "Love it. Are you planning to be a sole proprietor, or have you thought
about an LLC? An LLC gives you liability protection — if someone slips near
your truck, your personal assets are protected."

User: "LLC sounds right"

[...5-10 minutes of conversational interview...]

GaiaAgent: "Here's what I've got:

  Business: Austin Bites Food Truck LLC
  Owner: Maria Santos
  Industry: Food Service
  State: Texas (Austin)
  Entity: LLC
  Stage: Forming (not yet filed)
  Tools: Square POS, personal checking
  Budget: $500-2000/month

Does this look right? I'll set up your team once you confirm."

User: "Looks good, let's go!"

GaiaAgent: "Setting up your team now! You'll have three specialists:
📋 FormationAgent — handles LLC filing, EIN, operating agreement
📊 ComplianceAgent — tracks tax deadlines, permits, licenses
💰 FinanceAgent — bookkeeping, invoicing, cash flow

I'll keep you posted as they get to work."

[Spawns 3 specialists via Agent MCP Server]
[Each registers in Agent Registry]
[Initial tasks created with dependencies]
[User sees tasks appear in Agent UI sidebar]

GaiaAgent: "FormationAgent is starting on your LLC filing with the Texas
Secretary of State. I'll let you know when there's progress."
```

### Business Profile

Stored in shared memory as `user_profile` (accessible to all agents):

```python
@dataclass
class BusinessProfile:
    owner_name: str
    business_name: str
    business_description: str
    entity_type: Optional[str]        # "llc", "s_corp", "sole_prop"
    industry: str                     # "food_service", "saas", "retail"
    business_model: str               # "product", "service", "subscription"
    state: str                        # US state code
    city: Optional[str]
    operates_online: bool
    ships_physical_goods: bool
    stage: str                        # "idea", "forming", "operating", "scaling"
    has_employees: bool
    employee_count: int = 0
    existing_tools: List[str]
    monthly_budget_range: Optional[str]
    tech_comfort: str                 # "beginner", "intermediate", "advanced"
```

---

## 4. The Team

### Agent Roster

| Agent | Model | Focus | Scheduled |
|-------|-------|-------|-----------|
| **GaiaAgent** | 0.6B (NPU) | Orchestration, interview, progress narration, user interaction | Always active |
| **FormationAgent** | 1.7B (shared base) | Entity selection, state filing, EIN, operating agreements | On-demand |
| **ComplianceAgent** | 1.7B (shared base) | Tax obligations, permits, licenses, deadline monitoring | Weekly (Mondays) |
| **FinanceAgent** | 1.7B (shared base) | Bookkeeping, invoicing, cash flow, expense tracking | Daily |

**Memory footprint:** GaiaAgent (0.6B, 500MB) + one shared 1.7B base with 3 LoRA adapters (2.03GB) = **~2.5GB total**. Fits on any Ryzen AI PC.

### Why Only 3 Specialists?

More agents = more coordination overhead. Three covers the "I just started a business, now what?" journey. Additional agents spawn when needed:

| Business Stage | Agents Added | Trigger |
|---------------|-------------|---------|
| **idea** | None (GaiaAgent + interview only) | — |
| **forming** | FormationAgent, ComplianceAgent | Profile confirmed |
| **operating** | FinanceAgent | Entity formed |
| **scaling** | MarketingAgent, HRAgent, OperationsAgent | User requests or GaiaAgent suggests |

Scaling-stage agents can be built by **CodeAgent** on demand — "I need help with social media marketing" → CodeAgent creates MarketingAgent with relevant tools.

### Agent Configuration (Not Code Generation)

Each specialist is a base Agent instance with configuration injected at spawn time:
- System prompt template populated with business context ({{business_name}}, {{state}}, etc.)
- LoRA adapter for domain-specific tool calling accuracy
- RAG corpus pre-indexed (IRS publications, state filing guides)
- Initial tasks from blueprint
- Registered in Agent Registry for discovery

### Domain-Specific Guardrails

```
All agents include disclaimer:
"I'm an AI assistant. My guidance is informational only. Please consult a
qualified professional (lawyer, CPA, etc.) for legal or tax decisions."

Human approval REQUIRED for:
- Filing with government agencies (state SOS, IRS)
- Spending money (filing fees, subscriptions, payments)
- Sending external communications (emails, mail)
- Making legal or tax elections
- Connecting to third-party services (OAuth)

Autonomous actions (no approval needed):
- Research (web search, document Q&A)
- Generating documents and checklists
- Organizing information and summaries
- Updating workspace files
- Inter-agent communication
- Setting reminders and schedules
```

---

## 5. Task Workflow Example

### "Form an LLC in Texas"

This shows the full multi-agent system in action:

```
GaiaAgent spawns team after interview confirmation.

INITIAL TASKS (created by GaiaAgent, visible in Agent UI):

Task 1: "Reserve business name"
  assigned: FormationAgent
  priority: high
  depends_on: []

Task 2: "Research Texas LLC requirements"
  assigned: FormationAgent
  priority: high
  depends_on: []

Task 3: "File Articles of Organization"
  assigned: FormationAgent
  priority: high
  depends_on: [Task 1, Task 2]  ← BLOCKED until both complete

Task 4: "Apply for EIN"
  assigned: FormationAgent
  priority: high
  depends_on: [Task 3]  ← BLOCKED until filing complete

Task 5: "Identify required permits for food service in Austin"
  assigned: ComplianceAgent
  priority: medium
  depends_on: []  ← Can start immediately, parallel with Task 1-2

Task 6: "Set up quarterly tax reminders"
  assigned: ComplianceAgent
  priority: medium
  depends_on: [Task 4]  ← BLOCKED until EIN obtained

Task 7: "Set up bookkeeping system"
  assigned: FinanceAgent
  priority: medium
  depends_on: [Task 4]  ← BLOCKED until EIN obtained
```

**What the user sees in Agent UI:**

```
TASKS                          MAIN WINDOW
● Chat with Gaia               GaiaAgent: "Your team is getting started!
◐ Reserve business name (Form)  FormationAgent is checking name availability
◐ Research TX LLC reqs (Form)   and researching Texas requirements.
⊘ File Articles (Form) [blocked] ComplianceAgent is looking into food
⊘ Apply for EIN (Form) [blocked] service permits in Austin."
◐ Find permits (Compliance)
⊘ Set up tax reminders [blocked] [5 minutes later...]
⊘ Set up bookkeeping [blocked]
                                GaiaAgent: "Good news — 'Austin Bites LLC'
AGENTS                          is available! FormationAgent is ready to
● Gaia                          file. This costs $300 with the Texas SOS.
● Formation                     Should I go ahead?"
● Compliance
○ Finance [idle - waiting]      User: "Yes, file it"

                                GaiaAgent: "Filing now. I'll let you know
                                when it's confirmed."
```

### Inter-Agent Communication (all-to-all)

When FormationAgent completes EIN application:

```
FormationAgent → ComplianceAgent (via send_to_agent):
  "EIN obtained: 12-3456789. Entity: LLC. State: TX. Filed: 2026-03-15."

FormationAgent → FinanceAgent (via send_to_agent):
  "EIN obtained: 12-3456789. You can now set up the business bank account
   and bookkeeping system."

FormationAgent → GaiaAgent (via complete_task):
  Task 4 completed. Result: "EIN 12-3456789 obtained from IRS.gov"
```

GaiaAgent receives the completion event and narrates:

```
GaiaAgent: "Great news — you've got your EIN: 12-3456789! I've let
ComplianceAgent know so it can set up your tax reminders, and
FinanceAgent is starting on your bookkeeping system."
```

**Tasks 6 and 7 auto-unblock** (depends_on Task 4 now complete) → ComplianceAgent and FinanceAgent pick them up.

---

## 6. Shared Workspace

Per-agent owned files, RAG-indexed for cross-agent access:

```
~/.gaia/teams/austin_bites/
├── README.md        (GaiaAgent — team status dashboard)
├── formation.md     (FormationAgent — entity & legal status)
├── compliance.md    (ComplianceAgent — tax & regulatory status)
├── finance.md       (FinanceAgent — financial status)
└── profile.json     (read-only — BusinessProfile from interview)
```

Each agent updates its own file after completing tasks. GaiaAgent updates README.md with overall status. All files indexed via RAG so any agent can query the full business context.

---

## 7. Memory Usage

### Per-Agent Namespaces (from multi-agent architecture #676)

| Agent | Writes | Example Insights |
|-------|--------|-----------------|
| **GaiaAgent** | User preferences, delegation patterns | "User prefers email updates over chat notifications" |
| **FormationAgent** | Filing status, entity decisions, document locations | "LLC filed 2026-03-12, confirmation #TX-2026-44821" |
| **ComplianceAgent** | Deadline tracking, permit status, tax obligations | "Q1 estimated tax due April 15, $1,200 estimated" |
| **FinanceAgent** | Transaction patterns, account balances, invoice status | "Average monthly revenue: $8,500. Biggest expense: food supplies" |

### Cross-Agent Intelligence

```
FormationAgent writes: "Operating agreement requires annual member meeting"
  → ComplianceAgent reads this → adds annual meeting to deadline tracker

ComplianceAgent writes: "Food handler's permit expires Dec 2026"
  → GaiaAgent reads this → reminds user 60 days before expiration

FinanceAgent writes: "Cash flow negative for 3 consecutive months"
  → GaiaAgent reads this → proactively asks user if they want to discuss options
```

---

## 8. Eval: Proving the Team Works

### Ground Truth Scenarios

Each scenario represents a specific business type with verifiable facts:

| Scenario | Business | Key Assertions |
|----------|---------|---------------|
| **Texas Food Truck LLC** | Food service, Austin TX, forming | TX SOS filing = $300, need food handler's permit, quarterly estimated tax |
| **Delaware SaaS S-Corp** | Software, remote, operating | DE franchise tax, S-Corp election (Form 2553), no sales tax on SaaS in DE |
| **California Retail Sole Prop** | Retail, LA CA, forming | CA requires seller's permit, BOE registration, city business license |
| **New York Consulting LLC** | Professional services, NYC, scaling | NY LLC publication requirement, NYC UBT, quarterly estimated tax |
| **Oregon E-commerce LLC** | Online retail, Portland OR, forming | No sales tax in OR, but nexus states may require collection |

Each scenario has 10-15 factual assertions that can be verified against IRS publications and state regulations.

### What We're Measuring

1. **Interview quality** — Did GaiaAgent extract the right business profile from a natural conversation?
2. **Task planning** — Were the right tasks created with correct dependencies?
3. **Specialist accuracy** — Did FormationAgent recommend the right entity type for the scenario?
4. **Inter-agent coordination** — Did EIN flow from FormationAgent to ComplianceAgent and FinanceAgent?
5. **Deadline accuracy** — Are tax deadlines correct for the specific state and entity type?
6. **Guardrail compliance** — Did agents request human approval before filing/spending?
7. **Progress narration** — Did GaiaAgent keep the user informed throughout?

---

## 9. Dynamic Team Assembly (Not Templates)

The small business team is NOT a static template. GaiaAgent reasons about what specialists are needed based on the interview conversation, checks the Agent Registry for existing agents, and asks CodeAgent to build any that don't exist.

### How GaiaAgent Reasons

After the interview, GaiaAgent has the full business context. It reasons:

```
Business: Food truck LLC in Austin, TX. Stage: forming.

What does this business need?
1. Entity formation — LLC filing, EIN, operating agreement → FormationAgent ✓ (exists)
2. Tax compliance — Texas franchise tax, quarterly estimated, sales tax → ComplianceAgent ✓ (exists)
3. Finances — Bookkeeping, invoicing, cash flow → FinanceAgent ✓ (exists)
4. Food service permits — health department, food handler's cert → No specialist exists
   → Ask CodeAgent to build one

What tasks should each agent start with?
- FormationAgent: reserve name → research TX LLC requirements → file → get EIN
- ComplianceAgent: identify required permits (parallel with formation)
- FinanceAgent: set up bookkeeping (blocked on EIN)
- PermitAgent: research Austin health department requirements (parallel)
```

This reasoning scales to any business type. A SaaS company in Delaware gets different agents than a food truck in Texas — not because different templates exist, but because GaiaAgent reasons differently about what's needed.

### CodeAgent Fills Gaps

When GaiaAgent needs a specialist that doesn't exist, CodeAgent builds it:

```
GaiaAgent → CodeAgent: "Build a specialist for Texas food service permits.
  It needs web search to find current requirements, document generation
  for permit applications, and deadline tracking for renewal dates."

CodeAgent:
  1. Reads src/gaia/agents/base/agent.py (agent pattern)
  2. Reads existing ComplianceAgent as reference (similar domain)
  3. Creates src/gaia/agents/permits/agent.py
  4. Writes focused system prompt with food service + Texas context
  5. Registers tools: search_web, write_file, query_documents
  6. Registers in Agent Registry
  7. Writes tests
```

**This is how the platform extends itself.** No human developer needed to add support for food trucks in Texas. The agents figure it out.

---

## 10. What This Proves About the Architecture

If the small business team works, it validates every layer:

| Architecture Layer | Validated By |
|-------------------|-------------|
| **GaiaAgent personality** | Interview is natural, fun, extracts correct profile |
| **Agent spawning** | Team instantiated from blueprint after confirmation |
| **Task dependencies** | EIN blocks tax setup and bookkeeping — auto-unblocks correctly |
| **All-to-all comms** | FormationAgent → ComplianceAgent + FinanceAgent directly |
| **Shared memory** | BusinessProfile readable by all; each agent's insights visible to others |
| **Shared workspace** | Per-agent files updated, RAG-indexed, cross-agent queryable |
| **Scheduled execution** | ComplianceAgent runs weekly, FinanceAgent runs daily |
| **Security boundaries** | Filing/spending requires user approval, research is autonomous |
| **Progress narration** | GaiaAgent narrates every milestone to user |
| **Agent UI** | Tasks visible in sidebar, per-task conversations, agent status panel |
| **Domain guardrails** | Disclaimers on all legal/tax advice |
| **CodeAgent extension** | User asks for marketing help → new agent spawned |

**If this works, the architecture works for anything** — dev teams, research teams, home automation, healthcare, education. The small business team is the proof.
