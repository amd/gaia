---
schema_version: 1
agent:
  name: Email Triage
  version: 0.2.5
recipe:
  dataset:
    reference: tests/fixtures/email/ground_truth.json
    description: 'Synthetic email corpus for GAIA email-triage evaluation (FakeGmailBackend,
      schema-2.0 triage taxonomy: fyi / needs_response / promotional / urgent / personal)'
    size: 220
  methodology: gaia eval benchmark — category classification accuracy (case-insensitive
    exact match of the agent's triage label vs the ground-truth label) over a synthetic
    labeled corpus via FakeGmailBackend; no LLM judge. The corpus uses the schema-2.0
    triage taxonomy, aligned with the agent's output labels (#1874)
  config:
    harness: gaia eval benchmark
    model: Gemma-4-E4B-it-GGUF
    corpus: tests/fixtures/email/synthetic_inbox.mbox
    ground_truth: tests/fixtures/email/ground_truth.json
    limit: 220
  environment:
    gaia_commit: d15a154d
    lemonade_version: 10.7.0
    model: Gemma-4-E4B-it-GGUF
    hardware: AMD Ryzen AI MAX+ (Strix Halo)
results:
  test_cases_run: 100
  metrics:
  - name: category_accuracy
    value: 0.42
    weight: 1.0
  breakdown:
    per_category:
    - category: fyi
      total: 41
      correct: 20
      accuracy: 0.4878
    - category: needs_response
      total: 24
      correct: 13
      accuracy: 0.5417
    - category: promotional
      total: 24
      correct: 5
      accuracy: 0.2083
    - category: urgent
      total: 11
      correct: 4
      accuracy: 0.3636
    top_confusions:
    - expected: fyi
      predicted: needs_response
      count: 17
    - expected: needs_response
      predicted: fyi
      count: 10
    - expected: promotional
      predicted: needs_response
      count: 7
    - expected: promotional
      predicted: fyi
      count: 7
    - expected: urgent
      predicted: needs_response
      count: 6
aggregate:
  name: weighted_accuracy
  formula: round(100 * sum(weight_i * value_i) / sum(weight_i), 2)
  components:
  - metric: category_accuracy
    value: 0.42
    weight: 1.0
  value: 42.0
generated_at: '2026-06-29T16:24:34.052246+00:00'
inherited_from: null
---
# Email Triage — Eval Scorecard v0.2.5

**Aggregate score: 42.0** (out of 100)

## Recipe

| Field | Value |
|-------|-------|
| Dataset | [tests/fixtures/email/ground_truth.json](tests/fixtures/email/ground_truth.json) |
| Description | Synthetic email corpus for GAIA email-triage evaluation (FakeGmailBackend, schema-2.0 triage taxonomy: fyi / needs_response / promotional / urgent / personal) |
| Dataset size | 220 labeled examples |
| Test cases run | 100 |
| Methodology | gaia eval benchmark — category classification accuracy (case-insensitive exact match of the agent's triage label vs the ground-truth label) over a synthetic labeled corpus via FakeGmailBackend; no LLM judge. The corpus uses the schema-2.0 triage taxonomy, aligned with the agent's output labels (#1874) |

## Metrics

  - **category_accuracy**: 0.4200 × 1.0

## Aggregate score recomputation

Formula: `round(100 × Σ(weightᵢ × valueᵢ) / Σ(weightᵢ), 2)`

Worked example:

```
round(100 × ((0.4200 × 1.0)) / 1.0, 2) = 42.0
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
| gaia_commit | d15a154d |
| lemonade_version | 10.7.0 |
| model | Gemma-4-E4B-it-GGUF |
| hardware | AMD Ryzen AI MAX+ (Strix Halo) |

## Category breakdown

| Category | Total | Correct | Accuracy |
|----------|-------|---------|----------|
| fyi | 41 | 20 | 0.4878 |
| needs_response | 24 | 13 | 0.5417 |
| promotional | 24 | 5 | 0.2083 |
| urgent | 11 | 4 | 0.3636 |

**Top confusions:**

  - fyi → needs_response: 17
  - needs_response → fyi: 10
  - promotional → needs_response: 7
  - promotional → fyi: 7
  - urgent → needs_response: 6
