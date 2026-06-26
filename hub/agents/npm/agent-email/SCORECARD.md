---
schema_version: 1
agent:
  name: Email Triage
  version: 0.2.4
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
results:
  test_cases_run: 100
  metrics:
  - name: category_accuracy
    value: 0.46
    weight: 1.0
aggregate:
  name: weighted_accuracy
  formula: round(100 * sum(weight_i * value_i) / sum(weight_i), 2)
  components:
  - metric: category_accuracy
    value: 0.46
    weight: 1.0
  value: 46.0
generated_at: '2026-06-26T17:40:26.470285+00:00'
inherited_from: null
---
# Email Triage — Eval Scorecard v0.2.4

**Aggregate score: 46.0** (out of 100)

## Recipe

| Field | Value |
|-------|-------|
| Dataset | [tests/fixtures/email/ground_truth.json](tests/fixtures/email/ground_truth.json) |
| Description | Synthetic email corpus for GAIA email-triage evaluation (FakeGmailBackend, schema-2.0 triage taxonomy: fyi / needs_response / promotional / urgent / personal) |
| Dataset size | 220 labeled examples |
| Test cases run | 100 |
| Methodology | gaia eval benchmark — category classification accuracy (case-insensitive exact match of the agent's triage label vs the ground-truth label) over a synthetic labeled corpus via FakeGmailBackend; no LLM judge. The corpus uses the schema-2.0 triage taxonomy, aligned with the agent's output labels (#1874) |

## Metrics

  - **category_accuracy**: 0.4600 × 1.0

## Aggregate score recomputation

Formula: `round(100 × Σ(weightᵢ × valueᵢ) / Σ(weightᵢ), 2)`

Worked example:

```
round(100 × ((0.4600 × 1.0)) / 1.0, 2) = 46.0
```

A reader can reproduce this value from the `aggregate.components` in the front
matter alone — no eval-harness access needed.

## Reproduction

Run the following commands from the repository root:

```sh
# Step 1: run the benchmark (requires a Lemonade Server with the model loaded; AMD Ryzen AI / Strix Halo recommended)
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
GAIA_AGENT_TOOL_TIMEOUT=900 \
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
