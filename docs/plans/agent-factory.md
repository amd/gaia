# The Agent Factory — the developer lifecycle, automated (against the *live* SDK)

> **Sibling to the runtime architecture.** [`agent-ui-agent-capabilities-plan.md`](agent-ui-agent-capabilities-plan.md)
> §0 designs how agents **run** (out-of-process sidecars + a custodian daemon). This
> doc designs how agents are **built and maintained** — the factory. The two meet at
> the artifacts the runtime consumes: the **manifest** (§0.28), the **Hub** (§0.5), and
> **signing** (§0.24). The factory *produces* those; the runtime *enforces* them.
>
> **Dependency:** the `§0.x` cross-references live in
> `agent-ui-agent-capabilities-plan.md §0`, landed on `main` via the **Agent UI v2
> runtime PR (#1913, merged)** — this doc is the sibling half and assumes it as context.

**Reading map** (§ jump-list):
- **Why & what** — §0 thesis (SDLC automated) · §1 the live-SDK keystone · §1.5 the *recipe* (the one authored input)
- **Engine & governance** — §2 agentic-coding engine · §2.5 human approve/deny gates · §2.6 the factory's *own* least-privilege
- **The pipeline** — §3 the lifecycle stages · §4 docs-as-output · §5/§5.5 eval gate + synthetic-data discipline · §6/§6.5 ship rigor + recovery · §7 multi-component product · §8 runtime seam
- **Plan & honesty** — §9 exists-vs-net-new · §10 milestones M0→M4 (easiest→hardest) · §11 distinctiveness · §11.5 review record (all findings → resolutions) · §11.6 ecosystem gap analysis (OpenClaw/Hermes) · §12 open decisions

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
  `hub/agents/email/python/packaging/*` already do multi-platform freeze, cross-OS-version
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
already carry a per-agent manifest (`hub/agents/<id>/python/gaia-agent.yaml`) with
`id/name/version/models/python.entry_module/dependencies/requirements.platforms/interfaces`
— the recipe **extends that file** with the factory-specific fields (purpose, eval block,
connectors/scopes, egress, trust tier, gates, freeze targets) rather than inventing a
second per-agent YAML that would drift against it. Shape (factory fields added to the
existing schema):

```yaml
id: email
purpose: "Triage, search, and organize a personal Gmail/Outlook mailbox, locally."
model:      { llm: Gemma-4-E4B-it-GGUF, min_ctx: 65536 }   # → manifest.requiredModels (matches MODELS[…].min_ctx_size)
tools:      [rag, file_io]                                 # KNOWN_TOOLS mixins
skills:     [triage-inbox, follow-up-tracking]             # SKILL.md (skill-format; illustrative names)
mcp:        [gmail, google-calendar]                       # MCP servers (tool-loader)
connectors: { google: [gmail.modify, calendar.events] }    # → manifest.oauthScopes (least-priv; catalog scope names)
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
   │      ├─► GAIA coder (gaia-agent-code on main; newer CoderAgent on origin/coder)  │
   │      ├─► skills: brainstorming · writing-plans · TDD · debugging · review        │
   │      ├─► memory: prior spec, eval baselines, past failures, SDK-change history    │
   │      └─► tools: git worktree · gh (issues/PRs) · gaia eval · packaging line       │
   │  runs inside an ISOLATED WORKTREE clone of the live GAIA repo (HEAD, not a snap)  │
   └──────────────────────────────────────────────────────────────────────────────────┘
```

The GAIA coder does the *code*; Claude Code / the Agent SDK does the *orchestration,
judgment, spec, review, and eval loop* around it — integrating the two is the core
net-new engineering. Memory is load-bearing: each maintenance pass recalls the agent's
spec, baseline, failure modes, and *what changed in the SDK since last build*.

## 2.5 Human-in-the-loop — per-stage approve/deny (mirrors the agent model)

The factory is a **supervised** pipeline, not a fully-autonomous one. Every risky stage
carries an **approve/deny gate** — the *same* confirmation model the agents themselves use
(runtime §0.4 confirmation gate, §0.34 autonomy *policy layer* — a policy engine with
graduated levels as one component, not a bare enum of levels). A human (or a policy) accepts or
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
| 5b | **Held-out oracle** — curate/extend to cover the *current capability surface* (§5.5) | **human, NOT the factory** (curator ≠ spec author) | `quality_gate_thresholds.json` (*committed*); a *committed* oracle set is net-new — `ground_truth.json` today is derived + gitignored (§5.5) | 🚦 coverage-delta |
| 6 | **Implement** — write agent code **against the live SDK** | GAIA coder + TDD | `gaia-agent-code` `CodeAgent` *(on main)*; newer `src/gaia/coder/` `CoderAgent` *(unmerged, `origin/coder`)* + `agents/base/*` | compiles/lints |
| 7 | **Eval + optimize** 🚦 | eval → analyze failures → repair → re-eval | `gaia eval agent [--fix]` · `scorecard.py` · `analyze_failures.py` (*exists*) | scorecard ≥ bar |
| 8 | **PR** 🚦 | open PR(s) into the codebase (agent code — *and SDK changes it needs*) | orchestrator + `gh` (PR authoring) | `claude.yml` PR-review bot + CI (*exists*) | review + CI green |

**Back half — Ship (orchestrate the existing rigorous pipeline; §6 details):**

| # | Stage | What it does | Component (status) | Gate |
|---|---|---|---|---|
| 9 | **Docs: generate SCORECARD, publish the rest** | `SCORECARD.md` is CI-generated; README/SPEC/SKILL/CHANGELOG are authored/committed and *published* (not emitted), kept in sync (§4) | `gen_scorecard.py` (gen) · committed docs · CLAUDE.md sync rule (*exists, manual sync*) | docs-in-sync |
| 10 | **Manifest emit** | derive `manifest.json` (§0.28) from recipe + compose/eval outputs | net-new emitter (schema landed with #1913) | schema-valid |
| 11 | **Multi-platform freeze** 🚦 | freeze win32-x64 · darwin-arm64 · darwin-x64 · linux-x64; smoke-test each; **assert required platforms** | `packaging/freeze.py` · `smoke_test.py` · `release_agent_email.yml` (*exists*) | smoke + platforms |
| 12 | **OS-compat verify** 🚦 | run a newer-OS-built binary on an **older OS** — *darwin-x64 leg only today* (macos-26 build → macOS 15); other platforms have no older-OS verify | `release_agent_email.yml` verify job (*exists, darwin-x64*) | older-OS smoke |
| 13 | **Eval gate** 🚦 | on the **held-out oracle** (§5.5); **fixed** acceptance bar + safety floor (#1437); prev-release as a **non-inferiority band** `≥ prev − k·stdev` (§5) | `scorecard_gate.py` (*exists*) | floors + band ship today for email (#1894); per-agent adoption + oracle = **M2** |
| 14 | **Package assemble** | npm tarball (client + docs + lock) + R2 binaries + `package-files.json`. ⚠️ single whole-package **zip DISABLED** (`if: false`, Cloudflare 413) | `gen_package_files.py` · npm build (*exists; zip disabled*) | manifest complete |
| 15 | **Sign + real-hash lock + provenance** 🚦 | SHA-256, **npm OIDC trusted-publishing provenance**, regenerate `binaries.lock.json` with **real hashes**, embed provenance (spec · scorecard · SDK commit) | `gen_binaries_lock.py` · npm OIDC (*exists; signing partial*) | signature/provenance |
| 16 | **Publish** | cut-from-main + token gates; POST `/publish` to Hub Worker; npm publish; **redeploy catalog site** | `publish_to_r2.py` · Hub Worker · website deploy (*exists*) | governance gates |
| 17 | **Post-publish edge verify** 🚦 | fetch **every published object via the real fetch CLI** at the CDN edge (#1655 "user's real state") — *per-object verify works; the zip-verify leg is disabled with the zip (§6)* | fetch-verify steps (*exists; zip leg off*) | real download OK |
| 18 | **Maintain (continuous)** 🚦 | on an SDK delta that regresses the agent's eval, re-run the affected stages 1–17 for the delta (5b only when the capability surface changed, §5.5) | net-new trigger + the above | eval stays ≥ bar |

Stages 9–17 already run in CI (`release_agent_email.yml`, `build_agents.yml`,
`publish_agents.yml`, `email_scorecard_refresh.yml`) — **the factory generalizes them
from one-agent, human-kicked jobs into a continuous, orchestrated, per-agent line.**

## 4. Docs are a first-class shipped output (not an afterthought)

The shipped product (`hub/agents/email/npm/`) carries **five doc surfaces** —
`README.md` (integrator) · `SPEC.md` (technical) · `SKILL.md` (AI-assistant) ·
`CHANGELOG.md` (version) · `SCORECARD.md` (eval results) — plus `spec_html`/OpenAPI. Per
CLAUDE.md, *a functional change must update **every** doc*, or the package ships
self-contradicting documentation. So the factory needs a **docs generate-and-sync stage**
(stage 9). Timing: **M0 ships stage 9 as-is** (SCORECARD generation + publishing the
committed docs); the *sync-check automation* that makes docs-in-sync a **hard gate is an M1
deliverable** — doc drift is a real published-defect class, but the checker doesn't exist
yet and M0 shouldn't block on building it.

**The scorecard is a shipped, refreshed artifact — not just a gate.** `SCORECARD.md`
publishes *with* the agent and is refreshable on demand (`email_scorecard_refresh.yml`
is `workflow_dispatch`-only today — a scheduled cadence is a one-line upgrade), so the
eval result is both (a) the stage-13 gate and (b) provenance the product carries
and keeps current.

## 5. The eval gate is data-driven, not "≥ a committed baseline"

The gate has three parts — and the construction matters, because a naive "beat last
release" gate is statistically unsound (an eval-methodology review caught the original
draft reinventing the shipped gate in a strictly worse form):

- **Two FIXED hard gates (no drift):** a **fixed acceptance bar** and **fixed safety
  floors** — constants, exactly as #1437's `--min-aggregate` / `--min-urgent-recall`. A
  fixed bar is ratchet-free. (The constants live in the committed fixture —
  `quality_gate_thresholds.json` — and the CI wiring that passes the flags; the gate's
  flags default to off, so enforcement is a property of the workflow, not the script.)
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
  the serial-eval cost (§11.5). *For email, today's shipped gate already runs BOTH the
  fixed floors AND the `− k·stdev` band (#1894 — `_within_one_stdev` in
  `scorecard_gate.py`); the **M2 upgrade** is adopting that gate per-agent (17 more
  adapters + oracles, §10), not inventing the band.*
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
- **An injection-resistance floor, alongside the capability floors (§11.6 gap 4).** Every
  major 2026 skill-ecosystem incident was *content-borne* (instructions the agent itself
  relays; natural-language malice static scanners admit they miss) — and signing proves
  *who published*, never *what the text does to the model*. So the oracle carries an
  **adversarial bucket** (the committed `phishing_fixture.json` pattern, §5.5) whose recall
  is a **fixed tripwire like #1437's**, and anything the recipe pulls in as prompt-visible
  text (skills, MCP descriptions) is content-scanned at stages 5–6 — scan for the known
  patterns, *gate* on the behavioral floor.
- **Refreshed independently** so the shipped scorecard stays honest as models/SDK move.

The factory's job is to *run this gate on every dev-half output and every SDK-delta
rebuild* — the eval isn't a one-time ship check, it's the continuous regression net.

## 5.5 Synthetic data generation — seed-from-real, labels-by-construction, strict split

Stage 5 is where the review's circularity bites hardest: if the factory generates the eval
data *and* the agent *and* the labels, a green score is self-certification. GAIA's existing
practice already avoids the naive trap — the email corpus is **seeded from a real,
PII-scrubbed public-benchmark corpus** (`vendor_corpus_seed.jsonl` →
`generate_mbox.py`/`select_vendor_subset.py`), its **ground truth is derived by
construction from that committed seed** (`ground_truth.json` itself is regenerated on
demand and gitignored — the seed is the committed source of truth), and `pdf_document_generator.py`
generates from **templates** — so the factory must generalize *that discipline*, not just
"ask an LLM for test cases." Three rules:

1. **Seed from real, anonymized data — don't hallucinate the distribution.** Synthetic
   *volume* over a *real* distribution (the vendor-seed pattern) reflects inputs users
   actually send, not what a model imagines they send. Refresh the seed to catch drift.
   **🔒 Because seed refreshes are a *recurring* intake that may draw on real user data
   (today's seed is a scrubbed public benchmark; a future refresh may not be), a
   PII-scrub + consent/provenance gate 🚦 is mandatory *before* any seeded corpus is
   committed** — "anonymized" is a requirement with an owning gate, not an adjective
   (escalated to @kovtcharov-amd; see §11.5).
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
     versioned, committed (today only `quality_gate_thresholds.json` is committed;
     `ground_truth.json` is derived + gitignored, so *committing* the oracle is new
     discipline, not current practice), curated by **a human who is *not* the spec author** (the circular source is the person
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
  build never publishes. (darwin-x64 is explicitly *best-effort* in the matrix: if its
  older-OS compat check fails, its lock entry is dropped and the release ships without it.)
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
  a Hub-CLI install command *at all* — today the only hub-install path is the Agent UI
  (`POST /api/agents/setup`); the CLI command and non-latest version-pinning are both net-new.
- **The escape becomes an oracle case:** the failing real scenario is curated into the
  held-out oracle (stage 5b) so it can never re-ship — recovery *feeds* M2.

True catalog rollback (skip a `yanked[]` version in `latestVersion()`) is **net-new Worker
work**, not an existing capability — and it is **scheduled for M1** (§11.6 gap 5).
Roll-forward answers *quality* escapes, but a ClawHavoc-class **malicious** package needs a
registry **yank** plus a **daemon-side deny-list check**: a higher semver does not stop
existing installs, and the runtime's anti-rollback + TOFU pinning otherwise *entrenches* a
compromised publisher. Revocation is signing's other half; shipping M1's signing without it
is half a trust story.

**Versioning is a factory decision across TWO independent axes** — `stamp_version.py` today
only *propagates* a hand-set version, and it deliberately keeps them separate:
- the **contract/API version** (`API_VERSION` == `SCHEMA_VERSION`) — what the runtime's
  version guard (§0.15) actually checks; a contract-breaking change must bump **this**;
- the **package semver** (distribution) — a build/feature bump.
A package-major alone does *not* keep the runtime guard honest unless `API_VERSION` is also
bumped; the factory must decide both from the change's contract impact.

## 7. The product is multi-component, not a single binary

A shipped agent = **R2 platform binaries + the npm tarball (TS client `fetch`/`lifecycle`
+ the five docs + bundled `binaries.lock.json`)** — examples/tests live in-repo only; the
npm `files` whitelist excludes them from the tarball. The factory produces and
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
  └───────────────────────────────┬─────────────────────────────────────────┘            │ enforces grants,
                    emits: signed multi-component product + manifest.json (§0.28)          │ version, signature
                           + provenance (spec · scorecard · SDK commit)                    │
                                   ▼                                                        │
                          ┌──────── Agent Hub (§0.5) — the conveyor ─────────┐──────────────┘
                          └──────────────────────────────────────────────────┘
```

## 9. Component inventory — exists vs net-new

**Exists (orchestrate, don't rebuild):** the **GAIA coder** (`gaia-agent-code` `CodeAgent`
on main; a newer `src/gaia/coder/` `CoderAgent` sits unmerged on `origin/coder`) ·
**Claude Code in CI** (`claude.yml`, `claude-run.yml`; skills + memory exist in *local*
sessions only — CI wires neither yet, that wiring is part of net-new #1) — note `claude.yml`'s
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
| **M1** | **Provenance + edge-verified + revocable releases** | manifest emit (stage 10) · signing + source-hash + SDK-commit provenance (stage 15) · post-publish edge verify (stage 17) · **catalog yank + daemon deny-list** (§6.5, §11.6 gap 5 — revocation is signing's other half) | **Easy** — mechanical (manifest schema landed with #1913; yank is small net-new Worker + daemon work) | integrity/traceability (not "reproducibility," §11.5); docs-in-sync becomes a hard gate |
| **M2** | **Independent eval oracle + noise-aware gate** | the *trustworthy* eval gate: a **human-curated, held-out** ground-truth set per agent + fixed safety floors; gate = **fixed bar + non-inferiority band** `≥ prev − k·stdev` over `n_runs` repetitions (§5 — *not* LCB-vs-moving-baseline, §11.5). **Adoption reality: 1 of 18 agents has a scorecard today** (email) — M2 is 17 harness→payload adapters + oracles; the `adding-eval-scorecard` skill is the per-agent runbook. **M2 also owns stage 5b** (oracle curation/extension) **and its coverage-delta gate** (§5.5) | **Medium** — mostly discipline, but the oracle is **human judgment the factory does NOT automate** | **Prerequisite for everything generative** (M3–M4) — without an independent oracle the gate is self-certification (§11.5 #1) |
| **M3** | **Assisted dev automation** | the *mechanical* dev stages: scaffold (build on the existing `gaia agent init` starter-package generator + Builder templates), tool/skill/MCP wiring, synthetic-data gen, the eval-optimize (`--fix`) loop, PR authoring | **Harder** — net-new agentic coding (*needs the `src/gaia/coder/` `CoderAgent` ported from `origin/coder` — ~420 commits behind main; the orchestration/validators `CodeAgent` it builds on already ships as `gaia-agent-code`*), human-in-the-loop | human still owns **scope, spec, the oracle (M2, curator ≠ spec author), PR-approve, ship**; the GAIA coder + Claude Code loop assist, they don't decide |
| **M4** | **SDK-delta maintenance loop** (stage 18 — the keystone) | on an SDK delta that regresses an agent (measured on M2's held-out oracle via the §5 noise-band gate), re-run M3+M0/M1 for that agent | **Hardest** — the differentiator *and* the highest risk | **Last, and only after M2.** Built-in: (a) **serial-eval throughput cap** (one eval/backend — CLAUDE.md), size cadence against it; (b) SDK changes ship via **PR + tag through the existing release process**, gated by the **human approve/deny at the SDK-release checkpoint over a pre-cut all-agent blast-radius dry-run** (§2.5) — approve the radius, not the tag; the all-agent re-eval is the *intended* regression net |

**Reading the order:** M0–M1 ship *any* agent reproducibly-packaged and provenance-verified
with **no LLM in the loop** — pure, high-value CI. M2 buys the trust bar. Only then does
M3 add agentic authoring (human-gated), and M4 the continuous maintenance loop. The moat
(M4) is *last* because it depends on M2's oracle and is the unsolved-research part — you do
not build the loop before you can trust the gate it runs on.

**Merge-prerequisites (not laundered as "exists"):** M1's manifest schema (§0.28) landed
with **#1913 (merged)** — M0 *and* M1 now carry no external merge-dependency. M3–M4 still
depend on the **`src/gaia/coder/` `CoderAgent`** (unmerged on `origin/coder`, ~420 commits
behind `main` — a port, not a fast-forward). The differentiator (M4) sits behind that
port *and* M2's oracle work.

**M0 day-1 prerequisites (settle before the first line of workflow code):**
1. **The npm-OIDC publisher topology** — npm's trusted-publisher subject matches the
   *entry-point* workflow filename, so it's either **N thin per-agent caller workflows = N
   publisher registrations**, or **one caller workflow that publishes all agents = 1
   registration but a shared blast radius**. This is a supply-chain decision (§12.6), and it
   *is* the architecture of the workflow refactor.
2. **Recipe schema v1** — §1.5 is illustrative ("Shape:"); M0 is recipe-driven, so a
   **versioned field list + validation contract** for the fields M0 consumes is an **M0
   deliverable** (the *manifest* schema defers to #1913; the *recipe* schema cannot).
3. **Pick the second agent + run its packaging-parity audit** — name browser or analyst and
   inventory its freeze/smoke/R2/npm state vs email's `packaging/*` before scoping, since a
   second agent may have *none* of it.

## 11. Distinctiveness (brief, honest)

Not skill *distribution* (OpenClaw: near-zero-friction SKILL.md publishing, agent-drafted
skills behind an operator review, no signing, no eval), not skill *learning* (Hermes: the
agent self-authors skills from its own runs; rot managed by usage statistics, never by
execution tests), and not ordinary CI/CD — an **automated AI software-engineering pipeline
that builds *and maintains* agent products against a living SDK**, using the same flow a
human team uses (issues · specs · reviews · **data-driven evals** · PRs ·
**provenance-verified releases**). Both ecosystems lack exactly what the factory builds
(eval gates, provenance, maintenance-by-contract); the factory lacks what they have
(velocity, community authorship, a learning loop) — §11.6 turns that comparison into
scoped amendments. The moat is the *maintenance-against-a-moving-SDK* loop + the eval-gate
+ real PRs into the codebase. **Honest caveat:** the ship half exists and is rigorous, but
the dev-half orchestrator + the SDK-delta loop are substantial net-new work; this is a
*potential* moat that must be built, and stage 18 is the hard part.

## 11.5 Review record — findings, resolutions, and where they live

The plan was hardened by six review passes (two adversarial architecture reviews, two
independent cold reads, an eval-methodology specialist, a release-mechanics specialist —
the specialists verified against the real code). Every correction is **absorbed into the
normative sections**; this table is the traceability record, one row per finding:

| Review | Finding | Resolution → lives in |
|---|---|---|
| Adversarial #1 | "Reproducible against an SDK commit" is a category error — LLM codegen isn't regenerable | SDK-commit-pinned + source-hashed = *traceability/integrity*, not regeneration → **§1** |
| Adversarial #1 | No independent eval oracle — factory wrote the agent, the corpus, and the bar (self-certification) | Human-curated held-out oracle; **curator ≠ spec author**; human-judged real-data slice → **§5.5, M2** |
| Adversarial #1 | "Orchestrate the ship half" overclaimed reuse — `release_agent_email.yml` is ~718 lines, email-hardcoded; npm OIDC bound to the workflow *filename* | M0 = generalize the *sidecar* lane, proven on a 2nd agent; publisher-topology decision → **§6, §10, §12.6** |
| Adversarial #1 | Maintenance-loop convergence hazard + serial-eval throughput ceiling (~18 agents, one Lemonade slot — the cost is *wall-clock*, not dollars) | Noise-band gate + one-eval-per-backend cap; cadence sized to it → **§5, M4** |
| Adversarial #1 | AI PRs into the shared SDK = blast radius contained only by a rubber stamp | Per-stage human gates; factory *may* PR+tag the SDK behind them → **§2.5** |
| Adversarial #2 | "Different provenance" was defined against the LLM implementer (trivially true); the real circular source is the **spec author** | Curator ≠ spec author as a hard M2 constraint → **§5.5** |
| Adversarial #2 | The oracle was consumed (stage 13) but *produced nowhere*, and rots as the agent grows | Stage **5b** (curate/extend) + the coverage-delta gate → **§3, §5.5, M2** |
| Adversarial #2 | The SDK-release gate fired *blind to blast radius* (approving a tag before the N-agent fan-out is computed) | Pre-cut **all-agent dry-run**; approve the radius, not the tag → **§2.5** |
| Adversarial #2 | Non-convergence handling had no capacity model — escalations are un-eliminated manual maintenance | Escalation checkpoint; sustained rate = mis-sized M4 cadence signal → **§2.5** |
| Adversarial #2 | 🔒 Seed-from-real is a **recurring PII intake** with no owning gate | PII-scrub + consent/provenance gate 🚦 on every refresh → **§5.5** (escalated to @kovtcharov-amd) |
| Adversarial #2 | Unmerged branches laundered as "exists" (`origin/coder`, #1913) | Named merge-prerequisites → **§10** (#1913 has since merged; the surviving dep is the `src/gaia/coder/` port) |
| Eval-engineer | `LCB(candidate) ≥ prev_point` with a moving baseline is a **ratchet** (locks out releases in ~2–3 cycles); also reinvented the shipped gate in a worse form | **Fixed bar + fixed floors + non-inferiority band** `≥ prev − k·stdev`, matching `scorecard_gate.py` → **§5, §1.5, stage 13** |
| Eval-engineer | `k` overloaded (real `--regression-k` = stdev-band multiplier, not run count) — conflation also 5×'d the serial-eval cost | `k` = band multiplier (default 1.0); `n_runs` = repetitions (3) → **§5, §1.5** |
| Eval-engineer | 0.95 safety floor unsized (95%-confidence needs ≈59 positives); wrong axis name | Point *tripwire* at 0.90 on the **needs-attention** axis (URGENT+NEEDS_RESPONSE); size or label → **§5** |
| Eval-engineer | Near-duplicate leakage — both corpora seed from the same pool/templates | **Split by source/thread-id**; disjoint templates; oracle isolation as a *harness mechanism* → **§5.5** |
| Eval-engineer | Coverage-delta was presence-only — capability can deepen *inside* a tool | Two-level gate: presence (via `tool_recall.py`) + **scenario-class** → **§5.5** |
| Release-manager | §6.5 recovery didn't hold against the real infra: Hub Worker has **no rollback route**; npm-deprecate doesn't redirect; tag-revert un-ships nothing; the lock is immutable per version | **Roll forward** + **dist-tag** + **version re-install**; catalog-yank = net-new Worker work → **§6.5** |
| Release-manager | Versioning conflated the package semver with the contract version | Two independent axes: `API_VERSION` (drives the §0.15 guard) *and* package semver → **§6.5** |
| Release-manager | The whole-package **zip is disabled** (`if: false`, Cloudflare 413) but was cited as existing rigor in four places | Marked disabled/aspirational; product = npm tarball + R2 binaries → **§6, §7, stages 14/17** |
| Release-manager | §2.6 lumped three different privilege mechanisms; only the publish gate is built | Environment-secret (built) / branch protection (to build) / tag ruleset (to build); orchestrator token without `contents: write` → **§2.6** |
| Cold read #1 | §5.5 listed stage 7 as an oracle *consumer* — the exact leak rule 3 forbids | Stage 13 consumes; stage 7 must not → **§5.5** |
| Cold read #1 | M0's stage range overclaimed (10/13/15/17 are M1/M2 work) | M0 = the deterministic spine 9, 11, 12, 14, 16 → **§10** |
| Cold read #2 | LCB terminology leaked back into §5/stage-13/M2 after the band correction; the prose appendices' present-tense claims drifted against each other | "Non-inferiority band" everywhere normative; the three prose appendices consolidated into this table → **§5, §3, §10** |
| Cold read #2 | Docs-in-sync gate timing ambiguous (M0 or M1); M0 prerequisites unstated (OIDC topology, recipe schema, 2nd-agent parity) | Hard gate lands in **M1**; M0 day-1 prerequisites listed → **§4, §10, §12.6** |
| Codebase passes | Invented `finalize` skill; `claude.yml` misattributed to issue-opening; under-credited existing infra | Removed/corrected; grounded on `gaia-agent.yaml`, the wheel lane, `gaia agent init`, the two runbook skills → **§1.5, §6, §9** |

**Residual, by design:** the human gates' realness still depends on reviewers not
rubber-stamping. The factory reduces this to *deciding over evidence* (the blast-radius
dry-run, the failing scorecard) rather than reviewing blind, but cannot eliminate it.

**Honest reframe (kept from the first review):** the stated moat — the maintenance loop —
is the plan's least-built, highest-risk, last-scheduled component. M0–M1 deliver real value
with **no LLM in the loop**; the research risk is quarantined to M3–M4 behind M2's oracle.

## 11.6 Ecosystem gap analysis — OpenClaw / Hermes (July 2026)

A deep dive on the two systems §11 name-checks, run to pressure-test the factory's
positioning. The short version of each (sourced July 2026):

- **OpenClaw** (~382K stars, ~2.7M weekly npm downloads, ~52K ClawHub skills in 7 months)
  proved that **friction removal is the growth engine**: a skill is a markdown folder on the
  open agentskills.io spec, publishing is one CLI command, the agent drafts its own skills
  behind an operator-review "Skill Workshop." It is also the cautionary tale: **ClawHavoc**
  (Feb 2026) put 341→824+ malicious skills (AMOS credential stealers) on the registry before
  VirusTotal scanning was bolted on reactively — and the project concedes scanning cannot
  catch natural-language malicious instructions. No author signing, no lockfile, no
  behavioral evals, sandboxing off by default, a one-click-RCE CVE, 40K+ exposed gateways;
  skill drift is *managed by a doctor command*, not prevented by contracts.
- **Hermes Agent** (Nous Research, ~211K stars) is the **learning loop**: the agent
  self-authors SKILL.md skills after successful multi-step tasks, errors, or user
  corrections; a **Curator** manages rot *statistically* (30d unused → stale, 90d →
  archived). Its soft underbelly is quality: **no execution-based validation anywhere in
  core** — nothing ever runs a learned skill against a test before it enters the library;
  the injection scanner is regex-only and demonstrably bypassable; outcome-driven skill
  evolution (GEPA) is bolt-on community work.

Both ride the same portable SKILL.md standard, both scale through thousands of external
authors, and both lack exactly what this factory builds. That validates the design center —
and exposes eight gaps, each with a disposition:

| # | Gap | What the ecosystems teach | Disposition |
|---|---|---|---|
| 1 | **No skill lane.** The factory's only unit is the heavyweight packaged agent; skills appear once, as a recipe *input* (§1.5). GAIA's own sibling plans (skill-format #691, marketplace #647) make skills a first-class portable unit | The ~50KB markdown skill is the unit that spreads (52K skills vs our 18 agents). Full freeze-matrix treatment for a 2KB procedure is absurd; shipping it ungated is ClawHavoc | **Open decision §12.7** — a proportionate second lane (eval + scan + provenance, no freeze/OIDC) or an explicit scope hand-off |
| 2 | **No community-producer path.** The factory assumes first-party recipes, oracles, publish tokens; runtime trust tiers (§0.24) imply third-party submissions the factory never defines a pipeline for | Both ecosystems' scale came from external authors; ClawHavoc says a scan-only community lane is not acceptable | **Open decision §12.8** — tier-differentiated pipeline (Verified = full factory; Community = defined, gated subset) |
| 3 | **No field→factory feedback.** Stage 18 triggers on SDK deltas + CI evals only; a field regression invisible to the oracle never triggers. Skills synthesized on-device (skill-synthesis #887) have no path through the gates | Hermes learns but cannot verify; the factory verifies but does not learn. **Validation of learned skills is the thing neither competitor has** — synthesis → eval gate → signed Hub skill would be a real moat | **Open decision §12.9** (telemetry channel) + the M3/M4 intake note below |
| 4 | **No adversarial/security bucket in the eval gate.** Stage 13 measures capability; §5.5's phishing bucket is coverage, not a floor. Signing proves *who published*, never *what the text does to the model* | Every major incident in both ecosystems was content-borne: ClickFix instructions the agent itself relays, natural-language malice VirusTotal admits it misses, skill descriptions injected verbatim into prompts | **Inline, non-optional** — injection-resistance floor added to §5; static content-scan of recipe-pulled skills/MCP configs at stages 5–6 |
| 5 | **No malicious-package recovery.** §6.5 is roll-forward for *quality* escapes; a compromised publisher's installs keep running — and the runtime's anti-rollback + TOFU pinning *entrenches* the compromise | ClawHavoc-class response needs registry yank + client-side deny-list, not a higher semver | **Inline, non-optional** — yank/deny-list promoted from "scope if needed" to **M1** (§6.5, §10), as signing's other half |
| 6 | **No cycle-time budget.** Throughput is priced only via the serial-eval ceiling (M4) | OpenClaw ships multiple times a week; a skill publishes in seconds. If recipe→published takes days, the ecosystem forms elsewhere regardless of quality | Name a target latency per lane when the recipe schema lands (M0); identify amortizable gate costs (cached freezes, incremental eval) |
| 7 | **No interop intake.** skill-format #691 already promises agentskills.io + Hermes/OpenClaw compatibility — 50K+ community skills are syntactically ingestible | "Bring an OpenClaw skill, we validate and sign it" converts the competitors' authoring ecosystem into the factory's input stream, with the eval gate as the differentiator | Rides gap 1's lane (§12.7); the intake is the lane's second customer |
| 8 | **§11 mislabeled the competitors** ("skill-learning" lumped both; OpenClaw is skill *distribution* + self-authoring, Hermes is the *learning* one) | The precise contrast is the positioning: the factory is the missing **trust layer** for the loop those two popularized | **Fixed in §11** (this PR) |

**M3/M4 intake note (gap 3):** when M3's dev stages exist, the skill-synthesis output
(#887 — procedures distilled from an agent's own successful runs) becomes a factory *input*
class: a synthesized skill enters at stage 5b-adjacent (a human triages it exactly like an
oracle candidate), passes the stage-13 gate scoped to the skill lane, and ships signed. That
turns the factory into the validator Hermes lacks, without importing Hermes's
unverified-self-modification risk — the write path stays human-gated (§2.5), matching
OpenClaw's Skill-Workshop lesson that even friction-first ecosystems converged on operator
review for agent-authored capability.

## 12. Open decisions (need sign-off)

1. **Orchestrator substrate** — Claude Code (in CI today; skills + memory are local-session
   capabilities the orchestrator must wire — §9) + GAIA coder vs. a custom Anthropic
   Agent-SDK build. *Rec:* start on Claude Code + GAIA coder (`gaia-agent-code` ships; the
   newer `CoderAgent` needs the `origin/coder` port — §10 prerequisites); evaluate
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
6. **npm-OIDC publisher topology (M0 day-1)** — N thin per-agent caller workflows (**N
   registrations**, per-agent blast radius) vs one caller publishing all agents (**1
   registration**, shared blast radius). *Rec:* N thin callers — per-agent trusted
   publishers preserve supply-chain isolation (a compromised caller can publish only its
   own agent); the registration cost is one-time per agent.
7. **A skill lane (§11.6 gaps 1+7)** — does the factory own a second, proportionate lane
   for SKILL.md artifacts (eval + content-scan + sign, no freeze matrix / no per-skill OIDC
   publisher), including an intake path for agentskills.io-compatible community skills? Or
   is the skill SDLC explicitly out of scope and owned by the marketplace track (#647)?
   *Rec:* own the lane — it reuses stage 13/15 machinery, and "we validate what they only
   scan" is the differentiator the OpenClaw/Hermes analysis surfaced.
8. **Community-tier pipeline (§11.6 gap 2)** — what subset of the factory does a
   *Community*-tier third-party submission get (runtime §0.24 tiers imply submissions the
   factory never defines)? Who curates a community agent's oracle, given curator ≠ spec
   author? *Rec:* Verified = full factory; Community = content-scan + injection floor +
   sandbox smoke-test minimum, and the tier label on the Hub says exactly which gates ran.
9. **Field telemetry → stage 18/5b (§11.6 gap 3)** — a privacy-preserving, opt-in signal
   (install success, tool-call error classes; `gaia diagnostics` is the seed) so field
   regressions the oracle can't see still trigger the maintenance loop, and real failures
   feed oracle curation. *Rec:* design it with the local-first constraint as a feature —
   aggregate counters, never content — and treat it as M4's sensory input.
