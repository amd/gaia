# The Agent Factory — an automated, eval-gated agent production line

> **Sibling to the runtime architecture.** [`agent-ui-agent-capabilities-plan.md`](agent-ui-agent-capabilities-plan.md)
> §0 and [`agent-ui.mdx`](agent-ui.mdx) design how agents **run and are consumed**
> (out-of-process sidecars + a custodian daemon + thin front-doors). This doc designs
> the distinct other half — how agents are **produced**: the factory that turns an
> *intent* into a *deployed, evaluated, signed, Hub-distributed agent product*.
>
> The two halves meet at three interfaces the runtime doc already defined: the
> **manifest** (§0.28 — the capability/policy contract), the **Hub** (§0.5 —
> distribution), and **signing** (§0.24 — the trust root). The factory *emits* those;
> the runtime *enforces* them.

## 0. Thesis

Most of the field (Hermes, OpenClaw, Voyager, CoALA) can *learn skills*. Almost none
**manufacture trustworthy, shippable, isolated agent products.** The differentiator is
not codegen or skill-learning — it's the **production line**: a declarative recipe run
through automated stages with an **eval quality-gate**, emitting a **signed, versioned,
provenance-carrying product** that our own runtime dogfoods.

**The loop:**

```
  intent ─► SPECIFY ─► SCAFFOLD ─► COMPOSE ─► SYNTHESIZE ─► [EVAL GATE] ─► MANIFEST
                                                                │ pass         │
                                              refine ◄──────────┘ fail         ▼
                                                                          PACKAGE ─► SIGN ─► PUBLISH ─► Hub
                                                                                                        │
                                                                              runtime installs ◄────────┘
```

The factory is essentially **CI/CD for agents** — build → test → package → sign →
publish — with three agent-specific stages (scaffold, skill-synthesis, and an
**eval-scorecard gate**) that ordinary software CI doesn't have.

## 1. The recipe — the single source of truth

One declarative **`recipe.yaml`** fully specifies an agent to manufacture; running it is
deterministic and reproducible (same recipe → same agent — a *Dockerfile for agents*):

```yaml
id: email
purpose: "Triage, search, and organize a personal Gmail/Outlook mailbox, locally."
model: { llm: Gemma-4-E4B-it-GGUF, min_ctx: 8192 }     # → manifest.requiredModels
tools:  [rag, file_io]                                   # KNOWN_TOOLS mixins (tool-loader)
skills: [triage-inbox, follow-up-tracking]               # SKILL.md (skill-format)
mcp:    [gmail, google-calendar]                         # MCP servers (tool-loader Part 3)
connectors: { google: [gmail.modify, calendar] }         # → manifest.oauthScopes (least-priv)
egress: [googleapis.com]                                  # → manifest.egressAllowlist (§0.24)
eval:   { category: email_triage, baseline: baselines/email.json, gate: acceptance>=0.9 }
trust_tier: verified                                      # → manifest.trustTier (§0.24)
targets: [win32-x64, darwin-arm64, darwin-x64, linux-x64] # freeze targets
```

The recipe is the *provenance root*: its content-hash is stamped into the product, so a
published agent is traceable to the exact recipe that built it.

## 2. The production line — stages, components, and gates

Each stage maps to a component that **already exists** (cited), or is a small net-new
stitch. 🚦 = a gate that can fail the build.

| # | Stage | What it does | Component (status) | Gate |
|---|---|---|---|---|
| 1 | **Specify** | intent → `recipe.yaml` | `agents/builder/` (NL→recipe, *alpha*) or hand-authored | — |
| 2 | **Scaffold** | recipe → agent source (persona, system prompt, tool mixins, manifest stub) | `agents/builder/template.py` + `registry.py` (*exists*) | — |
| 3 | **Compose** | wire tools / MCP / connectors | `tool-loader.mdx` + `KNOWN_TOOLS` + `skill-format.mdx` (*loader proposed; format locked*) | — |
| 4 | **Synthesize** | attach/learn procedural skills → `SKILL.md` | `skill-synthesis.mdx` (CoALA, *proposed*) | — |
| 5 | **Evaluate** 🚦 | run eval scenarios → **scorecard**, gate vs a committed **baseline** | `src/gaia/eval/` (`runner`, `scorecard.py`, `behavior_harness`, baselines) (*exists*) | **quality bar** |
| 6 | **Manifest** | emit `manifest.json` (§0.28): capabilities, models, scopes, egress, render types, tier, contract version | net-new stitch (the seam) | schema-valid |
| 7 | **Package** 🚦 | freeze platform binary + npm package, version-stamp, smoke-test | `packaging/{freeze,gen_package_files,stamp_version,smoke_test}.py` (*exists*) | smoke-test |
| 8 | **Sign & Lock** 🚦 | SHA-256 + **sign** the lock (§0.24), embed provenance (recipe hash + scorecard digest) | `packaging/gen_binaries_lock.py` + signing (*lock exists; signing net-new*) | signature |
| 9 | **Publish** | binary → R2, npm via OIDC, register in Hub catalog | `packaging/publish_to_r2.py`, `release_agent_*.yml`, Hub Worker (*exists*) | — |
| 10 | **(Runtime installs)** | daemon downloads + verifies + runs (the loop closes) | v2 runtime §0.5/§0.24 | contract-version §0.15 |

The line is **CI-native** — stages 5–9 already live in `.github/workflows/`
(`build_agents.yml`, `publish_agents.yml`, `release_agent_email.yml`,
`test_agent_behavior_e2e.yml`). The factory *is* those workflows, generalized from
one-agent-at-a-time to a recipe-driven line, with the eval-gate promoted to a hard stage.

## 3. Key architectural properties

- **Eval is the gate, not an afterthought (the trust differentiator).** No agent
  publishes without passing its **scorecard vs a committed baseline** (§ the
  `adding-eval-scorecard` discipline). This is what makes the factory produce
  *trustworthy* products rather than plausible codegen — and it's the property
  skill-learning systems (Hermes/OpenClaw) lack: they learn skills with **no quality
  bar and no provenance**.
- **Reproducible + declarative.** The recipe is the single source of truth; a build is a
  pure function of `recipe.yaml` + pinned inputs (base image, model versions, tool
  versions). Re-running yields the same signed artifact — auditable and cache-able.
- **The manifest is the factory→runtime hand-off (§0.28).** The factory is the *only*
  writer of `manifest.json`; the runtime is the *only* enforcer. Provenance (recipe
  hash + eval scorecard digest) rides **inside the signed envelope** (§0.24), so what
  you install is provably what the recipe built and the eval passed.
- **Self-improvement loop (optional, closed-loop mode).** An eval failure at stage 5 can
  feed **skill-synthesis** (learn the missing procedure) or a prompt/tool refinement,
  then **re-eval** — an automated *generate → eval → refine* loop. A meta-agent (the
  Builder, matured past alpha) can drive it; the eval-gate keeps it honest (it can't
  ship itself worse).
- **Provenance + trust chain.** Every product carries: recipe hash · eval scorecard ·
  publisher signature · trust tier. The runtime and users verify this at install
  (§0.24) — the factory's output is a *trust-bearing* artifact, not just a binary.
- **Two operating modes (both first-class):**
  - **Assisted** (human-in-the-loop) — Builder scaffolds, a human authors the real
    tool logic, the factory evals + packages + signs + publishes. *This is today's
    reality* (Builder is alpha; it produces a template a human extends).
  - **Automated** (closed-loop) — the full recipe runs end-to-end, eval-gated,
    iterating on failures. *The vision* — feasible only once scaffold + synthesis are
    strong enough that the eval-gate reliably passes without a human in stage 2–4.

## 4. The factory ↔ runtime seam (why the two docs compose)

```
  ┌──────────── AGENT FACTORY (produce) ────────────┐        ┌──────── RUNTIME (run) ────────┐
  │ recipe → scaffold → compose → synth → EVAL 🚦   │        │  custodian daemon             │
  │        → manifest → freeze → SIGN 🚦 → publish  │        │  installs + verifies + runs   │
  └───────────────────────┬─────────────────────────┘        └───────────▲───────────────────┘
                          │  emits: signed binary + manifest.json (§0.28)  │  enforces: grants,
                          │         + eval scorecard (provenance)          │  contract version (§0.15),
                          ▼                                                │  signature/tier (§0.24)
                    ┌───────────── Agent Hub (§0.5) — the conveyor ──────────────┐
                    └────────────────────────────────────────────────────────---─┘
```

Nothing new is invented at the seam — the factory populates exactly the artifacts the
runtime doc already consumes (manifest §0.28, lock/signature §0.24, catalog §0.5,
contract-version §0.15). **Designing the factory is mostly *stitching + gating*, not
greenfield**, because the endpoints were defined by the runtime work.

## 5. Component inventory — exists vs. net-new

**Exists (reuse):** `agents/builder/` (scaffold, alpha) · `src/gaia/eval/` (runner,
`scorecard.py`, `behavior_harness`, baselines) · `packaging/{freeze,gen_binaries_lock,
gen_package_files,gen_scorecard,stamp_version,smoke_test,publish_to_r2}.py` ·
`skill-format.mdx` (locked) · the `release_agent_*.yml` / `build_agents.yml` /
`publish_agents.yml` CI · the Hub Worker + npm OIDC.

**Proposed elsewhere (adopt):** `skill-synthesis.mdx` (CoALA procedural memory) ·
`tool-loader.mdx` (dynamic tool + MCP loading) · manifest §0.28 (from the runtime doc).

**Net-new to build (the stitch):**
1. The **`recipe.yaml` schema + a `gaia factory build <recipe>` driver** that runs the
   line locally, and a CI workflow that runs it on push.
2. The **eval-gate promotion** — make "scorecard ≥ baseline" a *hard* stage that blocks
   packaging (today eval is run manually per CLAUDE.md; the factory makes it a gate).
3. The **manifest emitter** (stage 6) — derive `manifest.json` from the recipe +
   compose/eval outputs.
4. **Lock signing + provenance embedding** (stage 8) — extend `gen_binaries_lock.py`
   with a signature and the recipe-hash + scorecard-digest (§0.24).
5. The **closed-loop refiner** (optional, later) — the generate→eval→refine controller.

## 6. Phased build (strangler-fig, mirrors the runtime plan)

0. **Recipe + driver.** Define `recipe.yaml`; a `gaia factory build` that runs
   scaffold→compose→eval→package **locally** for one existing agent (email) end-to-end.
1. **Eval-gate + manifest emit.** Promote the scorecard to a hard gate; emit
   `manifest.json`. Now a build is quality-gated and produces the runtime hand-off.
2. **Sign + provenance + CI.** Add lock signing + provenance; run the line in CI on the
   existing `release_agent_*.yml` substrate. One-command reproducible publish.
3. **Assisted authoring.** Mature the Builder past alpha: NL → recipe, and scaffold that
   produces a *runnable* (not just template) agent for common tool shapes.
4. **Closed-loop refine.** Wire skill-synthesis + a refiner so eval failures auto-iterate
   — the self-improving factory. Guarded by the eval-gate (can't ship a regression).

Each phase is independently useful; email is the reference agent throughout.

## 7. Competitive distinctiveness (honest)

- **vs Hermes / OpenClaw / Voyager (skill learning):** they *learn skills*; they do **not
  manufacture eval-gated, signed, isolated products**. Skill-learning without a quality
  bar or provenance is codegen with vibes. The **eval-gate + signing + isolation** is the
  harder-to-copy asset — and it composes with the runtime's process isolation to yield
  *trustworthy, installable agent products*, which nobody in the comparison ships.
- **vs software CI/CD:** the factory borrows CI/CD's shape (build→test→sign→publish) but
  adds the three agent-specific stages — **scaffold from intent, synthesize skills, and
  gate on a behavioral eval scorecard** — that ordinary CI has no notion of.
- **Honest caveat:** most of this is *stitching existing components*, and two stages
  (scaffold-to-runnable, closed-loop refine) are aspirational today (Builder is alpha,
  synthesis is proposed). The differentiator is *real but must be built* — the parts
  exist; the **line and the gate** are the work.

## 8. Open decisions (need sign-off)

1. **Recipe format** — YAML (above) vs. extend the existing `manifest.json` to be the
   authored input too (one file vs. recipe-in / manifest-out). *Rec:* separate recipe
   (human intent) → manifest (machine contract); the recipe is richer (eval config,
   targets) than the runtime needs.
2. **Eval-gate strictness** — hard-block publish on any regression vs. warn + require
   explicit `--save-baseline`. *Rec:* hard-block for `trust_tier: verified`; warn for
   experimental.
3. **Closed-loop autonomy** — how far the refiner may go unattended before a human
   reviews (ties to runtime §0.34 autonomy). *Rec:* human approves the published
   artifact in v1; auto-iterate only within a build, never auto-publish.
4. **Where the driver lives** — a `gaia factory` CLI in core vs. a standalone tool. *Rec:*
   `gaia factory` subcommand, since it orchestrates existing `gaia eval` + packaging.
