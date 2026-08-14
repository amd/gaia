# GAIA vs. Hermes Agent, OpenClaw, and the field — architectural gap analysis

Long-horizon autonomy and a memory that compounds are the two things that separate an
agent from a chatbot with tools. This document asks, capability by capability: what does
GAIA's flagship agent actually do today (read from `feat/gaia-flagship-agent-2804`, not
guessed), what do the named competitors do instead, and what's the smallest change that
closes the gap without turning GAIA into a product that only works with a 200K-token
cloud model.

**Ground rule that shapes every recommendation below:** GAIA runs against a local
Lemonade server — a Gemma-4-E4B-class model, 32K context on NPU, 64K on GPU, one model
slot. That is the opposite regime from the 200K–1M-token frontier models most of these
competitors assume. A mechanism that only works with unlimited context is a different
product, and is labeled as such throughout.

**Sources.** Competitor claims are sourced inline with URLs; every GAIA claim is
`file.py:line`, verified by direct read on this branch. Two dedicated research passes
back this document — [`GAIA_COMPETITIVE_GAP_ANALYSIS_HERMES_OPENCLAW.md`](GAIA_COMPETITIVE_GAP_ANALYSIS_HERMES_OPENCLAW.md)
(Hermes Agent, OpenClaw) and a Claude Code / OpenHands / Devin / MemGPT-Letta /
Generative Agents / Voyager brief — plus a full-repo code pass recorded in
[`GAIA_CODEBASE_DEEPDIVE.md`](GAIA_CODEBASE_DEEPDIVE.md). This document is the synthesis;
those three are the primary evidence if you want to check a claim's working.

## Naming, resolved

**"Hermes" means Hermes Agent, not the Hermes model series.** Nous Research ships both:
the Hermes LLM fine-tunes (Hermes 2/3, not an agent) and, separately, **Hermes Agent** —
a self-hosted, MIT-licensed autonomous agent framework launched ~February 2026, marketed
explicitly against OpenClaw and Claude Code as an "always-on" agent. That's the one this
document compares against. Sources:
[official docs](https://hermes-agent.nousresearch.com/docs/),
[GitHub](https://github.com/NousResearch/hermes-agent).

**"OpenCLAW" is OpenClaw** (correct casing) — a single, unambiguous project: a
self-hosted, local-first personal-AI-agent daemon created by Peter Steinberger, shipped
November 2025 as "Clawdbot," renamed twice (→ Moltbot → OpenClaw) after a trademark
dispute with Anthropic. Sources:
[GitHub](https://github.com/openclaw/openclaw), [docs.openclaw.ai](https://docs.openclaw.ai),
[Wikipedia](https://en.wikipedia.org/wiki/OpenClaw).

No conflicting candidate for either name survived search — both identifications are
high-confidence. Full disambiguation trail in the linked research doc.

## Corrections to `GAIA_AGENT_V2_SCOPE.md`

Two claims in the sibling scope doc don't hold up against the current code. Both matter
enough to flag before the capability sections below, since they change what "the gap"
actually is.

**§4.3 "Whisper and Kokoro are already Lemonade-served" — wrong.** `whisper_asr.py:24,76`
imports and loads `openai-whisper` locally; `kokoro_tts.py:22,55` imports and loads the
`kokoro` package locally. Neither makes an HTTP call to Lemonade. `lemonade_client.py`
declares `ModelType.ASR`/`ModelType.TTS` enum values (`:253-254`) that are never
referenced anywhere in the repo — declared, unused. The scope doc's *conclusion* ("voice
isn't reachable from the flagship") is directionally right but the *reason* is wrong, and
it's incomplete: the flagship actually has a working, always-on `text_to_speech` tool
today (`hub/agents/chat/python/gaia_agent_chat/agent.py:1847-1903`, inherited by
`GaiaAgent`) that generates real audio via local Kokoro — it just writes a WAV file
nobody plays, because neither the TUI nor `gaia_agent/server.py` has playback code. See
[Relationship/delight](#the-relationshipdelight-dimension) below.

**§4.5 "Write a script, keep it as a tool: None. Edit its own skills: None." — half
wrong.** GAIA already has a fully automatic procedural-memory pipeline
(`procedural_memory.py`, wired into every turn at `memory.py:2162`) that distills
successful multi-step tool sequences into named, recallable procedures — this is real
skill compounding, already shipped, already running. What's still true from §4.5: nothing
turns a script into a registered, directly-callable tool, and nothing lets the agent
author or edit a `SKILL.md` file. See [Memory that compounds](#memory-that-compounds) and
[Self-improvement](#self-improvement) below for the precise line between what exists and
what doesn't — it's a genuinely subtle distinction the scope doc's testing session
apparently didn't surface.

---

## Long-horizon execution

**GAIA today can't resume a multi-step task across a restart, and the one piece of
infrastructure built for that isn't connected to the agent people actually talk to.**

A single `process_query` call is a closed loop: `execution_state` resets to
`STATE_PLANNING` every time (`agent.py:4016`), bounded by `DEFAULT_MAX_STEPS = 50`
(`agent.py:82`, overridable via `GAIA_AGENT_MAX_STEPS`). The five declared states —
`PLANNING`, `EXECUTING_PLAN`, `DIRECT_EXECUTION` (dead code — defined but never assigned
anywhere in the repo), `ERROR_RECOVERY`, `COMPLETION` (`agent.py:472-476`) — describe one
turn's arc, not a plan that outlives it. When the step cap is hit non-interactively, the
loop simply stops (`agent.py:5946-5948`); nothing is checkpointed for a next run to pick
up.

There *is* a real answer to this already built: `GoalStore` (`goal_store.py`) is a
proper SQLite-backed goal/task hierarchy with explicit state machines — goals move
`pending_approval → queued → in_progress → completed/failed`, tasks move
`queued → in_progress → completed`, with `get_actionable_goals()` and `get_next_task()`
as the exact "what should I work on" primitives a resumable agent loop needs
(`goal_store.py:587-612`). The base `Agent` already has `on_first_run`/`on_heartbeat`
hooks and a `propose()` method that write into it (`agent.py:3840-3874`). It's wired
into the Agent UI backend's event-driven `agent_loop.py`
(`src/gaia/ui/agent_loop.py`) — goal-driven execution is shipped there and is the
default agent mode, per the implementation-status note in
`docs/spec/autonomous-agent-mode.md:7-16`. **But `GoalStore` has zero references
anywhere under `hub/agents/gaia/`** — the flagship agent and the TUI a user actually
drives never create, read, or resume a goal. The infrastructure for "plans that outlive
a turn" exists in this repo; it just doesn't reach the product surface this whole
analysis is about.

**What competitors do instead.** Claude Code tracks long tasks with dependency-linked
`TaskCreate`/`TaskUpdate`/`TaskGet`/`TaskList` tools and delegates to subagents with
their own context ([Todo Lists docs](https://code.claude.com/docs/en/agent-sdk/todo-tracking),
[Create custom subagents](https://code.claude.com/docs/en/sub-agents)). OpenHands
maintains a persistent `PLAN.md`/`plan.json` with success criteria via a Planning Agent
explicitly modeled on Claude Code's approach
([issue #9970](https://github.com/OpenHands/OpenHands/issues/9970)), and its append-only
event stream is *designed* to support "pause/resume, recovery from context overflows"
([OpenHands SDK paper, arXiv:2511.03690](https://arxiv.org/html/2511.03690v1)). Hermes
Agent saves and resumes sessions and can spawn isolated subagents for parallel work
([Turing Post](https://www.turingpost.com/p/hermes)). None of Hermes Agent, OpenClaw,
Claude Code, or OpenHands has a *publicly documented* crash-mid-task recovery guarantee —
that's an open question industry-wide, not just a GAIA gap.

**Concrete failure today:** ask the flagship "refactor the auth module across these 8
files, keep going even if I close the terminal." At step 50, or the moment the TUI
process dies, everything is gone — no goal, no task list, no way to ask "where did you
leave off" next session.

**Proposed mechanism — contained, not a loop redesign.** Wire `GoalStore` into
`GaiaAgent`: detect a multi-step ask, create a `Goal` with `Task`s, drive execution
through the existing `STATE_EXECUTING_PLAN` machinery per task, and on startup check for
`in_progress` goals before waiting for a prompt. The state machine, persistence, and even
a working precedent (the Agent UI backend) already exist — this is integration work, not
new architecture. **Local feasibility:** yes without qualification — `GoalStore` is pure
SQLite with no LLM dependency of its own; only the planning step it feeds already needs
the local model, exactly as today.

---

## Autonomy

**Today the flagship waits for a prompt like every other chatbot — but GAIA has two
separate, real, shipped autonomy mechanisms that just haven't been generalized to the
agent people use.**

`gaia schedule` is cron-only (APScheduler `CronTrigger`, `daemon.py:45-64`) and fires a
bare prompt into a *fresh*, historyless `AgentSDK` session (`runner.py:32-47`,
`no_history=True`) — it explicitly cannot run a skill
(`resolve_input()` raises `NotImplementedError` if `schedule.skill` is set, `runner.py:16-29`,
and `gaia schedule add --skill` is rejected at the CLI, `cli.py:3360-3375`, pointing at
issue #1019). No file-watcher, webhook, or other event source exists anywhere in
`src/gaia/schedule/`. This is the "cheapest possible" autonomy primitive and it's what
ships for the CLI today.

Meanwhile `docs/spec/autonomous-agent-mode.md` defines a real three-mode trust gradient —
`manual` / `goal_driven` / `autonomous` — and its implementation-status note (`:7-16`,
dated 2026-07) says **`manual` and `goal_driven` are shipped, `goal_driven` is the
default**, driven by the event-driven, non-polling `AgentLoop`
(`src/gaia/ui/agent_loop.py`) that respects private sessions unconditionally, gates on
an onboarding-complete marker, and suspends entirely when a remote tunnel is active
(`:205-217`). The fully self-directed `autonomous` mode — where the agent observes its
environment and infers its own goals without a human approving each one first — is
explicitly gated behind issue #2005 and not shipped for general agents.

The one place that observe→decide→act loop *is* real: `EmailTriageAgent`, via a
different, independently-built mechanism —
[`docs/plans/email-full-autonomy.mdx`](docs/plans/email-full-autonomy.mdx) documents
Phases 1–5 as **shipped**: a `TrustLedger`/`TrustPolicy` earn-trust gradient
(`off → suggest → earn_trust → full`), a hard, unconditional confirm-floor for
send/delete/forward/RSVP that the policy layer can never lower, and a closed
correction→memory→behavior learning loop, opt-in via `GAIA_EMAIL_AUTONOMY_ENABLED`. This
is a genuinely good design — better-scoped than most of what's in the plan doc below —
and it's scoped to one agent.

**On `docs/plans/autonomy-engine.mdx` specifically** — read in full, since the task asked
for an explicit position: I largely **agree** with its shape (cheap-first tiered
escalation — deterministic checks, then a 0.6B triage model, then the full model only
when something's actually worth acting on — and its safe-mode confirm-by-default
posture). I **disagree that it should be built as described.** Two reasons. First, its
own reconciliation banner (`:9-15`) already concedes the design is stale: the "separate
background process" it specifies is supposed to be superseded by the Agent UI v2 daemon
owning one scheduler clock, "don't build a second one" — and the plan body below that
banner still describes the old architecture. Second, and more important: GAIA doesn't
need a new autonomy engine designed from scratch. It already has two independently-built,
working patterns — the `goal_driven` mode + `AgentLoop`, and the email agent's earn-trust
engine — that between them cover most of what the plan proposes (goal approval, a trust
gradient, a confirm floor, activity visibility). The highest-leverage move is
generalizing what's shipped, per #2005's own scope, not building `heartbeat.yaml`'s
three-tier model as a parallel system. One genuinely new and worthwhile piece from the
plan that doesn't exist anywhere yet: **event hooks** (filesystem watchers, webhooks) —
neither shipped mechanism has anything like it.

**A partial correction to the codebase deep-dive's own finding:** it reports `gaia
daemon` and `gaia schedule` as "two unrelated code paths" — true for the CLI's
`gaia schedule daemon`, but `src/gaia/daemon/scheduler/` (`clock.py`, `models.py`,
`store.py`, read directly) is a real, already-built reconciliation layer: `DaemonClock`
claims and fires jobs exactly-once from a single SQLite store
(`clock.py:110-163`), and `MigratableJob` (`models.py:57-73`) is explicitly designed to
adopt jobs from "the four clocks that used to die with their owning process — the UI
backend's Scheduler, the `gaia schedule` CLI, and the email sidecar's two in-process
clocks" (`models.py:5-12`). So the reconciliation the plan doc calls for is under active
construction, not merely acknowledged as a gap — I did not verify whether `gaia schedule`
CLI jobs are actually being migrated into it yet, so treat that specific link as
unconfirmed rather than done.

**What competitors do instead.** OpenClaw's heartbeat (30-minute default, confirmed
primary docs) and separate cron/Automations scheduler are both on by default —
"designed to act unprompted by default"
([docs.openclaw.ai/gateway/heartbeat](https://docs.openclaw.ai/gateway/heartbeat),
[docs.openclaw.ai/automation/cron-vs-heartbeat](https://docs.openclaw.ai/automation/cron-vs-heartbeat)).
Hermes Agent runs a cron scheduler plus 60-second gateway ticks on always-on
cloud/VPS infrastructure ([official docs](https://hermes-agent.nousresearch.com/docs/)).
Devin has the strongest "runs unprompted" precedent found anywhere in this research:
**Auto-Triage** stands watch across Slack, Linear, GitHub, Sentry, and Datadog and acts
on incidents without being prompted per-incident
([Introducing Auto-Triage](https://cognition.com/blog/auto-triage)); **Auto-Review**
fires on PR open/push without a prompt
([Devin 101](https://cognition.com/blog/devin-101-automatic-pr-reviews-with-the-devin-api)).
OpenHands is human-triggered by default; a label-configured GitHub Action can spin up a
sandboxed fix, but a broader standing "Automations" capability is still an open RFC
([RFC #13275](https://github.com/OpenHands/OpenHands/issues/13275)).

**Concrete failure today:** "watch my inbox and my repo, handle routine stuff without
asking every time" works for email (behind an env-var opt-in) and for nothing else. The
flagship GaiaAgent has zero standing triggers.

**Proposed mechanism — a reshape, honestly sized.** Generalizing `EmailTriageAgent`'s
trust engine and `autonomous-agent-mode.md`'s observation cycle into the base `Agent`
loop is exactly issue #2005's own scope — I'd do that generalization rather than design
something new, since two working reference implementations already exist. This is large
and safety-sensitive (the confirm-floor has to be done right per surface, not just
copied), not a quick win — see the ranked list in [Recommendation](#recommendation).
**Local feasibility:** yes, and arguably GAIA's design (`autonomy-engine.mdx`'s
deterministic-first / cheap-model-triage / full-model-last escalation, and
`autonomous-agent-mode.md`'s hybrid model routing, G13) is built *for* the local
single-model-slot constraint in a way OpenClaw and Hermes Agent — which assume always-on
cloud or VPS capacity — don't have to solve.

---

## Memory that compounds

**GAIA's single strongest asset in this whole comparison, and its own planning docs
don't know it landed.**

The DETECT→CLUSTER→DISTILL→RECONCILE/STORE→RECALL procedural-memory pipeline is fully
implemented and running automatically on every turn, not merely designed. Storage: a
dedicated `procedures` table (`memory_store.py:283`, columns include `when_to_use`,
`markdown_body`, `tools_required`, `success_count`, `attempt_count`) with its own FAISS
index over the `when_to_use` trigger vector, kept separate from the fact-memory index
specifically so goal→procedure recall never pollutes fact recall
(`procedural_memory.py:50-116`). Synthesis: `_synthesize_skills()`
(`procedural_memory.py:433-530`) clusters successful multi-step tool sequences by goal
similarity (cosine ≥ 0.82), distills a cluster into a named procedure with one LLM call,
and reconciles it into the store — gated at `MIN_STEPS=3`, `MIN_OCCURRENCES=3`,
`MIN_SUCCESS_RATE=0.80` (`skill_synthesis.py:50-56`). Recall:
`recall_skill(goal)` embeds the current goal, searches the procedures index, and — this
is the confirmed live wiring — `MemoryMixin.process_query` calls
`self._refresh_recalled_skills(user_input)` on every turn (`memory.py:2162`), injecting
matched procedures into the system prompt and signaling the tool loader to load exactly
the tools that procedure needs. This is genuine procedural memory: not "what did you
tell me," but "how did I solve this before" — automatically mined, automatically reused,
and it runs on the local embedder already, no frontier model required.

Its own planning doc, `docs/plans/skill-synthesis.mdx`, still opens with "Status: All
PROPOSED... No pipeline, `procedures` table, or `recall_skill` exists in code" — every
symbol it lists as "NOT FOUND (verified absent on main)" is now present and load-bearing.
The doc predates the landing and was never updated; `docs/plans/adaptive-skills.mdx`
(a newer plan) correctly cites the shipped mechanism by file:line and is the doc to trust
going forward.

That newer doc's own code review also surfaces two real, present-tense defects in the
shipped mechanism — not proposed future work, bugs in what's running now:

- **The success-rate gate only ever goes up.** `reconcile_and_store` always inserts new
  rows with `skill_id=None`, so `put_skill`'s `UPDATE...success_count` branch
  (`memory_store.py:2613`) is unreachable from the synthesis path — no
  `record_procedure_outcome` call exists anywhere. A procedure that fails 20 times in
  production keeps its birth-time confidence forever. "Compounds" today means
  "accumulates," not "corrects."
- **The recalled-procedure prompt block isn't actually KV-cache-friendly**, despite a
  comment near the call site claiming it is — it composes *first* in
  `_compose_system_prompt`, the position most disruptive to prefix/cache reuse, while the
  genuinely volatile tools block was deliberately moved last. Filed as issue #2686.

Separately: the "no context compaction — memory + RAG handles long conversations"
design decision (`CLAUDE.md`, `docs/roadmap.mdx:231`, `docs/plans/agent-ui.mdx:311`) has
**no documented rationale anywhere in the repo beyond that one line, repeated verbatim
three times.** I engaged with this directly rather than taking it at face value: my own
read is that the decision is *defensible* — GAIA's approach (keep facts/preferences in a
retrievable store, keep procedures as a separately-indexed, separately-verified corpus,
never silently summarize the live conversation) is arguably more principled than flat
auto-compaction, because it distinguishes "what to keep verbatim" from "what to
generalize into a reusable skill" instead of treating both as one lossy compression
problem. But an architectural decision with zero written tradeoff analysis is itself a
gap — not a functional one, a documentation one — and it should get one before the next
person reopens the question from scratch.

**What competitors do instead.** Voyager is the one unambiguous procedural-memory case
in the entire research pass: literal executable JavaScript functions, stored by
description embedding, only committed to the library after passing a three-signal
verification loop including a second-model critic
([arXiv:2305.16291](https://arxiv.org/abs/2305.16291), §2.3). Hermes Agent explicitly
self-writes skills after a sufficiently complex task (commonly cited threshold: 5+ tool
calls) via a `skill_manage` tool, agentskills.io-portable, shared through a Skills Hub
([official docs](https://hermes-agent.nousresearch.com/docs/)) — architecturally the
closest product-shaped analog to what GAIA's pipeline already does. OpenClaw's
self-authoring is contested across sources — one (Milvus) claims the agent can draft a
missing skill, another (Turing Post) frames OpenClaw as tighter human-authored control —
treat as unconfirmed. Claude Code's Skills are human- or generator-triggered, not
continuously self-mined. **MemGPT/Letta and Generative Agents have no procedural memory
at all** — MemGPT's core/recall/archival tiers page text (facts, conversation), never
code or a callable procedure; Generative Agents' memory stream + reflection produces
richer semantic knowledge but nothing invokable as "how." GAIA's shipped mechanism
already sits ahead of five of the six systems surveyed on this specific axis — Voyager
is the only one clearly further along, and only because it gates entry with a
verification step GAIA's pipeline currently skips.

**Concrete failure today:** the flagship does a 5-step "summarize these PDFs" task three
times successfully — the pipeline *does* fire and *does* recall a procedure on the
fourth attempt (confirmed live). But if that procedure later starts failing in
production, nothing demotes it — `success_count` never updates outside its initial
insert.

**Proposed mechanism — contained fixes on infrastructure that already works.** (1) Wire a
real outcome signal into the existing `put_skill` UPDATE path — increment/decrement
`success_count`/`attempt_count` from the actual result of executing a recalled
procedure's steps, not "no exception raised." (2) Reorder the recalled-procedure prompt
fragment to the end of `_compose_system_prompt`, matching the tools block's already-fixed
position (#2686). Both are scoped, low-risk changes to code that's already live — not new
subsystems. **Local feasibility:** already proven — this runs today on the local
embedder and the local chat model's own `send_messages` call for distillation, unlike
Voyager's GPT-4-critic-gated design.

---

## Self-improvement

**GAIA can write and run a script. Nothing lets it keep that script as a tool — and this
is a genuinely different gap from the memory one above, easy to conflate because both
are tracked under the same issue number.**

Two "skill" concepts share the name #887 in this codebase and need to be kept separate.
One is the procedures table just described (§Memory that compounds) — synthesized rows
recalled as *natural-language instructions* re-interpreted by the LLM each time, not
callable code. The other is the `SKILL.md` skills-directory mechanism
(`src/gaia/skills/`) — entirely human-authored. The flagship's complete skill-tool
surface (`SkillLibraryToolsMixin`, `skill_tools.py:64-72`) is `list_skills`,
`search_skill_hub`, `install_skill`, `remove_skill`, `load_skill`, `unload_skill`,
`skill_status` — discovery, hub-installation, and session activation only. No authoring
verb exists; a grep for `create_skill|write_skill|register_skill|save_skill` across
`src/` and `hub/` returns nothing. Skill *creation* exists only as `gaia skill create`
(`cli.py:114-133`) — a human-run terminal command. The skill-format doc's own roadmap
names issues #887 ("skill auto-synthesis") and #553 ("self-improving agent") as the
not-yet-built consumers that would emit real `SKILL.md` files from agent experience —
the codebase's own tracker confirms this doesn't exist, even though the *other* #887
mechanism (procedural memory) does.

So: the agent can solve a novel problem with a script today, and — separately — the
procedural-memory pipeline might later distill a text recipe for that class of problem.
What's missing is the middle ground: keeping the actual verified, working script as a
directly-callable tool for next time, instead of re-deriving it from a natural-language
recipe on every recurrence.

**What competitors do instead.** Voyager is again the reference: skills are literal code,
composed from simpler skills, gated by execution + self-verification before entering the
library ([arXiv:2305.16291](https://arxiv.org/abs/2305.16291), §2.2–2.3). Hermes Agent's
`skill_manage` tool is the nearest real product precedent to "write a script, keep it as
a tool" — self-authored procedure documents, not raw code, but genuinely agent-initiated
without a human writing the file. Claude Code's bundled `/run-skill-generator` and
`/verify` skills can write their own recipe as a new project skill after a successful
run — real, but triggered by a specific bundled skill rather than continuous background
learning.

**Concrete failure today:** the agent writes a 40-line script to parse a proprietary log
format. Next week, a similar question arrives. The procedural-memory recall (if it fires
at all — the task has to clear `MIN_OCCURRENCES=3` first) surfaces a natural-language
recipe the LLM re-interprets and re-writes from scratch, not the verified script itself.

**Proposed mechanism — medium, and it reshapes part of the loop.** Give the procedures
table (or a sibling table) an executable-artifact column alongside `markdown_body`, and
expose a thin callable wrapper the tool registry can register on recall, gated by the
same signature/tier-ceiling machinery `src/gaia/skills/tiers.py` already enforces for
human-authored skills — reuse the trust ladder rather than inventing a second one for
agent-authored code. Add a verification step before a script is trusted for reuse
(Voyager's pattern: rerun it, self-check the result). This is bigger than the memory
fixes above because it introduces a new kind of trusted artifact into the runtime, not
just a bug fix on an existing one. **Local feasibility:** yes for storage and recall; the
verification step costs one extra local LLM call per candidate, which is proportionate
given the DISTILL step already spends one call per cluster.

---

## Self-critique

**Nobody in this entire survey has a real "review my own answer" step for ordinary
turns — the one place it exists anywhere is scoped narrowly, and that scoping is the
right lesson for GAIA, not a gap to copy blindly.**

Confirmed absent in GAIA: none of the five agent-loop states is a reflection/review
state; a grep for `reflect|critique|self-review` in `agent.py` returns only inline
comments about PR-review rounds, unrelated to any runtime path. `STATE_COMPLETION`'s
prompt asks the model to check whether more plan steps remain — never whether the answer
is *correct* — which is the closest analog and still isn't a critique pass.

The one clean precedent found anywhere in this research is Voyager's self-verification:
a second model instance acts as critic before new code is committed to the skill library,
repeating until it passes
([arXiv:2305.16291](https://arxiv.org/abs/2305.16291), §2.3). Nothing in Hermes Agent,
OpenClaw, Claude Code, OpenHands, or Devin's public documentation describes an equivalent
step for ordinary answers — this is under-built industry-wide, not a GAIA-specific gap.

**When it's worth the extra steps, and when it's waste — this is a judgment call, and I
land on "selectively, not universally."** Blanket self-critique on every chat turn roughly
doubles LLM calls; on a single local model slot with no spare capacity, that's real
latency for a low-stakes answer with no guarantee of better accuracy. It's worth it in
exactly two places GAIA already has infrastructure for: (1) before a procedure is
promoted into the recall-eligible `procedures` table — this is Voyager's exact pattern,
and it bolts directly onto the existing `_synthesize_skills` DISTILL step, which already
spends one LLM call per cluster; adding a verification call there is proportionate. (2)
Before any destructive/irreversible action — which the email agent's confirm-floor
already effectively achieves through a *different*, arguably better mechanism (mandatory
human confirmation, never bypassable by policy) rather than self-critique. I would **not**
add a blanket review state to the core five-state loop — the cost is real, the evidence
that it improves ordinary-turn accuracy is absent everywhere I looked, and GAIA's
resource ceiling makes that trade worse than it would be against a frontier model.

**Proposed mechanism — contained.** Add a verification sub-step to the existing
`_synthesize_skills` DISTILL stage, gated behind the already-present
`MAX_CLUSTERS_PER_PASS=10` cap so cost stays bounded on infrastructure that's already
paid for. **Local feasibility:** yes — one more local LLM call per candidate cluster.

---

## The relationship/delight dimension

**GAIA already has the raw material for "feels remembered" — a trust ledger that
narrates its own progress, real local text-to-speech, fact/preference memory — and none
of it has been assembled into something the user actually experiences that way.**

The single best precedent in the codebase for "grows with you and shows it" isn't in the
flagship at all: `EmailTriageAgent`'s earn-trust ledger literally narrates its own
graduation to the user — "Day 1: 'Archive this newsletter?' → asks. Day 14: archives
newsletters silently (proven 14/14)" — visible via `gaia email autonomy trust`
(`docs/plans/email-full-autonomy.mdx:47-52`, `:124-126`). That's a working example of
*visible, evidenced* trust-building, not a vague "personalization" claim. It's scoped to
one agent.

Voice is the clearest case of "half the pipe already works, nobody connected the other
half." `ChatAgent` registers a real, always-on `text_to_speech` tool
(`gaia_agent_chat/agent.py:1847-1903`) that generates genuine local audio via Kokoro —
`GaiaAgent` inherits it unmodified. But the TUI (191 Go files, zero audio references) and
`gaia_agent/server.py` (the HTTP/SSE server the TUI talks to) have no playback code on
either side — so today the tool writes a WAV file to `~/.gaia/tts/` that nothing ever
plays. The full ASR+TTS voice session (`gaia talk`) exists and works, but only as a
standalone CLI command nobody wires into the product surface people actually use.

Nothing in the flagship surfaces its own memory proactively. The recalled-procedures
mechanism (§Memory that compounds) is real and running, but it's silent — injected into
the system prompt, never narrated as "I've done this for you before." A user who
benefits from procedural recall has no way to know it happened.

**What competitors do instead.** Devin Knowledge is the closest documented "shows its
learning" UX found anywhere: it's self-curating — Devin proposes new organization-wide
knowledge items from chat feedback, and a human accepts, edits, or dismisses each one,
rather than a developer hand-writing every entry
([Devin Knowledge docs](https://docs.devin.ai/product-guides/knowledge)). Devin
Auto-Triage is the only genuinely unprompted, standing-presence behavior found across
every system surveyed. Claude Code's auto-memory gives quiet cross-session continuity
about a codebase, but it's reactive — surfaced when a session starts, not proactive
between sessions
([How Claude remembers your project](https://code.claude.com/docs/en/memory)). None of
the six deep-researched systems (Claude Code, OpenHands, Devin, MemGPT/Letta, Generative
Agents, Voyager) is built as a genuine long-term companion in the "grows with you,
remembers you unprompted" sense — that survey explicitly flagged this as out of its scope
rather than asserting a false negative.

**What's cheap, what's hard — concretely, not adjectives:**

- **Cheap:** narrate recalled-procedure hits ("I've done this before — reusing last
  time's approach") instead of silently injecting them. The render function
  (`_build_recalled_skills_prompt`) already exists; this is surfacing a line the system
  already computes, not new infrastructure.
- **Cheap:** wire the existing `text_to_speech` tool's WAV output into TUI playback. The
  generation half of voice already runs locally end-to-end; this is a missing player, not
  a missing pipeline.
- **Cheap:** a short "since last time" recap at session start from existing
  fact/preference memory, on the same narrated-progress pattern the email agent already
  proves works for trust — same idea, different surface.
- **Hard:** full duplex voice (ASR *and* TTS) wired into the TUI as a real interaction
  mode, not a side channel.
- **Hard:** anything resembling Devin's self-curating Knowledge-proposal loop — GAIA has
  no "propose this as a durable preference, user confirms" mechanism for anything except
  email corrections today; building the general version is real design work, not a
  wiring job.
- **Hard, and gated on the Autonomy section above:** a genuinely proactive flagship that
  speaks up unprompted, since that needs the `goal_driven`/observation-cycle
  generalization to exist first.

**Local feasibility:** every "cheap" item above is pure local plumbing — Kokoro already
generates audio locally, the recall/narration is prompt text. Nothing here needs a bigger
model than what's already resident.

---

## Recommendation

Ranked highest-leverage-first. Size is engineering effort, not calendar time; risk is
about breaking something that works today, not about difficulty.

| # | Recommendation | Size | Risk | Status |
|---|---|---|---|---|
| 1 | Fix `procedures.success_count` to actually update from real outcomes, not stay frozen at insert | S | Low | v2 candidate |
| 2 | Reorder the recalled-procedure prompt block to preserve the KV-cache prefix (#2686) | S | Low | v2 candidate |
| 3 | Narrate recalled-procedure hits to the user ("I've done this before…") | S | Low | v2 candidate |
| 4 | Wire the existing `text_to_speech` tool's WAV output into TUI playback | S/M | Low | v2 candidate |
| 5 | Wire `GoalStore` into `GaiaAgent` for cross-turn/cross-session task persistence | M | Medium | v2 candidate |
| 6 | Add a Voyager-style self-verification gate to `_synthesize_skills`' DISTILL step | S/M | Low–Medium | v2 candidate |
| 7 | Reconcile `gaia schedule` CLI jobs into `gaia.daemon.scheduler.DaemonClock` | M | Medium | v2 candidate — do before building new autonomy on top of the wrong clock |
| 8 | Write the missing rationale/tradeoff doc for "no context compaction" | XS | None | Do it, but it's paperwork, not a product gap |
| 9 | Generalize the email agent's earn-trust engine + observation cycle (#2005) into the base `Agent` for the flagship | L | High (safety-critical, per-surface confirm-floor design) | Further out |
| 10 | A registered-tool sense of procedural memory — persist a verified script as a callable tool, not just a recalled recipe | L | High (new trusted-artifact surface; reuse `tiers.py`, don't invent a second sandbox) | Further out |
| 11 | Build `autonomy-engine.mdx`'s event hooks (filesystem watchers, webhooks) — the one genuinely new capability from that plan with no existing analog | M | Medium | Further out, after #9 |

**Explicitly not worth it:** a blanket self-critique/reflection state added to the core
five-state loop for every ordinary turn. The cost — roughly double the LLM calls on a
single local model slot — isn't justified by evidence that it improves accuracy on
low-stakes chat answers anywhere in this research; item 6 above captures the actual value
(gating something durable before it's trusted) at a fraction of the cost, the same way
Voyager scopes it.

**The throughline, if there's one thing to take from this document:** GAIA's biggest gap
isn't invention — it's integration. The procedural-memory pipeline, the goal-store state
machine, the earn-trust autonomy engine, and working local TTS all already exist and
mostly already work. None of them talk to the flagship agent a user actually opens. The
highest-leverage year of work here is connecting what's already been built, not
designing new subsystems to match what Hermes Agent or OpenClaw ship.
