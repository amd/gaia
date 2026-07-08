# The Agent Factory — what it is and why we need it

> Plain-language companion to the full spec, [`agent-factory.md`](agent-factory.md).
> Read this first; read the spec when you need the stage-by-stage detail.

## What it is

The Agent Factory is an automated assembly line for GAIA agents. It takes a short,
human-written **recipe** ("build an email-triage agent with these tools, these OAuth
scopes, and this quality bar") and drives the *entire* developer lifecycle from it:
scope the work, write and review the spec, generate test data, write the code against
the live SDK, evaluate it against a quality gate, open the PR, package it for every
platform, publish it to the Agent Hub — and then **keep it working** as the SDK
changes underneath it.

It is not a code generator and not a packaging script. It is the whole software
development lifecycle a GAIA developer performs by hand today, run as a supervised
pipeline with a human approving every consequential step.

## Why we need it

**1. Agents rot.** The GAIA SDK (`src/gaia/agents/base/*`, tool mixins, LLM clients,
connectors) changes almost every week. An agent built once and frozen drifts from the
platform until it silently breaks. Someone has to notice, re-test, fix, and re-ship —
today that is manual work, per agent, per SDK change.

**2. That maintenance is the dominant cost.** GAIA ships ~18 hub agents. Every
SDK-affecting change potentially touches all of them. Keeping N agents correct against
a moving SDK is more work than building any one of them — and it is exactly the kind
of repetitive, verifiable work automation is for. The factory turns "an SDK change
broke the email agent" from a human fire drill into a pipeline trigger.

**3. Our ship rigor exists for exactly one agent.** The email agent has a genuinely
rigorous release line: multi-platform builds, cross-OS verification, a data-driven
eval gate, supply-chain provenance (npm OIDC), and post-publish verification of what
users actually download. The other agents have none of that. The factory generalizes
the pipeline we already trust instead of hand-copying it 17 times.

**4. Quality needs a gate a machine can't game.** An LLM that writes the agent *and*
its test data *and* grades the result certifies nothing. The factory's eval gate rests
on a **held-out oracle** — test cases curated by a human who did not write the spec,
stored where the optimization loop cannot read them — plus fixed quality bars and
safety floors that never drift. A release that can't clear the bar stops and escalates
to a human; it never ships a degraded result quietly.

**5. Autonomy needs supervision, not trust.** Every risky stage — approving the spec,
merging a PR, cutting an SDK release, publishing to the Hub — is an explicit human
approve/deny checkpoint, the same confirmation model GAIA agents themselves use. The
highest-blast-radius decision (releasing an SDK change that fans out to every agent)
is only approvable *after* the factory re-evaluates all agents against the candidate
and shows the human the actual regression set. You approve evidence, not a tag.

## How it works, at a glance

```
recipe (human writes this)
   │
   ▼
DEV HALF (mostly to build): scope → spec → synthetic data → code → eval loop → PR
   │                                   human gates: spec ✋  PR/merge ✋
   ▼
SHIP HALF (exists for email): docs → manifest → freeze → verify → eval gate →
   sign → publish → post-publish verify        human gate: ship ✋
   │
   ▼
MAINTAIN LOOP (the point of it all): SDK change regresses an agent's eval →
   re-run dev + ship for that agent            human gate: SDK release ✋
```

## What exists today vs. what gets built

**Already real:** the email agent's full release pipeline; a per-agent wheel→PyPI
publish lane with a manual approval gate; the eval framework (runner, scorecard,
statistical gate, failure analysis, auto-fix loop); synthetic-corpus generators seeded
from real data; Claude Code running in CI for reviews and auto-fixes; per-agent
`gaia-agent.yaml` manifests the recipe extends.

**To build:** the orchestrator that stitches those pieces into one driver
(`gaia factory <recipe>`); the recipe schema; the manifest emitter; generalizing the
email release line to any agent; the held-out oracles (human work, deliberately not
automated); and the SDK-delta trigger.

## The plan, in five steps (easiest → hardest)

| Milestone | One-liner |
|---|---|
| **M0** | Make the email release pipeline recipe-driven and prove it on a second agent — no LLM involved |
| **M1** | Add provenance: manifest, signing, source-hash, post-publish edge verification |
| **M2** | The trust bar: a human-curated held-out oracle + noise-aware eval gate for every agent |
| **M3** | LLM-assisted dev stages (scaffold, wiring, data gen, eval-optimize, PR authoring) — human-gated |
| **M4** | The maintenance loop: SDK change → re-eval all agents → rebuild what regressed |

The order is deliberate: M0–M1 deliver value with zero research risk; M2 buys the
trustworthy gate; only then do the generative stages (M3) and the continuous loop (M4)
build on it. The moat is M4 — and it is scheduled last precisely because you don't
build the loop before you can trust the gate it runs on.

## Where to read more

- Full spec: [`agent-factory.md`](agent-factory.md) (stages, gates, eval methodology, review record)
- The runtime it feeds: [`agent-ui-agent-capabilities-plan.md`](agent-ui-agent-capabilities-plan.md) §0 (sidecars, manifest, Hub, signing)
- Hub publishing today: [`../guides/hub-publishing.mdx`](../guides/hub-publishing.mdx)
- The eval scorecard pattern: [`../reference/eval-scorecard.mdx`](../reference/eval-scorecard.mdx)
