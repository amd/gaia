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

**Reading map** (§ jump-list):
- **Why & what** — §0 thesis (SDLC automated) · §1 the live-SDK keystone · §1.5 the *recipe* (the one authored input)
- **Engine & governance** — §2 agentic-coding engine · §2.5 human approve/deny gates · §2.6 the factory's *own* least-privilege
- **The pipeline** — §3 the lifecycle stages · §4 docs-as-output · §5/§5.5 eval gate + synthetic-data discipline · §6/§6.5 ship rigor + recovery · §7 multi-component product · §8 runtime seam
- **Plan & honesty** — §9 exists-vs-net-new · §10 milestones M0→M4 (easiest→hardest) · §11 distinctiveness · §11.5/§11.6 adversarial corrections · §11.7 domain-review corrections (eval + release) · §12 open decisions

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
  verification (darwin-x64 leg), a **data-driven** scorecard gate, scorecard generation, Hub
  + npm publishing with OIDC provenance, real-hash lock regeneration, and **post-publish edge
  verification**. (The single whole-package *zip* is the one piece currently disabled — §6.)
  **The factory ORCHESTRATES this half — it must not reinvent it, and must not lose its rigor.**
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

## 1.5 The recipe — the single authored input

Everything downstream is driven by one declarative **`recipe`** — the human intent the
orchestrator plans from and the source the machine `manifest` (§0.28) is derived from
(§12.5 keeps the two separate: recipe = intent, manifest = enforced contract). It is the
*human-authored* artifact; the factory produces everything else.

**Ground it on the format that already exists: `gaia-agent.yaml`.** Eighteen hub agents
already carry a per-agent manifest (`hub/agents/python/<id>/gaia-agent.yaml`) with
`id/name/version/models/python.entry_module/dependencies/requirements.platforms/interfaces`
— the recipe **extends that file** with the factory-specific fields (purpose, eval block,
connectors/scopes, egress, trust tier, gates, freeze targets) rather than inventing a
second per-agent YAML that would drift against it. Shape (factory fields added to the
existing schema):

```yaml
id: email
purpose: "Triage, search, and organize a personal Gmail/Outlook mailbox, locally."
model:      { llm: Gemma-4-E4B-it-GGUF, min_ctx: 8192 }   # → manifest.requiredModels
tools:      [rag, file_io]                                 # KNOWN_TOOLS mixins
skills:     [triage-inbox, follow-up-tracking]             # SKILL.md (skill-format)
mcp:        [gmail, google-calendar]                       # MCP servers (tool-loader)
connectors: { google: [gmail.modify, calendar] }           # → manifest.oauthScopes (least-priv)
egress:     [googleapis.com]                                # → manifest.egressAllowlist (§0.24)
eval:                                                       # the gate (§5, §5.5)
  held_out_oracle: oracles/email/            # human-curated, curator ≠ spec author; split by thread-id
  n_runs:          3                          # repetitions to estimate variance (NOT k)
  acceptance_bar:  0.80                       # FIXED hard bar — no drift (like --min-aggregate)
  safety_floor:    { needs_attention_recall: 0.90 }  # FIXED tripwire; #1437 axis = URGENT+NEEDS_RESPONSE
  non_inferiority: "candidate >= previous_release - k*stdev"   # k = stdev-band mult (default 1.0), matches scorecard_gate.py
trust_tier: verified                                        # → manifest.trustTier (§0.24)
gates:      { sdk_release: human, ship: human }             # illustrative subset of the §2.5 checkpoints
targets:    [win32-x64, darwin-arm64, darwin-x64, linux-x64]
```

The recipe's content-hash is part of provenance (§1) — a published agent is traceable to
the exact recipe that built it. Authoring the recipe *is* the human's core input; note the
recipe names the **held-out oracle path** but not its contents (that stays human-curated,
§5.5) and does not set the version (the factory decides the bump, §6.5).

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
(runtime §0.4 confirmation gate, §0.34 autonomy *policy layer* — a policy engine, not an
enum of levels). A human (or a policy) accepts or
rejects at each checkpoint before the pipeline proceeds:

| Checkpoint | What the human approves / denies |
|---|---|
| **Spec** (stages 3–4) | the scoped design, before any code is written |
| **PR open / Merge** (stage 8) | the agent-code — *or SDK* — changes, before they land / landing them |
| **SDK release** (M4 only, *distinct from agent-publish*) | **cutting + tagging a new SDK version** — the factory drives the existing release process (PR + tag); **the human approves against a pre-cut all-agent blast-radius report** (below), not just a tag |
| **Agent ship** (stage 16) | publishing an agent product to the Hub |
| **Escalation** (stages 4/7, on non-convergence) | how to proceed when a dev-half loop exhausts its iteration budget (below) — fix by hand, re-scope, or abandon the run |

The gate is **configurable per stage per trust level** — exactly like agent autonomy: a
trusted lane may auto-approve low-risk stages while **always halting on the
high-blast-radius ones** (SDK release, ship).

**The SDK-release gate must fire *after* blast radius is computed, not before.** The
agent-confirmation analogy breaks here: an agent confirmation gates one action whose effect
the human sees; an SDK release gates a change that **fans out to N agents whose regressions
aren't known until they're re-eval'd**. Approving a bare tag + changelog is the rubber stamp
the review warned of. So the SDK-release gate's *input* is a **pre-cut dry-run: re-eval all N
agents against the SDK candidate on their held-out oracles, and surface the per-agent
regression set.** The human approves the radius, not the tag. (The real containment is thus
the dry-run + the N downstream stage-13 gates; the approval is the human decision *over* that
evidence.)

**Non-convergence fails loudly — never ships a degraded result.** The dev-half loops (spec
iteration stage 4, eval-optimize stage 7) are LLM loops that may not converge. Each has a
**bounded auto-iteration budget**; on exhaustion the run **halts and escalates to the human
gate with the transcript + last failing scorecard** — it does **not** lower the bar,
disable a failing scenario, or ship the best-so-far (per CLAUDE.md's no-silent-fallbacks
rule). A gate that can't be met is a stop, not a downgrade. **This has a capacity cost the
factory must own:** escalations are *manual maintenance the factory did not eliminate*, so a
sustained escalation rate above a threshold is the signal that the **M4 SDK-delta cadence is
mis-sized** (too many deltas for the human throughput behind the single serial-eval backend,
§11.5) — throttle the cadence, don't grow the queue.

## 2.6 The factory's own authority — least-privilege, isolated, gated

The factory is the **most privileged actor in the system**: it writes the repo, opens and
merges PRs, cuts + tags SDK releases, and publishes to npm (OIDC) and the Hub (publish
token). It would be incoherent to scope every *agent's* capabilities tightly (runtime §0.24)
yet leave the *factory* — which can change the SDK all agents run on — with ambient
god-rights. So the factory is subject to the same capability discipline it ships:

- **Least-privilege via three *distinct* GH mechanisms** (not one) — and only the first is
  built today:
  - **Publish** — a **GitHub Environment secret** (`GAIA_HUB_TOKEN`) with required reviewers,
    plus npm **OIDC** (no stored secret): the token is unreadable until the human gate
    approves. *This is already how `release_agent_email.yml` works* — "secrets injected only
    at the gated stage" is real, not aspirational, for publishing.
  - **Merge** — **branch protection + required reviews** (Environments don't gate "who may
    merge"). *To build.*
  - **SDK tag** — a **tag-protection ruleset** (and note a tag that *triggers* release can't
    sit behind the downstream environment gate). *To build.*
  - The "cannot self-merge/tag" guarantee also requires the **orchestrator token to lack
    `contents: write`/admin** — otherwise a `gh`-authed run holds whatever its token can do;
    all privileged git ops must route through the gated Actions jobs.
- **Isolated execution.** Each run is a throwaway worktree/sandbox (§2) with no standing
  access to publish secrets.
- **Auditable.** Every privileged action (merge, tag, publish) is attributable to the run +
  the approving human — the runtime's audit plane, applied to the factory itself.

## 3. The two halves + the full lifecycle (stage by stage)

Each row is a real developer activity; 🚦 = a gate that can fail the run. **Front (dev)**
is mostly net-new automation; **Back (ship)** cites the pipeline that *already exists*.

**Front half — Dev (automate the human judgment work):**

| # | Stage | Automated by | Component (status) | Gate |
|---|---|---|---|---|
| 1 | **Clone + scope** — pull live SDK; scope vs the *current* API surface | orchestrator + code-index | git worktree · `code_index` (*exists*) | — |
| 2 | **Track** — open GitHub **issues + milestones**, decompose | orchestrator + `gh` | `gh` CLI (*exists*) | — |
| 3 | **Spec** — author the design/spec doc | `brainstorming` → `writing-plans` skills | this session's method (*skills exist*) | — |
| 4 | **Iterate spec** 🚦 | adversarial review loop to convergence | review agents + memory (*exists*) | converges |
| 5 | **Dev/optimize corpus** — generate the *training* corpus (seed-from-real, labels-by-construction, §5.5) | dataset generators (*automated*) | `generate_mbox.py` · `vendor_corpus_seed` · `pdf_document_generator` (*exists*) | 🚦 PII-scrub on seed |
| 5b | **Held-out oracle** — curate/extend to cover the *current capability surface* (§5.5) | **human, NOT the factory** (curator ≠ spec author) | `ground_truth.json` + `quality_gate_thresholds.json` pattern (*exists*) | 🚦 coverage-delta |
| 6 | **Implement** — write agent code **against the live SDK** | GAIA coder + TDD | `origin/coder` `CodeAgent` *(on a branch)* + `agents/base/*` | compiles/lints |
| 7 | **Eval + optimize** 🚦 | eval → analyze failures → repair → re-eval | `gaia eval agent [--fix]` · `scorecard.py` · `analyze_failures.py` (*exists*) | scorecard ≥ bar |
| 8 | **PR** 🚦 | open PR(s) into the codebase (agent code — *and SDK changes it needs*) | orchestrator + `gh` (PR authoring) | `claude.yml` PR-review bot + CI (*exists*) | review + CI green |

**Back half — Ship (orchestrate the existing rigorous pipeline; §6 details):**

| # | Stage | What it does | Component (status) | Gate |
|---|---|---|---|---|
| 9 | **Docs: generate SCORECARD, publish the rest** | `SCORECARD.md` is CI-generated; README/SPEC/SKILL/CHANGELOG are authored/committed and *published* (not emitted), kept in sync (§4) | `gen_scorecard.py` (gen) · committed docs · CLAUDE.md sync rule (*exists, manual sync*) | docs-in-sync |
| 10 | **Manifest emit** | derive `manifest.json` (§0.28) from recipe + compose/eval outputs | net-new emitter (schema in #1913) | schema-valid |
| 11 | **Multi-platform freeze** 🚦 | freeze win32-x64 · darwin-arm64 · darwin-x64 · linux-x64; smoke-test each; **assert required platforms** | `packaging/freeze.py` · `smoke_test.py` · `release_agent_email.yml` (*exists*) | smoke + platforms |
| 12 | **OS-compat verify** 🚦 | run a newer-OS-built binary on an **older OS** — *darwin-x64 leg only today* (macos-26 build → macOS 15); other platforms have no older-OS verify | `release_agent_email.yml` verify job (*exists, darwin-x64*) | older-OS smoke |
| 13 | **Eval gate** 🚦 | on the **held-out oracle** (§5.5); **fixed** acceptance bar + safety floor (#1437); prev-release as a **non-inferiority band** `≥ prev − k·stdev` (§5) | `scorecard_gate.py` (*exists*) | point-estimate `min-aggregate` today; LCB/band = **M2** |
| 14 | **Package assemble** | npm tarball (client + docs + lock) + R2 binaries + `package-files.json`. ⚠️ single whole-package **zip DISABLED** (`if: false`, Cloudflare 413) | `gen_package_files.py` · npm build (*exists; zip disabled*) | manifest complete |
| 15 | **Sign + real-hash lock + provenance** 🚦 | SHA-256, **npm OIDC trusted-publishing provenance**, regenerate `binaries.lock.json` with **real hashes**, embed provenance (spec · scorecard · SDK commit) | `gen_binaries_lock.py` · npm OIDC (*exists; signing partial*) | signature/provenance |
| 16 | **Publish** | cut-from-main + token gates; POST `/publish` to Hub Worker; npm publish; **redeploy catalog site** | `publish_to_r2.py` · Hub Worker · website deploy (*exists*) | governance gates |
| 17 | **Post-publish edge verify** 🚦 | fetch **every published object via the real fetch CLI** at the CDN edge (#1655 "user's real state") — *per-object verify works; the zip-verify leg is disabled with the zip (§6)* | fetch-verify steps (*exists; zip leg off*) | real download OK |
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

The gate has three parts — and the construction matters, because a naive "beat last
release" gate is statistically unsound (an eval-methodology review caught the original
draft reinventing the shipped gate in a strictly worse form):

- **Two FIXED hard gates (no drift):** a **fixed acceptance bar** and **fixed safety
  floors** — constants, exactly as #1437's `--min-aggregate` / `--min-urgent-recall`. A
  fixed bar is ratchet-free.
- **Previous-release comparison is a NON-INFERIORITY BAND, never a moving bar.** The shipped
  gate (`scorecard_gate.py`) is `candidate_point ≥ baseline_point − k·stdev` — the noise
  band sits *below* the baseline, so flat-true-capability passes and the accepted point can
  drift *down* as well as up (mean-reverting, not a ratchet). **Do not** gate `LCB(candidate)
  ≥ prev_point`: that enshrines the noisy upper tail as the next floor and ratchets the bar
  out of reach within ~2–3 releases. For a real two-sample check, use the framework's
  `mann_whitney_u` / `bootstrap_ci`.
- **`k` is the stdev-band multiplier (default 1.0), NOT a run count.** Repetitions are
  `n_runs` (3 in the shipped fixture) — enough for a crude band, not a reliable CI (n≈5
  normal-approx is anti-conservative; use t or bootstrap). Conflating the two silently 5×'s
  the serial-eval cost (§11.5). *Today's gate is a point-estimate `min-aggregate ≥ bar`;
  the noise-band/LCB is the **M2 upgrade**, not existing behavior.*
- **Know which pipeline the metric comes from** (different noise): structured agents (email)
  gate on **deterministic confusion-matrix** metrics vs `ground_truth.json` (small spread —
  the band matters little); generic scenario scorecards gate on **LLM-judge `avg_score`**
  (larger spread — the band matters). The recipe declares which.
- **Safety floors are point tripwires, not CI-backed guarantees** unless the bucket is sized
  for it: 95%-confidence that true recall ≥ 0.95 with zero observed misses needs ≈ **59**
  positive instances. So state a minimum positive count per safety bucket, or label the floor
  a tripwire (as #1437 does at **0.90** on the *needs-attention* axis = URGENT + NEEDS_RESPONSE,
  not URGENT alone).
- **Runs on the held-out oracle, not the dev/optimize corpus** (§5.5) — gating on the
  tuned-against set measures memorization, not capability.
- **Refreshed independently** so the shipped scorecard stays honest as models/SDK move.

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
   **🔒 Because the seed is real user data on a *recurring* intake (every refresh), a
   PII-scrub + consent/provenance gate 🚦 is mandatory *before* any seeded corpus is
   committed** — "anonymized" is a requirement with an owning gate, not an adjective
   (escalated to @kovtcharov-amd; see §11.6).
2. **Ground truth: known by construction *plus* a human-judged real-data slice — not one or
   the other.** By-construction labels (build an email *to be* urgent → label = `urgent`)
   are internally consistent but only *relative to the template author's definition* — they
   encode the author's assumptions *as* ground truth (a subtler circularity than
   AI-labelling). So the held-out oracle must **also** carry a fraction of labels
   **human-judged on real seed data**, free to *disagree* with the templates — that slice is
   the only thing that can catch a systematically mis-defined task, not just instance-level
   overfitting. **Size it (a floor, e.g. ≥ 20% of the oracle) and track template-vs-human
   label agreement** — if templates disagree with humans beyond a threshold, the template
   *definition* is suspect; a 5% decorative slice detects nothing.
3. **Strict train/held-out separation — split by SOURCE, not just by instance.** Two
   corpora, never crossed — and "never cross an *instance*" is not enough, because both
   corpora seed from the **same vendor pool + same templates**, so a dev instance and an
   oracle instance can be *near-duplicates* off one seed/template (leakage that reads as
   "held-out"):
   - **Split key = source document / thread ID:** no oracle item may share a seed thread with
     any dev item; the oracle's by-construction portion uses **templates disjoint** from the
     dev corpus (or leans on real, non-templated seed instances). Otherwise "held-out"
     measures template-surface memorization, not capability.
   - **Dev/optimize corpus (M3, factory-generated):** fuel for the eval-optimize (`--fix`)
     loop; the factory may generate it freely (rules 1–2) — overfitting *to it* is fine.
   - **Held-out gate oracle (M2, human-curated):** what stage 13 / regression gates on —
     versioned, committed (as `ground_truth.json` + `quality_gate_thresholds.json` today),
     curated by **a human who is *not* the spec author** (the circular source is the person
     who defined the task, *not* the LLM implementer — "different from the implementer" is
     trivially true), and **never** used in the optimize loop. Make that a **mechanism, not
     discipline:** the oracle lives at a path the `gaia eval agent --fix` run *cannot read*,
     enforced in the harness — so a config slip can't leak it.

**The oracle is manufactured and maintained by an explicit stage — it does not pre-exist by
magic.** Stage 13 (and the regression gates) *consume* the held-out oracle — stage 7's
optimize loop must **not** (rule 3) — so a stage must *produce* it: **stage 5b
"curate/extend the held-out oracle to cover the current capability surface,"** owned by a
human, triggered on **capability change** (not just model/SDK drift). Its gate is a
**coverage-delta 🚦: block release when the agent's capability grew but oracle coverage
didn't** — otherwise a maintenance pass (stage 6) that expands the agent passes green while
testing only the *old* behavior. Two levels, because presence-coverage alone is not enough:
- **Presence (measurable today):** capability *surface* = the tool/skill/route set from the
  recipe + manifest (a diff detects additions); coverage = "≥1 oracle scenario invokes every
  manifest tool" — instrumentable via the shipped `tool_recall.py`.
- **Behavior (the harder half):** a capability can deepen *inside* an existing tool (new
  parameter/path) with **no** new named surface, so presence-coverage won't fire. Tie the
  gate to the **scenario-class** enumeration (templates already enumerate the space):
  a new capability must add a new scenario *class*, not merely hit an existing tool once.
Visibility of gaps is necessary but not sufficient; the coverage-delta gate makes it real.

**Coverage discipline:** templates enumerate the scenario space explicitly; hold a dedicated
**adversarial/edge bucket** (the committed `phishing_fixture.json` is the pattern) so
safety-critical cases aren't diluted by the easy mass; track which scenario classes the
corpus covers so gaps are *visible* — and gate on the coverage-delta above so they aren't
silently untested.

## 6. Ship-half rigor the factory must preserve (do not reinvent, do not lose)

**The ship half is THREE lanes, not one — and one is already generalized:**

| Lane | Pipeline | Status |
|---|---|---|
| **Wheel → PyPI** | `publish_agents.yml`: discover agents from `setup.py` → wheel matrix → **one manual approve gate** ("publish" environment) → **per-agent PyPI OIDC trusted publishing** (`gaia-agent-*`) | **already generalized, per-agent** |
| **Frozen-binary sidecar → Hub R2 + npm** | `release_agent_email.yml` + `packaging/*` (the rigor analyzed below) | **email-only — this is M0's actual work** |
| **C++ static binaries** | `build_agents.yml`: cross-compile matrix → binary + `gaia-agent.yaml` + checksums | generalized for `cpp/` examples |

Two consequences the plan must absorb: (a) **M0 = generalize the *sidecar* lane
specifically** — the wheel lane needs orchestration, not generalization; (b) the wheel
lane's *manual-gate + per-agent OIDC* is a **shipped validation of §2.5/§2.6's model** —
human-gated publishing with per-agent trusted publishers isn't aspirational, it's how
`publish_agents.yml` works today. (The Hub's PR-route curation — "review earns the trust
tier," `docs/guides/hub-publishing.mdx` — is the same human-gate policy at the ecosystem
level.)

The sidecar pipeline encodes hard-won rigor. The factory orchestrates it and keeps every
property:

- **Multi-platform matrix + "assert required platforms present"** (stage 11) — a partial
  build never publishes.
- **Cross-OS-version compatibility** (stage 12) — a binary built on a newer OS is verified
  on an older one; catches glibc/macOS-SDK regressions users would hit.
- **Real-hash lock regeneration** (stage 15) — `binaries.lock.json` is rewritten from the
  **actually-published** artifacts, so the integrity manifest reflects reality, not a
  pre-computed guess.
- **Multi-component product** (stage 14) — the product is *not one binary* (§7): the npm
  tarball carries client + docs + lock, and binaries live on R2. ⚠️ The single **whole-package
  zip is currently DISABLED** in the live workflow (`if: false` — the ~177 MB all-platforms
  zip hits Cloudflare's edge 413 limit; revive via presigned-to-R2 or per-platform zips), so
  treat it as *aspirational*, not existing rigor.
- **npm OIDC trusted publishing + provenance** (stage 15) — supply-chain provenance, not a
  bare signature.
- **Post-publish edge verification** (stage 17) — the #1655 discipline: verify the thing a
  *user actually downloads from the CDN* fetches + verifies, not just that CI built it.
- **Release governance** (stage 16) — cut-from-main assertion, publish-token gates, and
  **version single-source-of-truth** via `stamp_version.py --check` (pyproject/version.py/
  package.json/docs must agree before publish).

## 6.5 Recovery — a passed-but-broken release must be revertible

Gates reduce but never eliminate escapes: an agent can clear the held-out oracle (stage 13)
and still fail in the wild. **There is no rollback path today** (verified — all fail-forward).
Recovery must use the levers the real infra actually supports — published npm tarballs + R2
objects are **immutable** (npm unpublish blocked after 72h; R2 append-only), and the Hub
Worker has **no delete/yank/rollback route** (`catalog.ts` serves `latest = max-semver`).
So "roll the catalog back" is *not* a thing; the workable levers are:

- **Roll forward (the real recovery):** re-publish the last-good content at a **higher
  semver** — since the catalog always serves the highest version, this is how you displace a
  bad `latest`. (Deprecation only *flags* a version; it does not redirect installs.)
- **Redirect new installs immediately:** move the npm **`latest` dist-tag** back to the
  last-good version (`npm dist-tag add @amd-gaia/agent-email@<good> latest`) — the dist-tag,
  **not** deprecate, is the npm redirect lever.
- **Pin-previous (fast path for a known-good consumer):** re-install the last-good **version**
  (`gaia agent install email@<good>` / `npm i …@<good>`); its *immutable bundled*
  `binaries.lock.json` still points at present R2 binaries, so no rebuild — the version pin
  is the lever, not "re-pinning the lock" (the lock is fixed content of each version). Needs
  the Hub-CLI install path to support pinning a **non-latest** version.
- **The escape becomes an oracle case:** the failing real scenario is curated into the
  held-out oracle (stage 5b) so it can never re-ship — recovery *feeds* M2.

*True catalog rollback (skip a `yanked[]` version in `latestVersion()`) is **net-new Worker
work**, not an existing capability — scope it if roll-forward isn't enough.*

**Versioning is a factory decision across TWO independent axes** — `stamp_version.py` today
only *propagates* a hand-set version, and it deliberately keeps them separate:
- the **contract/API version** (`API_VERSION` == `SCHEMA_VERSION`) — what the runtime's
  version guard (§0.15) actually checks; a contract-breaking change must bump **this**;
- the **package semver** (distribution) — a build/feature bump.
A package-major alone does *not* keep the runtime guard honest unless `API_VERSION` is also
bumped; the factory must decide both from the change's contract impact.

## 7. The product is multi-component, not a single binary

A shipped agent = **R2 platform binaries + the npm tarball (TS client `fetch`/`lifecycle`
+ the five docs + bundled `binaries.lock.json` + examples/tests)**. The factory produces and
ships all of it as one versioned release; "the binary" is one component, not the product.
(This is why stage 9 owns docs and the lock is bundled per-version.) A *single* whole-package
zip would be convenient but is currently disabled (§6) — the shipped multi-component form is
the npm tarball + R2 binaries, not one zip.

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
**Claude Code in CI** (`claude.yml`, `claude-run.yml`) + skills + memory — note `claude.yml`'s
**`auto-fix`** (issue → locatable bug → branch → PR + test steps) is a *shipped, scoped
instance of the dev-half loop*, real evidence M3 isn't vapor · the **eval
framework** (`eval/{runner,benchmark,scorecard,analyze_failures,audit}.py`, baselines,
`--fix`, synthetic corpus, `email_scorecard_refresh.yml`) · **`gh`** issues/PRs + review
bot · **`code_index`** over the live SDK · the **entire ship half**
(`release_agent_email.yml`, `packaging/{freeze,gen_binaries_lock,gen_package_files,
gen_scorecard,stamp_version,smoke_test,publish_to_r2}.py`, Hub Worker, npm OIDC, edge
verify) · the **wheel lane** (`publish_agents.yml` — already per-agent, gated, OIDC; §6) ·
**`gaia agent init`** (starter-package scaffold, python|cpp) + **`gaia agent publish`**
(direct publish CLI) · **`gaia-agent.yaml`** (the per-agent manifest 18 agents already
carry — the recipe's base, §1.5) · two **operational playbooks already written as Claude
skills**: `agent-hub-release` (onboard/cut a sidecar release — *M0's runbook*) and
`adding-eval-scorecard` (adapter → real scorecard → README link → release gate — *M2's
per-agent adoption runbook*) · git worktrees.

**Net-new (the stitch — the real work):**
1. **The dev-half orchestrator** — integrate the GAIA coder + an Agent-SDK/Claude-Code
   loop + skills + memory into one driver (`gaia factory <recipe>`) in an isolated live-SDK
   worktree, producing a merged, evaluated agent on `main`.
2. **The recipe / agent-spec input** (§1.5) — the declarative intent the orchestrator
   plans from → also the source of the manifest.
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
| **M0** | **Generalize the *sidecar* lane** (recipe-driven, per-agent) | the *deterministic packaging spine* — stages **9, 11, 12, 14, 16** — for *any* agent: turn `release_agent_email.yml` + `packaging/*` into a reusable, recipe-parametrized pipeline. **The wheel lane needs no generalizing** — `publish_agents.yml` is already per-agent + gated + OIDC (§6); M0 orchestrates it and generalizes the *sidecar* lane (M1 adds provenance stages 10/15/17; M2 the trustworthy gate 13). The `agent-hub-release` skill is the existing runbook to automate | **Easiest — but not risk-free**: deterministic/no-LLM, yet per-agent OIDC-publisher provisioning is **supply-chain work**, and the ship half exists *only for email* (a 2nd agent may lack `packaging/*` parity) | **Prove on a *second, non-email* agent** (browser/analyst) — the empirical reuse-vs-rewrite *and* parity check; includes per-agent OIDC publisher provisioning, tags, R2 prefixes |
| **M1** | **Provenance + edge-verified releases** | manifest emit (stage 10) · signing + source-hash + SDK-commit provenance (stage 15) · post-publish edge verify (stage 17) | **Easy** — mechanical (*but the manifest schema lives in unmerged #1913*) | integrity/traceability (not "reproducibility," §11.5); docs-in-sync becomes a hard gate |
| **M2** | **Independent eval oracle + noise-aware gate** | the *trustworthy* eval gate: a **human-curated, held-out** ground-truth set per agent + fixed safety floors; gate = **fixed bar + non-inferiority band** `≥ prev − k·stdev` over `n_runs` repetitions (§5 — *not* LCB-vs-moving-baseline, §11.7). **Adoption reality: 1 of 18 agents has a scorecard today** (email) — M2 is 17 harness→payload adapters + oracles; the `adding-eval-scorecard` skill is the per-agent runbook | **Medium** — mostly discipline, but the oracle is **human judgment the factory does NOT automate** | **Prerequisite for everything generative** (M3–M4) — without an independent oracle the gate is self-certification (§11.5 #1) |
| **M3** | **Assisted dev automation** | the *mechanical* dev stages: scaffold (build on the existing `gaia agent init` starter-package generator + Builder templates), tool/skill/MCP wiring, synthetic-data gen, the eval-optimize (`--fix`) loop, PR authoring | **Harder** — net-new agentic coding (*needs `origin/coder` merged to main*), human-in-the-loop | human still owns **scope, spec, the oracle (M2, curator ≠ spec author), PR-approve, ship**; the GAIA coder + Claude Code loop assist, they don't decide |
| **M4** | **SDK-delta maintenance loop** (stage 18 — the keystone) | on an SDK delta that regresses an agent (measured on M2's held-out oracle via the §5 noise-band gate), re-run M3+M0 for that agent | **Hardest** — the differentiator *and* the highest risk | **Last, and only after M2.** Built-in: (a) **serial-eval throughput cap** (one eval/backend — CLAUDE.md), size cadence against it; (b) SDK changes ship via **PR + tag through the existing release process**, gated by the **human approve/deny at the SDK-release checkpoint over a pre-cut all-agent blast-radius dry-run** (§2.5) — approve the radius, not the tag; the all-agent re-eval is the *intended* regression net |

**Reading the order:** M0–M1 ship *any* agent reproducibly-packaged and provenance-verified
with **no LLM in the loop** — pure, high-value CI. M2 buys the trust bar. Only then does
M3 add agentic authoring (human-gated), and M4 the continuous maintenance loop. The moat
(M4) is *last* because it depends on M2's oracle and is the unsolved-research part — you do
not build the loop before you can trust the gate it runs on.

**Merge-prerequisites (not laundered as "exists"):** M1 depends on **#1913** (the manifest
schema §0.28); M3–M4 depend on **`origin/coder`** (the GAIA coder) merged to `main`. Both are
unmerged today (§11.5) — so the value that lands with *zero* external merge-dependency is M0
alone, and the differentiator (M4) sits behind two branch merges *and* M2's oracle work.

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
  **a registered npm publisher per agent** — substantial, not "just drive it." And npm's
  trusted-publisher subject matches the **entry-point** workflow file GitHub triggered, *not*
  a `workflow_call` reusable — so N thin per-agent caller workflows still need **N publisher
  registrations** (unless one caller publishes all agents). **M0 proves it empirically on a
  second, non-email agent.**
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

## 11.6 Second adversarial pass — what the fixes did and didn't close

A second review pressure-tested the §11.5 fixes. Results, folded into the sections above:

- **Closed:** silent-degradation (§2.5 non-convergence fails loudly); the *memorization* axis
  of eval circularity (the train/held-out split, §5.5).
- **Was half-closed — now hardened:** the *self-certification* axis. "Different provenance"
  had been defined against the LLM implementer (trivially true); the real circular source is
  the **spec author**. §5.5 now requires the oracle curator ≠ spec author, adds a
  **human-judged real-data label slice** (by-construction labels alone just encode the
  author's definition as truth), and adds an explicit **oracle-production/maintenance stage
  (5b) with a coverage-delta gate** so the oracle can't silently rot as the agent grows
  (previously *no stage produced it*).
- **Was not closed — now fixed:** the **SDK-release gate fired blind to blast radius**
  (approving a tag before the N-agent fan-out is computed just relocates the rubber stamp).
  §2.5 now feeds that gate a **pre-cut all-agent dry-run** — approve the radius, not the
  tag — and disambiguates SDK-release (M4-only) from agent-publish (stage 16).
- **New items surfaced & addressed:** the LCB/k-runs gate is now in the canonical spec
  (§5, stage 13), not only the critique; §10 names the **unmerged-branch prerequisites**
  (#1913, `origin/coder`) instead of laundering them as "exists"; **non-convergence has a
  capacity cost** (escalations are un-eliminated manual maintenance → a mis-sized-cadence
  signal, §2.5); and **🔒 seed-from-real is a recurring PII intake** needing a scrub/consent
  gate (§5.5, escalated to @kovtcharov-amd).
- **Residual, by design:** the human gates' realness still depends on reviewers not
  rubber-stamping; the factory reduces this to *deciding over evidence* (the blast-radius
  dry-run, the failing scorecard) rather than reviewing blind, but cannot eliminate it.

## 11.7 Domain-review corrections (eval methodology + release mechanics)

Two specialist reviews (eval-engineer, release-manager) checked the densest sections against
the real code and found factual errors the generalist passes missed. Corrected in-place:

- **Eval gate was statistically unsound → matched to the shipped gate.** The draft's
  `LCB(candidate) ≥ prev_release_point` with a moving baseline is a **ratchet** (the accepted
  noisy mean becomes the next floor → nothing passes within ~2–3 releases). Now a **fixed
  bar + fixed safety floor** with the previous release as a **non-inferiority band**
  `≥ prev − k·stdev` — exactly what `scorecard_gate.py` already does (§5, §1.5, stage 13).
- **`k` overload fixed:** `k` = stdev-band multiplier (default 1.0), `n_runs` = repetitions
  (3) — the draft's "k=5 runs" both collided with the real `--regression-k` and silently 5×'d
  the serial-eval cost.
- **Safety floor is a point tripwire, not a CI guarantee** (0.95 needs ≈59 sized instances;
  #1437 uses 0.90 on the needs-attention axis); **split by source/thread-id** (near-duplicate
  leakage); enforce oracle-separation as a **harness mechanism** (§5.5).
- **Recovery rewritten to the real infra (§6.5):** the Hub Worker has **no rollback route**
  (roll *forward*), npm redirect is the **dist-tag** (not deprecate), pin-previous is a
  **version re-install** (the lock is immutable per version), tag-revert un-ships nothing.
- **Two-axis versioning:** contract `API_VERSION` (drives the §0.15 guard) *and* package
  semver — kept separate by `stamp_version.py` (§6.5).
- **"Exists" corrected:** the whole-package **zip is disabled** (Cloudflare 413) — the shipped
  multi-component product is the npm tarball + R2 binaries (§6, §7, stages 14/17); §2.6's
  publish-secret gate is *built*, but merge/tag gating needs branch/tag-protection rulesets +
  an orchestrator token without `contents: write`.

## 12. Open decisions (need sign-off)

1. **Orchestrator substrate** — Claude Code (skills + memory, already in CI) + GAIA coder
   vs. a custom Anthropic Agent-SDK build. *Rec:* start on Claude Code + GAIA coder (Claude
   Code is in CI today; the coder needs `origin/coder` merged — §10 prerequisites); evaluate
   a custom build if the loop needs tighter control.
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
