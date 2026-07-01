# The Agent Factory — the developer lifecycle, automated (against the *live* SDK)

> **Sibling to the runtime architecture.** [`agent-ui-agent-capabilities-plan.md`](agent-ui-agent-capabilities-plan.md)
> §0 designs how agents **run** (out-of-process sidecars + a custodian daemon). This
> doc designs how agents are **built and maintained** — the factory. The two meet at
> the artifacts the runtime consumes: the **manifest** (§0.28), the **Hub** (§0.5), and
> **signing** (§0.24). The factory *produces* those; the runtime *enforces* them.
>
> **Dependency:** the `§0.x` cross-references live in the **Agent UI v2 runtime PR
> (#1913)** — this doc is the sibling half and assumes that PR's
> `agent-ui-agent-capabilities-plan.md §0` as context.

## 0. Thesis — the factory is the SDLC, automated, against a living SDK

An agent is **not a static artifact frozen against an SDK snapshot.** The GAIA SDK
(`src/gaia/agents/base/*`, tool mixins, LLM clients, connectors) changes almost every
week; an agent built against a stale snapshot rots. So the factory is **not a packaging
pipeline** — it is the full **software development lifecycle** (SDLC: scope → design →
implement → test → ship → **maintain**) a GAIA developer performs today, **automated,
against the live SDK.**

It splits into **two halves that already differ sharply in maturity:**

- **Dev half (front) — mostly *net-new* automation.** The judgment-heavy work humans do
  by hand: scope, open issues/milestones, write + iterate a spec, generate synthetic
  datasets, implement against the live SDK, eval-and-optimize, open PRs. The factory
  automates this with **agentic coding**.
- **Ship half (back) — *already exists and is rigorous*.** `release_agent_email.yml` +
  `hub/agents/python/email/packaging/*` already do multi-platform freeze, cross-OS-version
  verification, a **data-driven** scorecard gate, doc + scorecard generation, whole-package
  assembly, Hub + npm publishing with provenance, real-hash lock regeneration, and
  **post-publish edge verification**. **The factory ORCHESTRATES this half — it must not
  reinvent it, and must not lose any of its rigor.**
- **Maintain loop — the keystone that ties both halves.** An SDK delta that breaks an
  agent's eval re-runs dev + ship for the affected agents.

> **This very session is the dev-half prototype, run by hand.** Cloning the repo into a
> worktree, scoping, writing specs, iterating via adversarial review agents, using memory
> across turns, running against live code, opening a PR — *that is the dev half.* The
> factory productizes it and hands off to the ship half that already exists.

## 1. The live-SDK principle (the keystone)

**Static snapshot = rot.** If the factory freezes an agent against `sdk@v0.23` and the
base `Agent` class, a mixin signature, or the Lemonade client changes at `v0.24`, the
agent silently drifts from the platform. Therefore:

- **Every build is SDK-commit-pinned + agent-source-hashed — traceable, not reproducible.**
  The deterministic tail (freeze/sign/publish) is reproducible, but the *generative head*
  (LLM codegen, LLM-judged eval) is **not** — re-running yields a *different* agent. So
  pinning the SDK commit + hashing the generated source buys **provenance/integrity, not
  regeneration** (see §11.5). The agent is a **living product**, not a one-shot artifact.
- **The factory is continuous.** An SDK change that breaks an agent's eval is a factory
  **trigger** — re-scope / re-implement / **re-eval against the held-out oracle** (§5.5) /
  re-PR / re-ship — not a human fire drill. Keeping N agents correct against a moving SDK
  is the dominant, valuable work; it is exactly what the factory automates.

## 2. The engine — agentic coding in an isolated SDK clone

```
   ┌──────────────── FACTORY ORCHESTRATOR (per agent, per SDK-delta) ────────────────┐
   │  Claude Code (skills + memory)  ─and/or─  custom Agent-SDK orchestrator          │
   │      ├─► GAIA coder (CodeAgent, origin/coder: orchestration, validators, infer)  │
   │      ├─► skills: brainstorming · writing-plans · TDD · debugging · review        │
   │      ├─► memory: prior spec, eval baselines, past failures, SDK-change history    │
   │      └─► tools: git worktree · gh (issues/PRs) · gaia eval · packaging line       │
   │  runs inside an ISOLATED WORKTREE clone of the live GAIA repo (HEAD, not a snap)  │
   └──────────────────────────────────────────────────────────────────────────────---─┘
```

The GAIA coder does the *code*; Claude Code / the Agent SDK does the *orchestration,
judgment, spec, review, and eval loop* around it — integrating the two is the core
net-new engineering. Memory is load-bearing: each maintenance pass recalls the agent's
spec, baseline, failure modes, and *what changed in the SDK since last build*.

## 2.5 Human-in-the-loop — per-stage approve/deny (mirrors the agent model)

The factory is a **supervised** pipeline, not a fully-autonomous one. Every risky stage
carries an **approve/deny gate** — the *same* confirmation model the agents themselves use
(runtime §0.4 confirmation gate, §0.34 autonomy levels). A human (or a policy) accepts or
rejects at each checkpoint before the pipeline proceeds:

| Checkpoint | What the human approves / denies |
|---|---|
| **Spec** (stages 3–4) | the scoped design, before any code is written |
| **PR open** (stage 8) | the agent-code — *or SDK* — changes, before they land |
| **Merge** (stage 8) | landing the PR into the codebase |
| **SDK release** (stage 16) | **cutting + tagging a new SDK version** — the factory *drives the existing release process* (PR + tag), a human approves the cut |
| **Ship** (stage 16) | publishing the agent product to the Hub |

The gate is **configurable per stage per trust level** — exactly like agent autonomy: a
trusted lane may auto-approve low-risk stages while **always halting on the
high-blast-radius ones** (SDK release, ship). This is the containment for the shared-SDK
concern (§11.5): **the factory *may* PR + tag the SDK — a human approves the release**, the
same accept/deny the product already exposes for agent actions. (Residual risk: the gate
must not degrade to a rubber stamp; halting-by-default on the high-blast-radius stages is
what keeps it real.)

**Non-convergence fails loudly — never ships a degraded result.** The dev-half loops (spec
iteration stage 4, eval-optimize stage 7) are LLM loops that may not converge. Each has a
**bounded auto-iteration budget**; on exhaustion the run **halts and escalates to the human
gate with the transcript + last failing scorecard** — it does **not** lower the bar,
disable a failing scenario, or ship the best-so-far (per CLAUDE.md's no-silent-fallbacks
rule). A gate that can't be met is a stop, not a downgrade.

## 3. The two halves + the full lifecycle (stage by stage)

Each row is a real developer activity; 🚦 = a gate that can fail the run. **Front (dev)**
is mostly net-new automation; **Back (ship)** cites the pipeline that *already exists*.

**Front half — Dev (automate the human judgment work):**

| # | Stage | Automated by | Component (status) | Gate |
|---|---|---|---|---|
| 1 | **Clone + scope** — pull live SDK; scope vs the *current* API surface | orchestrator + code-index | git worktree · `code_index` (*exists*) | — |
| 2 | **Track** — open GitHub **issues + milestones**, decompose | orchestrator + `gh` | `gh` · `claude.yml` bot (*exists*) | — |
| 3 | **Spec** — author the design/spec doc | `brainstorming` → `writing-plans` skills | this session's method (*skills exist*) | — |
| 4 | **Iterate spec** 🚦 | adversarial review loop to convergence | review agents + memory (*exists*) | converges |
| 5 | **Synthetic data** — generate the *dev/optimize* corpus (seed-from-real, labels-by-construction); held-out gate oracle stays human-curated, §5.5 | dataset generators | `generate_mbox.py` · `vendor_corpus_seed` · `pdf_document_generator` (*exists*) | train/held-out split |
| 6 | **Implement** — write agent code **against the live SDK** | GAIA coder + TDD | `origin/coder` `CodeAgent` + `agents/base/*` (*exists*) | compiles/lints |
| 7 | **Eval + optimize** 🚦 | eval → analyze failures → repair → re-eval | `gaia eval agent [--fix]` · `scorecard.py` · `analyze_failures.py` (*exists*) | scorecard ≥ bar |
| 8 | **PR** 🚦 | open PR(s) into the codebase (agent code — *and SDK changes it needs*) | orchestrator + `gh` + `finalize` | `claude.yml` review + CI (*exists*) | review + CI green |

**Back half — Ship (orchestrate the existing rigorous pipeline; §6 details):**

| # | Stage | What it does | Component (status) | Gate |
|---|---|---|---|---|
| 9 | **Docs generate + sync** | emit/refresh README · SPEC · SKILL · CHANGELOG · SCORECARD · openapi/spec_html, kept in sync (§4) | `packaging/gen_*` · `spec_html` · CLAUDE.md sync rule (*exists, manual sync*) | docs-in-sync |
| 10 | **Manifest emit** | derive `manifest.json` (§0.28) from recipe + compose/eval outputs | net-new emitter | schema-valid |
| 11 | **Multi-platform freeze** 🚦 | freeze win32-x64 · darwin-arm64 · darwin-x64 · linux-x64; smoke-test each; **assert required platforms** | `packaging/freeze.py` · `smoke_test.py` · `release_agent_email.yml` (*exists*) | smoke + platforms |
| 12 | **OS-compat verify** 🚦 | run a newer-OS-built binary on an **older OS** (e.g. macos-26 build → macOS 15) | `release_agent_email.yml` verify job (*exists*) | older-OS smoke |
| 13 | **Eval gate (data-driven)** 🚦 | baseline = **previous release**; **acceptance bar + URGENT floor** (#1437) | scorecard gate job (*exists*) | ≥ bar, ≥ floor |
| 14 | **Package assemble** | whole-package zip (all binaries + npm TS client + docs + lock) + `package-files.json` | `gen_package_files.py` · npm build (*exists*) | manifest complete |
| 15 | **Sign + real-hash lock + provenance** 🚦 | SHA-256, **npm OIDC trusted-publishing provenance**, regenerate `binaries.lock.json` with **real hashes**, embed provenance (spec · scorecard · SDK commit) | `gen_binaries_lock.py` · npm OIDC (*exists; signing partial*) | signature/provenance |
| 16 | **Publish** | cut-from-main + token gates; POST `/publish` to Hub Worker; npm publish; **redeploy catalog site** | `publish_to_r2.py` · Hub Worker · website deploy (*exists*) | governance gates |
| 17 | **Post-publish edge verify** 🚦 | fetch **every published object via the real fetch CLI**; assert the package zip is **fetchable at the CDN edge** (the #1655 "user's real state" rule) | fetch-verify steps (*exists*) | real download OK |
| 18 | **Maintain (continuous)** 🚦 | on an SDK delta that regresses the agent's eval, re-run 1–17 for the delta | net-new trigger + the above | eval stays ≥ bar |

Stages 9–17 already run in CI (`release_agent_email.yml`, `build_agents.yml`,
`publish_agents.yml`, `email_scorecard_refresh.yml`) — **the factory generalizes them
from one-agent, human-kicked jobs into a continuous, orchestrated, per-agent line.**

## 4. Docs are a first-class shipped output (not an afterthought)

The shipped product (`hub/agents/npm/agent-email/`) carries **five doc surfaces** —
`README.md` (integrator) · `SPEC.md` (technical) · `SKILL.md` (AI-assistant) ·
`CHANGELOG.md` (version) · `SCORECARD.md` (eval results) — plus `spec_html`/OpenAPI. Per
CLAUDE.md, *a functional change must update **every** doc*, or the package ships
self-contradicting documentation. So the factory needs a **docs generate-and-sync stage**
(stage 9) — and this is a hard gate, because doc drift is a real published-defect class.

**The scorecard is a shipped, refreshed artifact — not just a gate.** `SCORECARD.md`
publishes *with* the agent and is refreshed on a cadence (`email_scorecard_refresh.yml`),
so the eval result is both (a) the stage-13 gate and (b) provenance the product carries
and keeps current.

## 5. The eval gate is data-driven, not "≥ a committed baseline"

The real gate (stage 13) is more than a static comparison:

- **Baseline = the previous release** (resolved dynamically), so an agent must not regress
  against *what shipped last*, not against a hand-committed file.
- **A data-driven acceptance bar + an URGENT floor** (#1437) — the bar adapts to the
  scenario mix; the floor is a hard minimum on the safety-critical bucket (e.g. URGENT
  email recall) that no aggregate score can paper over.
- **Refreshed independently** so the shipped scorecard stays honest as models/SDK move.
- **Runs on the held-out oracle, not the dev/optimize corpus** (§5.5, M2) — gating on the
  set the agent was tuned against would measure memorization, not capability.

The factory's job is to *run this gate on every dev-half output and every SDK-delta
rebuild* — the eval isn't a one-time ship check, it's the continuous regression net.

## 5.5 Synthetic data generation — seed-from-real, labels-by-construction, strict split

Stage 5 is where the review's circularity bites hardest: if the factory generates the eval
data *and* the agent *and* the labels, a green score is self-certification. GAIA's existing
practice already avoids the naive trap — the email corpus is **seeded from a real vendor
corpus** (`vendor_corpus_seed.jsonl` → `generate_mbox.py`/`select_vendor_subset.py`), its
**ground truth is committed/curated** (`ground_truth.json`), and `pdf_document_generator.py`
generates from **templates** — so the factory must generalize *that discipline*, not just
"ask an LLM for test cases." Three rules:

1. **Seed from real, anonymized data — don't hallucinate the distribution.** Synthetic
   *volume* over a *real* distribution (the vendor-seed pattern) reflects inputs users
   actually send, not what a model imagines they send. Refresh the seed to catch drift.
2. **Ground truth known BY CONSTRUCTION — never AI-labelled post-hoc.** Synthesize each
   case *from a template/spec whose parameters fix the correct answer* (build an email *to
   be* urgent → label = `urgent` by construction), rather than generating text and asking a
   model to label it. A label the generator *knows* (it built the case to have it) is sound;
   a label an LLM *guesses* afterward inherits the model's blind spots.
3. **Strict train/held-out separation — the anti-overfitting rule (the review's core fix).**
   Two corpora, never crossed:
   - **Dev/optimize corpus (M3, factory-generated):** fuel for the eval-optimize (`--fix`)
     loop; the factory may generate it freely (rules 1–2) — overfitting *to it* is fine, it's
     the training signal.
   - **Held-out gate oracle (M2, human-curated):** what stage 13 / regression gates on —
     **human-curated, versioned, committed** (as `ground_truth.json` +
     `quality_gate_thresholds.json` are today), of *different provenance than the agent's
     implementer*, and **never** used in the optimize loop. Leakage = the score measures
     memorization, not capability.

**Coverage discipline:** templates enumerate the scenario space explicitly; hold a dedicated
**adversarial/edge bucket** (the committed `phishing_fixture.json` is the pattern) so
safety-critical cases aren't diluted by the easy mass; track which scenario classes the
corpus covers so gaps are *visible* rather than silently untested.

## 6. Ship-half rigor the factory must preserve (do not reinvent, do not lose)

The existing publish pipeline encodes hard-won rigor. The factory orchestrates it and
keeps every property:

- **Multi-platform matrix + "assert required platforms present"** (stage 11) — a partial
  build never publishes.
- **Cross-OS-version compatibility** (stage 12) — a binary built on a newer OS is verified
  on an older one; catches glibc/macOS-SDK regressions users would hit.
- **Real-hash lock regeneration** (stage 15) — `binaries.lock.json` is rewritten from the
  **actually-published** artifacts, so the integrity manifest reflects reality, not a
  pre-computed guess.
- **Whole-package multi-component assembly** (stage 14) — the product is *not one binary*
  (§7); the zip bundles all platform binaries + the npm client + docs + lock + examples.
- **npm OIDC trusted publishing + provenance** (stage 15) — supply-chain provenance, not a
  bare signature.
- **Post-publish edge verification** (stage 17) — the #1655 discipline: verify the thing a
  *user actually downloads from the CDN* fetches + verifies, not just that CI built it.
- **Release governance** (stage 16) — cut-from-main assertion, publish-token gates, and
  **version single-source-of-truth** via `stamp_version.py --check` (pyproject/version.py/
  package.json/docs must agree before publish).

## 7. The product is multi-component, not a single binary

A shipped agent = **platform binaries + the npm TS integration client (`fetch`/`lifecycle`)
+ the five docs + `binaries.lock.json` + examples/tests**. The factory produces and ships
all of it as one versioned release; "the binary" is one component, not the product. (This
is why stage 14 assembles a whole-package zip and stage 9 owns docs.)

## 8. The factory ↔ runtime seam

```
  ┌──────────── AGENT FACTORY (automated SDLC on the live SDK) ─────────────┐   ┌──── RUNTIME ────┐
  │ DEV: clone→scope→issues→spec→iterate→synth→CODE→EVAL🚦→PR🚦             │   │ daemon installs │
  │ SHIP: docs→manifest→freeze→OS-compat→GATE🚦→assemble→SIGN🚦→publish→    │   │ + verifies+runs │
  │       edge-verify🚦   └──── continuous maintenance ◄──── SDK delta ──── │   └────────▲────────┘
  └───────────────────────────────┬─────────────────────────────────────---─┘            │ enforces grants,
                    emits: signed multi-component product + manifest.json (§0.28)          │ version, signature
                           + provenance (spec · scorecard · SDK commit)                    │
                                   ▼                                                        │
                          ┌──────── Agent Hub (§0.5) — the conveyor ─────────┐──────────────┘
                          └──────────────────────────────────────────────---─┘
```

## 9. Component inventory — exists vs net-new

**Exists (orchestrate, don't rebuild):** the **GAIA coder** (`origin/coder` `CodeAgent`) ·
**Claude Code in CI** (`claude.yml`, `claude-run.yml`) + skills + memory · the **eval
framework** (`eval/{runner,benchmark,scorecard,analyze_failures,audit}.py`, baselines,
`--fix`, synthetic corpus, `email_scorecard_refresh.yml`) · **`gh`** issues/PRs + review
bot · **`code_index`** over the live SDK · the **entire ship half**
(`release_agent_email.yml`, `packaging/{freeze,gen_binaries_lock,gen_package_files,
gen_scorecard,stamp_version,smoke_test,publish_to_r2}.py`, Hub Worker, npm OIDC, edge
verify) · git worktrees.

**Net-new (the stitch — the real work):**
1. **The dev-half orchestrator** — integrate the GAIA coder + an Agent-SDK/Claude-Code
   loop + skills + memory into one driver (`gaia factory <recipe>`) in an isolated live-SDK
   worktree, producing a merged, evaluated agent on `main`.
2. **The recipe / agent-spec input** — declarative intent (purpose, capabilities, eval
   config, targets) the orchestrator plans from → also the source of the manifest.
3. **The manifest emitter** (stage 10) — derive `manifest.json` from recipe + outputs.
4. **Generalize the ship half from one-agent to per-agent** — the pipeline exists for
   email; make it recipe-driven for any agent.
5. **The SDK-delta trigger (stage 18)** — CI hook that re-runs the factory for affected
   agents on an SDK change; synthetic datasets + baselines are the regression net.
6. **Signing + provenance embedding** (extend `gen_binaries_lock.py` with a signature +
   spec/scorecard/SDK-commit provenance).

## 10. Milestones — automate easiest → hardest (difficulty-ordered)

Deliberately ordered by **difficulty and risk**: automate the *deterministic, already-built*
work first, defer the *judgment-heavy, unsolved-research* work last. This is also the
adversarial review's de-risking (§11.5): the ship half is real → cheap; the dev half is
net-new; the maintenance loop is the hardest and must come **last**, gated on the oracle
work from M2. Each milestone is independently valuable and shippable.

| M | Milestone | Automates | Difficulty | Why here / gate |
|---|---|---|---|---|
| **M0** | **Generalize the ship half** (recipe-driven, per-agent) | stages 9–17 for *any* agent: turn `release_agent_email.yml` + `packaging/*` into a reusable, recipe-parametrized pipeline | **Easiest** — deterministic, already exists, no LLM | **Prove on a *second, non-email* agent** (browser/analyst) — the review's empirical reuse-vs-rewrite check; includes per-agent OIDC publisher provisioning, tags, R2 prefixes |
| **M1** | **Provenance + edge-verified releases** | manifest emit (stage 10) · signing + source-hash + SDK-commit provenance (stage 15) · post-publish edge verify (stage 17) | **Easy** — mechanical, extends existing scripts | integrity/traceability (not "reproducibility," §11.5); docs-in-sync becomes a hard gate |
| **M2** | **Independent eval oracle + confidence-bound gate** | the *trustworthy* eval gate: a **human-curated, held-out** ground-truth set per agent + per-agent safety floors; gate on `LCB(score, k runs) ≥ bar` | **Medium** — mostly discipline, but the oracle is **human judgment the factory does NOT automate** | **Prerequisite for everything generative** (M3–M4) — without an independent oracle the gate is self-certification (§11.5 #1) |
| **M3** | **Assisted dev automation** | the *mechanical* dev stages: scaffold, tool/skill/MCP wiring, synthetic-data gen, the eval-optimize (`--fix`) loop, PR authoring | **Harder** — net-new agentic coding, but human-in-the-loop | human still owns **scope, spec, the oracle (M2), PR-approve, ship**; the GAIA coder + Claude Code loop assist, they don't decide |
| **M4** | **SDK-delta maintenance loop** (stage 18 — the keystone) | on an SDK delta that regresses an agent (measured on M2's held-out oracle, LCB-gated), re-run M3+M0 for that agent | **Hardest** — the differentiator *and* the highest risk | **Last, and only after M2.** Built-in: (a) **serial-eval throughput cap** (one eval/backend — CLAUDE.md), size cadence against it; (b) SDK changes ship via **PR + tag through the existing release process**, gated by the **human approve/deny at the SDK-release checkpoint** (§2.5); the resulting all-agent re-eval is the *intended* regression net |

**Reading the order:** M0–M1 ship *any* agent reproducibly-packaged and provenance-verified
with **no LLM in the loop** — pure, high-value CI. M2 buys the trust bar. Only then does
M3 add agentic authoring (human-gated), and M4 the continuous maintenance loop. The moat
(M4) is *last* because it depends on M2's oracle and is the unsolved-research part — you do
not build the loop before you can trust the gate it runs on.

## 11. Distinctiveness (brief, honest)

Not skill-learning (Hermes/OpenClaw) and not ordinary CI/CD — an **automated AI
software-engineering pipeline that builds *and maintains* agent products against a living
SDK**, using the same flow a human team uses (issues · specs · reviews · **data-driven
evals** · PRs · **provenance-verified releases**). The moat is the *maintenance-against-a-
moving-SDK* loop + the eval-gate + real PRs into the codebase. **Honest caveat:** the ship
half exists and is rigorous, but the dev-half orchestrator + the SDK-delta loop are
substantial net-new work; this is a *potential* moat that must be built, and stage 18 is
the hard part.

## 11.5 Critique & corrections (adversarial review)

An adversarial review (grounded in the real workflow + packaging code) returned:
**sound as a *packaging* architecture; over-reach as an *SDLC-automation* one — a rigorous
back half bolted to an aspirational front half where ~90% of both the value and the
unsolved-research risk lives.** The "two halves" framing is honest but must not let the
ship half's maturity launder the dev half's + M4's risk. Corrections, folded into the
milestone order (§10):

**Load-bearing claims that were wrong — corrected:**
- **"Reproducible against an SDK commit" is a category error.** The deterministic tail
  (freeze/sign/publish) is reproducible; the *generative head* (LLM codegen, LLM-judged
  eval) is **not** — re-running yields a *different* agent. Pinning buys **traceability +
  integrity (source-hashed), not regeneration** (§1 corrected).
- **"Orchestrate the ship half, don't reinvent it" overclaims reuse.**
  `release_agent_email.yml` is ~718 lines, **email-hardcoded** — npm OIDC is **bound to the
  workflow *filename***, and tags/binary-names/manifest-path/R2-prefix/the `urgent_recall_
  floor` gate are all baked in. Per-agent generalization is a reusable-workflow rewrite +
  **a registered npm publisher per agent** — substantial, not "just drive it." **M0 proves
  it empirically on a second, non-email agent.**
- **"Exists" overstated for two dev-half deps:** the GAIA coder is on `origin/coder` (not
  main); the §0.x refs depend on **#1913 (unmerged)** — both "exist *on a branch*."

**The eval gate has no independent oracle — the deepest flaw.** The factory writes the
agent *and* the corpus + ground truth *and* sets the bar *and* reviews the PR: a green
scorecard proves the agent matches the *factory's own* notion of correct, not correctness.
The pipeline's *only* real oracle today is the **hand-authored `urgent_recall_floor`**
(`quality_gate_thresholds.json`, #1437). **M2 is the fix and is a hard prerequisite for
M3–M4:** gate on a **human-curated, held-out** ground-truth set per agent (different
provenance than the implementer — *never* the factory-generated training corpus) + per-agent
hand-set safety floors, and gate on the **lower confidence bound over *k* runs** (the judge
is noisy) — not the point estimate. *The oracle is human judgment the factory does NOT
automate; admitting that is the factory's honest scope — it automates the mechanics, not
the oracle.*

**M4 (the keystone) has a convergence hazard + a hard throughput ceiling.** Moving baseline
(previous release) + fixed corpus + noisy judge churns → the LCB gating above is the fix.
Cascade: an SDK change to fix agent A re-triggers B…Z; evals **must run serially** on one
Lemonade backend (CLAUDE.md), so N≈19 agents × every SDK delta is a **wall-clock ceiling** —
the real cost driver is *throughput, not dollars*. M4 caps to one-eval-per-backend and sizes
cadence against it.

**AI PRs into the shared SDK — contained by a per-stage human gate, not a prohibition
(resolved §2.5).** The review flagged the blast radius of an automated system PRing the
shared SDK. Resolution: **the factory *may* ship SDK versions via PR + tag** (driving the
*existing* release process), because the containment is the **human approve/deny gate at
the SDK-release checkpoint** (§2.5) — the same accept/deny model the agents use, halting-by-
default on this high-blast-radius stage. The cascade (an SDK change re-evals all agents) is
then the **intended regression net**, not a hazard — bounded by the serial-eval throughput
cap. The residual poisoned-issue → injected-scope path is contained at the PR-review + merge
gates; keeping those gates real (not rubber stamps) on SDK changes is the standing
requirement.

**Honest reframe:** the stated moat (the maintenance loop) is the doc's **least-built,
highest-risk, last-scheduled** component. "The back half exists, we're most of the way
there" is false — **we're most of the way through the *cheap* half.** M0–M1 deliver real
value with **no LLM in the loop**; the research risk is quarantined to M3–M4, behind M2's
oracle.

## 12. Open decisions (need sign-off)

1. **Orchestrator substrate** — Claude Code (skills + memory, already in CI) + GAIA coder
   vs. a custom Anthropic Agent-SDK build. *Rec:* start on Claude Code + GAIA coder (both
   exist); evaluate a custom build if the loop needs tighter control.
2. **Autonomy at the gates** — *resolved* (§2.5): per-stage approve/deny mirroring the agent
   confirmation model; configurable per trust level, halting-by-default on the
   high-blast-radius stages (SDK release, ship). Open only: which stages a *trusted* lane
   may auto-approve.
3. **SDK-release scope** — *resolved* (§2.5): the factory **may** ship SDK versions via
   PR + tag (driving the existing release process), contained by the human approve/deny at
   the SDK-release checkpoint. Open only: the auto-approve trust threshold for it.
4. **Stage-18 trigger policy** — re-run on every SDK commit vs. only on eval regression vs.
   scheduled. *Rec:* run eval on SDK-affecting deltas; rebuild only on regression.
5. **Recipe vs. manifest** — one authored input or recipe-in / manifest-out. *Rec:*
   separate recipe (human intent, richer: eval config + targets) → manifest (machine
   contract the runtime enforces).
