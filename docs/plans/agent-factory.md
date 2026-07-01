# The Agent Factory — the developer flow, automated (against the *live* SDK)

> **Sibling to the runtime architecture.** [`agent-ui-agent-capabilities-plan.md`](agent-ui-agent-capabilities-plan.md)
> §0 designs how agents **run** (out-of-process sidecars + a custodian daemon). This
> doc designs how agents are **built and maintained** — the factory. The two meet at
> the artifacts the runtime consumes: the **manifest** (§0.28), the **Hub** (§0.5), and
> **signing** (§0.24). The factory *produces* those; the runtime *enforces* them.

## 0. Thesis — the factory is the developer flow, automated

An agent is **not a static artifact frozen against an SDK snapshot.** The GAIA SDK
(`src/gaia/agents/base/*`, tool mixins, LLM clients, connectors) changes almost every
week; an agent built against a stale snapshot rots. So the factory is **not a packaging
pipeline** — it is **the software-development lifecycle a GAIA developer performs today,
automated**: clone the live SDK, scope requirements, open issues + milestones, write and
iterate a spec, generate synthetic datasets, run evals and optimize, **open PRs into the
codebase**, and finally build + ship the agent product — and then **maintain** it as the
SDK moves.

The engine is **agentic coding**: Claude Code (with advanced **skills** + **memory**)
and/or a custom orchestrator on **Anthropic's Agent SDK**, driving the **GAIA coder**
(the `CodeAgent` on the `origin/coder` branch) for code generation, review, and repair.

> **This very session is the prototype, run by hand.** Cloning the repo into a worktree,
> scoping, writing specs, iterating via adversarial review agents, using memory across
> turns, running against live code, opening a PR — *that is the factory loop.* The
> factory **productizes this workflow** so it runs unattended, per agent, continuously.

## 1. The critical principle — develop against the LIVE SDK, continuously

**Static snapshot = rot.** If the factory freezes an agent against `sdk@v0.23` and the
base `Agent` class, a tool mixin signature, or the Lemonade client changes at `v0.24`,
the frozen agent silently drifts from the platform. So:

- **Every build pins an SDK commit** and is *reproducible against it*, but the agent is
  a **living product**, not a one-shot artifact.
- **The factory is continuous (dev *and* maintenance).** When the SDK evolves, the
  factory **re-runs** the relevant stages — re-scope against the new API surface,
  re-implement/adapt, **re-eval against the same synthetic datasets**, re-PR, re-ship. An
  SDK change that breaks an agent's eval is a factory trigger, not a human fire drill.
- **The agent's provenance records the SDK commit** it was built + evaluated against, so
  drift is detectable (the runtime's contract-version check §0.15 is the coarse guard;
  the SDK-commit pin is the fine one).

This is the piece a scaffold-and-freeze pipeline misses: the hard, valuable work is
**keeping N agents correct against a moving SDK** — which is exactly what a human dev
team spends most of its time on, and exactly what the factory automates.

## 2. The engine — agentic coding in an isolated SDK clone

```
   ┌──────────────────── FACTORY ORCHESTRATOR (per agent, per SDK-delta) ────────────────────┐
   │  Claude Code (skills + memory)  ─and/or─  custom Agent-SDK orchestrator                  │
   │      │ drives                                                                            │
   │      ├─► GAIA coder (CodeAgent, origin/coder: orchestration, validators, schema-infer)  │
   │      ├─► skills: brainstorming · writing-plans · TDD · systematic-debugging · review    │
   │      ├─► memory: prior specs, eval baselines, past failures, SDK-change history          │
   │      └─► tools: git worktree · gh (issues/PRs) · gaia eval · packaging/freeze            │
   │  runs inside an ISOLATED WORKTREE clone of the live GAIA repo (HEAD, not a snapshot)     │
   └──────────────────────────────────────────────────────────────────────────────────────---┘
```

- **Isolated worktree per run** (the pattern this session uses) — so parallel agent builds
  and SDK-delta rebuilds don't collide.
- **Memory is load-bearing** — the orchestrator recalls the agent's prior spec, its eval
  baseline, its recurring failure modes, and *what changed in the SDK since last build*,
  so each maintenance pass is informed, not from-scratch.
- **The GAIA coder does the code**; Claude Code / the Agent SDK does the *orchestration,
  judgment, spec, review, and eval-loop* around it. (Integrating the two — coder for
  generation, agentic loop for planning/verification — is the core net-new engineering.)

## 3. The automated SDLC (the developer flow, stage by stage)

Each stage is a real developer activity, automated; 🚦 = a gate that can fail the run.
Cited components already exist.

| # | Stage (developer activity) | Automated by | Component (status) | Gate |
|---|---|---|---|---|
| 1 | **Clone + scope** — pull live SDK; scope requirements against the *current* API surface | orchestrator + code-index over live `src/gaia` | git worktree · `code_index` (*exists*) | — |
| 2 | **Track** — open GitHub **issues + milestones**, decompose the work | orchestrator + `gh` | `gh` CLI · `claude.yml` bot (*exists*) | — |
| 3 | **Spec** — author the design/spec doc | `brainstorming` → `writing-plans` skills | this session's method (*exists as skills*) | — |
| 4 | **Iterate spec** 🚦 | adversarial review loop until convergence | review agents + memory (*exists*) | spec-review converges |
| 5 | **Synthetic data** — generate eval corpus + ground truth for the domain | dataset generators | `eval/benchmark.py` (synthetic corpus), `pdf_document_generator`, `audit` (*exists*) | — |
| 6 | **Implement** — write the agent code **against the live SDK** (reuse base classes/mixins as they are *now*) | GAIA coder + TDD skill | `origin/coder` `CodeAgent` + `agents/base/*` (*exists*) | compiles/lints |
| 7 | **Eval + optimize** 🚦 | run evals → analyze failures → repair → re-eval, until scorecard ≥ baseline | eval-driven loop | `gaia eval agent [--fix]` · `scorecard.py` · `analyze_failures.py` · baselines (*exists*) | **quality bar** |
| 8 | **PR** 🚦 | open PR(s) into the codebase (agent code — *and SDK improvements the agent needs*); pass real review | orchestrator + `gh` + `finalize` skill | `claude.yml` review bot · `finalize` (*exists*) | review + CI green |
| 9 | **Build + ship** | freeze binary + npm, version-stamp, smoke-test, **sign** + provenance, publish to Hub | packaging line | `packaging/{freeze,gen_*,stamp_version,smoke_test,publish_to_r2}.py` · `release_agent_*.yml` (*exists*) | smoke + signature |
| 10 | **Maintain (continuous)** 🚦 | on an SDK delta that breaks the agent's eval, re-run 1–9 for the delta | orchestrator, triggered by SDK CI | net-new trigger | eval stays ≥ baseline |

Stages 2, 5, 7, 8, 9 already run in CI (`claude.yml`, `build_agents.yml`,
`publish_agents.yml`, `release_agent_email.yml`) — **the factory generalizes them from
one-off, human-kicked jobs into a continuous, orchestrated, eval-gated line.**

## 4. Key architectural properties

- **Eval-gated (stage 7) — the trust bar.** No PR merges / no ship without the behavioral
  **scorecard clearing a committed baseline**. This is the property skill-learning systems
  lack — a quality bar with provenance. `gaia eval agent --fix` already closes a
  scope-limited version of this loop; the factory makes it the gate.
- **PRs into the real codebase — the factory writes software.** The agent's code (and,
  when it needs a new base-class/mixin, **SDK changes**) land as PRs through the *same*
  review + CI a human uses (`claude.yml`). So the factory can *improve the SDK*, not just
  consume it — closing the loop the "static snapshot" model breaks.
- **Reproducible + pinned to an SDK commit.** A build is a pure function of
  `(recipe, SDK commit, model versions)`; provenance records all three inside the signed
  envelope (§0.24). Drift is *detectable*, and a rebuild is deterministic.
- **Continuous maintenance, not one-shot (stage 10).** The dominant cost — keeping agents
  correct against a moving SDK — is automated: an SDK change is a factory trigger; the
  synthetic datasets + baselines are the regression net.
- **Memory-driven.** The orchestrator carries each agent's spec, baseline, failure modes,
  and SDK-change history across runs, so maintenance passes are incremental and informed.
- **The manifest (§0.28) is the factory→runtime hand-off** — emitted at ship, enforced at
  install; provenance (spec hash · eval scorecard · SDK commit · signature) rides inside
  the signed lock.

## 5. The factory ↔ runtime seam

```
  ┌───────────── AGENT FACTORY (automated SDLC on the live SDK) ─────────────┐   ┌──── RUNTIME ────┐
  │ clone→scope→issues→spec→iterate→synth-data→CODE→EVAL🚦→PR🚦→build→SIGN🚦 │   │ daemon installs │
  │ └──────────────────────── continuous maintenance loop ◄──── SDK delta ── │   │ + verifies+runs │
  └───────────────────────────────┬─────────────────────────────────────────┘   └────────▲────────┘
                     emits: signed binary + manifest.json (§0.28) + provenance             │ enforces grants,
                            (spec · scorecard · SDK commit)                                 │ version, signature
                                   ▼                                                        │
                          ┌──────── Agent Hub (§0.5) — the conveyor ─────────┐──────────────┘
                          └──────────────────────────────────────────────---─┘
```

## 6. Component inventory — exists vs net-new

**Exists (orchestrate, don't rebuild):** the **GAIA coder** (`origin/coder` `CodeAgent`)
· **Claude Code in CI** (`claude.yml`, `claude-run.yml`) + skills + memory · the **eval
framework** (`eval/{runner,benchmark,scorecard,analyze_failures,audit}.py`, baselines,
`--fix`, synthetic corpus) · **`gh`** issues/PRs + the review bot · **`code_index`** over
the live SDK · the **packaging line** (`packaging/*`) + `release_agent_*.yml` · **git
worktrees**.

**Net-new (the stitch — this is the real work):**
1. **The factory orchestrator** — the controller that runs the SDLC per agent: integrate
   the GAIA coder + an Agent-SDK/Claude-Code loop + the skills + memory into one driver
   (`gaia factory <recipe>`), operating in an isolated live-SDK worktree.
2. **The `recipe` + agent-spec input** — declarative intent (purpose, capabilities, eval
   config, targets) the orchestrator plans from.
3. **The eval-gate promotion** — make scorecard ≥ baseline a *hard* merge/ship gate.
4. **The SDK-delta trigger (stage 10)** — CI hook that re-runs the factory for affected
   agents when the SDK changes; the synthetic datasets are the regression net.
5. **Manifest emit + lock signing + provenance** (spec · scorecard · SDK commit).

## 7. Phased build (strangler-fig; email as the reference agent)

0. **One agent, human-triggered, end-to-end.** `gaia factory` runs scope→code→eval→build
   for the email agent against a live worktree, human-approved at the PR + ship gates.
1. **Eval-gate + provenance.** Promote scorecard-≥-baseline to a hard gate; emit the
   manifest + provenance (SDK commit pinned).
2. **PR automation.** The factory opens agent-code PRs through `claude.yml` review + CI;
   human approves merge.
3. **SDK-delta maintenance (the keystone).** Wire stage 10 — an SDK change re-runs the
   factory for affected agents; ship only if eval holds. *This is the differentiator; it's
   also the hardest and should follow, not lead.*
4. **Assisted → automated authoring.** Mature the spec + code stages so a new agent goes
   from intent → shipped with human approval only at the gates.

## 8. Distinctiveness (brief, honest)

This is **not** skill-learning (Hermes/OpenClaw/Voyager) and **not** software CI/CD — it's
an **automated AI software-engineering pipeline that builds *and maintains* agent products
against a living SDK**, using the same flow a human team uses (issues · specs · reviews ·
evals · PRs). The moat is the *maintenance-against-a-moving-SDK* loop + the eval-gate +
real PRs into the codebase — nobody in the comparison runs an automated SDLC that opens
PRs against a live platform and keeps a fleet of agents correct as it evolves. **Honest
caveat:** the components exist but the orchestrator + the SDK-delta loop are substantial
net-new work; this is a *potential* moat that must be built, and stage 10 is the hard part.

## 9. Open decisions (need sign-off)

1. **Orchestrator substrate** — Claude Code (skills + memory, already in CI) vs. a custom
   Anthropic Agent-SDK build vs. both (Claude Code for judgment/loop, GAIA coder for
   generation). *Rec:* start on Claude Code + GAIA coder (both exist), evaluate a custom
   Agent-SDK build if the loop needs tighter control.
2. **Autonomy at the PR + ship gates** — fully auto-merge/ship vs. human-approve. *Rec:*
   human approves merge + ship in v1; the factory auto-iterates only *up to* the gate
   (ties to runtime §0.34 autonomy).
3. **SDK-improvement scope** — may the factory open PRs that change the *SDK itself*, or
   only agent code? *Rec:* agent code auto; SDK changes proposed-for-human-review.
4. **Trigger policy for stage 10** — re-run on every SDK commit vs. only on eval
   regression vs. scheduled. *Rec:* run the eval on SDK-affecting deltas; rebuild only on
   regression.
