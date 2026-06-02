---
type: plan
source-issue: 1278
repo: amd/gaia
created: 2026-06-02
status: complete
work_type: code-feature
complexity: standard
tdd_required: true
suggested_team_size: 1
estimated_files_changed: 5
test_command: ".venv/bin/python -m pytest tests/unit/eval/ -q"
build_command: "uv pip install -e .[dev]"
lint_command: "python util/lint.py --black --isort"
branch: tmi/issue-1278-quality-metrics
reflection_iterations: 1
agents_used: [planning, execution, validation]
---

# Issue #1278 — Quality & connection metrics logging

## Goal
Log/export categorization results + FP/FN + connector diagnostics, feed them into
the eval harness (`benchmark.py`), and ship a configurable-threshold CI gate that
*can* assert FP<5% / FN<2% on the #1230 corpus — in **report mode** for now.

## Scope boundary (critical)
- The hard FP<5%/FN<2% ENFORCEMENT depends on categorization accuracy (#1266,
  out of scope; corpus accuracy ~0.40). So build the full machinery but ship the
  gate **non-enforcing** by default. Thresholds + the `enforce` switch live in ONE
  config value so #1112 (CI) / #1266 can flip enforcement on later.
- OWN: `src/gaia/eval/quality_metrics.py`, `src/gaia/eval/benchmark.py` (quality
  wiring only), a thresholds manifest, `tests/unit/eval/`.
- DO NOT touch: `performance.py` (#1277), `statistics.py`, the email agent
  `tools/`/`agent.py`/`api/`/`connectors/` source. Connector status read-only.

## Existing state
- `quality_metrics.py` already has `Confusion` (tp/fp/fn/tn + rates) and axis
  helpers (`confusion_for_flag`, `confusion_for_categories`, `category_accuracy`).
- `benchmark.build_result` already emits a per-run `quality` block (spam /
  phishing / needs_attention confusion dicts + category_accuracy).
- #1230 ground truth: `tests/fixtures/email/ground_truth.json` (220 labelled +
  one `_meta` row). Schema: per-id `category`, `is_spam`, `is_phishing`, ….
- `connectors.api.list_connections()` → `[{provider, account_email, scopes,
  connected_at, error?}]` — the read-only connection-status shape to diagnose.

## What is MISSING (this issue)
1. **Categorization-results export** — per-email predicted-vs-expected rows that
   flag FP/FN on the gated axis, plus aggregate confusion. (`quality_metrics`)
2. **Connector/connection diagnostics export** — pure transform from the
   `list_connections()` shape → normalized per-connector + aggregate diagnostics
   (reachable / scope-complete / stale / errored). Read-only, deterministic.
3. **Configurable-threshold gate** (`QualityThresholds` + `evaluate_gate`) —
   compares FP-rate/FN-rate on a chosen axis to thresholds; returns pass/breaches;
   `enforce` flag gates whether the harness should fail. Report mode by default.
4. **Thresholds manifest** — `tests/fixtures/eval_baselines/quality_gate_thresholds.json`
   (fp_max=0.05, fn_max=0.02, axis="needs_attention", enforce=false) loaded by ONE
   helper so #1112/#1266 can flip `enforce`.
5. **Wire into `summarize_benchmark`** — add aggregate `quality` + `quality_gate`
   blocks to the summary dict so the harness/CI consume them. (`benchmark.py`)

## TDD steps
1. FAILING tests in `tests/unit/eval/test_quality_metrics.py` (extend) +
   `test_benchmark.py` (extend): categorization export shape + FP/FN listing;
   connection-diagnostics export shape; gate flags a synthetic high-FP breach and
   passes a clean input; malformed inputs raise loudly; threshold-manifest load;
   `summarize_benchmark` carries `quality_gate`.
2. Implement in `quality_metrics.py` + `benchmark.py` + manifest.
3. Green + lint.

## Fail-loud contract
- Malformed connection entry (not a dict, missing `provider`) → `ValueError`.
- Unknown gate axis / missing axis in the quality block → `ValueError`.
- Malformed thresholds manifest (missing key, non-numeric) → `ValueError`.
- No silent skips, no default-to-pass.

## What #1112 (CI) consumes
- `quality_metrics.load_quality_thresholds()` → the single manifest value.
- `summarize_benchmark(...)["quality_gate"]` → `{passed, enforce, breaches, ...}`.
  CI flips the manifest `enforce` to `true` (after #1266) and fails on
  `enforce and not passed`.

## Eval-trigger: NO — pure metrics math, no LLM-affecting product path.
