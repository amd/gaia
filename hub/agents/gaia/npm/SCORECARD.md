---
schema_version: 1
agent:
  name: GAIA (flagship agent)
  version: 0.1.1
recipe:
  dataset:
    reference: eval/scenarios/
    description: 'Judged multi-turn scenario corpus for the flagship gaia agent (13
      gaia_* categories: core conversation/memory tiers through adversarial, RAG,
      files, data, web, shell gate, skill lifecycle + all 12 skills, honesty floor,
      tool selection, code index, skill capture); deterministic fixtures under tests/fixtures/gaia/,
      planted-fact ground truth per eval/scenarios/GAIA_FIXTURE_VALUES.md'
    size: 103
  methodology: 'gaia eval agent --agent-type gaia: each scenario is driven against
    the Agent UI backend (REST/SSE) by the eval driver and scored by the Claude judge
    on planted-fact ground truth + success criteria. Aggregate = judged_pass_rate
    over judged scenarios (PASS/FAIL/BLOCKED_BY_ARCHITECTURE; infra failures are excluded
    from the rate and fail the run''s integrity gate instead). Reported secondaries
    (weight 0): the judge''s 0-10 avg_score normalized to [0,1], and per-category
    pass rates. Thresholds/enforcement: tests/fixtures/gaia/quality_gate_thresholds.json'
  config:
    harness: gaia eval agent
    run_id: eval-20260824-104200
    agent_type: gaia
    eval_model: claude-sonnet-4-6
    budget_per_scenario_usd: 5.0
    categories:
    - gaia_core
  environment:
    gaia_commit: d104b3b1
    model: claude-haiku-4-5
    ctx_size: 200000
    hardware: developer workstation (x86-64, agent on the Claude API — no Lemonade,
      no AMD NPU/GPU inference)
    eval_model: claude-sonnet-4-6
    note: 'HARNESS-VALIDATION card, pending first runner baseline: agent on claude-haiku-4-5
      over the gaia_core category only (11 scenarios, tags local_blocked_no_embedder
      excluded). NOT the product baseline — that is Gemma-4-E4B-it-GGUF on the self-hosted
      runner, captured by the first full gaia_scorecard_refresh.yml dispatch (rebaseline=true).'
results:
  test_cases_run: 11
  metrics:
  - name: judged_pass_rate
    value: 0.7272727272727273
    weight: 1.0
  - name: avg_score_normalized
    value: 0.885
    weight: 0.0
  - name: gaia_core_pass_rate
    value: 0.7273
    weight: 0.0
  breakdown:
    per_category:
    - category: gaia_core
      total: 11
      correct: 8
      accuracy: 0.7273
  performance:
    ttft_s: 7.254
    throughput_tps: 38.7
    total_input_tokens: 90710
    total_output_tokens: 810
    scenarios_with_data: 2
aggregate:
  name: judged_pass_rate
  formula: round(100 * sum(weight_i * value_i) / sum(weight_i), 2)
  components:
  - metric: judged_pass_rate
    value: 0.7272727272727273
    weight: 1.0
  - metric: avg_score_normalized
    value: 0.885
    weight: 0.0
  - metric: gaia_core_pass_rate
    value: 0.7273
    weight: 0.0
  value: 72.73
generated_at: '2026-08-24T11:25:31.271112+00:00'
inherited_from: null
---
# GAIA (flagship agent) — Eval Scorecard v0.1.0

**Aggregate score: 72.73** (out of 100)

## Recipe

| Field | Value |
|-------|-------|
| Dataset | [eval/scenarios/](eval/scenarios/) |
| Description | Judged multi-turn scenario corpus for the flagship gaia agent (13 gaia_* categories: core conversation/memory tiers through adversarial, RAG, files, data, web, shell gate, skill lifecycle + all 12 skills, honesty floor, tool selection, code index, skill capture); deterministic fixtures under tests/fixtures/gaia/, planted-fact ground truth per eval/scenarios/GAIA_FIXTURE_VALUES.md |
| Dataset size | 103 labeled examples |
| Test cases run | 11 |
| Methodology | gaia eval agent --agent-type gaia: each scenario is driven against the Agent UI backend (REST/SSE) by the eval driver and scored by the Claude judge on planted-fact ground truth + success criteria. Aggregate = judged_pass_rate over judged scenarios (PASS/FAIL/BLOCKED_BY_ARCHITECTURE; infra failures are excluded from the rate and fail the run's integrity gate instead). Reported secondaries (weight 0): the judge's 0-10 avg_score normalized to [0,1], and per-category pass rates. Thresholds/enforcement: tests/fixtures/gaia/quality_gate_thresholds.json |

## Metrics

  - **judged_pass_rate**: 0.7273 × 1.0
  - **avg_score_normalized**: 0.8850 × 0.0
  - **gaia_core_pass_rate**: 0.7273 × 0.0

## Aggregate score recomputation

Formula: `round(100 × Σ(weightᵢ × valueᵢ) / Σ(weightᵢ), 2)`

Worked example:

```
round(100 × ((0.7273 × 1.0) + (0.8850 × 0.0) + (0.7273 × 0.0)) / 1.0, 2) = 72.73
```

A reader can reproduce this value from the `aggregate.components` in the front
matter alone — no eval-harness access needed.

## Reproduction

Run the following commands from the repository root:

```sh
# Prerequisites: install the eval extras + this repo's chat/gaia hub
# packages, start a Lemonade Server with the model on AMD Ryzen AI
# hardware, and have the Claude Code CLI on PATH (the eval driver).
uv pip install -e ".[dev,eval,ui,api]" -e hub/agents/chat/python -e hub/agents/gaia/python
lemonade-server serve   # in a separate shell; must stay running

# Step 0: stage fixtures + start the fixture server (see
# tests/fixtures/gaia/README.md for the staging contract)
python tests/fixtures/gaia/prepare_fixture_hub.py --skills-root <skills-root>
python tests/fixtures/gaia/serve_fixtures.py --port 8765   # separate shell

# Step 1: start the Agent UI backend (separate shell; NOT port 4001)
python -m gaia.ui.server --port 4200 --host 127.0.0.1

# Step 2: run every gaia category serially (never in parallel)
for c in gaia_core gaia_memory gaia_rag gaia_files gaia_data gaia_web \
         gaia_shell gaia_skills_lifecycle gaia_skills_tasks \
         gaia_skills_capture gaia_honesty gaia_tool_selection \
         gaia_code; do
  gaia eval agent --agent-type gaia --category $c \
    --backend http://127.0.0.1:4200 --budget 5.00 --exclude-tag live
done

# Step 3: generate this scorecard from ONE combined run's output dir
python hub/agents/gaia/python/packaging/gen_scorecard.py \
    --run-dir <run-dir-printed-by-the-eval> \
    --model claude-haiku-4-5 \
    --ctx-size 200000 \
    --hardware "<hardware class>"
```

See [eval-scorecard docs](https://amd-gaia.ai/docs/reference/eval-scorecard) and the [`adding-eval-scorecard` skill](.claude/skills/adding-eval-scorecard/SKILL.md) for the full setup guide.

## Environment

| Field | Value |
|-------|-------|
| gaia_commit | d104b3b1 |
| model | claude-haiku-4-5 |
| ctx_size | 200000 |
| hardware | developer workstation (x86-64, agent on the Claude API — no Lemonade, no AMD NPU/GPU inference) |
| eval_model | claude-sonnet-4-6 |
| note | HARNESS-VALIDATION card, pending first runner baseline: agent on claude-haiku-4-5 over the gaia_core category only (11 scenarios, tags local_blocked_no_embedder excluded). NOT the product baseline — that is Gemma-4-E4B-it-GGUF on the self-hosted runner, captured by the first full gaia_scorecard_refresh.yml dispatch (rebaseline=true). |

## Category breakdown

| Category | Total | Correct | Accuracy |
|----------|-------|---------|----------|
| gaia_core | 11 | 8 | 0.7273 |

## Performance

_Measured on the run environment above (model / hardware / gaia_commit / corpus size); the perf gate is report-only, so these are observed values, not pass/fail bars (see `tests/fixtures/email/perf_gate_thresholds.json`)._

| Metric | Value |
|--------|-------|
| ttft_s | 7.254 |
| throughput_tps | 38.7 |
| total_input_tokens | 90710 |
| total_output_tokens | 810 |
| scenarios_with_data | 2 |
