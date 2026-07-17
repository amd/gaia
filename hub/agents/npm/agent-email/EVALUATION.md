# How the Email Triage agent is evaluated

Short version: we measure how reliably the agent sorts email into the right
priority, using a fixed set of labeled emails and comparing its answer to the
correct one. The current result is on the
[**Scorecard**](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/npm/agent-email/SCORECARD.md)
tab. This page explains what that number means and how it's measured — in plain
terms first, with the technical recipe at the end.

## What we measure

The agent sorts each email into one of five buckets — **urgent**,
**needs-reply**, **FYI**, **promotional**, or **personal** — so nothing important
gets buried. The eval checks how often it puts an email in the **right bucket, or
a close one**.

Why "or a close one"? Priority is a ranking (urgent > needs-reply > FYI >
promotional). Calling a *needs-reply* email *urgent* is a near miss, not a
disaster — you still see it. Calling it *promotional* is a real miss — it gets
buried. So the headline score gives full credit for the exact bucket **or** the
one next to it, and no credit for anything further off. That's what the
**83.4 / 100** headline means: on most emails, the agent lands on the right
priority or right next to it.

Alongside the headline we also report the stricter "exact bucket" rate, how many
truly-urgent emails it catches (so a model can't cheat by calling everything
urgent), and a couple of others. Only the headline counts toward the published
score; the rest are there for transparency.

## What it's tested on

- A **balanced set of ~250 labeled emails** drawn from a real vendor mailbox
  dataset — not emails we made up to make the agent look good, and balanced across
  the five buckets so every category (including the rare *personal* one) is
  measured fairly.
- **No real personal data** ever enters the test set — that's a deliberate policy.
- The whole run is **on-device**: the agent uses a local AI model to classify each
  email, and the scoring is a simple, exact comparison to the known-correct label
  (no cloud, no second AI "judge", no API key). That keeps the numbers stable and
  cheap to re-run.

There's also a separate, optional check that rates how well the agent drafts
replies *in your voice* — that one uses an AI judge and is reported on its own; it
does not affect the 83.4 triage score.

## Can you trust the number?

Yes, and you can re-run it yourself. Every published score is stamped with the
exact command, model, and dataset that produced it, and the test emails are
rebuilt deterministically from one committed source file — so the score is
reproducible, not a one-off. Each release has to clear a minimum bar before it can
ship, and the score is re-measured whenever the agent's behavior or the dataset
changes.

## Reproducing it yourself

You need a source checkout of [amd/gaia](https://github.com/amd/gaia) and **AMD
Ryzen AI hardware** (the npm package ships neither the test corpus nor the eval
harness). The exact, version-stamped command lives in the
[Scorecard's *Reproduction* section](https://github.com/amd/gaia/blob/agent-pkg-email-v0.5.0/hub/agents/npm/agent-email/SCORECARD.md#reproduction)
— it's auto-generated so it always matches the published number. Run that block;
it installs the eval tools, starts a local model server, rebuilds the test emails
from the committed seed, and runs the benchmark (~17 minutes on a 4B model).

<details>
<summary>Technical detail (for maintainers)</summary>

- **Harness:** `gaia eval benchmark` (`src/gaia/eval/benchmark.py`) drives the
  unchanged agent over a `FakeGmailBackend` synthetic inbox; scoring is exact
  label-matching in `src/gaia/eval/quality_metrics.py` (no LLM judge, no
  `ANTHROPIC_API_KEY`).
- **Dataset:** committed source of truth is
  `tests/fixtures/email/vendor_corpus_seed.jsonl`; `generate_mbox.py` builds the
  gitignored `synthetic_inbox.mbox` + `ground_truth.json` from it
  (`--verify` checks they're in sync). Full schema/provenance/PII policy in
  `tests/fixtures/email/_schema.md`.
- **Metrics:** the aggregate is `within_one_bucket_accuracy` (weight 1.0);
  `category_accuracy`, `urgent_recall`, `urgent_vs_not_accuracy`, and
  `personal_recall` are reported at weight 0. Formula + worked recomputation are in
  `SCORECARD.md`.
- **Running it:** set `GAIA_AGENT_TOOL_TIMEOUT=1800` (full-corpus triage is one
  long tool call); run evals **serially** (two `gaia eval` runs against one
  Lemonade server race-evict each other's model); use `--experiments 3` for
  run-to-run variance (mean/stdev/95% CI).
- **CI:** `test_email_agent_eval.yml` (nightly, report-mode on the self-hosted AMD
  `stx` pool) and `email_scorecard_refresh.yml` (manual dispatch only; a full-corpus
  run regenerates `SCORECARD.md`, a subset run smoke-tests the pipeline without
  committing). The drafting eval needs `ANTHROPIC_API_KEY`; absent → loud skip,
  never a pass.
</details>
