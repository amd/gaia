---
schema_version: 1
agent:
  name: Email Triage
  version: 0.3.0
recipe:
  dataset:
    reference: tests/fixtures/email/ground_truth.json
    description: 'Synthetic email corpus for GAIA email-triage evaluation (FakeGmailBackend,
      schema-2.0 triage taxonomy: fyi / needs_response / promotional / urgent / personal)'
    size: 220
  methodology: 'gaia eval benchmark over a synthetic labeled corpus via FakeGmailBackend;
    no LLM judge. Aggregate = within-one-bucket ACCEPTANCE accuracy (#1437): triage
    priority is ordinal (URGENT>NEEDS_RESPONSE>FYI>PROMOTIONAL), so a prediction is
    credited when it is exact or an adjacent bucket (|rank diff|<=1) — what users
    feel (nothing urgent buried). Reported secondaries (not in the aggregate): urgent-vs-not
    binary accuracy, urgent recall (anti-gaming floor), and exact 4-way category_accuracy.
    The corpus uses the schema-2.0 taxonomy aligned with the agent''s output labels
    (#1874); averaged over 3 run(s) for run-to-run variance/CI (#1894)'
  config:
    harness: gaia eval benchmark
    model: Gemma-4-E4B-it-GGUF
    corpus: tests/fixtures/email/synthetic_inbox.mbox
    ground_truth: tests/fixtures/email/ground_truth.json
    limit: 220
    n_runs: 3
    acceptance_variance:
      n_runs: 3
      within_one_bucket_accuracy:
        n: 3
        mean: 0.8467
        stdev: 0.0115
        min: 0.84
        max: 0.86
        cv_pct: 1.36
        ci95_half_width: 0.0131
        ci95_low: 0.8336
        ci95_high: 0.8597
      urgent_vs_not_accuracy:
        n: 3
        mean: 0.58
        stdev: 0.01
        min: 0.57
        max: 0.59
        cv_pct: 1.72
        ci95_half_width: 0.0113
        ci95_low: 0.5687
        ci95_high: 0.5913
      urgent_recall:
        n: 3
        mean: 0.6571
        stdev: 0.0
        min: 0.6571
        max: 0.6571
        cv_pct: 0.0
        ci95_half_width: 0.0
        ci95_low: 0.6571
        ci95_high: 0.6571
      category_accuracy:
        n: 3
        mean: 0.4567
        stdev: 0.0115
        min: 0.45
        max: 0.47
        cv_pct: 2.53
        ci95_half_width: 0.0131
        ci95_low: 0.4436
        ci95_high: 0.4697
  environment:
    gaia_commit: 7f52903b
    lemonade_version: 10.7.0
    model: Gemma-4-E4B-it-GGUF
    hardware: AMD Ryzen AI MAX+ (Strix Halo)
    temperature: 0.0
results:
  test_cases_run: 100
  metrics:
  - name: within_one_bucket_accuracy
    value: 0.8467
    weight: 1.0
  - name: urgent_vs_not_accuracy
    value: 0.58
    weight: 0.0
  - name: urgent_recall
    value: 0.6571
    weight: 0.0
  - name: category_accuracy
    value: 0.4567
    weight: 0.0
  breakdown:
    per_category:
    - category: fyi
      total: 123
      correct: 63
      accuracy: 0.5122
    - category: needs_response
      total: 72
      correct: 39
      accuracy: 0.5417
    - category: promotional
      total: 72
      correct: 23
      accuracy: 0.3194
    - category: urgent
      total: 33
      correct: 12
      accuracy: 0.3636
    top_confusions:
    - expected: fyi
      predicted: needs_response
      count: 50
    - expected: needs_response
      predicted: fyi
      count: 30
    - expected: promotional
      predicted: needs_response
      count: 21
    - expected: urgent
      predicted: needs_response
      count: 18
    - expected: promotional
      predicted: fyi
      count: 16
aggregate:
  name: weighted_accuracy
  formula: round(100 * sum(weight_i * value_i) / sum(weight_i), 2)
  components:
  - metric: within_one_bucket_accuracy
    value: 0.8467
    weight: 1.0
  - metric: urgent_vs_not_accuracy
    value: 0.58
    weight: 0.0
  - metric: urgent_recall
    value: 0.6571
    weight: 0.0
  - metric: category_accuracy
    value: 0.4567
    weight: 0.0
  value: 84.67
generated_at: '2026-06-29T21:45:03.635443+00:00'
inherited_from: null
---
# Email Triage — Eval Scorecard v0.3.0

**Aggregate score: 84.67** (out of 100)

## Recipe

| Field | Value |
|-------|-------|
| Dataset | [tests/fixtures/email/ground_truth.json](tests/fixtures/email/ground_truth.json) |
| Description | Synthetic email corpus for GAIA email-triage evaluation (FakeGmailBackend, schema-2.0 triage taxonomy: fyi / needs_response / promotional / urgent / personal) |
| Dataset size | 220 labeled examples |
| Test cases run | 100 |
| Methodology | gaia eval benchmark over a synthetic labeled corpus via FakeGmailBackend; no LLM judge. Aggregate = within-one-bucket ACCEPTANCE accuracy (#1437): triage priority is ordinal (URGENT>NEEDS_RESPONSE>FYI>PROMOTIONAL), so a prediction is credited when it is exact or an adjacent bucket (|rank diff|<=1) — what users feel (nothing urgent buried). Reported secondaries (not in the aggregate): urgent-vs-not binary accuracy, urgent recall (anti-gaming floor), and exact 4-way category_accuracy. The corpus uses the schema-2.0 taxonomy aligned with the agent's output labels (#1874); averaged over 3 run(s) for run-to-run variance/CI (#1894) |

## Metrics

  - **within_one_bucket_accuracy**: 0.8467 × 1.0
  - **urgent_vs_not_accuracy**: 0.5800 × 0.0
  - **urgent_recall**: 0.6571 × 0.0
  - **category_accuracy**: 0.4567 × 0.0

## Aggregate score recomputation

Formula: `round(100 × Σ(weightᵢ × valueᵢ) / Σ(weightᵢ), 2)`

Worked example:

```
round(100 × ((0.8467 × 1.0) + (0.5800 × 0.0) + (0.6571 × 0.0) + (0.4567 × 0.0)) / 1.0, 2) = 84.67
```

A reader can reproduce this value from the `aggregate.components` in the front
matter alone — no eval-harness access needed.

## Reproduction

Run the following commands from the repository root:

```sh
# Step 1: run the benchmark (requires a Lemonade Server with the model loaded; AMD Ryzen AI / Strix Halo recommended)
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
GAIA_AGENT_TOOL_TIMEOUT=1800 \
PYTHONPATH="$(pwd)" \
gaia eval benchmark \
    --model Gemma-4-E4B-it-GGUF \
    --mbox-path tests/fixtures/email/synthetic_inbox.mbox \
    --ground-truth tests/fixtures/email/ground_truth.json \
    --limit 220 \
    --output-dir /tmp/email-eval

# Step 2: generate this scorecard from the benchmark output
PYTHONPATH="$(pwd)" \
python hub/agents/python/email/packaging/gen_scorecard.py \
    --benchmark-dir /tmp/email-eval \
    --ground-truth tests/fixtures/email/ground_truth.json \
    --limit 220
```

See [eval-scorecard docs](https://amd-gaia.ai/docs/reference/eval-scorecard) and the [`adding-eval-scorecard` skill](.claude/skills/adding-eval-scorecard/SKILL.md) for the full setup guide.

## Environment

| Field | Value |
|-------|-------|
| gaia_commit | 7f52903b |
| lemonade_version | 10.7.0 |
| model | Gemma-4-E4B-it-GGUF |
| hardware | AMD Ryzen AI MAX+ (Strix Halo) |
| temperature | 0.0 |

## Category breakdown

| Category | Total | Correct | Accuracy |
|----------|-------|---------|----------|
| fyi | 123 | 63 | 0.5122 |
| needs_response | 72 | 39 | 0.5417 |
| promotional | 72 | 23 | 0.3194 |
| urgent | 33 | 12 | 0.3636 |

**Top confusions:**

  - fyi → needs_response: 50
  - needs_response → fyi: 30
  - promotional → needs_response: 21
  - urgent → needs_response: 18
  - promotional → fyi: 16
