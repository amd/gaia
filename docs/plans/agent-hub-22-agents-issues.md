# Agent Hub 22-Agent Enablement — Issue Set

Source of truth for the GitHub issues backing `agent-hub-22-agents-spec.md`. Each issue below is **self-contained**: a developer should be able to act on it without opening the spec (spec links are supplementary).

- **Milestone:** `Agent Hub: 22 Agents [OSS]` (#48)
- **Generated issues:** epics #1458–#1470 (edited in place); net-new layer/agent/cross-cutting created from this file; 13 existing duplicates moved into the milestone.

## Generator format

Each issue is a delimited block the generator script consumes:

```
<!--issue
action: create | edit | move
number: <required for edit/move>
title: <required for create>
labels: <comma-separated, for create/edit>
addlabels: <comma-separated, for move>
reopen: true            (optional, for move on a closed issue)
-->
<markdown body>
<!--/issue-->
```

`action`: `create` (net-new), `edit` (rewrite body of an epic we already made), `move` (reassign an existing issue to the milestone + add labels + link comment).

---

# Epics

<!--issue
action: edit
number: 1458
labels: epic,agent-hub-22,p0
-->
## Program

Ship **22 purpose-built, proactive agents** through the GAIA Agent Hub. The thesis: we are not building 22 codebases — we are building **7 shared infrastructure layers** (L1–L7) on top of which each agent is a thin (~150–500 LOC) Hub-native package. Each agent ships as **one compiled package exposing all five interfaces** (TUI, CLI, pipe, API server + OpenAPI, MCP server), published to R2 and surfaced on the website + Agent UI, **autonomous by default** with **local personalization**.

## Build order

Hub-first. The Agent Hub Platform (v0.29) is a hard prerequisite. Agents are Hub-native from inception (`hub/agents/python/<id>/`), importing `gaia` from the published `amd-gaia` wheel. **A1 (`knowledge`) is the platform's end-to-end acceptance test** — if it can't be authored/tested/published through the Hub, the platform isn't done.

## Children

- Layer epics: L1 Memory (#1459), L2 Web Search (#1460), L3 Connectors (#1461), L4 VLM/Media (#1462), L5 Filesystem (#1463), L6 Notifications (#1464), L7 Autonomy (#1465)
- Wave epics: W1 (#1466), W2 (#1467), W3 (#1468), W4 (#1469), W5 (#1470)
- Cross-cutting: AH-X1…X12

## Exit criteria

- All 22 agents published as `verified`, ≤500 LOC glue each, all 5 interfaces, dual-surface discovery, passing eval baselines, ≥1 C++ supersession.

## Reference
Spec: `docs/plans/agent-hub-22-agents-spec.md`.
<!--/issue-->

<!--issue
action: edit
number: 1459
labels: epic,agent-hub-layer,agent,p0
-->
## Context
Personalization is what makes the 22 agents *yours* and is the retention moat — OpenClaw's most-downloaded skill (419K) is its "self-improving" memory. GAIA already ships Memory v2 (#606); this epic closes the gaps needed for per-agent, adaptive personalization.

## Goal
A memory layer that lets each agent maintain a private, namespaced personalization store (preferences, learned patterns, feedback) distinct from its domain datastore.

## Scope (children)
- AH-L1.1 — Per-agent memory namespaces (**existing #676**)
- AH-L1.2 — Behavioral pattern detection engine
- AH-L1.3 — Implicit feedback capture (accept/edit/ignore/reject)
- AH-L1.4 — Per-agent memory-schema declaration API

## Key distinction
`memory` (L1 personalization — *who you are to this agent*) is separate from `datastore:*` (RAG / media / ledger / timeseries / records — *the data this agent manages*). Wiping one must not wipe the other.

## Exit criteria
- An agent can declare a memory schema, capture implicit feedback, and have behavior adapt from detected patterns; memory is auditable/editable/wipeable per namespace.

## Reference
Spec §3 (L1), §6 (Personalization Model).
<!--/issue-->

<!--issue
action: edit
number: 1460
labels: epic,agent-hub-layer,agent,p0
-->
## Context
Web search is the #1 community-requested capability for local agents; without it, agents are "trapped in a knowledge bubble." Several agents (morning, learning, deals, travel, security, knowledge) need it.

## Goal
A composable, provider-abstracted web search + extraction capability any agent adds by name.

## Scope (children)
- AH-L2.1 — `web_search` provider-abstracted MCP tool (**existing #669 / #1144**)
- AH-L2.2 — `web_extract` URL→clean→chunk (**existing #1144**)
- AH-L2.3 — Register web tools in `KNOWN_TOOLS`

## Exit criteria
- Any agent can compose `web_search`/`web_extract` via the registry; default provider works offline-degraded gracefully; results are RAG-ingestable.

## Reference
Spec §3 (L2).
<!--/issue-->

<!--issue
action: edit
number: 1461
labels: epic,agent-hub-layer,agent,domain:distribution,p0
-->
## Context
Email, calendar, and contacts are the connective tissue for the highest-value agents (email, crm, family, morning, meeting, habit, travel). This is the hardest layer (OAuth, provider APIs) and should start early.

## Goal
A GA connector framework with email/calendar/contacts backends and a `ConnectorMixin` agents declare requirements against.

## Scope (children)
- AH-L3.1 — OAuth framework GA, Google + Microsoft (**existing #927 / #1105**)
- AH-L3.2 — Email backend Protocol, Gmail + Outlook (Gmail #965 merged; **Outlook #963 CLOSED — verify it landed**)
- AH-L3.3 — Calendar connector (Google Calendar + MS Graph) — net-new (distinct from #660 Playwright approach)
- AH-L3.4 — Contacts connector
- AH-L3.5 — `ConnectorMixin` + grant UI (**existing #735**)

## Exit criteria
- An agent declares `connectors: [google, microsoft]`; the UI prompts for grant; email/calendar/contacts are reachable through a stable Protocol regardless of provider.

## Reference
Spec §3 (L3).
<!--/issue-->

<!--issue
action: edit
number: 1462
labels: epic,agent-hub-layer,agent,p1
-->
## Context
Vision unlocks photo, meeting (slides/screens), finance (receipts), family (flyers), and health (lab images). GAIA has `VLMToolsMixin`; this epic adds the media indexing + extraction it lacks.

## Goal
Local VLM-backed media indexing, structured document/receipt extraction, and natural-language media search.

## Scope (children)
- AH-L4.1 — Photo-library semantic index (CLIP + faces) MCP server (**existing #730**)
- AH-L4.2 — Receipt/document structured extraction (**existing #664**)
- AH-L4.3 — `media_search` natural-language tool

## Exit criteria
- A user can ask "photos of the kids at the beach last summer" and get results; a receipt photo yields structured key/value/total — all local.

## Reference
Spec §3 (L4).
<!--/issue-->

<!--issue
action: edit
number: 1463
labels: epic,agent-hub-layer,agent,p1
-->
## Context
Full filesystem + system awareness is a capability cloud agents structurally cannot have. Powers files, code, security, and first-run discovery across all agents.

## Goal
A system scanner, a semantic file index, and safe confirmation-tiered file operations.

## Scope (children)
- AH-L5.1 — System scanner: hardware, OS, installed apps, file types, available MCP servers (**existing #466**)
- AH-L5.2 — Semantic filesystem index (tag/search files by content)
- AH-L5.3 — Safe write/move/dedup with confirmation tiers (builds on existing `file_io`)

## Exit criteria
- Agents can discover the user's environment on first run and search files by content; destructive ops are confirmation-gated.

## Reference
Spec §3 (L5).
<!--/issue-->

<!--issue
action: edit
number: 1464
labels: epic,agent-hub-layer,agent,p0
-->
## Context
Autonomy is meaningless if the agent can't reach the user with results. A lightweight notification primitive is needed now, ahead of full messaging/tray.

## Goal
A delivery layer (toast + in-UI feed + optional webhook) with per-agent preferences, plus messaging adapters.

## Scope (children)
- AH-L6.1 — Notification primitive (toast + in-UI feed + webhook)
- AH-L6.2 — Notification preferences (quiet hours, batching, channel)
- AH-L6.3 — Messaging delivery: Signal → Telegram (**existing #693 / #635**)

## Exit criteria
- An autonomous agent can report a result that surfaces as a desktop toast + UI feed entry, respecting quiet hours.

## Reference
Spec §3 (L6), §5.3.
<!--/issue-->

<!--issue
action: edit
number: 1465
labels: epic,agent-hub-layer,agent,p0
-->
## Context
"Autonomous by default" is the north star. This layer provides the scheduler, the loop, the trust model, and the base-class hooks every proactive agent builds on.

## Goal
A heartbeat/event-driven autonomy engine with graduated trust and a proactive base-Agent contract.

## Scope (children)
- AH-L7.1 — Heartbeat scheduler + cron + event hooks (**existing #634**)
- AH-L7.2 — Autonomous loop: think-act-schedule (**existing #557**)
- AH-L7.3 — Graduated trust model (supervised → semi-auto → autonomous; always-confirm floor)
- AH-L7.4 — Base-Agent lifecycle hooks (`on_first_run`/`on_heartbeat`/`propose`) — **Wave-0 prerequisite**
- AH-L7.5 — Background service + system tray (**existing #643**)

## Exit criteria
- A scheduled/event trigger runs one observe→decide→propose|act→report→learn cycle; class-3 actions stay gated; a kill switch halts all autonomy.

## Reference
Spec §3 (L7), §5 (Autonomy Model).
<!--/issue-->

<!--issue
action: edit
number: 1466
labels: epic,agent-hub-22,p1
-->
## Wave 1 — knowledge, files, smarthome, code, morning

**Gate:** Hub platform GA + AH-L7.4 lifecycle hooks + L1 (✓ baseline), L2, L5, Home Assistant MCP catalog entry.

The first wave is shippable on existing/near-term infrastructure and proves the Hub workflow end-to-end. **A1 (`knowledge`) is the platform acceptance test.**

**Agents:** AH-A1 knowledge · AH-A2 files · AH-A3 smarthome · AH-A4 code · AH-A5 morning.

**Exit:** all five published as `verified` with all 5 interfaces and passing eval baselines.
<!--/issue-->

<!--issue
action: edit
number: 1467
labels: epic,agent-hub-22,p1
-->
## Wave 2 — email, crm, family

**Gate:** L3 connectors GA (OAuth + email/calendar/contacts).

The connector-dependent agents — the highest-value, most privacy-sensitive set.

**Agents:** AH-A6 email · AH-A7 crm · AH-A8 family.

**Exit:** all three published with connector grant flows working for Google + Microsoft.
<!--/issue-->

<!--issue
action: edit
number: 1468
labels: epic,agent-hub-22,p1
-->
## Wave 3 — writing, journal, habit, health

**Gate:** L1 behavioral detection (AH-L1.2) + L6 notifications.

Personalization- and pattern-heavy agents that compound in value over time.

**Agents:** AH-A9 writing · AH-A10 journal · AH-A11 habit · AH-A12 health.

**Exit:** all four published; pattern detection demonstrably adapts behavior.
<!--/issue-->

<!--issue
action: edit
number: 1469
labels: epic,agent-hub-22,p1
-->
## Wave 4 — photo, meeting, finance, freelance

**Gate:** L4 VLM/media (photo index, receipt extraction).

The vision-dependent agents.

**Agents:** AH-A13 photo · AH-A14 meeting · AH-A15 finance · AH-A16 freelance.

**Exit:** all four published; media search + receipt extraction working locally.
<!--/issue-->

<!--issue
action: edit
number: 1470
labels: epic,agent-hub-22,p1
-->
## Wave 5 — learning, deals, travel, security, presentation, maintenance

**Gate:** L7 autonomy GA + L6 messaging.

The fully-autonomous and long-tail agents.

**Agents:** AH-A17 learning · AH-A18 deals · AH-A19 travel · AH-A20 security · AH-A21 presentation · AH-A22 maintenance.

**Exit:** all six published; ≥1 agent demonstrates the graduated-trust ramp to autonomous operation.
<!--/issue-->

---

# Layer issues (net-new)

<!--issue
action: create
title: L1.2 — Behavioral pattern detection engine
labels: agent-hub-layer,enhancement,p0
-->
## Context
An adaptive agent should notice repetition and offer to automate it ("you always move newsletters to /Reading — want me to do that automatically?"). This turns accumulated feedback (AH-L1.3) into actionable rules and is the engine behind "adaptive" in the north star.

## Goal
A pattern-detection component that mines an agent's memory + feedback history for recurring user behaviors and emits candidate automation rules.

## Scope / Deliverables
- Aggregate AH-L1.3 feedback signals per agent namespace.
- Detect recurring (action, context) → outcome patterns above a confidence threshold.
- Emit candidate rules an agent can surface via `propose()` ("automate this?").
- Expose detected patterns in the Memory Dashboard (#575) for inspection/editing.

## Acceptance criteria
- [ ] Given a synthetic history where the user repeats an action ≥N times in a context, the engine emits a candidate rule.
- [ ] Detected patterns are stored in the agent's memory namespace and visible/editable.
- [ ] No pattern fires below the confidence threshold (precision over recall).
- [ ] Unit tests with seeded histories.

## Dependencies
- Blocked by: AH-L1.1 (namespaces, #676), AH-L1.3 (feedback capture).
- Powers: adaptive autonomy (AH-L7.2), Wave-3 agents (habit, writing).

## Technical notes
- Build on Memory v2 (#606) hybrid store. Keep detection local + cheap (no extra model calls in the hot path where avoidable).

## References
Spec §6.3, §6.6 (adaptive loop).
<!--/issue-->

<!--issue
action: create
title: L1.3 — Implicit feedback capture (accept / edit / ignore / reject)
labels: agent-hub-layer,enhancement,p1
-->
## Context
The richest personalization signal is how the user reacts to an agent's output. An edit (the diff between the agent's draft and what the user actually sent) is a direct lesson. Today nothing captures this.

## Goal
Capture, per agent action, whether the user accepted, edited, ignored, or rejected it — and store the signal (including edit diffs) for AH-L1.2.

## Scope / Deliverables
- A feedback API the agent loop calls when an action's outcome is known.
- Capture the four signal types; for edits, capture the before→after diff.
- Persist to the agent's memory namespace; expose to AH-L1.2 and the Memory Dashboard.

## Acceptance criteria
- [ ] Accepting/editing/ignoring/rejecting a proposed action each records a distinct signal.
- [ ] Edit diffs are stored and retrievable.
- [ ] Signals are namespaced per agent and wipeable.
- [ ] Unit tests for each signal path.

## Dependencies
- Blocked by: AH-L1.1 (#676), AH-X1 (approve/deny widget emits the accept/reject signal).
- Powers: AH-L1.2.

## References
Spec §6.3, §6.6.
<!--/issue-->

<!--issue
action: create
title: L1.4 — Per-agent memory-schema declaration API
labels: agent-hub-layer,enhancement,p1
-->
## Context
Each agent personalizes on a specific, bounded set of fields (email: sender importance + reply style; finance: category map + thresholds). Agents need a declarative way to define what they persist so memory stays structured and auditable.

## Goal
A schema API agents use (in `memory_schema.py`) to declare their personalization fields (profile / patterns / feedback) with types.

## Scope / Deliverables
- A declaration API + loader that the base Agent reads on init.
- Validation of stored memory against the declared schema.
- Surface the schema in the Memory Dashboard so users see exactly what an agent can know.

## Acceptance criteria
- [ ] An agent declaring a schema gets typed read/write helpers for those fields.
- [ ] Writing an undeclared field is rejected (fail loudly).
- [ ] Schema is visible in the dashboard.
- [ ] Unit tests.

## Dependencies
- Blocked by: AH-L1.1 (#676).
- Used by: every agent package's `memory_schema.py`.

## References
Spec §6.2.
<!--/issue-->

<!--issue
action: create
title: L2.3 — Register web tools in KNOWN_TOOLS
labels: agent-hub-layer,enhancement,p1
-->
## Context
`web_search`/`web_extract` must be composable by name so any agent adds them without bespoke wiring — consistent with the `KNOWN_TOOLS` registry pattern.

## Goal
Register the L2 web tools in `src/gaia/agents/registry.py` `KNOWN_TOOLS`.

## Scope / Deliverables
- Add `web_search` (and `web_extract`) entries pointing at the L2 mixin/tool.
- Document composition in the registry table.

## Acceptance criteria
- [ ] An agent listing `web_search` in its tools gets the tool registered automatically.
- [ ] Registry unit test covers the new entries.

## Dependencies
- Blocked by: AH-L2.1 (#669/#1144), AH-L2.2 (#1144).

## References
Spec §3 (L2). CLAUDE.md `KNOWN_TOOLS` convention.
<!--/issue-->

<!--issue
action: create
title: L3.3 — Calendar connector (Google Calendar + MS Graph)
labels: agent-hub-layer,enhancement,p1
-->
## Context
Morning, meeting, habit, family, and travel agents need calendar read/write. This is a first-class API/MCP connector — **distinct from #660**, which proposes Playwright browser automation for email/calendar (a different, more brittle approach).

## Goal
A calendar connector exposing read events / create event / find free-slots through the L3 framework, backed by Google Calendar and Microsoft Graph.

## Scope / Deliverables
- Calendar Protocol (list/get events, create/update, free-busy).
- Google Calendar + MS Graph implementations behind the Protocol.
- Wire into `ConnectorMixin` so agents declare `connectors: [google|microsoft]`.

## Acceptance criteria
- [ ] An agent can list today's events and find a free slot via the Protocol, provider-agnostic.
- [ ] OAuth grant flow works for both providers.
- [ ] Unit tests with mocked provider APIs.

## Dependencies
- Blocked by: AH-L3.1 (OAuth GA, #927/#1105).
- Related (different approach, do not duplicate): #660.

## References
Spec §3 (L3).
<!--/issue-->

<!--issue
action: create
title: L3.4 — Contacts connector
labels: agent-hub-layer,enhancement,p1
-->
## Context
The CRM and family agents need to read the user's contacts/people to build profiles and match names.

## Goal
A contacts connector (Google People + MS Graph) exposing read/search contacts through the L3 framework.

## Scope / Deliverables
- Contacts Protocol (list/search, fetch details).
- Google People + MS Graph implementations.
- `ConnectorMixin` integration.

## Acceptance criteria
- [ ] An agent can search contacts and fetch details, provider-agnostic.
- [ ] OAuth scopes are least-privilege (read-only by default).
- [ ] Unit tests with mocked APIs.

## Dependencies
- Blocked by: AH-L3.1 (#927/#1105).
- Used by: AH-A7 crm, AH-A8 family.

## References
Spec §3 (L3).
<!--/issue-->

<!--issue
action: create
title: L4.3 — media_search natural-language tool
labels: agent-hub-layer,enhancement,p2
-->
## Context
The photo agent's headline feature ("show me photos of the kids at the beach last summer") needs a natural-language query over the local CLIP/face index.

## Goal
A `media_search` tool that translates a natural-language query into a ranked search over the local photo/video index.

## Scope / Deliverables
- Query interface over the AH-L4.1 index (semantic + face + date/location filters).
- Return ranked media references with metadata.

## Acceptance criteria
- [ ] Natural-language queries return relevant local media with no cloud calls.
- [ ] Face/date/location filters compose with semantic search.
- [ ] Unit tests over a fixture index.

## Dependencies
- Blocked by: AH-L4.1 (photo index, #730).
- Used by: AH-A13 photo.

## References
Spec §3 (L4).
<!--/issue-->

<!--issue
action: create
title: L5.2 — Semantic filesystem index
labels: agent-hub-layer,enhancement,p2
-->
## Context
The files agent's `semantic_find` and the security agent's file-sensitivity scan need content-based file search, not just name/glob.

## Goal
A local semantic index over user files (content-embedded), incrementally updated.

## Scope / Deliverables
- Index builder over configured roots (respect ignore rules; incremental).
- Content-based search API; sensitivity tagging hook (SSNs, keys, etc.).

## Acceptance criteria
- [ ] Searching by content returns relevant files; index updates incrementally on change.
- [ ] Sensitive-content detection flags configurable patterns.
- [ ] Unit tests over a fixture tree.

## Dependencies
- Related: AH-L5.1 (#466) scanner; existing `file_search` mixin.
- Used by: AH-A2 files, AH-A20 security.

## References
Spec §3 (L5).
<!--/issue-->

<!--issue
action: create
title: L5.3 — Safe write/move/dedup with confirmation tiers
labels: agent-hub-layer,enhancement,p2
-->
## Context
Agents that reorganize files (files, photo) must perform moves/dedup/deletes safely, with confirmation scaled to risk (move-to-staging is low risk; permanent delete is high).

## Goal
Confirmation-tiered file operations building on the existing `file_io` mixin.

## Scope / Deliverables
- `move` (to a reversible staging area), `dedup` (keep-best heuristics), `delete` (always-confirm).
- Tier mapping integrated with the graduated-trust model (AH-L7.3).

## Acceptance criteria
- [ ] Moves are reversible; dedup keeps the best copy; delete always confirms.
- [ ] Operations respect the agent's current trust stage.
- [ ] Unit tests for each operation + the reversal path.

## Dependencies
- Builds on: existing `file_io` (`FileIOToolsMixin`).
- Related: AH-L7.3 (trust tiers).

## References
Spec §3 (L5), §5.4.
<!--/issue-->

<!--issue
action: create
title: L6.1 — Notification primitive (toast + in-UI feed + webhook)
labels: agent-hub-layer,enhancement,p0
-->
## Context
Autonomy requires the agent to reach the user when it has something to report — *before* full messaging/tray ship. A lightweight primitive unblocks every proactive agent.

## Goal
A notification primitive with three sinks: OS desktop toast, in-UI activity feed, and an optional webhook.

## Scope / Deliverables
- A `notify(agent, level, title, body, actions?)` API.
- Sinks: desktop toast (per-OS), Agent UI feed entry, optional webhook POST.
- Optional inline actions (approve/deny) that route back to `propose()`.

## Acceptance criteria
- [ ] An agent can emit a notification that appears as a toast + UI feed entry.
- [ ] Webhook sink delivers a structured payload when configured.
- [ ] Actions on a notification round-trip to the agent.
- [ ] Unit tests per sink (mocked OS/UI/webhook).

## Dependencies
- Used by: all proactive agents; AH-L7 autonomous loop's `report` step.
- Related: AH-L6.3 messaging (#693/#635), AH-L7.5 tray (#643).

## References
Spec §3 (L6), §5.3.
<!--/issue-->

<!--issue
action: create
title: L6.2 — Notification preferences (quiet hours, batching, channel)
labels: agent-hub-layer,enhancement,p1
-->
## Context
Proactive agents must not become noisy. Users need per-agent control over when and how they're notified.

## Goal
A preferences layer over AH-L6.1: quiet hours, batching/digest, and per-agent channel selection.

## Scope / Deliverables
- Per-agent prefs: quiet hours, batch window, allowed channels.
- Batching/digest delivery that coalesces non-urgent notifications.
- Urgent override path.

## Acceptance criteria
- [ ] Notifications respect quiet hours and batch windows.
- [ ] Urgent notifications bypass batching.
- [ ] Prefs are per-agent and persisted.
- [ ] Unit tests.

## Dependencies
- Blocked by: AH-L6.1.

## References
Spec §3 (L6).
<!--/issue-->

<!--issue
action: create
title: L7.3 — Graduated trust model
labels: agent-hub-layer,enhancement,p1
-->
## Context
Agents must earn the right to commit actions. Trust ramps per action class (observe → prepare → act), with an always-confirm floor for irreversible/physical/costly actions. This is the **inverse** of #559 "dangerous mode" (a blunt full bypass); graduated trust is the safe ramp.

## Goal
A per-action-class trust model: supervised → semi-autonomous → autonomous, with graduation rules and an always-confirm floor.

## Scope / Deliverables
- Trust stages tracked per agent + action type.
- Graduation via `proactive.trust_graduation.autonomous_after: N` (approved-action count) or explicit user grant.
- Always-confirm floor for class-3 (send-to-external, delete, purchase, physical `call_service`).
- Integrates with the global kill switch (AH-L7.5).

## Acceptance criteria
- [ ] An action type auto-commits only after N approvals or explicit grant.
- [ ] Class-3 actions never auto-fire from graduation alone.
- [ ] Kill switch immediately reverts all agents to supervised/paused.
- [ ] Unit tests for graduation + floor + kill switch.

## Dependencies
- Related: AH-L7.2 loop, AH-L7.4 hooks, AH-X1 approve/deny.
- Distinct from: #559 (dangerous mode).

## References
Spec §5.4.
<!--/issue-->

<!--issue
action: create
title: L7.4 — Base-Agent lifecycle hooks (on_first_run / on_heartbeat / propose)
labels: agent-hub-layer,enhancement,p0
-->
## Context
Hub agents must be proactive — discover what the user needs and propose actions rather than waiting at a blank prompt. The base `Agent` has no lifecycle for this. **Wave-0 prerequisite:** no proactive agent can ship until the base class supports it.

## Goal
Add a proactive lifecycle to the base `Agent` so every Hub agent gets first-run discovery, scheduled/triggered work, and propose-with-approval for free.

## Scope / Deliverables
- `on_first_run(context)` — discover relevant context, return 2–4 proposed actions; no autonomous commits.
- `on_heartbeat(context)` — one cycle of the autonomous loop (observe → decide → propose|act → report → learn).
- `propose(action)` — surface an action for approve/deny; record the decision (feeds AH-L1.3).
- Wire into the loop in `src/gaia/agents/base/agent.py`; default no-op implementations so existing agents are unaffected.

## Acceptance criteria
- [ ] A sample agent implementing `on_first_run` returns proposals rendered with approve/deny.
- [ ] `propose()` decisions are persisted and retrievable (AH-L1.3).
- [ ] Existing agents (no hooks) behave exactly as before — regression test.
- [ ] Unit tests for all three hooks with a mocked LLM.

## Dependencies
- Blocks: every agent package (AH-A1…A22), AH-X1.
- Related: AH-L7.1 (heartbeat fires `on_heartbeat`, #634), AH-L1.3.

## Technical notes
- `src/gaia/agents/base/agent.py`. Keep hooks optional (default no-op) — honor the "no silent breakage" rule. Approve/deny UI widget is AH-X1.

## References
Spec §2.4, §5.
<!--/issue-->

---

# Agent packages

> Every agent block shares the same **Build checklist** (shown in full per issue for self-containment) and the same **proactive contract**: on first run it discovers context and proposes 2–4 actions; it never opens with a blank prompt.

<!--issue
action: create
title: hub/agents/python/knowledge — Knowledge & Research Agent
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
Document Q&A over the user's own files is GAIA's strongest shipped capability (mature RAG) and the gateway use case that brings professionals into local AI. This agent wraps RAG in a proactive package — and is **Wave 1 / the Hub platform acceptance test (A1)**: if it can't be authored, tested, and published through the Hub, the platform isn't done.

## Goal
Ship `hub/agents/python/knowledge/` — a proactive agent that turns watched folders + the web into a queryable local knowledge base and answers with citations.

## Agent profile
- **Memory tier:** enhance · **Datastore:** `rag` (vector index) · **Layers:** L1, L2, L5 · **Wave:** 1
- **Triggers:** event (new files in watched folders).
- **Unique tools:** `index_folder`, `query_kb`, `monitor_topic`.
- **Personalizes on:** interests, query patterns (implicit).

## User journey (autonomy)
Day 0: "Point me at folders to index." → indexes, reports counts. Days 1–7: auto-indexes new files, summarizes deltas, answers with citations. Steady state: proactively surfaces "3 new docs in /Research — here's what changed" and monitors web topics the user researches. Always-confirm: deleting/moving source files.

## Build checklist
- [ ] `gaia-agent.yaml` (layers, `proactive` block, `interfaces: all true`)
- [ ] agent class (`Agent` + `RAGToolsMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt (first-run discovery + propose)
- [ ] `index_folder` / `query_kb` / `monitor_topic` tools + `memory_schema.py` + triggers
- [ ] 5 interfaces verified (TUI/CLI/pipe/API+OpenAPI/MCP)
- [ ] unit tests (mocked LLM) + eval scenarios + committed baseline
- [ ] `gaia agent pack` + `publish` to R2 (`verified`) + verify on website + Agent UI

## Dependencies
- Blocked by: Hub platform (v0.29), AH-L7.4, AH-L2.x (web), AH-L5.x (filesystem).
- Related existing: Knowledge Agent milestone (#1141–#1148).

## References
Spec §4 (A1), §5.7, §6.8, §9 checklist.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/files — File & Desktop Organizer
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
Full filesystem access is a structural local advantage cloud agents can't match. This agent tames the chronic mess (Downloads, duplicates, unsorted docs) proactively.

## Goal
Ship `hub/agents/python/files/` — a proactive organizer that scans the filesystem, proposes organization, dedups, and finds files by content.

## Agent profile
- **Memory tier:** enhance · **Datastore:** `fileindex` · **Layers:** L1, L5 · **Wave:** 1
- **Triggers:** threshold (e.g. Downloads > N files).
- **Unique tools:** `scan_disk`, `suggest_organization`, `find_duplicates`, `semantic_find`.
- **Personalizes on:** folder conventions, rejected moves (implicit).

## User journey (autonomy)
Day 0: "Downloads has 847 unsorted files — want me to organize?" Days 1–7: proposes sorts into a reversible staging area; learns from rejects. Steady state: keeps folders tidy, dedups, answers "find that proposal from last week" by content. Always-confirm: permanent delete.

## Build checklist
- [ ] `gaia-agent.yaml` (layers, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + `FileSearchToolsMixin` + `FileIOToolsMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt
- [ ] `scan_disk` / `suggest_organization` / `find_duplicates` / `semantic_find` + `memory_schema.py` + triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L7.4, AH-L5.2 (semantic index), AH-L5.3 (safe ops).

## References
Spec §4 (A2), §5.7, §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/smarthome — Smart Home Voice Agent
labels: enhancement,agent,agent-hub-agent,p0
-->
## Context
Home Assistant + local LLM is the #1 use case on r/selfhosted, and NPU inference (always-on, ~2W, sub-second) is purpose-built for it. The HA MCP server already exists — much of this is wiring + voice.

## Goal
Ship `hub/agents/python/smarthome/` — voice + natural-language control of Home Assistant devices, with proactive routine learning and anomaly alerts.

## Agent profile
- **Memory tier:** enhance · **Datastore:** none · **Layers:** L1, L6 + Home Assistant MCP · **Wave:** 1
- **Triggers:** sensor (HA device/state changes).
- **Unique tools:** HA MCP (`get_states`, `call_service`, …), voice (Whisper ASR + Kokoro TTS).
- **Personalizes on:** device names, routines, preferences (implicit observation).

## User journey (autonomy)
Day 0: "Found 14 devices — want voice control, and should I watch for anything?" Days 1–7: executes spoken commands, observes routines. Wk 2+: "You turn off all lights at 11pm most nights — automate that?" Steady state: runs learned automations, alerts on anomalies ("garage open 1h"). Always-confirm: locks, oven, garage, anything physical.

## Build checklist
- [ ] add Home Assistant MCP server to the curated catalog
- [ ] `gaia-agent.yaml` (HA MCP dependency, voice, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + `MCPClientMixin` + `MemoryMixin` + voice + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + `memory_schema.py` + sensor triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline (incl. tool-calling reliability against HA MCP)
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L7.4, AH-L6.1 (notifications), Home Assistant MCP catalog entry.
- Related existing: #705, #646.

## References
Spec §4 (A4), §5.6 (sensor archetype), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/code — Code & Project Context Agent
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
Source code is IP many orgs forbid sending to the cloud. This agent gives Claude-Code-style session awareness locally — "welcome back, here's where you left off" — without cloud. It does **not** compete with cloud coding agents on deep multi-file reasoning (cede that); it competes on local context + privacy.

## Goal
Ship `hub/agents/python/code/` — restores project context across sessions, summarizes PRs/advisories, and assists with local-privacy-sensitive coding.

## Agent profile
- **Memory tier:** essential · **Datastore:** `records` (code-index) · **Layers:** L1, L5 · **Wave:** 1
- **Triggers:** event (repo opened).
- **Unique tools:** `repo_status`, `restore_context`, `review_prs`, `dep_advisories`.
- **Personalizes on:** active repos, session state, workflow (implicit).

## User journey (autonomy)
Day 0: "Restore your context for this repo?" Days 1–7: on repo open, summarizes where you left off, open PRs, failing tests. Steady state: proactively flags "security advisory for a package you use in 2 repos." Always-confirm: commits, pushes, branch ops.

## Build checklist
- [ ] `gaia-agent.yaml` (layers, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + code-index mixin + `FileIOToolsMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + `memory_schema.py` + repo-open trigger
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L7.4; uses existing CodeIndex + git tooling.

## References
Spec §4 (code), §5.7, §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/morning — Morning Intelligence Agent
labels: enhancement,agent,agent-hub-agent,p0
-->
## Context
A personalized morning brief is the textbook daily-retention ritual (Google shipped Gemini Daily Brief; OpenClaw's "Morning Briefing" is a top skill). GAIA's runs locally. A **news-only v1 ships in Wave 1** on web search alone; email/calendar sections layer in once connectors land.

## Goal
Ship `hub/agents/python/morning/` — delivers a tailored brief on a schedule, learning what the user reads vs. skips.

## Agent profile
- **Memory tier:** essential · **Datastore:** none · **Layers:** L1, L2, L6, L7 · **Wave:** 1 (news-only v1)
- **Triggers:** scheduled (daily cron).
- **Unique tools:** `compose_brief`.
- **Personalizes on:** topics, read/skip history, delivery time (explicit + implicit).

## User journey (autonomy)
Day 0: "What topics matter, and when should the brief arrive?" Days 1–7: daily brief; user expands/skips items. Wk 2+: drops skipped sections, reorders by what's opened first. Steady state: a 30-second brief that's *theirs*, delivered before they ask. Read-only output (no class-3 actions).

## Build checklist
- [ ] `gaia-agent.yaml` (layers, `proactive.heartbeat` cron, `interfaces: all true`)
- [ ] agent class (`Agent` + web tools + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + `memory_schema.py` + daily schedule trigger
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L7.1/L7.4 (schedule), AH-L2.x (web), AH-L6.1 (delivery).
- Related existing: #663. Email/calendar sections depend on L3 (Wave 2).

## References
Spec §4 (A5), §5.6 (time archetype), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/email — Email Companion
labels: enhancement,agent,agent-hub-agent,p0
-->
## Context
Email triage is the highest-demand local-agent use case (~90→25 min/day) and email is the most privacy-sensitive non-health data — a structural win for local-first. Wave-2 flagship.

## Goal
Ship `hub/agents/python/email/` — triages the inbox, drafts replies in the user's voice, delivers a digest, graduating from supervised to auto-sending routine replies.

## Agent profile
- **Memory tier:** essential · **Datastore:** none (operates on live mail) · **Layers:** L1, L3, L6, L7 · **Wave:** 2
- **Triggers:** event (new mail, ~15-min heartbeat).
- **Unique tools:** `triage`, `draft_reply`, `digest`.
- **Personalizes on:** sender importance, per-recipient reply style (implicit, from edits).

## User journey (autonomy)
Day 0: scans inbox, reports sender stats, asks to start triaging. Days 1–7: classifies + drafts; user edits/sends (edits captured). Wk 2–4: after ~200 approved drafts, auto-sends *routine types only*. Steady state: overnight triage + morning digest + the few that need the user. Always-confirm: sending to new/sensitive recipients.

## Build checklist
- [ ] `gaia-agent.yaml` (`connectors: [google, microsoft]`, `proactive` + `trust_graduation`, `interfaces: all true`)
- [ ] agent class (`Agent` + `ConnectorMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + `triage`/`draft_reply`/`digest` + `memory_schema.py` + new-mail trigger
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L3.1/L3.2 (OAuth + email backend), AH-L1.x, AH-L6.1, AH-L7.3/L7.4.
- Related existing: Gmail #965 (merged), Outlook #963 (CLOSED — verify).

## References
Spec §4 (A6), §5.6 (event archetype), §6.5 (before→after), §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/crm — Personal CRM & Relationship Agent
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
Relationship maintenance is universal but unserved locally; the relationship graph (who you know, how well, what you discussed) is deeply private.

## Goal
Ship `hub/agents/python/crm/` — builds contact profiles from email/calendar/contacts, flags dormant relationships, and drafts follow-ups in the right tone.

## Agent profile
- **Memory tier:** essential · **Datastore:** `records` (contacts) · **Layers:** L1, L3 · **Wave:** 2
- **Triggers:** scheduled + event (new interactions).
- **Unique tools:** `build_profile`, `relationship_health`, `draft_followup`.
- **Personalizes on:** contact profiles, per-person tone (explicit + implicit).

## User journey (autonomy)
Day 0: "Build profiles from your contacts + recent mail?" Days 1–7: builds timelines, surfaces "haven't talked to Sarah in 3 months." Steady state: proactive follow-up drafts before momentum breaks; occasion reminders. Always-confirm: sending outreach.

## Build checklist
- [ ] `gaia-agent.yaml` (`connectors: [google, microsoft]`, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + `ConnectorMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L3.4 (contacts), AH-L3.2/L3.3 (email/calendar), AH-L1.x.
- Related existing: #704.

## References
Spec §4 (crm), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/family — Family Command Center
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
Family logistics (school flyers, sports schedules, meals, chores) is a high mental-load, privacy-sensitive domain; 98% of parents in surveys report reduced load with AI organization.

## Goal
Ship `hub/agents/python/family/` — parses school/activity emails and flyers into a family calendar, plans meals, tracks chores, and notifies.

## Agent profile
- **Memory tier:** essential · **Datastore:** `records` · **Layers:** L1, L3, L4 (flyer OCR), L6 · **Wave:** 2
- **Triggers:** event (school email / flyer).
- **Unique tools:** `parse_flyer`, `family_calendar`, `meal_plan`, `chore_track`.
- **Personalizes on:** family members, schedules, dietary prefs (explicit + implicit).

## User journey (autonomy)
Day 0: "Manage the family calendar? Forward me a flyer to try." Days 1–7: flyer/email → proposed calendar event. Steady state: "Soccer moved to Thursdays — calendar updated, partner notified"; weekly meal plan from fridge + prefs. Always-confirm: inviting others, purchases.

## Build checklist
- [ ] `gaia-agent.yaml` (`connectors`, L4, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + `ConnectorMixin` + `VLMToolsMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L3.x, AH-L4.2 (flyer extraction), AH-L6.1.

## References
Spec §4 (A8 / family), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/writing — Writing & Communication Agent
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
A local "Grammarly that learns *your* voice" — high daily use, and your writing style is your professional identity (don't want it in a cloud training set). Not currently on the roadmap; this spec adds it.

## Goal
Ship `hub/agents/python/writing/` — learns the user's style from their writing and drafts/edits in their voice, per recipient and medium.

## Agent profile
- **Memory tier:** essential · **Datastore:** none · **Layers:** L1 · **Wave:** 3
- **Triggers:** conversational (proactive within a session).
- **Unique tools:** `learn_style`, `draft`, `tone_adjust`.
- **Personalizes on:** style/voice per recipient & medium (implicit, from edits).

## User journey (autonomy)
Day 0: "Learn your style from past writing?" → builds a style profile. Days 1–7: drafts in your voice on request; learns from edits. Steady state: drafts need little editing; "this reply doesn't match your usual tone — adjust?" Always-confirm: publishing/sending (it drafts; other agents send).

## Build checklist
- [ ] `gaia-agent.yaml` (L1, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + `learn_style`/`draft`/`tone_adjust` + `memory_schema.py`
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L7.4, AH-L1.4 (schema for style profile).

## References
Spec §4 (writing), §6.5 (style before→after), §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/journal — Journal & Reflection Agent
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
A journal is the most private document a person writes — the strongest possible case for local-only. The APA recognized AI-assisted reflection as an "emerging adjunct" (Jan 2026); privacy is the #1 adoption barrier this solves.

## Goal
Ship `hub/agents/python/journal/` — a daily check-in that detects mood/theme patterns over time and offers reflective prompts.

## Agent profile
- **Memory tier:** essential · **Datastore:** `timeseries` (entries/mood) · **Layers:** L1, L6, L7 · **Wave:** 3
- **Triggers:** scheduled (daily check-in; weekly review).
- **Unique tools:** `daily_checkin`, `detect_patterns`, `weekly_review`.
- **Personalizes on:** themes, mood baselines, which prompts land (implicit).

## User journey (autonomy)
Day 0: "When should I prompt your daily check-in?" Days 1–7: gentle daily prompt; stores entries. Steady state: "your stress mentions correlate with Tuesday deadlines"; weekly pattern review. Read-only/private — no external actions.

## Build checklist
- [ ] `gaia-agent.yaml` (L1, `timeseries` datastore, `proactive.heartbeat`, `interfaces: all true`)
- [ ] agent class (`Agent` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + daily/weekly triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L7.1/L7.4, AH-L1.2 (pattern detection), AH-L6.1.

## References
Spec §4 (journal), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/habit — Habit & Routine Agent
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
Behavioral/biometric data is the most granular personal data possible; correlating routines with outcomes (sleep↔focus) demands local processing.

## Goal
Ship `hub/agents/python/habit/` — tracks routines, correlates them with outcomes, and nudges on pattern breaches.

## Agent profile
- **Memory tier:** essential · **Datastore:** `timeseries` · **Layers:** L1, L3 (calendar), L6, L7 · **Wave:** 3
- **Triggers:** scheduled + threshold (e.g. sleep < target).
- **Unique tools:** `track_routine`, `correlate_outcomes`, `nudge`.
- **Personalizes on:** routines, streaks, which nudges work (implicit).

## User journey (autonomy)
Day 0: "Track your routines and nudge you?" Days 1–7: observes patterns. Steady state: "you averaged 5.5h sleep; focus dropped 30% — block calendar after 10pm tomorrow?" Advisory; proposed calendar blocks confirm.

## Build checklist
- [ ] `gaia-agent.yaml` (L1, `timeseries`, calendar connector, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + `MemoryMixin` + `ConnectorMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L7.x, AH-L1.2, AH-L3.3 (calendar), AH-L6.x.

## References
Spec §4 (habit), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/health — Health & Wellness Agent
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
Health data is the most sensitive category, period (HIPAA, personal). GAIA's EMR agent (#770) is a foundation; this extends it to consumer wellness.

## Goal
Ship `hub/agents/python/health/` — imports wearable/lab data, surfaces trends and correlations, tracks medications.

## Agent profile
- **Memory tier:** enhance · **Datastore:** `timeseries` · **Layers:** L1, L4 (lab images), L6 · **Wave:** 3
- **Triggers:** scheduled + threshold (anomaly).
- **Unique tools:** `import_wearable`, `analyze_labs`, `med_tracker`, `doctor_summary`.
- **Personalizes on:** metric baselines, med schedule (explicit + implicit).

## User journey (autonomy)
Day 0: "Import your wearable export?" Days 1–7: builds baselines. Steady state: "sleep quality down 20% this week, steps also down — correlation?"; med reminders; doctor-visit summary on request. Always-confirm: sharing data; no diagnostic claims (advisory framing + gating).

## Build checklist
- [ ] `gaia-agent.yaml` (L1, L4, `timeseries`, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + `VLMToolsMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L4.2 (lab image extraction), AH-L6.1; builds on EMR (#770).

## References
Spec §4 (health), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/photo — Photo & Memory Agent
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
"Show me photos of the kids at the beach last summer" — natural-language photo search with no tags. Photo libraries (faces, homes, locations) are intimate; Google/Apple use them for training. Local keeps every pixel private.

## Goal
Ship `hub/agents/python/photo/` — indexes the local library (CLIP + faces), searches by natural language, proposes albums, dedups.

## Agent profile
- **Memory tier:** enhance · **Datastore:** `media` (CLIP/face index) · **Layers:** L1, L4 · **Wave:** 4
- **Triggers:** event (new photos).
- **Unique tools:** `index_photos`, `face_group`, `media_search`, `highlight_reel`, `dedup`.
- **Personalizes on:** people names, favorites, album style (explicit naming + implicit).

## User journey (autonomy)
Day 0: "Index your library?" → builds CLIP + face index. Days 1–7: organizes new photos, proposes event albums. Steady state: instant natural-language search; "your year in photos" highlight reels. Always-confirm: deleting photos.

## Build checklist
- [ ] `gaia-agent.yaml` (L4, `media` datastore, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + `VLMToolsMixin` + media-index + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + new-photo trigger
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L4.1 (photo index, #730), AH-L4.3 (media_search).

## References
Spec §4 (photo), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/meeting — Meeting Companion
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
Cloud meeting tools face lawsuits/bans over recording consent (Otter.ai class action; Read AI bans). Local NPU-accelerated Whisper transcription eliminates that risk and is a strong AMD hardware demo.

## Goal
Ship `hub/agents/python/meeting/` — transcribes meetings locally, diarizes speakers, extracts action items, preps the user beforehand.

## Agent profile
- **Memory tier:** enhance · **Datastore:** `archive` (transcripts) · **Layers:** L1, L3 (calendar), L6 + Whisper · **Wave:** 4
- **Triggers:** event (meeting start/end).
- **Unique tools:** `transcribe`, `diarize`, `extract_actions`, `meeting_prep`.
- **Personalizes on:** recurring attendees, preferred note format (implicit).

## User journey (autonomy)
Day 0: "Transcribe + summarize your meetings?" Before a meeting: "last time with Sarah you discussed the Q4 pipeline — here's a recap." During: live transcription. After: "3 action items — add to your tasks?" Always-confirm: sharing notes externally.

## Build checklist
- [ ] `gaia-agent.yaml` (L3 calendar, Whisper, `archive`, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + ASR + `ConnectorMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools (+ diarization) + `memory_schema.py` + meeting triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L3.3 (calendar), AH-L6.1; uses Whisper ASR. Diarization: #700.

## References
Spec §4 (meeting, #700), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/finance — Personal Finance Agent
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
Financial data is the #1 "refuse to send to cloud" category. "Your bank data never leaves your PC" is an unbeatable pitch; no polished local solution exists.

## Goal
Ship `hub/agents/python/finance/` — ingests bank/card CSVs (and receipts via VLM), categorizes, detects anomalies, reports spending.

## Agent profile
- **Memory tier:** enhance · **Datastore:** `ledger` · **Layers:** L1, L4 (receipts), L6 · **Wave:** 4
- **Triggers:** threshold (anomaly, unused subscription).
- **Unique tools:** `import_transactions`, `categorize`, `detect_anomaly`, `spending_report`.
- **Personalizes on:** category mappings, budgets, thresholds (explicit + corrections).

## User journey (autonomy)
Day 0: "Drop a bank/card CSV." → first report. Days 1–7: auto-categorizes; user corrects a few (captured). Steady state: monthly report unprompted; "3 subscriptions unused 60+ days — cancel?"; "you'll hit your savings goal by October." Always-confirm: any payment/transfer.

## Build checklist
- [ ] `gaia-agent.yaml` (L4, `ledger`, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + `VLMToolsMixin` + scratchpad/SQL + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + threshold triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L4.2 (receipt extraction, #664), AH-L6.1.
- Related existing: #707, #664.

## References
Spec §4 (finance), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/freelance — Freelancer Business Agent
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
Freelancers spend ~25% of their week on invoices, expenses, and financial admin — privacy-sensitive, privileged client data ideal for local processing.

## Goal
Ship `hub/agents/python/freelance/` — tracks projects/hours, generates invoices at milestones, chases payments, estimates taxes.

## Agent profile
- **Memory tier:** enhance · **Datastore:** `ledger` · **Layers:** L1, L3 (email), L4 (receipts), L6 · **Wave:** 4
- **Triggers:** event + threshold (milestone reached, payment overdue).
- **Unique tools:** `track_project`, `generate_invoice`, `chase_payment`, `tax_estimate`.
- **Personalizes on:** clients, rates, invoice cadence (explicit + implicit).

## User journey (autonomy)
Day 0: "Track projects and invoices?" Days 1–7: logs hours/milestones; drafts first invoice. Steady state: "project at 80% — invoice $4,000 ready?"; "Acme 15 days overdue — send reminder?"; quarterly tax set-aside. Always-confirm: sending invoices, payment ops.

## Build checklist
- [ ] `gaia-agent.yaml` (L3 email, L4 receipts, `ledger`, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + `ConnectorMixin` + `VLMToolsMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L3.2 (email), AH-L4.2 (receipts), AH-L6.1.
- Related existing: #708.

## References
Spec §4 (freelance), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/learning — Learning & Self-Improvement Agent
labels: enhancement,agent,agent-hub-agent,p2
-->
## Context
A "second brain" that tracks what you're learning, finds resources, and runs spaced repetition over what you've read — your knowledge gaps and interests are personal.

## Goal
Ship `hub/agents/python/learning/` — tracks learning topics, builds flashcards from read material, curates resources, quizzes on schedule.

## Agent profile
- **Memory tier:** essential · **Datastore:** `records` (flashcards) · **Layers:** L1, L2, L7 · **Wave:** 5
- **Triggers:** scheduled (spaced-repetition due).
- **Unique tools:** `track_learning`, `make_flashcards`, `find_resources`, `quiz`.
- **Personalizes on:** knowledge gaps, pace, review schedule (implicit, from quiz results).

## User journey (autonomy)
Day 0: "Track what you're learning?" Days 1–7: builds cards from docs you read. Steady state: "Day 7 since your Python review — quiz time?"; "you read 3 Kubernetes articles — build a study guide?" Advisory only.

## Build checklist
- [ ] `gaia-agent.yaml` (L2, `records`, `proactive.heartbeat`, `interfaces: all true`)
- [ ] agent class (`Agent` + web tools + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + SRS schedule trigger
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L7.x, AH-L2.x.

## References
Spec §4 (learning), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/deals — Deal & Price Watch Agent
labels: enhancement,agent,agent-hub-agent,p2
-->
## Context
Wishlist + price-drop alerts; shopping patterns/wishlists are profiling data cloud trackers monetize. Keep them local.

## Goal
Ship `hub/agents/python/deals/` — watches products, tracks price history, alerts on genuine drops with quality assessment.

## Agent profile
- **Memory tier:** enhance · **Datastore:** `timeseries` (prices) · **Layers:** L1, L2, L6, L7 · **Wave:** 5
- **Triggers:** scheduled (periodic checks) + sensor (price).
- **Unique tools:** `watch_product`, `track_price`, `assess_deal`.
- **Personalizes on:** wishlist, price thresholds (explicit).

## User journey (autonomy)
Day 0: "Which products should I watch?" Days 1–7: tracks prices. Steady state: "AirPods hit a 6-month low — buy?"; "this 'sale' is only 5% off typical — skip." Always-confirm: purchases.

## Build checklist
- [ ] `gaia-agent.yaml` (L2, `timeseries`, `proactive.heartbeat`, `interfaces: all true`)
- [ ] agent class (`Agent` + web tools + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + schedule trigger
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L2.x, AH-L6.1, AH-L7.1.
- Related existing: #480–#491 (DealAgent work).

## References
Spec §4 (deals), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/travel — Travel Companion Agent
labels: enhancement,agent,agent-hub-agent,p2
-->
## Context
Travel data reveals income, location patterns, relationships, and interests — private. A local agent watches fares against the user's free windows and tastes.

## Goal
Ship `hub/agents/python/travel/` — proposes trips on fare drops in free calendar windows, builds packing lists/itineraries, journals trips.

## Agent profile
- **Memory tier:** essential · **Datastore:** none · **Layers:** L1, L2, L3 (calendar), L6 · **Wave:** 5
- **Triggers:** scheduled + sensor (fare drop).
- **Unique tools:** `find_trips`, `packing_list`, `itinerary`, `trip_journal`.
- **Personalizes on:** destination tastes, trip style, budget (explicit + implicit from past trips).

## User journey (autonomy)
Day 0: "Watch destinations / your free windows?" Steady state: "free June 15–22 — flights to Lisbon $380, 40% below average — draft an itinerary?" Always-confirm: booking.

## Build checklist
- [ ] `gaia-agent.yaml` (L2, L3 calendar, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + web tools + `ConnectorMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L2.x, AH-L3.3 (calendar), AH-L6.1.

## References
Spec §4 (travel), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/security — Security & Privacy Guardian
labels: enhancement,agent,agent-hub-agent,p1
-->
## Context
A security agent that runs in the cloud is an irony — monitoring your passwords, breaches, and filesystem must be local. High-value, privacy-critical.

## Goal
Ship `hub/agents/python/security/` — monitors breach DBs for the user's identities, audits passwords, scans for sensitive files, and guides remediation.

## Agent profile
- **Memory tier:** enhance · **Datastore:** `records` (identities) · **Layers:** L1, L2 (breach DBs), L5, L6 · **Wave:** 5 (deep OS features depend on v0.30 OS Agents)
- **Triggers:** sensor (breach DB update).
- **Unique tools:** `breach_check`, `password_audit`, `privacy_audit`, `file_sensitivity_scan`.
- **Personalizes on:** monitored identities, known-safe baseline (explicit).

## User journey (autonomy)
Day 0: "Monitor your accounts and passwords?" Steady state: "your email appeared in today's breach; you reuse this password on 3 sites — walk through changing them?"; flags unencrypted files containing SSNs. Always-confirm: changing/deleting credentials.

## Build checklist
- [ ] `gaia-agent.yaml` (L2, L5, `records`, `proactive`, `interfaces: all true`)
- [ ] agent class (`Agent` + web tools + `FileSearchToolsMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + breach-DB trigger
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L2.x, AH-L5.2 (sensitivity scan), AH-L6.1.
- Related: v0.30 OS Agents for deeper OS integration.

## References
Spec §4 (security), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/presentation — Presentation & Content Agent
labels: enhancement,agent,agent-hub-agent,p2
-->
## Context
Board decks, financial projections, strategic plans are the most confidential business documents; cloud AI explicitly warns against uploading them.

## Goal
Ship `hub/agents/python/presentation/` — turns data + audience into narrative, slide outlines, and talking points in the user's deck style.

## Agent profile
- **Memory tier:** optional · **Datastore:** none · **Layers:** L1, L5 · **Wave:** 5
- **Triggers:** conversational (on-demand only; no background triggers).
- **Unique tools:** `analyze_data`, `build_narrative`, `slide_outline`, `talking_points`.
- **Personalizes on:** deck structure, tone, audience profiles (implicit, from edits).

## User journey (autonomy)
On-demand: "Here's the Q2 spreadsheet; it's for the board." → analyzes data, builds a narrative arc, slide outlines, per-slide talking points in the user's style. No between-session autonomy.

## Build checklist
- [ ] `gaia-agent.yaml` (L1, L5, `proactive.first_run` only, `interfaces: all true`)
- [ ] agent class (`Agent` + data/scratchpad tools + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py`
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L5.x; RAG/data tooling.

## References
Spec §4 (presentation), §6.8, §9.
<!--/issue-->

<!--issue
action: create
title: hub/agents/python/maintenance — Home & Vehicle Maintenance Agent
labels: enhancement,agent,agent-hub-agent,p2
-->
## Context
Home/vehicle maintenance is long-horizon record-keeping (appliances, warranties, service history) that benefits from an agent remembering for years.

## Goal
Ship `hub/agents/python/maintenance/` — maintains an inventory, schedules maintenance to the user's actual usage, diagnoses issues from photos, finds parts.

## Agent profile
- **Memory tier:** enhance · **Datastore:** `records` (inventory/history) · **Layers:** L1, L2, L4 (photo diagnosis), L6, L7 · **Wave:** 5
- **Triggers:** scheduled (maintenance due).
- **Unique tools:** `inventory`, `maintenance_schedule`, `diagnose_photo`, `find_parts`.
- **Personalizes on:** appliances/vehicles, service history, intervals (explicit + implicit).

## User journey (autonomy)
Day 0: "Inventory your appliances and vehicles?" Steady state: "furnace filter due (87 days; your usage) — here's the model number and cheapest source"; snap a photo of a leak → DIY-vs-pro guidance. Always-confirm: scheduling paid service.

## Build checklist
- [ ] `gaia-agent.yaml` (L2, L4, `records`, `proactive.heartbeat`, `interfaces: all true`)
- [ ] agent class (`Agent` + web tools + `VLMToolsMixin` + `MemoryMixin` + `ApiAgent` + `MCPAgent`)
- [ ] proactive system prompt + tools + `memory_schema.py` + due-date triggers
- [ ] 5 interfaces verified
- [ ] unit tests + eval scenarios + baseline
- [ ] pack + publish to R2 (`verified`) + verify website + Agent UI

## Dependencies
- Blocked by: Hub platform, AH-L2.x, AH-L4.2 (photo diagnosis), AH-L6.1, AH-L7.1.

## References
Spec §4 (maintenance), §6.8, §9.
<!--/issue-->

---

# Cross-cutting issues

<!--issue
action: create
title: AH-X1 — Proactive interaction contract on base Agent (approve/deny UI)
labels: enhancement,agent,sdk,p0
-->
## Context
The proactive model (agent proposes, user approves) needs a UI surface. AH-L7.4 adds the agent-side hooks; this issue adds the **user-facing approve/deny widget** and wires its decisions back as feedback.

## Goal
An approve/deny/edit UI component (Agent UI + CLI/TUI) that renders an agent's proposed actions and routes the decision to `propose()` and AH-L1.3.

## Scope / Deliverables
- Proposal card UI (Agent UI) + CLI/TUI equivalent: title, rationale, approve/deny/edit.
- Decision routing: approve → execute; edit → execute modified + capture diff; deny → record reason.
- Emit the accept/edit/reject signal to AH-L1.3.

## Acceptance criteria
- [ ] A proposed action renders with working approve/deny/edit in Agent UI and CLI/TUI.
- [ ] Each decision routes correctly and emits the right feedback signal.
- [ ] Unit/component tests.

## Dependencies
- Blocked by: AH-L7.4 (hooks). Powers: AH-L1.3, every agent. **Wave-0 prerequisite.**

## References
Spec §2.4, §5.1, §6.6.
<!--/issue-->

<!--issue
action: create
title: AH-X2 — Eval harness for proactive agents (first-run + heartbeat simulation)
labels: enhancement,eval,tests,p0
-->
## Context
Every agent has a domain-specific system prompt and proactive behavior — LLM-affecting surfaces that unit tests don't cover (per CLAUDE.md eval rule). We need an eval harness that exercises the proactive lifecycle, not just request/response.

## Goal
An eval harness that simulates `on_first_run` (does the agent discover context and propose sensible actions?) and `on_heartbeat` (does it act within trust and never exceed permissions?).

## Scope / Deliverables
- Scenario format for first-run discovery + heartbeat cycles.
- Assertions: proposals are relevant; class-3 actions never auto-fire; permissions respected.
- Integrate with `gaia eval agent` and scorecard/baseline flow.

## Acceptance criteria
- [ ] A sample agent runs a first-run + heartbeat eval producing a scorecard.
- [ ] A scenario that attempts an over-permission action fails the eval.
- [ ] Docs for authoring proactive scenarios.

## Dependencies
- Blocked by: AH-L7.4. Used by: every agent's eval baseline (AH-X5).

## References
Spec §5, §10 (testing).
<!--/issue-->

<!--issue
action: create
title: AH-X3 — gaia agent init --proactive scaffold template
labels: enhancement,cli,sdk,p1
-->
## Context
Authoring 22 agents (and community agents) should start from a scaffold with the proactive contract, manifest, and interface harness pre-wired — so authors write domain logic, not boilerplate.

## Goal
Extend `gaia agent init` with a `--proactive` template that scaffolds the full package shape.

## Scope / Deliverables
- Scaffold `gaia-agent.yaml` (with `proactive`, `interfaces: all true`, `layers`), `agent.py` (hooks pre-wired), `prompt.py`, `tools.py`, `memory_schema.py`, `triggers.py`, `tests/`.
- README stub + eval scenario stub.

## Acceptance criteria
- [ ] `gaia agent init <id> --proactive` produces a package that passes `gaia agent test --lint` out of the box.
- [ ] Generated agent runs (no-op proposals) end-to-end.

## Dependencies
- Blocked by: Hub platform (init), AH-L7.4, AH-X8 (interface harness).

## References
Spec §2.2, §9 (developer workflow).
<!--/issue-->

<!--issue
action: create
title: AH-X4 — Hub category taxonomy + filters for the 22 agents
labels: enhancement,gui,agent-hub,p2
-->
## Context
22 agents need browseable categories/filters on both the website and the Agent UI Available tab.

## Goal
A shared category taxonomy + filter UI consumed by both surfaces from the R2 index.

## Scope / Deliverables
- Taxonomy (e.g. Productivity, Communication, Home, Money, Health, Creative, Developer, Security).
- Filter UI (category, memory tier, trigger type, security tier) reading the R2 index.

## Acceptance criteria
- [ ] Each agent's manifest carries a category; both surfaces filter by it.
- [ ] Filters work offline against the cached index.

## Dependencies
- Blocked by: AH-X10 (R2 index), AH-X12 (website).

## References
Spec §2.3 (surfaces), `agent-hub-ui.mdx`.
<!--/issue-->

<!--issue
action: create
title: AH-X5 — Per-agent eval baselines committed to tests/fixtures/eval_baselines
labels: enhancement,eval,tests,p1
-->
## Context
Each agent's domain prompt is an LLM-affecting surface; regressions must be catchable. CLAUDE.md requires committed baselines per category.

## Goal
A committed eval baseline per agent, with a documented refresh process.

## Scope / Deliverables
- `tests/fixtures/eval_baselines/<model>/scorecard_<agent>.json` per agent.
- CI gate comparing current vs baseline; documented `--save-baseline` refresh.

## Acceptance criteria
- [ ] Every published agent has a committed baseline.
- [ ] CI flags a regression against baseline.

## Dependencies
- Blocked by: AH-X2 (proactive eval harness); each agent issue produces its baseline.

## References
Spec §10; CLAUDE.md eval rule.
<!--/issue-->

<!--issue
action: create
title: AH-X6 — Least-privilege permission audit per agent
labels: enhancement,security,p0
-->
## Context
With 22 agents touching email, files, finances, and the home, manifest permissions must match what tools actually do. Over-broad permissions are a security and trust risk.

## Goal
An audit (and gate) verifying each agent's declared `permissions` match its tools' actual calls, before `security_tier: verified`.

## Scope / Deliverables
- A checker mapping declared permissions ↔ tool capabilities.
- A `verified`-tier gate that blocks publish on mismatch.
- Audit checklist for reviewers.

## Acceptance criteria
- [ ] An agent declaring fewer permissions than its tools use fails the gate.
- [ ] An agent with unused declared permissions is flagged.
- [ ] Runs in CI on agent packages.

## Dependencies
- Blocked by: manifest format (Hub platform). Gates: every agent's publish.

## References
Spec §10; `agent-hub.mdx` quality gates.
<!--/issue-->

<!--issue
action: create
title: AH-X7 — amd-gaia[agents] meta-package installs all verified agents
labels: enhancement,sdk,domain:distribution,p2
-->
## Context
Convenience install for the full AMD agent set, mirroring the Hub's per-agent packages.

## Goal
An `amd-gaia[agents]` extra that installs all `verified` AMD agent packages.

## Scope / Deliverables
- Extras definition aggregating the published agent wheels.
- CI check that the extra resolves and installs.

## Acceptance criteria
- [ ] `pip install amd-gaia[agents]` installs all verified agents, each runnable via `gaia agent run <id>`.

## Dependencies
- Blocked by: per-agent wheel packaging (Hub platform), agents published.

## References
Spec §1; `agent-hub-ui.mdx`.
<!--/issue-->

<!--issue
action: create
title: AH-X8 — 5-interface harness (one agent class → TUI/CLI/pipe/API+OpenAPI/MCP)
labels: enhancement,sdk,agent-hub,p0
-->
## Context
The packaging standard requires every agent to expose all five interfaces from a single package without hand-building five entry points. The framework must provide the harness.

## Goal
A harness so a correct `Agent` (+ `ApiAgent` + `MCPAgent`) yields all five interfaces via `gaia agent run <id> [--prompt|--pipe|--api|--mcp]`, including a generated OpenAPI spec.

## Scope / Deliverables
- Runner modes: TUI (default), `--prompt` (CLI one-shot), `--pipe` (stdin/stdout), `--api` (OpenAI-compatible server + `openapi.json`), `--mcp` (stdio+HTTP MCP server).
- OpenAPI spec generation bundled into the package.

## Acceptance criteria
- [ ] A sample agent answers identically across all five interfaces.
- [ ] `openapi.json` validates and matches the API surface.
- [ ] Integration test exercising all five modes.

## Dependencies
- Blocked by: AH-L7.4 (agent contract). Powers: every agent, AH-X3, AH-X9. **Wave-0 prerequisite.**

## References
Spec §2.3.
<!--/issue-->

<!--issue
action: create
title: AH-X9 — gaia agent pack compile step (wheel/static binary + OpenAPI + manifest)
labels: enhancement,cli,agent-hub,p0
-->
## Context
"Packaged, native, compiled" requires a build step producing a self-contained artifact, not a loose script — Python wheel now, C++ static binary later.

## Goal
`gaia agent pack` that compiles an agent into a distributable artifact bundling the manifest and generated `openapi.json`.

## Scope / Deliverables
- Python: build an isolated wheel from `gaia-agent.yaml` + `pyproject.toml`.
- C++ (later, AH-X11): static binary via vcpkg matrix.
- Bundle `manifest.json` + `openapi.json`; emit a content hash.

## Acceptance criteria
- [ ] `gaia agent pack` on a sample agent produces an installable wheel + bundled manifest/openapi.
- [ ] Output is reproducible (stable hash for unchanged input).

## Dependencies
- Blocked by: manifest format, AH-X8 (OpenAPI). Powers: AH-X10 (publish).

## References
Spec §2.3.
<!--/issue-->

<!--issue
action: create
title: AH-X10 — R2 publish + Worker index consumed by website AND Agent UI (one index.json)
labels: enhancement,agent-hub,domain:distribution,p0
-->
## Context
Both discovery surfaces (website + Agent UI) must read one source of truth so they never diverge. Umbrella over existing R2/frontend issues.

## Goal
`gaia agent publish` uploads to R2; a Cloudflare Worker rebuilds `index.json` from per-agent manifests; website and Agent UI both consume it.

## Scope / Deliverables
- Publish flow: validate → test → sign → upload artifact + manifest + openapi to R2.
- Worker rebuilds `index.json`; both surfaces read it.

## Acceptance criteria
- [ ] Publishing an agent makes it appear on both the website and the Agent UI from the same index.
- [ ] Version immutability + server-side hash verification.

## Dependencies
- Blocked by: AH-X9 (pack). Links existing: #1095 (R2/Worker), #1097 (frontend), #1178 (website).

## References
Spec §2.3.
<!--/issue-->

<!--issue
action: create
title: AH-X11 — C++ supersession track (NativeAgentLauncher + per-agent reimplementation)
labels: enhancement,cpp,agent-hub,p2
-->
## Context
Agents start in Python and are superseded by C++ native binaries for performance/footprint, with **no change to id, manifest, or interfaces** so consumers are unaffected.

## Goal
The infrastructure + pattern to ship a C++ implementation of an agent behind the same manifest/interfaces, run via the subprocess launcher.

## Scope / Deliverables
- `NativeAgentLauncher` (JSON-RPC over stdio) for C++ agents in Electron + web.
- C++ binary packaging in `gaia agent pack` (vcpkg, win-x64/linux-x64/darwin-arm64).
- Reference C++ supersession of one agent proving parity.

## Acceptance criteria
- [ ] A C++ agent answers identically to its Python version across all 5 interfaces; consumers detect no difference.

## Dependencies
- Links existing: #1092 (subprocess launcher), #1094 (C++ packaging/CI). Blocked by: AH-X8/X9.

## References
Spec §2.3 (native + compiled).
<!--/issue-->

<!--issue
action: create
title: AH-X12 — Website Hub page (amd-gaia.ai/hub)
labels: enhancement,gui,agent-hub,p1
-->
## Context
The public discovery front door for the 22 agents, reading the same R2 index as the Agent UI.

## Goal
A public Hub page (browse/search/Try-or-Install) at amd-gaia.ai/hub backed by the R2 index.

## Scope / Deliverables
- Browse + search + category filters (AH-X4); per-agent detail (manifest, screenshots, compatibility).
- "Install" deep-link to the Agent UI; optional "Try in Arena" hook.

## Acceptance criteria
- [ ] All published agents are browseable/searchable on the website from the R2 index.
- [ ] Detail pages render manifest metadata + compatibility.

## Dependencies
- Links existing: #1178. Blocked by: AH-X10 (index).

## References
Spec §2.3; `agent-hub.mdx`.
<!--/issue-->

---

# Move directives — existing duplicates/related issues → milestone #48

> The generator reassigns each to `Agent Hub: 22 Agents [OSS]`, adds the noted label, reopens if closed, and posts the linking comment (the block body).

<!--issue
action: move
number: 676
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L1.1** (per-agent memory namespaces) under epic L1 Memory (#1459). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 669
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L2.1** (web_search tool) under epic L2 Web Search (#1460). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 1144
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L2.1/L2.2** (web search + extraction via Tavily) under epic L2 (#1460). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 927
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L3.1** (connector/OAuth framework) under epic L3 Connectors (#1461). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 1105
addlabels: agent-hub-layer
reopen: true
-->
Reopened and pulled into the **Agent Hub 22-Agent program** → **AH-L3.1** (Microsoft OAuth / MS Graph) under epic L3 (#1461). If MS OAuth is in fact complete, re-close with a note confirming it satisfies AH-L3.1. Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 735
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L3.5** (ConnectorMixin + grant UI) under epic L3 (#1461). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 730
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L4.1** (image/photo indexing via VLM) under epic L4 VLM/Media (#1462). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 664
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → informs **AH-L4.2** (receipt/document extraction) under epic L4 (#1462) and the `finance` agent (Wave 4). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 466
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L5.1** (system scanner) under epic L5 Filesystem (#1463). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 693
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L6.3** (Signal messaging delivery) under epic L6 Notifications (#1464). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 635
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L6.3** (Telegram/Discord/Slack messaging delivery) under epic L6 (#1464). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 634
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L7.1** (heartbeat scheduler + event hooks) under epic L7 Autonomy (#1465). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 557
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L7.2** (autonomous think-act-schedule loop) under epic L7 (#1465). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 643
addlabels: agent-hub-layer
-->
Part of the **Agent Hub 22-Agent program** → satisfies **AH-L7.5** (background service + system tray) under epic L7 (#1465). Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->

<!--issue
action: move
number: 963
addlabels: agent-hub-layer
reopen: true
-->
Reopened and pulled into the **Agent Hub 22-Agent program** → **AH-L3.2** (Outlook email backend) under epic L3 (#1461) and the `email` agent (Wave 2). If the Outlook backend actually landed before this was closed, re-close with a note confirming it satisfies AH-L3.2. Moved into milestone "Agent Hub: 22 Agents [OSS]".
<!--/issue-->
