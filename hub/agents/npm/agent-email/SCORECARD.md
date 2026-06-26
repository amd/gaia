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
    limit: 25
results:
  test_cases_run: 25
  metrics:
  - name: category_accuracy
    value: 0.4
    weight: 1.0
aggregate:
  name: weighted_accuracy
  formula: round(100 * sum(weight_i * value_i) / sum(weight_i), 2)
  components:
  - metric: category_accuracy
    value: 0.4
    weight: 1.0
  value: 40.0
generated_at: '2026-06-26T17:29:34.631236+00:00'
inherited_from: null
---
# Email Triage — Eval Scorecard v0.2.4

**Aggregate score: 40.0** (out of 100)

## Recipe

| Field | Value |
|-------|-------|
| Dataset | [tests/fixtures/email/ground_truth.json](tests/fixtures/email/ground_truth.json) |
| Description | Synthetic email corpus for GAIA email-triage evaluation (FakeGmailBackend, schema-2.0 triage taxonomy: fyi / needs_response / promotional / urgent / personal) |
| Dataset size | 220 labeled examples |
| Test cases run | 25 |
| Methodology | gaia eval benchmark — category classification accuracy (case-insensitive exact match of the agent's triage label vs the ground-truth label) over a synthetic labeled corpus via FakeGmailBackend; no LLM judge. The corpus uses the schema-2.0 triage taxonomy, aligned with the agent's output labels (#1874) |

## Metrics

  - **category_accuracy**: 0.4000 × 1.0

## Aggregate score recomputation

Formula: `round(100 × Σ(weightᵢ × valueᵢ) / Σ(weightᵢ), 2)`

Worked example:

```
round(100 × ((0.4000 × 1.0)) / 1.0, 2) = 40.0
```

A reader can reproduce this value from the `aggregate.components` in the front
matter alone — no eval-harness access needed.

## Reproduction

Run the following commands from the repository root:

```sh
# Step 1: run the benchmark (requires a running Lemonade Server on :13305)
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
GAIA_AGENT_TOOL_TIMEOUT=120 \
PYTHONPATH="$(pwd)" \
gaia eval benchmark --limit 25

# Step 2: generate the scorecard from the benchmark output
PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
PYTHONPATH="$(pwd)" \
python hub/agents/python/email/packaging/gen_scorecard.py \
    --benchmark-dir /private/tmp/claude-501/-Users-tomasz-src-amd-gaia--claude-worktrees-sleepy-chatelet-2b818a/314bd25e-fbc0-4ab7-aab0-a8825585e5ef/scratchpad/email-eval-relabeled \
    --ground-truth tests/fixtures/email/ground_truth.json \
    --limit 25
```

See [eval-scorecard docs](https://amd-gaia.ai/docs/reference/eval-scorecard) and the [`adding-eval-scorecard` skill](.claude/skills/adding-eval-scorecard/SKILL.md) for the full setup guide.
