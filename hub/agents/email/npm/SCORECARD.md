---
schema_version: 1
agent:
  name: Email Triage
  version: 0.5.0
recipe:
  dataset:
    reference: tests/fixtures/email/ground_truth.json
    description: 'Vendor-derived labelled email corpus for GAIA email-triage evaluation
      (FakeGmailBackend, schema-2.0 triage taxonomy: urgent / needs_response / fyi
      / promotional / personal); a deterministic, category-balanced subset of the
      vendor mailbox dataset'
    size: 299
  methodology: 'gaia eval benchmark over the vendor-derived labelled corpus via FakeGmailBackend;
    no LLM judge. The full corpus is scored — see dataset_size (GAIA_EMAIL_TRIAGE_MAX_MESSAGES
    lifts the interactive per-call scan cap for the eval so the whole corpus is covered).
    Aggregate = within-one-bucket ACCEPTANCE accuracy (#1437): triage priority is
    ordinal (URGENT>NEEDS_RESPONSE>FYI>PROMOTIONAL), so a prediction is credited when
    it is exact or an adjacent bucket (|rank diff|<=1) — what users feel (nothing
    urgent buried). Reported secondaries (not in the aggregate): urgent-vs-not binary
    accuracy, urgent recall (anti-gaming floor), and exact 4-way category_accuracy.
    The corpus uses the schema-2.0 taxonomy aligned with the agent''s output labels
    (#1874); averaged over 1 run(s) for run-to-run variance/CI (#1894)'
  config:
    harness: gaia eval benchmark
    model: Gemma-4-E4B-it-GGUF
    corpus: tests/fixtures/email/synthetic_inbox.mbox
    ground_truth: tests\fixtures\email\ground_truth.json
    limit: 25
    n_runs: 1
    acceptance_variance:
      n_runs: 1
      within_one_bucket_accuracy:
        n: 1
        mean: 1.0
        stdev: 0.0
        min: 1.0
        max: 1.0
        cv_pct: 0.0
        ci95_half_width: 0.0
        ci95_low: 1.0
        ci95_high: 1.0
      urgent_vs_not_accuracy:
        n: 1
        mean: 0.84
        stdev: 0.0
        min: 0.84
        max: 0.84
        cv_pct: 0.0
        ci95_half_width: 0.0
        ci95_low: 0.84
        ci95_high: 0.84
      urgent_recall:
        n: 1
        mean: 1.0
        stdev: 0.0
        min: 1.0
        max: 1.0
        cv_pct: 0.0
        ci95_half_width: 0.0
        ci95_low: 1.0
        ci95_high: 1.0
      personal_recall:
        n: 1
        mean: 0.0
        stdev: 0.0
        min: 0.0
        max: 0.0
        cv_pct: 0.0
        ci95_half_width: 0.0
        ci95_low: 0.0
        ci95_high: 0.0
      category_accuracy:
        n: 1
        mean: 0.8
        stdev: 0.0
        min: 0.8
        max: 0.8
        cv_pct: 0.0
        ci95_half_width: 0.0
        ci95_low: 0.8
        ci95_high: 0.8
  environment:
    gaia_commit: 905e954c
    lemonade_version: 10.10.0
    model: Gemma-4-E4B-it-GGUF
    ctx_size: 16384
    hardware: AMD Ryzen AI MAX+ (Strix Halo)
results:
  test_cases_run: 25
  metrics:
  - name: within_one_bucket_accuracy
    value: 1.0
    weight: 1.0
  - name: urgent_vs_not_accuracy
    value: 0.84
    weight: 0.0
  - name: urgent_recall
    value: 1.0
    weight: 0.0
  - name: personal_recall
    value: 0.0
    weight: 0.0
  - name: category_accuracy
    value: 0.8
    weight: 0.0
  - name: draft_approval_rate
    value: 0.5556
    weight: 0.0
  breakdown:
    per_category:
    - category: fyi
      total: 15
      correct: 11
      accuracy: 0.7333
    - category: needs_response
      total: 7
      correct: 7
      accuracy: 1.0
    - category: urgent
      total: 3
      correct: 2
      accuracy: 0.6667
    top_confusions:
    - expected: fyi
      predicted: needs_response
      count: 4
    - expected: urgent
      predicted: needs_response
      count: 1
  performance:
    ttft_s: 26.409
    throughput_tps: 23.8
    pipeline_s: 809.531
    total_input_tokens: 46908.0
    total_output_tokens: 18111.0
    tokens_per_triage: 2068.6
    llm_classified_count: 25.0
    emails_per_run: 25
  capability_quality:
    spam:
      precision: 0.0
      recall: 0.0
      f1: 0.0
    action_items:
      precision: 0.0
      recall: 0.0
      f1: 0.0
    briefing:
      approval: 0.0
      must_include_recall: 0.05
      faithful: 1.0
      hallucination_free: 1.0
aggregate:
  name: weighted_accuracy
  formula: round(100 * sum(weight_i * value_i) / sum(weight_i), 2)
  components:
  - metric: within_one_bucket_accuracy
    value: 1.0
    weight: 1.0
  - metric: urgent_vs_not_accuracy
    value: 0.84
    weight: 0.0
  - metric: urgent_recall
    value: 1.0
    weight: 0.0
  - metric: personal_recall
    value: 0.0
    weight: 0.0
  - metric: category_accuracy
    value: 0.8
    weight: 0.0
  - metric: draft_approval_rate
    value: 0.5556
    weight: 0.0
  value: 100.0
generated_at: '2026-07-15T23:48:55.149673+00:00'
inherited_from: null
---
# Email Triage — Eval Scorecard v0.5.0

**Aggregate score: 100.0** (out of 100)

## Recipe

| Field | Value |
|-------|-------|
| Dataset | [tests/fixtures/email/ground_truth.json](tests/fixtures/email/ground_truth.json) |
| Description | Vendor-derived labelled email corpus for GAIA email-triage evaluation (FakeGmailBackend, schema-2.0 triage taxonomy: urgent / needs_response / fyi / promotional / personal); a deterministic, category-balanced subset of the vendor mailbox dataset |
| Dataset size | 299 labeled examples |
| Test cases run | 25 |
| Methodology | gaia eval benchmark over the vendor-derived labelled corpus via FakeGmailBackend; no LLM judge. The full corpus is scored — see dataset_size (GAIA_EMAIL_TRIAGE_MAX_MESSAGES lifts the interactive per-call scan cap for the eval so the whole corpus is covered). Aggregate = within-one-bucket ACCEPTANCE accuracy (#1437): triage priority is ordinal (URGENT>NEEDS_RESPONSE>FYI>PROMOTIONAL), so a prediction is credited when it is exact or an adjacent bucket (|rank diff|<=1) — what users feel (nothing urgent buried). Reported secondaries (not in the aggregate): urgent-vs-not binary accuracy, urgent recall (anti-gaming floor), and exact 4-way category_accuracy. The corpus uses the schema-2.0 taxonomy aligned with the agent's output labels (#1874); averaged over 1 run(s) for run-to-run variance/CI (#1894) |

## Metrics

  - **within_one_bucket_accuracy**: 1.0000 × 1.0
  - **urgent_vs_not_accuracy**: 0.8400 × 0.0
  - **urgent_recall**: 1.0000 × 0.0
  - **personal_recall**: 0.0000 × 0.0
  - **category_accuracy**: 0.8000 × 0.0
  - **draft_approval_rate**: 0.5556 × 0.0

## Aggregate score recomputation

Formula: `round(100 × Σ(weightᵢ × valueᵢ) / Σ(weightᵢ), 2)`

Worked example:

```
round(100 × ((1.0000 × 1.0) + (0.8400 × 0.0) + (1.0000 × 0.0) + (0.0000 × 0.0) + (0.8000 × 0.0) + (0.5556 × 0.0)) / 1.0, 2) = 100.0
```

A reader can reproduce this value from the `aggregate.components` in the front
matter alone — no eval-harness access needed.

## Reproduction

Run the following commands from the repository root:

```sh
# Prerequisites: install the eval extras and start a Lemonade Server
# with the model on AMD Ryzen AI hardware (Strix Halo recommended).
uv pip install -e ".[dev,eval,api]"
lemonade-server serve   # in a separate shell; must stay running

# Step 0: build the corpus from the committed seed. The mbox +
# ground_truth are GENERATED artifacts (gitignored), so a fresh
# checkout must materialise them before the benchmark can read them.
python tests/fixtures/email/generate_mbox.py

# Step 1: run the benchmark (requires the Lemonade Server above with the
# model loaded; AMD Ryzen AI / Strix Halo recommended)
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
GAIA_AGENT_TOOL_TIMEOUT=1800 \
PYTHONPATH="$(pwd)" \
gaia eval benchmark \
    --model Gemma-4-E4B-it-GGUF \
    --mbox-path tests/fixtures/email/synthetic_inbox.mbox \
    --ground-truth tests\fixtures\email\ground_truth.json \
    --limit 25 \
    --output-dir /tmp/email-eval

# Step 2: generate this scorecard from the benchmark output
PYTHONPATH="$(pwd)" \
python hub/agents/email/python/packaging/gen_scorecard.py \
    --benchmark-dir /tmp/email-eval \
    --ground-truth tests\fixtures\email\ground_truth.json \
    --limit 25

# Background, dataset details, a worked example, and metric
# definitions: see EVALUATION.md (next to this scorecard).
```

See [eval-scorecard docs](https://amd-gaia.ai/docs/reference/eval-scorecard) and the [`adding-eval-scorecard` skill](.claude/skills/adding-eval-scorecard/SKILL.md) for the full setup guide.

## Environment

| Field | Value |
|-------|-------|
| gaia_commit | 905e954c |
| lemonade_version | 10.10.0 |
| model | Gemma-4-E4B-it-GGUF |
| ctx_size | 16384 |
| hardware | AMD Ryzen AI MAX+ (Strix Halo) |

## Category breakdown

| Category | Total | Correct | Accuracy |
|----------|-------|---------|----------|
| fyi | 15 | 11 | 0.7333 |
| needs_response | 7 | 7 | 1.0000 |
| urgent | 3 | 2 | 0.6667 |

**Top confusions:**

  - fyi → needs_response: 4
  - urgent → needs_response: 1

## Performance

_Measured on the run environment above (model / hardware / gaia_commit / corpus size); the perf gate is report-only, so these are observed values, not pass/fail bars (see `tests/fixtures/email/perf_gate_thresholds.json`)._

| Metric | Value |
|--------|-------|
| ttft_s | 26.409 |
| throughput_tps | 23.8 |
| pipeline_s | 809.531 |
| total_input_tokens | 46908.0 |
| total_output_tokens | 18111.0 |
| tokens_per_triage | 2068.6 |
| llm_classified_count | 25.0 |
| emails_per_run | 25 |

## Capability quality

_Beyond the headline triage accuracy, these are the agent's other capabilities scored by their own evals (spam detection, action-item extraction, briefing quality). Report-only — they don't feed the aggregate above; see the per-capability gate thresholds under `tests/fixtures/email/`._

| Capability | Metric | Value |
|------------|--------|-------|
| spam | precision | 0.0000 |
| spam | recall | 0.0000 |
| spam | f1 | 0.0000 |
| action_items | precision | 0.0000 |
| action_items | recall | 0.0000 |
| action_items | f1 | 0.0000 |
| briefing | approval | 0.0000 |
| briefing | must_include_recall | 0.0500 |
| briefing | faithful | 1.0000 |
| briefing | hallucination_free | 1.0000 |
