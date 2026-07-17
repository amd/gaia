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
    (#1874); averaged over 3 run(s) for run-to-run variance/CI (#1894)'
  config:
    harness: gaia eval benchmark
    model: Gemma-4-E4B-it-GGUF
    corpus: tests/fixtures/email/synthetic_inbox.mbox
    ground_truth: tests\fixtures\email\ground_truth.json
    limit: 250
    n_runs: 3
    acceptance_variance:
      n_runs: 3
      within_one_bucket_accuracy:
        n: 3
        mean: 0.8453
        stdev: 0.0023
        min: 0.844
        max: 0.848
        cv_pct: 0.27
        ci95_half_width: 0.0026
        ci95_low: 0.8427
        ci95_high: 0.8479
      urgent_vs_not_accuracy:
        n: 3
        mean: 0.7987
        stdev: 0.0046
        min: 0.796
        max: 0.804
        cv_pct: 0.58
        ci95_half_width: 0.0052
        ci95_low: 0.7934
        ci95_high: 0.8039
      urgent_recall:
        n: 3
        mean: 1.0
        stdev: 0.0
        min: 1.0
        max: 1.0
        cv_pct: 0.0
        ci95_half_width: 0.0
        ci95_low: 1.0
        ci95_high: 1.0
      personal_recall:
        n: 3
        mean: 0.3636
        stdev: 0.0
        min: 0.3636
        max: 0.3636
        cv_pct: 0.0
        ci95_half_width: 0.0
        ci95_low: 0.3636
        ci95_high: 0.3636
      category_accuracy:
        n: 3
        mean: 0.7813
        stdev: 0.0023
        min: 0.78
        max: 0.784
        cv_pct: 0.3
        ci95_half_width: 0.0026
        ci95_low: 0.7787
        ci95_high: 0.7839
  environment:
    gaia_commit: eca42a0e
    lemonade_version: 10.10.0
    model: Gemma-4-E4B-it-GGUF
    ctx_size: 16384
    hardware: AMD Ryzen AI MAX+ (Strix Halo)
results:
  test_cases_run: 250
  metrics:
  - name: within_one_bucket_accuracy
    value: 0.8453
    weight: 1.0
  - name: urgent_vs_not_accuracy
    value: 0.7987
    weight: 0.0
  - name: urgent_recall
    value: 1.0
    weight: 0.0
  - name: personal_recall
    value: 0.3636
    weight: 0.0
  - name: category_accuracy
    value: 0.7813
    weight: 0.0
  - name: draft_approval_rate
    value: 0.6111
    weight: 0.0
  breakdown:
    per_category:
    - category: fyi
      total: 162
      correct: 124
      accuracy: 0.7654
    - category: needs_response
      total: 162
      correct: 162
      accuracy: 1.0
    - category: personal
      total: 99
      correct: 36
      accuracy: 0.3636
    - category: promotional
      total: 165
      correct: 112
      accuracy: 0.6788
    - category: urgent
      total: 162
      correct: 152
      accuracy: 0.9383
    top_confusions:
    - expected: personal
      predicted: needs_response
      count: 44
    - expected: promotional
      predicted: urgent
      count: 40
    - expected: fyi
      predicted: needs_response
      count: 38
    - expected: personal
      predicted: urgent
      count: 16
    - expected: promotional
      predicted: needs_response
      count: 13
  performance:
    ttft_s: 24.673
    throughput_tps: 23.767
    pipeline_s: 6926.411
    total_input_tokens: 316983.667
    total_output_tokens: 169056.667
    tokens_per_triage: 1906.033
    llm_classified_count: 250.0
    emails_per_run: 250
  capability_quality:
    spam:
      precision: 0.1078
      recall: 0.3333
      f1: 0.1629
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
    value: 0.8453
    weight: 1.0
  - metric: urgent_vs_not_accuracy
    value: 0.7987
    weight: 0.0
  - metric: urgent_recall
    value: 1.0
    weight: 0.0
  - metric: personal_recall
    value: 0.3636
    weight: 0.0
  - metric: category_accuracy
    value: 0.7813
    weight: 0.0
  - metric: draft_approval_rate
    value: 0.6111
    weight: 0.0
  value: 84.53
generated_at: '2026-07-16T07:54:50.556072+00:00'
inherited_from: null
---
# Email Triage — Eval Scorecard v0.5.0

**Aggregate score: 84.53** (out of 100)

## Recipe

| Field | Value |
|-------|-------|
| Dataset | [tests/fixtures/email/ground_truth.json](tests/fixtures/email/ground_truth.json) |
| Description | Vendor-derived labelled email corpus for GAIA email-triage evaluation (FakeGmailBackend, schema-2.0 triage taxonomy: urgent / needs_response / fyi / promotional / personal); a deterministic, category-balanced subset of the vendor mailbox dataset |
| Dataset size | 299 labeled examples |
| Test cases run | 250 |
| Methodology | gaia eval benchmark over the vendor-derived labelled corpus via FakeGmailBackend; no LLM judge. The full corpus is scored — see dataset_size (GAIA_EMAIL_TRIAGE_MAX_MESSAGES lifts the interactive per-call scan cap for the eval so the whole corpus is covered). Aggregate = within-one-bucket ACCEPTANCE accuracy (#1437): triage priority is ordinal (URGENT>NEEDS_RESPONSE>FYI>PROMOTIONAL), so a prediction is credited when it is exact or an adjacent bucket (|rank diff|<=1) — what users feel (nothing urgent buried). Reported secondaries (not in the aggregate): urgent-vs-not binary accuracy, urgent recall (anti-gaming floor), and exact 4-way category_accuracy. The corpus uses the schema-2.0 taxonomy aligned with the agent's output labels (#1874); averaged over 3 run(s) for run-to-run variance/CI (#1894) |

## Metrics

  - **within_one_bucket_accuracy**: 0.8453 × 1.0
  - **urgent_vs_not_accuracy**: 0.7987 × 0.0
  - **urgent_recall**: 1.0000 × 0.0
  - **personal_recall**: 0.3636 × 0.0
  - **category_accuracy**: 0.7813 × 0.0
  - **draft_approval_rate**: 0.6111 × 0.0

## Aggregate score recomputation

Formula: `round(100 × Σ(weightᵢ × valueᵢ) / Σ(weightᵢ), 2)`

Worked example:

```
round(100 × ((0.8453 × 1.0) + (0.7987 × 0.0) + (1.0000 × 0.0) + (0.3636 × 0.0) + (0.7813 × 0.0) + (0.6111 × 0.0)) / 1.0, 2) = 84.53
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
    --limit 250 \
    --output-dir /tmp/email-eval

# Step 2: generate this scorecard from the benchmark output
PYTHONPATH="$(pwd)" \
python hub/agents/python/email/packaging/gen_scorecard.py \
    --benchmark-dir /tmp/email-eval \
    --ground-truth tests\fixtures\email\ground_truth.json \
    --limit 250

# Background, dataset details, a worked example, and metric
# definitions: see EVALUATION.md (next to this scorecard).
```

See [eval-scorecard docs](https://amd-gaia.ai/docs/reference/eval-scorecard) and the [`adding-eval-scorecard` skill](.claude/skills/adding-eval-scorecard/SKILL.md) for the full setup guide.

## Environment

| Field | Value |
|-------|-------|
| gaia_commit | eca42a0e |
| lemonade_version | 10.10.0 |
| model | Gemma-4-E4B-it-GGUF |
| ctx_size | 16384 |
| hardware | AMD Ryzen AI MAX+ (Strix Halo) |

## Category breakdown (pooled across all 3 runs)

_Each of the 250 test cases is scored once per run, so the totals below sum to test_cases_run × 3._

| Category | Total | Correct | Accuracy |
|----------|-------|---------|----------|
| fyi | 162 | 124 | 0.7654 |
| needs_response | 162 | 162 | 1.0000 |
| personal | 99 | 36 | 0.3636 |
| promotional | 165 | 112 | 0.6788 |
| urgent | 162 | 152 | 0.9383 |

**Top confusions:**

  - personal → needs_response: 44
  - promotional → urgent: 40
  - fyi → needs_response: 38
  - personal → urgent: 16
  - promotional → needs_response: 13

## Performance

_Measured on the run environment above (model / hardware / gaia_commit / corpus size); the perf gate is report-only, so these are observed values, not pass/fail bars (see `tests/fixtures/email/perf_gate_thresholds.json`)._

| Metric | Value |
|--------|-------|
| ttft_s | 24.673 |
| throughput_tps | 23.767 |
| pipeline_s | 6926.411 |
| total_input_tokens | 316983.667 |
| total_output_tokens | 169056.667 |
| tokens_per_triage | 1906.033 |
| llm_classified_count | 250.0 |
| emails_per_run | 250 |

## Capability quality

_Beyond the headline triage accuracy, these are the agent's other capabilities scored by their own evals (spam detection, action-item extraction, briefing quality). Report-only — they don't feed the aggregate above; see the per-capability gate thresholds under `tests/fixtures/email/`._

| Capability | Metric | Value |
|------------|--------|-------|
| spam | precision | 0.1078 |
| spam | recall | 0.3333 |
| spam | f1 | 0.1629 |
| action_items | precision | 0.0000 |
| action_items | recall | 0.0000 |
| action_items | f1 | 0.0000 |
| briefing | approval | 0.0000 |
| briefing | must_include_recall | 0.0500 |
| briefing | faithful | 1.0000 |
| briefing | hallucination_free | 1.0000 |
