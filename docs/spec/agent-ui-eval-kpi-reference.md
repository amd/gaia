---
title: Agent UI Eval KPI Quick Reference
description: Quick reference table for all 16 Agent UI Eval KPIs including per-turn, scenario, and scorecard-level metrics with pass thresholds and file locations.
status: Published
---

# GAIA Agent UI Eval — KPI Quick Reference

**Date:** 2026-03-31 | **Source:** `docs/spec/agent-ui-eval-kpis.md` v1.1

---

## Per-Turn Quality KPIs (scored 0–10, every agent response)


| ID     | Name                | One-liner                                                                                                      | Weight | Pass Rule                                               |
| ------ | ------------------- | -------------------------------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------- |
| KPI-01 | `correctness`       | Factual accuracy of the agent's answer vs. ground truth; forced to 0 on hallucination or garbled output.       | 25%    | ≥ 4 required (runner-enforced); 0 = automatic turn FAIL |
| KPI-02 | `tool_selection`    | Right tool(s) called in the right order; 0 if wrong or missing tool entirely.                                  | 20%    | None (feeds overall)                                    |
| KPI-03 | `context_retention` | Whether the agent remembered and used information from prior turns; caps at 4 if it re-asks established facts. | 20%    | None (feeds overall)                                    |
| KPI-04 | `completeness`      | All parts of the user's question answered; 0 if the question was not addressed at all.                         | 15%    | None (feeds overall)                                    |
| KPI-05 | `efficiency`        | Actual tool calls vs. optimal path; 0 if the agent looped (3+ identical calls with no progress).               | 10%    | None (feeds overall)                                    |
| KPI-06 | `personality`       | Response is direct and confident, not sycophantic or hedged; 0 for agreeing with a wrong premise.              | 5%     | None (feeds overall)                                    |
| KPI-07 | `error_recovery`    | Agent handled failures or empty results gracefully; 0 if it gave up without trying an alternative.             | 5%     | None (feeds overall)                                    |


---

## Turn-Level Derived KPI


| ID     | Name                 | One-liner                                                                                           | Pass Rule                 | File:line           |
| ------ | -------------------- | --------------------------------------------------------------------------------------------------- | ------------------------- | ------------------- |
| KPI-08 | `turn_overall_score` | Deterministic weighted sum of KPI-01–07; the runner **overwrites** the judge's self-reported score. | ≥ 6.0 AND correctness ≥ 4 | `runner.py:322-334` |


---

## Scenario-Level KPIs (one per scenario run)


| ID     | Name                     | One-liner                                                                                                                                           | Pass Rule         | File:line                 |
| ------ | ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | ------------------------- |
| KPI-09 | `scenario_overall_score` | Mean of all `turn_overall_score` values for the scenario.                                                                                           | ≥ 6.0             | `runner.py:616-623`       |
| KPI-10 | `scenario_status`        | Categorical outcome: PASS / FAIL / BLOCKED_BY_ARCHITECTURE / TIMEOUT / BUDGET_EXCEEDED / INFRA_ERROR / SETUP_ERROR / SKIPPED_NO_DOCUMENT / ERRORED. | PASS              | `runner.py:633-709`       |
| KPI-11 | `root_cause`             | Judge-assigned failure category: `architecture`, `prompt`, `tool_description`, or `rag_pipeline`; null on PASS.                                     | null              | `judge_scenario.md:13-21` |
| KPI-12 | `elapsed_s`              | Wall-clock seconds per scenario; times out at 900s base + 90s/doc + 200s/turn (max 7200s).                                                          | No SLA (Gap G-02) | `runner.py:46-59`         |
| KPI-13 | `cost_estimate_usd`      | Claude judge API cost per scenario; baseline avg $0.126 (range $0.00–$0.33); hard cap $2.00.                                                        | ≤ $2.00           | `runner.py:46`            |


---

## Scorecard-Level KPIs (aggregated across all scenarios in a run)


| ID     | Name               | One-liner                                                                                                   | Production Target                  | File:line              |
| ------ | ------------------ | ----------------------------------------------------------------------------------------------------------- | ---------------------------------- | ---------------------- |
| KPI-14 | `judged_pass_rate` | **Primary metric.** `passed ÷ (PASS + FAIL + BLOCKED)` — infrastructure failures excluded from denominator. | ≥ 0.95 (Production)                | `scorecard.py:148-152` |
| KPI-15 | `avg_score`        | Mean scenario score; FAIL scores capped at 5.99 to prevent averaging inflation. Baseline: 8.61.             | ≥ 8.5 (Production)                 | `scorecard.py:49-59`   |
| KPI-16 | `by_category`      | Pass/fail counts and avg score broken down by scenario category (rag_quality, context_retention, etc.).     | No category avg < 7.0 (Production) | `scorecard.py:62-105`  |


---

## Legacy RAG Eval KPIs (`src/gaia/eval/eval.py` — separate system)


| ID     | Name               | One-liner                                                                                                                         | Pass Rule                  | File:line        |
| ------ | ------------------ | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------- | ---------------- |
| KPI-L1 | `similarity_score` | TF-IDF cosine similarity between agent response and ground truth answer.                                                          | ≥ 0.70                     | `eval.py:28-49`  |
| KPI-L2 | `overall_rating`   | Claude qualitative rating (excellent/good/fair/poor) weighted: correctness 40%, completeness 30%, conciseness 15%, relevance 15%. | ≥ fair + normalized ≥ 0.60 | `eval.py:88-128` |


---

## Gap KPIs (not yet implemented — tracked for roadmap)


| ID   | Name                   | One-liner                                                                                                            | Priority |
| ---- | ---------------------- | -------------------------------------------------------------------------------------------------------------------- | -------- |
| G-01 | `flakiness_score`      | Fraction of runs a scenario FAILs across N passes; needed to distinguish STABLE_PASS from FLAKY_PASS.                | P0       |
| G-02 | `latency_sla`          | Per-scenario elapsed time target; `avg_latency_ms` is tracked but no alert threshold is defined.                     | P1       |
| G-03 | `chunk_count`          | Documents indexed chunk count surfaced in scorecard; low chunk_count (e.g., 2 for a 500-row CSV) is invisible today. | P1       |
| G-04 | `exercisable_fraction` | `(total - SKIPPED_NO_DOCUMENT) / total`; 19 real-world scenarios always skip in CI.                                  | P1       |
| G-05 | `score_trend`          | Time-series of `judged_pass_rate` and `avg_score` across runs; no persistent history store exists.                   | P2       |
| G-06 | `judge_model_version`  | Exact judge model snapshot pinned in scorecard; needed to detect score drift from model updates.                     | P2       |
| G-07 | `ci_regression_gate`   | Automated PR/nightly workflow that blocks on PASS→FAIL regressions vs. baseline.                                     | P2       |
| G-08 | `by_persona`           | Pass rate breakdown by user persona (casual, power, confused, adversarial, data_analyst).                            | P3       |
| G-09 | `tool_call_count`      | Actual and unique tool call counts per turn for objective efficiency scoring.                                        | P3       |
| G-10 | `fact_coverage_pct`    | Fraction of manifest.json ground-truth facts exercised by at least one scenario turn.                                | P3       |


