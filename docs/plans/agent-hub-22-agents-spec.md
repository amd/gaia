# Agent Hub: 22-Agent Enablement Spec

> **Status:** Draft / Planning
> **Depends on:** Agent Hub Platform (v0.29, milestone #21), `docs/plans/agent-hub-ui.mdx`

---

## 1. Purpose

This spec defines how to ship **22 purpose-built, autonomous agents** through the GAIA Agent Hub. It is the implementation companion to the strategic analysis that identified the agents; this document is concerned only with *how to build and ship them*.

The core thesis: **we are not building 22 agents — we are building 7 shared infrastructure layers, on top of which each agent is a thin package** (a `gaia-agent.yaml` manifest + a system prompt + tool bindings + a memory schema + activation triggers). This is exactly what the Agent Hub packaging model (`hub/agents/<name>/python/`) and the SKILL.md format are designed to enable.

### Goals

1. Every agent is a standalone Hub package under `hub/agents/<id>/python/` that `import gaia` from the published `amd-gaia` wheel.
2. Every agent is **packaged, native, and compiled** into one self-contained artifact exposing **all five interfaces** (TUI, CLI, pipe, API server, MCP server), published to **R2** and surfaced on both the **GAIA website** and the **Agent UI**. Python first; each agent has a C++ supersession path that keeps the same id, manifest, and interfaces.
3. Every agent follows the **proactive interaction model**: it discovers the user's context, proposes actions, and acts with consent — never a blank text box.
4. Every agent embodies the north star: **autonomous, personalized, adaptive, local, with memory.**
5. Shared capabilities (web search, connectors, VLM, notifications, autonomy) are built once as reusable mixins/MCP tools and composed by name.

### Build order: Hub first, agents native

**The Hub platform is built first; all 22 agents are implemented directly as Hub packages in `hub/agents/<id>/python/` from inception.** There is no in-tree-then-migrate path for these agents — they are Hub-native from their first commit, importing `gaia` from the published `amd-gaia` wheel exactly like third-party agents will. This means:

- The Hub restructure (#1102) and manifest/registry/packaging work (Phases 0–2 of `agent-hub-ui.mdx`) are a **hard prerequisite** for agent A1, not a parallel track.
- Each agent is authored with `gaia agent init <id>` → developed in `hub/agents/<id>/python/` → validated with `gaia agent test` → shipped with `gaia agent publish`. The same workflow we expect external contributors to use — we dogfood it for all 22.
- No agent code lands in `src/gaia/agents/` (which stays framework-only: base classes, shared mixins, registry).

### Non-goals

- Building the Hub platform itself (manifest parser, R2 distribution, install API) — that is the v0.29 milestone and a **hard prerequisite** (see Build order above), not part of this spec's scope.
- Cloud/Arena hosting of these agents — out of scope; these are local-first.
- Migrating the framework's existing in-tree agents — that is the Hub restructure (#1102), separate from these 22 net-new Hub-native agents.

---

## 2. Architecture

### 2.1 The two-layer model

```
┌─────────────────────────────────────────────────────────────────┐
│  AGENT LAYER (thin — per agent: manifest + prompt + bindings)     │
│                                                                   │
│  morning  email  knowledge  smarthome  writing  meeting  finance  │
│  files    health  learning  photo  crm  freelance  journal  …     │
└───────────────┬───────────────────────────────────────────────────┘
                │ composes by name
┌───────────────▼───────────────────────────────────────────────────┐
│  SHARED INFRASTRUCTURE LAYER (build once, reused everywhere)        │
│                                                                    │
│  L1 Memory      L2 Web Search   L3 Connectors   L4 VLM/Media       │
│  L5 Filesystem  L6 Notifications L7 Autonomy + Graduated Trust      │
└───────────────┬───────────────────────────────────────────────────┘
                │ import gaia
┌───────────────▼───────────────────────────────────────────────────┐
│  GAIA FRAMEWORK (amd-gaia wheel)                                    │
│  Agent · @tool · MCPAgent · ApiAgent · ToolLoader · LLM clients     │
└────────────────────────────────────────────────────────────────────┘
```

### 2.2 Anatomy of an agent package

Each agent is a directory under `hub/agents/<id>/python/`:

```
hub/agents/email/python/
├── gaia-agent.yaml          # manifest (Hub display, requirements, interfaces, permissions)
├── pyproject.toml           # dependencies = ["amd-gaia>=0.20.0", "gaia-skill-connectors"]
├── gaia_agent_email/
│   ├── __init__.py
│   ├── agent.py             # class EmailAgent(Agent, MemoryMixin, ConnectorMixin, …)
│   ├── prompt.py            # system prompt (proactive, domain-specific)
│   ├── tools.py             # @tool methods unique to this agent
│   ├── memory_schema.py     # what this agent remembers between sessions
│   └── triggers.py          # when this agent proactively acts (cron / event hooks)
├── tests/
│   ├── test_unit.py
│   └── scenarios/           # eval scenarios (YAML) for gaia eval agent
└── README.md
```

**The thin-layer principle in numbers:** the framework + shared layers are ~thousands of LOC each; a new agent on top is typically **150–500 LOC of glue** plus a manifest and prompt. If an agent needs more than that, it usually means a missing shared capability that should be promoted to L1–L7.

### 2.3 Packaging & interface standard (mandatory for all 22)

Every agent ships as **one self-contained, compiled, published package** that exposes **all five interfaces** — no agent is "CLI-only" or "API-only". This is the 5-interface standard from `agent-hub-ui.mdx`, made a hard requirement here.

| Interface | What it is | How the agent gets it |
|-----------|-----------|----------------------|
| **TUI** | Interactive terminal UI (Bubble Tea / Rich) | Hub runner renders the agent's stream — `gaia agent run <id>` |
| **CLI** | One-shot command | `gaia agent run <id> --prompt "…"` |
| **pipe** | stdin→stdout for shell composition | `echo "…" \| gaia agent run <id> --pipe` |
| **API server** | OpenAI-compatible REST endpoint + an **OpenAPI spec** shipped in the package | `ApiAgent` mixin → `gaia agent run <id> --api` |
| **MCP server** | stdio + HTTP MCP server exposing the agent's tools | `MCPAgent` mixin → `gaia agent run <id> --mcp` |

**The framework provides the harness; the agent gets all five for free.** A correct `Agent` subclass composing `ApiAgent` + `MCPAgent` automatically yields the API and MCP servers; TUI/CLI/pipe come from the Hub runner. The agent author writes one agent class and declares `interfaces: {all: true}` in the manifest — they do not hand-build five entry points.

**Native + compiled, Python now → C++ later.** Each agent is "native" in the sense that it is a compiled, dependency-isolated, distributable artifact — not a loose script:

- **Python (start):** built into a wheel via `gaia agent pack`, installed isolated under `~/.gaia/agents/{id}/site-packages/`. `language: python` in the manifest.
- **C++ (supersession):** the same agent re-implemented as a statically-linked native binary (vcpkg, CI matrix for win-x64/linux-x64/darwin-arm64), driven through the **C++ subprocess launcher** (`NativeAgentLauncher`, JSON-RPC over stdio) so it runs in both Electron and web contexts. `language: cpp` in the manifest. The 5-interface contract is identical — a consumer can't tell whether the agent behind the API/MCP endpoint is Python or C++.
- The manifest `language` field and the launcher abstraction mean an agent can be **upgraded from Python to C++ without consumers changing anything** — same id, same interfaces, same manifest shape.

**Build → publish → surface pipeline (every agent, every release):**

```
gaia agent pack            # compile: wheel (py) or static binary (cpp) + OpenAPI spec + manifest
gaia agent publish         # validate → test → sign → upload to R2
   └─> R2 bucket           # agents/{id}/{version}/{artifact, manifest.json, openapi.json}
   └─> Cloudflare Worker   # rebuilds index.json from per-agent manifests
        ├─> GAIA website   # amd-gaia.ai/hub — browse, search, "Try / Install"  (#1178)
        └─> Agent UI       # Installed / Available tabs, compatibility checks, install flow (#1097)
```

Both surfaces (website + Agent UI) read the **same R2 `index.json`** — one source of truth. The website is the public discovery front door; the Agent UI is the install-and-run surface on the user's machine.

### 2.4 The proactive interaction contract

Every agent MUST implement three lifecycle hooks (added to the base `Agent` as part of L7):

| Hook | When | Behavior |
|------|------|----------|
| `on_first_run(context)` | First time the user opens the agent | Discover the user's relevant data, propose 2–4 concrete actions. Never act without consent. |
| `on_heartbeat(context)` | Scheduled (cron) or event-triggered | Do autonomous work within the agent's domain at the user's granted trust level; queue results. |
| `propose(action)` | Any time the agent wants to act above its trust level | Surface a proposal with a one-click approve/deny. Record the decision (implicit feedback → L1). |

---

## 3. Shared Infrastructure Layers (L1–L7)

Each layer is an **epic**. Agents cannot ship reliably until their required layers exist. The dependency-aware build order is L1→L7 but several are already partially shipped.

### L1 — Memory & Personalization Engine
**Status:** Largely shipped (Memory v2, #606). Gaps: per-agent namespaces, behavioral pattern detection, implicit feedback capture.

> **Memory ≠ datastore.** Three kinds of persistent state are easy to conflate. L1 covers only the first:
>
> 1. **`memory` (L1 personalization)** — preferences, learned behavior, implicit feedback. The "knows YOU" layer.
> 2. **`datastore:<type>` (domain persistence)** — data the agent manages, *not* personalization: `rag` (doc/vector index), `media` (CLIP/photo+face index), `ledger` (transactions), `timeseries` (time-stamped metrics), `fileindex` (semantic file index), `archive` (transcripts/docs), `records` (structured entities like contacts/inventory). These are agent-owned stores, built per agent, not part of L1.
> 3. **session state** — within-conversation context. Not persisted, not L1.
>
> Each agent's **memory tier** is one of: **essential** (pointless without it), **enhance** (works without, better with), **optional** (on-demand use needs none). Only `essential` agents gate on the full L1 engine (esp. AH-L1.2 behavioral detection); `enhance`/`optional` agents can ship a v1 with light or no L1 and add personalization as a fast-follow. The canonical per-agent split is the dependency table in §9.

| Issue | Scope | Est. | Status |
|-------|-------|------|--------|
| AH-L1.1 | Per-agent memory namespaces (isolate each agent's memory, shared read where opted in) | M | #676 exists |
| AH-L1.2 | Behavioral pattern detection ("you always do X after Y → offer to automate") | L | **new** |
| AH-L1.3 | Implicit feedback capture (did the user accept/edit/ignore an agent action?) | M | **new** |
| AH-L1.4 | Memory schema API so each agent declares its persistent fields | S | **new** |

### L2 — Web Search & Extraction
**Status:** Not shipped as a composable tool. #669 (DuckDuckGo/Perplexity) and Knowledge Agent's Tavily work (#1141, #1144) overlap.

| Issue | Scope | Est. | Status |
|-------|-------|------|--------|
| AH-L2.1 | `web_search` MCP tool — provider-abstracted (DuckDuckGo default, Tavily/Perplexity optional) | M | #669 / #1144 |
| AH-L2.2 | `web_extract` — fetch + clean + chunk a URL into RAG-ingestable text | S | #1144 |
| AH-L2.3 | Register `web_search` in `KNOWN_TOOLS` so any agent composes it by name | S | **new** |

### L3 — Connectors (Email / Calendar / Contacts / OAuth)
**Status:** In progress. Gmail merged (#965), connectors framework (#927), MS OAuth (#1105). **Outlook #963 is CLOSED** — verify whether the Outlook backend actually landed before relying on it; if it was closed without a merged implementation, L3.2 needs a fresh issue.

| Issue | Scope | Est. | Status |
|-------|-------|------|--------|
| AH-L3.1 | OAuth connector framework GA (Google + Microsoft) | L | #927, #1105 |
| AH-L3.2 | Email backend Protocol (Gmail + Outlook implementations) | M | #963 (CLOSED — verify) |
| AH-L3.3 | Calendar connector (Google Calendar + MS Graph) | M | **new** (≠ #660, which is Playwright browser automation) |
| AH-L3.4 | Contacts connector (read contacts/people for CRM agent) | M | **new** |
| AH-L3.5 | `ConnectorMixin` — agents declare required connectors; UI prompts for grant | S | #735 |

### L4 — VLM & Media Processing
**Status:** VLMToolsMixin exists; needs photo-library indexing + receipt/document extraction.

| Issue | Scope | Est. | Status |
|-------|-------|------|--------|
| AH-L4.1 | Photo-library semantic index (CLIP embeddings + face grouping) as MCP server | L | #730 (partial) |
| AH-L4.2 | Document/receipt structured extraction via VLM (key-value, totals) | M | #664 (partial) |
| AH-L4.3 | `media_search` tool — natural-language query over local photo/video index | M | **new** |

### L5 — Filesystem & System Discovery
**Status:** FileSearchToolsMixin + FileIOToolsMixin shipped. Needs system scanner + semantic file index.

| Issue | Scope | Est. | Status |
|-------|-------|------|--------|
| AH-L5.1 | System scanner (hardware, installed apps, file types, available MCP servers) | M | #466 |
| AH-L5.2 | Semantic filesystem index (tag/search files by content) | M | **new** |
| AH-L5.3 | Safe write/move/dedup operations with confirmation tiers | S | exists (file_io) |

### L6 — Proactive Notifications
**Status:** Not shipped. System tray (#643) + messaging (#635, #693) are downstream. Needs a lightweight delivery primitive now.

| Issue | Scope | Est. | Status |
|-------|-------|------|--------|
| AH-L6.1 | Notification primitive (desktop toast + in-UI feed + optional webhook) | M | **new** |
| AH-L6.2 | Notification preferences (per-agent, quiet hours, batching) | S | **new** |
| AH-L6.3 | Messaging delivery adapters (Signal first, then Telegram) | L | #693, #635 |

### L7 — Autonomy Engine & Graduated Trust
**Status:** Designed (`docs/plans/autonomy-engine.mdx`), not implemented. Critical path for "autonomous."

| Issue | Scope | Est. | Status |
|-------|-------|------|--------|
| AH-L7.1 | Heartbeat scheduler + cron + event hooks | L | #634 |
| AH-L7.2 | Autonomous loop (think-act-schedule) | L | #557 |
| AH-L7.3 | Graduated trust model (supervised → semi-auto → autonomous per tool tier) | M | **new** (≠ #559 "dangerous mode", which is the inverse — full bypass, not incremental control) |
| AH-L7.4 | Lifecycle hooks on base Agent (`on_first_run`, `on_heartbeat`, `propose`) | M | **new** |
| AH-L7.5 | Background service + system tray | L | #643 |

---

## 4. The 22 Agents

Each agent below lists its **Hub id**, **required layers**, **unique tools**, **memory schema**, **proactive triggers**, and **effort**. Effort: S (≤1wk), M (1–2wk), L (2–4wk) for the agent glue *assuming its layers exist*.

> The `Layers:` lines below name L1 generically. The **canonical memory-vs-datastore split and memory tier (essential/enhance/optional)** for each agent is the dependency table in §9 — refer to it where they differ.

### Wave 1 — Ship on existing/near-term infrastructure

#### A1. `knowledge` — Knowledge & Research Agent
- **Layers:** L1, L2, L5 · **Unique tools:** `index_folder`, `query_kb`, `monitor_topic` · **Memory:** indexed sources, user interests, prior queries · **Triggers:** new files in watched folder → auto-index + summarize · **Effort:** S (RAG shipped) · **Existing:** Knowledge Agent milestone (#1141–#1148)

#### A2. `files` — File & Desktop Organizer
- **Layers:** L1, L5 · **Unique tools:** `scan_disk`, `suggest_organization`, `find_duplicates`, `semantic_find` · **Memory:** folder conventions, user-approved moves · **Triggers:** Downloads folder threshold → propose sort · **Effort:** S (file tools exist)

#### A3. `smarthome` — Smart Home Voice Agent
- **Layers:** L1, L6 + Home Assistant MCP · **Unique tools:** HA MCP (`get_states`, `call_service`, …), voice (Whisper/Kokoro) · **Memory:** device names, routines, preferences · **Triggers:** sensor events (door open, temp threshold) → notify/act · **Effort:** S (HA MCP exists; add to catalog) · **Existing:** #705, #646

#### A4. `code` — Code & Project Context Agent
- **Layers:** L1, L5 + git/code-index · **Unique tools:** `repo_status`, `restore_context`, `review_prs`, `dep_advisories` · **Memory:** active repos, session state, last-touched files · **Triggers:** repo opened → restore context; advisory published → alert · **Effort:** S (CodeAgent + index exist)

#### A5. `morning` — Morning Intelligence (news-only v1)
- **Layers:** L1, L2, L6, L7 (scheduler) · **Unique tools:** `compose_brief` · **Memory:** interests, read/skip history, delivery time · **Triggers:** daily cron → deliver brief · **Effort:** M · **Existing:** #663

### Wave 2 — Ship with Connectors (Email Agent platform, June 12)

#### A6. `email` — Email Companion
- **Layers:** L1, L3, L6, L7 · **Unique tools:** `triage`, `draft_reply`, `digest` · **Memory:** sender importance, reply style, classification rules · **Triggers:** new mail → classify + draft (graduated: propose → auto after 200 approvals) · **Effort:** M · **Existing:** Email Agent & Platform Foundations milestone

#### A7. `crm` — Personal CRM & Relationship Agent
- **Layers:** L1, L3 (email+calendar+contacts) · **Unique tools:** `build_profile`, `relationship_health`, `draft_followup` · **Memory:** contact profiles, interaction history, per-person tone · **Triggers:** dormant contact / upcoming occasion → propose outreach · **Effort:** M · **Existing:** #704

#### A8. `family` — Family Command Center
- **Layers:** L1, L3, L4 (flyer OCR), L6 · **Unique tools:** `parse_flyer`, `family_calendar`, `meal_plan`, `chore_track` · **Memory:** family members, schedules, preferences, dietary needs · **Triggers:** new school email → calendar event + notify · **Effort:** L

### Wave 3 — Ship with Memory dashboard + daily briefs (v0.24)

#### A9. `writing` — Writing & Communication Agent
- **Layers:** L1 · **Unique tools:** `learn_style`, `draft`, `tone_adjust` · **Memory:** style profile, per-recipient tone · **Triggers:** draft detected → offer expansion · **Effort:** M · **Status:** not currently planned — **new**

#### A10. `journal` — Journal & Reflection Agent
- **Layers:** L1, L6, L7 (scheduler) · **Unique tools:** `daily_checkin`, `detect_patterns`, `weekly_review` · **Memory:** entries, mood trends, goals · **Triggers:** daily check-in prompt; weekly review · **Effort:** S · **Status:** new

#### A11. `habit` — Habit & Routine Agent
- **Layers:** L1, L3 (calendar), L6, L7 · **Unique tools:** `track_routine`, `correlate_outcomes`, `nudge` · **Memory:** routines, correlations, streaks · **Triggers:** pattern breach (low sleep, no break) → nudge · **Effort:** S · **Status:** new

#### A12. `health` — Health & Wellness Agent
- **Layers:** L1, L4 (lab images), L6 · **Unique tools:** `import_wearable`, `analyze_labs`, `med_tracker`, `doctor_summary` · **Memory:** health metrics, medications, trends · **Triggers:** anomaly detection; medication reminder · **Effort:** M · **Existing:** EMR base (#770)

### Wave 4 — Ship with VLM/Multimodal (v0.25)

#### A13. `photo` — Photo & Memory Agent
- **Layers:** L1, L4 · **Unique tools:** `index_photos`, `face_group`, `media_search`, `highlight_reel`, `dedup` · **Memory:** people, events, favorites · **Triggers:** new photos → organize + propose albums · **Effort:** M (depends on L4.1)

#### A14. `meeting` — Meeting Companion
- **Layers:** L1, L3 (calendar), L6 + Whisper · **Unique tools:** `transcribe`, `diarize`, `extract_actions`, `meeting_prep` · **Memory:** past meetings, attendees, action items · **Triggers:** meeting starts → transcribe; ends → minutes + actions · **Effort:** M · **Existing:** #700

#### A15. `finance` — Personal Finance Agent
- **Layers:** L1, L4 (receipts), L6 · **Unique tools:** `import_transactions`, `categorize`, `detect_anomaly`, `spending_report` · **Memory:** categories, recurring expenses, budgets · **Triggers:** anomaly / unused subscription → alert · **Effort:** M · **Existing:** #707, #664

#### A16. `freelance` — Freelancer Business Agent
- **Layers:** L1, L3 (email), L4 (receipts), L6 · **Unique tools:** `track_project`, `generate_invoice`, `chase_payment`, `tax_estimate` · **Memory:** projects, contracts, hours, payments · **Triggers:** milestone reached → propose invoice; payment overdue → reminder · **Effort:** M · **Existing:** #708 (related)

### Wave 5 — Ship with Autonomy + Messaging (v0.28) / later

#### A17. `learning` — Learning & Self-Improvement
- **Layers:** L1, L2, L7 · **Unique tools:** `track_learning`, `make_flashcards`, `find_resources`, `quiz` · **Memory:** topics, knowledge gaps, review schedule · **Triggers:** spaced-repetition due → quiz · **Effort:** M · **Status:** new

#### A18. `deals` — Deal & Price Watch
- **Layers:** L1, L2, L6, L7 (scheduler) · **Unique tools:** `watch_product`, `track_price`, `assess_deal` · **Memory:** wishlist, price history · **Triggers:** price drop → alert · **Effort:** S · **Existing:** #480–#491

#### A19. `travel` — Travel Companion
- **Layers:** L1, L2, L3 (calendar), L6 · **Unique tools:** `find_trips`, `packing_list`, `itinerary`, `trip_journal` · **Memory:** travel preferences, past trips · **Triggers:** free window + watched destination price drop → propose trip · **Effort:** M

#### A20. `security` — Security & Privacy Guardian
- **Layers:** L1, L2 (breach DBs), L5, L6 + OS (v0.30) · **Unique tools:** `breach_check`, `password_audit`, `privacy_audit`, `file_sensitivity_scan` · **Memory:** monitored identities, known-safe state · **Triggers:** breach published → alert + remediation · **Effort:** M · **Existing:** OS Agents (v0.30)

#### A21. `presentation` — Presentation & Content Agent
- **Layers:** L1, L5 · **Unique tools:** `analyze_data`, `build_narrative`, `slide_outline`, `talking_points` · **Memory:** presentation style, audience profiles · **Triggers:** none (on-demand) · **Effort:** M

#### A22. `maintenance` — Home & Vehicle Maintenance
- **Layers:** L1, L2, L4 (photo diagnosis), L6, L7 (scheduler) · **Unique tools:** `inventory`, `maintenance_schedule`, `diagnose_photo`, `find_parts` · **Memory:** appliances, vehicles, service history · **Triggers:** maintenance due → reminder · **Effort:** M

---

## 5. Autonomy Model

The north star says **autonomous by default**. That does not mean "acts recklessly without asking" — it means the agent is *always working* (observing, preparing, proposing) instead of waiting at a blank prompt, and it earns the right to *commit* actions over time. This section defines what "autonomous by default" means concretely, what wakes an agent, and the per-agent user journey from install to steady state.

### 5.1 Default posture: three action classes

Every agent action falls into one of three classes, each with a different default autonomy:

| Class | Examples | Default autonomy | Confirmation |
|-------|----------|------------------|--------------|
| **Observe / read** | scan inbox, read files, fetch prices, read sensor state, transcribe | **Autonomous from first run** | None — never leaves the machine, never changes user-visible state |
| **Prepare / draft** | draft a reply, propose a file reorg, compose a brief, build an invoice | **Autonomous to produce, surfaced as a proposal** | Approve-to-apply (until graduated) |
| **Act / commit** | send email, move/delete files, `call_service`, purchase, post | **Gated** | Confirm each time, OR standing grant, OR after graduation |

So "autonomous by default" = the agent is **continuously doing class-1 and class-2 work in the background** and presenting a queue of *"here's what I noticed / drafted / can do for you."* The user reviews, not commands. Class-3 (irreversible, costly, outward-facing, or physical-world) stays gated until trust is explicitly earned.

### 5.2 Trigger taxonomy — what wakes an agent

Autonomy is driven by triggers wired through the L7 autonomy engine (heartbeat scheduler + event hooks). Six trigger types:

| Trigger | Mechanism (layer) | Examples | Agents (primary) |
|---------|-------------------|----------|------------------|
| **Scheduled** | cron via heartbeat (AH-L7.1) | morning brief @ 7am; weekly review Sun; spaced-repetition due | morning, journal, habit, learning, maintenance |
| **Event** | event hook (AH-L7.1) | new email; file added to watched folder; meeting started; repo opened | email, files, knowledge, meeting, code, family |
| **Threshold** | heartbeat + condition | Downloads > N files; spend > budget; sleep < target; filter age > 90d | files, finance, habit, maintenance |
| **Sensor / external** | event hook from an MCP server | HA device state change; breach-DB update; price drop > X% | smarthome, security, deals |
| **Inter-agent** | Agent MCP Server / delegation (#675) | email finds an attachment → knowledge; family flyer → calendar | all (as delegators/delegatees) |
| **Conversational** | direct user message | "what needs my attention?"; "plan a trip" | all |

A manifest declares its triggers in the `proactive` block (`heartbeat: "<cron>"`, event subscriptions). An agent with no schedule and no events (e.g. presentation) is purely conversational — still proactive *within* a session (it proposes), just not between sessions.

### 5.3 The autonomous loop

Each trigger runs one cycle of `on_heartbeat` (AH-L7.4) through the autonomous loop (AH-L7.2):

```
observe → decide → (propose │ act-within-trust) → report → learn
   │         │            │                          │        │
 read     is this      class-2: queue proposal    notify   write feedback
 state    worth        class-3: confirm/act        (L6)     signal (L1.3) +
          acting on?   class-1: just record                 update patterns (L1.2)
```

The `learn` step is what makes autonomy *improve*: every cycle records whether the user accepted, edited, or ignored the agent's output, feeding personalization (§6).

### 5.4 Graduated trust

Agents start cautious and earn autonomy. Trust progresses **per action class**, not globally:

| Stage | Behavior | How it advances |
|-------|----------|-----------------|
| **Supervised** (default day 0) | Proposes everything; commits nothing without per-action approval | — |
| **Semi-autonomous** | Auto-commits low-risk class-2 (drafts staged, files moved to a review area); still confirms class-3 | After *N* approvals of that action type (`proactive.trust_graduation.autonomous_after`), or explicit user grant |
| **Autonomous** | Acts within its domain and reports; surfaces only exceptions | Explicit standing grant per action, or sustained acceptance |

**Always-confirm floor:** irreversible, costly, outward-facing, or physical-world actions (send to external recipients, delete, purchase, `call_service` on a lock/oven) **never auto-fire from graduation alone** — they require an explicit standing permission the user sets deliberately. This is distinct from "dangerous mode" (#559), which is a blunt full-bypass; graduated trust is the safe ramp (AH-L7.3). A global kill switch (system tray, AH-L7.5) pauses all autonomy instantly.

### 5.5 User journey — framework

Every agent follows the same four-phase arc:

1. **Day 0 — First run (`on_first_run`).** The agent discovers the user's relevant context and proposes 2–4 concrete actions. No autonomous commits yet.
2. **Days 1–7 — Supervised.** The agent works in the background (observe + draft), surfaces proposals, learns from accept/edit/ignore. The user is approving, not commanding.
3. **Weeks 2–4 — Graduating.** Low-risk actions the user has repeatedly approved become automatic; the agent asks only for the rest.
4. **Steady state — Autonomous.** The agent handles its domain and reports outcomes; the user sees a digest of what happened and a short list of exceptions needing a decision.

### 5.6 User journey — worked archetypes

**Time-triggered — Morning Intelligence.**
Day 0: "I can prepare a morning brief. What matters to you — news topics, your calendar, your inbox? When should it arrive?" → user picks 7am, 3 topics. Days 1–7: brief arrives daily; user expands some items, skips others. Week 2+: the agent has learned which sections are read and drops the rest; reorders by what the user opens first. Steady state: a 30-second brief that is *theirs*, delivered before they ask, every morning.

**Event-triggered — Email Companion.**
Day 0: "I scanned your inbox — 1,240 messages, ~40 senders you reply to often. Want me to start triaging and drafting?" Days 1–7: every new email is classified and a reply drafted; user edits/sends. The agent records every edit. Week 2–4: after ~200 approved drafts of routine types, it begins auto-sending *those specific types* (e.g. meeting confirmations) — still drafting-only for anything sensitive. Steady state: inbox triaged overnight; a morning digest of what was handled and the 3 emails that genuinely need the user.

**Threshold-triggered — Personal Finance.**
Day 0: "Drop a bank/card CSV and I'll categorize it." → first report. Days 1–7: each import auto-categorized; user corrects a few categories (feedback). Week 2+: categorization matches the user's mental model; the agent proactively flags anomalies and unused subscriptions when thresholds trip. Steady state: monthly report appears unprompted; the agent surfaces "you'll hit your savings goal by October" and the 2 charges worth a look.

**Sensor-triggered — Smart Home.**
Day 0: "I found 14 Home Assistant devices. Want voice control, and should I watch for anything?" Days 1–7: executes spoken commands; observes routines. Week 2+: "You turn off all lights at 11pm most nights — want me to automate that?" (proposes an automation). Steady state: routine automations run; the agent proactively notifies on exceptions ("garage open 1h") and asks before anything that affects a lock or appliance.

**On-demand — Presentation.**
No background triggers. Day 0 and always: "Give me the data and the audience, I'll draft narrative + slides + talking points." Personalization still accrues (it learns the user's deck structure and tone), but it never acts between sessions.

### 5.7 Per-agent autonomy summary

The per-agent journey in one row each: primary trigger, what `on_first_run` proposes, the steady-state autonomous behavior, and the actions that **always** require confirmation.

| Agent | Primary trigger | First-run proposal | Steady-state autonomy | Always-confirm |
|-------|-----------------|--------------------|-----------------------|----------------|
| knowledge | event (new files) | "Index these folders?" | auto-indexes new docs, summarizes deltas | deleting/moving source files |
| files | threshold (folder size) | "Organize Downloads?" | sorts into staging, dedups | permanent delete |
| smarthome | sensor | "Control these 14 devices? Watch for X?" | runs learned routines, alerts on anomalies | locks, oven, garage, anything physical |
| code | event (repo opened) | "Restore your context?" | restores session, summarizes PRs/advisories | commits, pushes, branch ops |
| morning | scheduled (daily) | "Daily brief — topics & time?" | delivers tailored brief | n/a (read-only output) |
| email | event (new mail) | "Triage & draft?" | triages, auto-sends graduated routine types | sending sensitive/new-recipient mail |
| crm | scheduled + event | "Build profiles from your contacts?" | flags dormant contacts, drafts follow-ups | sending outreach |
| family | event (school mail) | "Manage the family calendar?" | flyer→event, meal plans, notifies | inviting others, purchases |
| writing | conversational | "Learn your style from past writing?" | drafts in your voice on request | publishing/sending |
| journal | scheduled (daily) | "Daily check-in time?" | prompts check-in, weekly pattern review | n/a (private to user) |
| habit | scheduled + threshold | "Track routines & nudge?" | nudges on pattern breaches | n/a (advisory) |
| health | scheduled + threshold | "Import wearable data?" | trend analysis, med reminders | sharing data, any medical advice gating |
| photo | event (new photos) | "Index your library?" | organizes, proposes albums | deleting photos |
| meeting | event (meeting start) | "Transcribe & summarize meetings?" | transcribes, extracts actions | sharing notes externally |
| finance | threshold | "Categorize a CSV?" | categorizes, flags anomalies | any payment/transfer |
| freelance | event + threshold | "Track projects & invoices?" | drafts invoices at milestones | sending invoices, payment ops |
| learning | scheduled (SRS due) | "Track what you're learning?" | quizzes when due, curates resources | n/a (advisory) |
| deals | sensor (price) | "Watch which products?" | alerts on real drops | purchases |
| travel | scheduled + sensor | "Watch destinations / free windows?" | proposes trips on fare drops | booking |
| security | sensor (breach DB) | "Monitor your accounts & passwords?" | alerts + remediation steps | changing/deleting credentials |
| presentation | conversational | "Draft from data + audience?" | (on-demand only) | n/a |
| maintenance | scheduled (due dates) | "Inventory appliances & vehicles?" | reminders on due maintenance | scheduling paid service |

---

## 6. Personalization Model

Personalization is what turns 22 generic agents into *your* 22 agents. It is built on L1 (memory) plus each agent's domain datastore (§3), and it is the mechanism behind "adaptive" and "gets more valuable every day." OpenClaw's single most-downloaded skill (419K) is its "Self-Improving Agent" — the market signal is unambiguous: **users want agents that learn them.**

### 6.1 What each agent personalizes on

Personalization is per-agent and concrete — not a vague "it learns about you." Each agent learns a specific, bounded set of things (its **personalization surface**), declared in `memory_schema.py`:

- **Preferences** — explicit settings the user states ("CC my assistant on client mail", "brief me at 7am").
- **Learned patterns** — behaviors the agent infers from observation (AH-L1.2): which senders you reply to fast, which file types go where, when you focus best.
- **Style/voice** — how you write and present (tone per recipient, deck structure, reply length).
- **Entities & relationships** — people, projects, devices, accounts (held in the agent's `datastore:records`, keyed to your context).
- **Thresholds** — your personal "normal" (typical spend, sleep target, price you'd pay).

### 6.2 Memory architecture inside an agent

```
agent.py
  ├─ MemoryMixin            → L1 personalization memory, namespaced to this agent (AH-L1.1)
  │    ├─ profile           explicit preferences (from bootstrap + settings)
  │    ├─ patterns          learned behaviors (AH-L1.2)
  │    └─ feedback          accept/edit/ignore signals (AH-L1.3)
  ├─ memory_schema.py       declares the fields above (AH-L1.4)
  └─ <datastore>            domain data — RAG / media / ledger / timeseries / records (§3)
```

L1 memory holds *who you are to this agent*; the datastore holds *the data this agent manages*. They are separate: wiping the email agent's memory forgets your reply style but not your emails; wiping the datastore forgets the emails but not your style.

### 6.3 How memory is captured

Three channels, in increasing subtlety:

1. **Bootstrap (day 0).** The conversational onboarding (#556) seeds an initial cross-agent profile: role, domains, what eats the user's time, hardware/system scan (AH-L5.1). Each agent reads what's relevant on first run.
2. **Explicit.** The user states a preference in conversation or settings; it's written to `profile`.
3. **Implicit (the important one).** Every agent action emits a feedback signal (AH-L1.3): did the user **accept**, **edit**, **ignore**, or **reject** it? Edits are the richest signal — the diff between the agent's draft and what the user sent *is* the lesson. Behavioral pattern detection (AH-L1.2) aggregates these into rules ("user shortens my drafts → bias shorter").

### 6.4 How memory is used (retrieval → injection)

On each turn or heartbeat, the agent retrieves the relevant slice of its memory (hybrid search, Memory v2) and injects it into the prompt/context — scoped, not dumped:

- **email** retrieves: this sender's importance + history, the user's reply style for this recipient type.
- **finance** retrieves: the user's category mappings + thresholds for this kind of transaction.
- **writing** retrieves: the style profile for the target recipient/medium.

Scoped retrieval keeps the context small (critical for local models — ties to the ToolLoader philosophy) while still feeling like the agent "remembers."

### 6.5 Personalization lifecycle — the compounding curve

| Phase | Timeframe | Experience |
|-------|-----------|------------|
| **Cold start** | Day 0 | Generic but useful (RAG answers, basic triage). Bootstrap gives a head start, not a finished profile. |
| **Warming** | Days–weeks | The agent visibly adapts: categories match your model, drafts need less editing, briefs drop what you skip. |
| **Personalized** | Steady state | Output sounds like you / matches your patterns. The agent anticipates. Switching to any cloud tool now feels like starting over — this is the retention moat. |

**Before → after, concretely:** email day 1 draft = competent but generic; email week 4 draft = your greeting, your sign-off, your typical length, the right formality for *that* recipient. Same model, same code — the difference is accumulated memory.

### 6.6 The adaptive feedback loop

```
agent acts → user reacts (accept / edit / ignore / reject)
     ▲                          │
     │                          ▼
 updated behavior  ◀──  signal stored (L1.3) → pattern updated (L1.2)
```

This loop is why autonomy (§5) and personalization compound together: more autonomy → more actions → more feedback → better personalization → safe to grant more autonomy.

### 6.7 Privacy & user control

Personalization is GAIA's structural advantage *because* it is local. The behavioral profile that makes a cloud agent valuable is the exact data users fear sharing. Locally:

- Memory is **on-device, human-readable, auditable, editable, and deletable** (Memory Dashboard, #575).
- The user can **see exactly what each agent knows**, correct a wrong inference, or wipe an agent's memory namespace without losing domain data.
- Nothing is transmitted; no training on user data. This is the compliance story (EU AI Act / GDPR / HIPAA) and the trust story in one.

### 6.8 Per-agent personalization summary

| Agent | Personalizes on | Stored in | Captured via |
|-------|-----------------|-----------|--------------|
| knowledge | interests, query patterns | memory + `datastore:rag` | implicit (queries) |
| files | folder conventions, rejected moves | memory + `datastore:fileindex` | implicit (accept/reject) |
| smarthome | routines, device preferences | memory | implicit (observation) |
| code | active repos, session state, workflow | memory + `datastore:records` | implicit |
| morning | topics, read/skip, delivery time | memory | explicit + implicit |
| email | sender importance, reply style per recipient | memory | implicit (edits) |
| crm | contact profiles, per-person tone | memory + `datastore:records` | explicit + implicit |
| family | members, schedules, dietary prefs | memory + `datastore:records` | explicit + implicit |
| writing | style/voice per recipient & medium | memory | implicit (edits) |
| journal | themes, mood baselines, prompts that land | memory + `datastore:timeseries` | implicit |
| habit | routines, streaks, what nudges work | memory + `datastore:timeseries` | implicit |
| health | metric baselines, med schedule | memory + `datastore:timeseries` | explicit + implicit |
| photo | people names, favorites, album style | memory + `datastore:media` | explicit (naming) + implicit |
| meeting | recurring attendees, note format | memory + `datastore:archive` | implicit |
| finance | category mappings, budgets, thresholds | memory + `datastore:ledger` | explicit + implicit (corrections) |
| freelance | clients, rates, invoice cadence | memory + `datastore:ledger` | explicit + implicit |
| learning | knowledge gaps, pace, review schedule | memory + `datastore:records` | implicit (quiz results) |
| deals | wishlist, price thresholds | memory + `datastore:timeseries` | explicit |
| travel | destination tastes, trip style, budget | memory | explicit + implicit (past trips) |
| security | monitored identities, known-safe baseline | memory + `datastore:records` | explicit |
| presentation | deck structure, tone, audience profiles | memory | implicit (edits) |
| maintenance | inventory, service history, intervals | memory + `datastore:records` | explicit + implicit |

> **No new issues:** §5 and §6 are realized entirely by existing layer issues — autonomy by **AH-L7.1/L7.2/L7.4** (triggers, loop, hooks) + **AH-L7.3** (graduated trust) + **AH-L6.x** (notifications); personalization by **AH-L1.1–L1.4** (namespaces, patterns, feedback, schema) + per-agent `datastore`. The agent-package checklist (§9) already requires a `memory_schema.py` and proactive triggers, so these models add design guidance, not scope.

---

## 7. Per-Agent Manifest Template

Every agent ships a `gaia-agent.yaml` (extends the format in `agent-hub-ui.mdx`). New fields for proactive agents are marked `# NEW`:

```yaml
id: email
name: Email Companion
version: 0.1.0
description: "Triages your inbox, drafts replies in your voice, and delivers a daily digest."
author: AMD
license: MIT

category: communication
tags: [email, productivity, autonomous]
icon: mail
conversation_starters:
  - "What needs my attention in my inbox?"
  - "Draft a reply to the latest email from my manager."

language: python
min_gaia_version: "0.20.0"
models: [Qwen3.5-35B-A3B-GGUF]
security_tier: verified

requirements:
  min_memory_gb: 8
  min_context_size: 32768
  platforms: [win-x64, linux-x64, darwin-arm64]

python:
  entry_module: gaia_agent_email.agent
  entry_class: EmailAgent
  dependencies: []

# Shared layers this agent composes (NEW — drives dependency resolution + UI grant prompts)
layers: [memory, connectors, notifications, autonomy]
connectors: [google, microsoft]          # NEW — triggers OAuth grant UI
permissions:
  - email:read
  - email:draft
  - network:connector

# Proactive behavior (NEW)
proactive:
  first_run: true                          # implements on_first_run discovery
  heartbeat: "*/15 * * * *"                # cron for on_heartbeat
  trust_default: supervised                # supervised | semi_auto | autonomous
  trust_graduation:
    autonomous_after: 200                  # approved actions before auto-send

interfaces:
  tui: true
  cli: true
  pipe: true
  api_server: true
  mcp_server: true
```

---

## 8. Implementation Waves & Sequencing

| Wave | Gate (infra ready) | Agents | Cumulative agents |
|------|--------------------|--------|-------------------|
| **0** | **Hub platform GA (v0.29)** — restructure, manifest, registry, packaging, `gaia agent` CLI + **L7.4 lifecycle hooks** | — (platform only; no agents can ship before this) | 0 |
| **1** | L1✓, L2, L5, HA MCP | knowledge, files, smarthome, code, morning | 5 |
| **2** | L3 connectors GA | email, crm, family | 8 |
| **3** | L1 behavioral + L6 notifications | writing, journal, habit, health | 12 |
| **4** | L4 VLM/media | photo, meeting, finance, freelance | 16 |
| **5** | L7 autonomy GA + L6 messaging | learning, deals, travel, security, presentation, maintenance | 22 |

**Wave 0 is a hard gate.** Because agents are Hub-native (not in-tree), nothing in Waves 1–5 can ship until the Hub platform exists: `gaia agent init/test/pack/publish`, the `gaia-agent.yaml` manifest + registry scan, and per-agent wheel packaging. The first concrete agent (A1 `knowledge`) is the **proof that the Hub workflow works end-to-end** — if A1 can't be authored, tested, and published through the Hub, the platform isn't done.

**Critical path:** Hub platform (v0.29) → L7.4 lifecycle hooks → L2 web search → L3 connectors → L6 notifications → L4 media → L7 full autonomy. Within each wave, agents are shippable independently; users get value at every wave.

---

## 9. Complete Issue List

### Epics (one per layer + one per wave + program epic)

| Epic | Title | Priority |
|------|-------|----------|
| EPIC-0 | Agent Hub: 22-Agent Enablement Program (tracking) | P0 |
| EPIC-L1 | Memory & Personalization Engine (per-agent ns, behavioral, feedback) | P0 |
| EPIC-L2 | Web Search & Extraction composable tool | P0 |
| EPIC-L3 | Connectors GA (email/calendar/contacts/OAuth) | P0 |
| EPIC-L4 | VLM & Media Processing (photo index, receipt extract) | P1 |
| EPIC-L5 | Filesystem & System Discovery | P1 |
| EPIC-L6 | Proactive Notifications | P0 |
| EPIC-L7 | Autonomy Engine & Graduated Trust | P0 |
| EPIC-W1..W5 | One epic per wave grouping the agent packages | P1 |

### Layer issues (detail)

> Issues tagged **new** need creating; others reference existing GitHub numbers. Estimates: S ≤1wk, M 1–2wk, L 2–4wk.

**L1 — Memory** (EPIC-L1)
- AH-L1.1 Per-agent memory namespaces — M — ref #676
- AH-L1.2 Behavioral pattern detection engine — L — new
- AH-L1.3 Implicit feedback capture (accept/edit/ignore) — M — new
- AH-L1.4 Per-agent memory-schema declaration API — S — new

**L2 — Web Search** (EPIC-L2)
- AH-L2.1 `web_search` provider-abstracted MCP tool — M — ref #669/#1144
- AH-L2.2 `web_extract` URL→clean text→chunk — S — ref #1144
- AH-L2.3 Register in `KNOWN_TOOLS` — S — new

**L3 — Connectors** (EPIC-L3)
- AH-L3.1 OAuth framework GA (Google+MS) — L — ref #927/#1105
- AH-L3.2 Email backend Protocol (Gmail+Outlook) — M — ref #963 (CLOSED — verify Outlook landed)
- AH-L3.3 Calendar connector — M — **new** (#660 is Playwright, a different approach)
- AH-L3.4 Contacts connector — M — new
- AH-L3.5 `ConnectorMixin` + grant UI — S — ref #735

**L4 — VLM/Media** (EPIC-L4)
- AH-L4.1 Photo-library semantic index (CLIP+faces) MCP server — L — ref #730
- AH-L4.2 Receipt/document structured extraction — M — ref #664
- AH-L4.3 `media_search` natural-language tool — M — new

**L5 — Filesystem/Discovery** (EPIC-L5)
- AH-L5.1 System scanner — M — ref #466
- AH-L5.2 Semantic filesystem index — M — new
- AH-L5.3 Safe write/move/dedup with confirmation tiers — S — exists

**L6 — Notifications** (EPIC-L6)
- AH-L6.1 Notification primitive (toast+feed+webhook) — M — new
- AH-L6.2 Notification preferences (quiet hours, batching) — S — new
- AH-L6.3 Messaging delivery (Signal→Telegram) — L — ref #693/#635

**L7 — Autonomy** (EPIC-L7)
- AH-L7.1 Heartbeat scheduler + cron + event hooks — L — ref #634
- AH-L7.2 Autonomous loop (think-act-schedule) — L — ref #557
- AH-L7.3 Graduated trust model — M — **new** (#559 "dangerous mode" is the inverse concept)
- AH-L7.4 Base-Agent lifecycle hooks (`on_first_run`/`on_heartbeat`/`propose`) — M — new
- AH-L7.5 Background service + system tray — L — ref #643

### Agent package issues

Each agent gets **one tracking issue** with a fixed sub-task checklist (manifest, agent class, prompt, tools, memory schema, triggers, tests + eval scenarios, README, Hub publish). Template:

```
AH-A<n>: hub/agents/<id>/python — <Agent Name>
  [ ] gaia-agent.yaml manifest (+ layers/connectors/proactive/interfaces: all true)
  [ ] pyproject.toml (amd-gaia + skill deps)
  [ ] agent class composing required layer mixins + ApiAgent + MCPAgent
  [ ] proactive system prompt (first-run discovery + propose pattern)
  [ ] unique @tool methods
  [ ] memory schema declaration
  [ ] proactive triggers (cron/event)
  [ ] 5 interfaces verified: TUI, CLI, pipe, API server (+ OpenAPI spec), MCP server
  [ ] unit tests (mocked LLM)
  [ ] eval scenarios (gaia eval agent --category <id>)
  [ ] README + screenshots
  [ ] compile/pack (gaia agent pack → wheel + openapi.json + manifest)
  [ ] publish to R2 (gaia agent publish), security_tier: verified
  [ ] verify surfaced on website (amd-gaia.ai/hub) + Agent UI (Available tab)
```

> The same checklist applies to the eventual **C++ supersession** of each agent (`language: cpp`), with `gaia agent pack` producing a static binary instead of a wheel. Same id, same manifest, same 5 interfaces — consumers are unaffected.

Layer deps below split **memory tier** (essential/enhance/optional) from **datastore** (domain persistence) and **other layers**. Agents with `enhance`/`optional` memory are **not gated on AH-L1.2** and can ship as soon as their other layers land.

| Issue | Agent | Wave | Effort | Memory | Datastore | Other layers |
|-------|-------|------|--------|--------|-----------|--------------|
| AH-A1 | knowledge | 1 | S | enhance | rag | L2, L5 |
| AH-A2 | files | 1 | S | enhance | fileindex | L5 |
| AH-A3 | smarthome | 1 | S | enhance | — | L6, HA-MCP |
| AH-A4 | code | 1 | S | essential | records (code-index) | L5 |
| AH-A5 | morning | 1 | M | essential | — | L2, L6, L7 |
| AH-A6 | email | 2 | M | essential | — | L3, L6, L7 |
| AH-A7 | crm | 2 | M | essential | records (contacts) | L3 |
| AH-A8 | family | 2 | L | essential | records | L3, L4, L6 |
| AH-A9 | writing | 3 | M | essential | — | — |
| AH-A10 | journal | 3 | S | essential | timeseries | L6, L7 |
| AH-A11 | habit | 3 | S | essential | timeseries | L3, L6, L7 |
| AH-A12 | health | 3 | M | enhance | timeseries | L4, L6 |
| AH-A13 | photo | 4 | M | enhance | media | L4 |
| AH-A14 | meeting | 4 | M | enhance | archive | L3, L6 |
| AH-A15 | finance | 4 | M | enhance | ledger | L4, L6 |
| AH-A16 | freelance | 4 | M | enhance | ledger | L3, L4, L6 |
| AH-A17 | learning | 5 | M | essential | records (flashcards) | L2, L7 |
| AH-A18 | deals | 5 | S | enhance | timeseries (prices) | L2, L6, L7 |
| AH-A19 | travel | 5 | M | essential | — | L2, L3, L6 |
| AH-A20 | security | 5 | M | enhance | records (identities) | L2, L5, L6 |
| AH-A21 | presentation | 5 | M | optional | — | L5 |
| AH-A22 | maintenance | 5 | M | enhance | records (inventory) | L2, L4, L6, L7 |

**Memory tier counts:** essential = 10 (code, morning, email, crm, family, writing, journal, habit, learning, travel) · enhance = 11 · optional = 1 (presentation). Only the 10 essential agents gate on the full L1 engine; the 12 others can ship with light/no L1 and a domain datastore.

### Cross-cutting issues

| Issue | Title | Priority |
|-------|-------|----------|
| AH-X1 | Proactive interaction contract — base Agent `on_first_run`/`propose` + UI approve/deny widget | P0 |
| AH-X2 | Eval scenario harness for proactive agents (simulate first-run + heartbeat) | P0 |
| AH-X3 | `gaia agent init --proactive` scaffold template (manifest + hooks pre-wired) | P1 |
| AH-X4 | Hub category taxonomy + filters for the 22 agents | P2 |
| AH-X5 | Per-agent eval baselines committed to `tests/fixtures/eval_baselines/` | P1 |
| AH-X6 | Security review of permissions per agent (least-privilege audit) | P0 |
| AH-X7 | `amd-gaia[agents]` meta-package installs all 22 verified agents | P2 |
| AH-X8 | 5-interface harness — single package yields TUI/CLI/pipe/API+OpenAPI/MCP from one agent class (`gaia agent run --prompt\|--pipe\|--api\|--mcp`) | P0 |
| AH-X9 | `gaia agent pack` compile step — wheel (py) / static binary (cpp) + bundled `openapi.json` + manifest | P0 |
| AH-X10 | R2 publish + Cloudflare Worker index rebuild consumed by website **and** Agent UI from one `index.json` | P0 — ref #1095, #1178, #1097 |
| AH-X11 | C++ supersession track — `NativeAgentLauncher` + per-agent C++ reimplementation matrix (id/manifest/interfaces unchanged) | P2 — ref #1092, #1094 |
| AH-X12 | Website Hub page (amd-gaia.ai/hub) — browse/search/Try, reads R2 index | P1 — ref #1178 |

### Totals

| Group | Count | Net-new | Duplicates open issue | Conditional |
|-------|-------|---------|----------------------|-------------|
| Program + layer + wave epics | 13 | 13 | 0 | 0 |
| Layer issues (AH-L*) | 26 | 13 | 12 | 1 (L3.2 / closed #963) |
| Agent issues (AH-A1–22) | 22 | 22 | 0 | 0 |
| Cross-cutting (AH-X1–12) | 12 | 12¹ | 0 | 0 |
| **Total** | **73** | **~60** | **12** | **1** |

¹ X10/X11/X12 are net-new *umbrella* issues that link existing distribution issues (#1095/#1178/#1097/#1092/#1094) as children — they coordinate, they don't duplicate.

**Verified against live GitHub (this session):** the 12 duplicates reference already-OPEN issues (#676, #669, #1144, #927, #1105, #735, #730, #664, #466, #693, #635, #634, #557, #643) and must be **linked, not recreated**. #963 (L3.2) is **CLOSED** — verify Outlook landed before creating. #660 and #559 were scope-mismatches (Playwright / dangerous-mode) so L3.3 and L7.3 are net-new, not references. The bulk-create script in `agent-hub-22-agents-issues.md` lists the exact 12 `create` lines to delete before a real run.

---

## 10. Testing & Quality Strategy

1. **Per-agent unit tests** with mocked LLM (framework requirement).
2. **Eval scenarios** per agent run via `gaia eval agent --category <id>`, compared to a committed baseline (`tests/fixtures/eval_baselines/`). Required because every agent has a domain-specific system prompt (an LLM-affecting surface — see CLAUDE.md eval rule).
3. **Proactive-behavior eval (AH-X2):** simulate `on_first_run` (does the agent discover context and propose sensible actions?) and `on_heartbeat` (does it act within trust bounds and never exceed permissions?).
4. **Hub quality gates** (from `agent-hub.mdx`): responds, stays on task, no crashes, safe — every agent must pass before `security_tier: verified`.
5. **Permission least-privilege audit (AH-X6):** each agent's manifest permissions reviewed against what its tools actually call.
6. **Tool-calling reliability gate:** agents requiring many tools validated against the model tier in their manifest; if they fail on the declared minimum model, raise `min` model or reduce tool surface (ToolLoader bundles).

---

## 11. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Hub platform (v0.29) slips | Med | High | **Hard blocker** — agents are Hub-native, so a platform slip blocks all 22. Mitigate by treating A1 (`knowledge`) as the platform's own acceptance test: build the Hub and A1 together so the platform is proven before scaling to the other 21. Keep Wave 0 ruthlessly minimal (manifest + registry + `gaia agent init/test/publish` only; R2/Arena can follow). |
| L7 autonomy late → "autonomous" claim unmet | Med | High | Ship agents in supervised mode first (propose-only); autonomy is an upgrade, not a prerequisite for value |
| Tool-calling unreliable on small models | High | High | Fine-tuning flywheel (v0.23) + ToolLoader bundles; declare honest `min` model per agent |
| 22 agents → maintenance bankruptcy | Med | Med | Thin-layer model + shared eval harness; community owns the long tail via Hub once L1–L7 are stable |
| Connector OAuth complexity blocks Wave 2 | Med | High | L3 is the single hardest layer; start it in parallel with Wave 1 |
| Permission/security gaps across 22 agents | Med | High | AH-X6 least-privilege audit gate before any agent is `verified` |

---

## 12. Success Criteria

- All 22 agents published to R2 as `verified` packages, each ≤500 LOC of glue over shared layers.
- Every agent ships **all five interfaces** (TUI, CLI, pipe, API server + OpenAPI spec, MCP server) from a single compiled package — verified by the AH-X8 harness.
- Every agent is discoverable on **both** the GAIA website (amd-gaia.ai/hub) and the Agent UI Available tab, from one R2 `index.json`.
- Every agent implements the proactive contract (`on_first_run` proposes, never a blank box).
- Every agent has passing unit tests + committed eval baselines.
- A user can install any agent with `gaia agent install <id>` and get a useful proposed action within 60 seconds.
- Shared layers L1–L7 are reused (no agent re-implements web search, connectors, memory, or notifications).
- At least one agent ships a **C++ supersession** proving the Python→native upgrade path leaves id/manifest/interfaces unchanged.

---

## Rendering on the docs site (optional)

This spec lives in `docs/plans/` as Markdown. To surface it on the Mintlify site (amd-gaia.ai): convert to `.mdx` with frontmatter (`title`, `description`, `icon`) and add it to `docs/docs.json` navigation. As `.md` it is committed to the repo but not rendered on the docs site — matching the other `.md` plan documents.
