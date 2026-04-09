---
title: Pipeline Metrics KPI Quick Reference
description: Quick reference for all 14 pipeline metric types (PM-01 to PM-14), production monitoring alerts, health bands, risk scoring, benchmark suite (BF-01 to BF-06), and QualityScorer 27 categories with pass thresholds.
status: Published
---

# Pipeline Metrics — KPI Quick Reference

**Branch:** `feature/pipeline-orchestration-v1` | **Date:** 2026-04-01
**Scope:** All pipeline performance and quality KPIs added in this branch — Core Metrics (PM-01–06), Pipeline-Native Metrics (PM-07–14), Production Monitor alerts, Health/Risk scoring, Benchmark Suite (BF-01–06), Quality Scorer (27 categories, 5 certification levels).

> For Agent UI Eval KPIs (`judged_pass_rate`, `avg_score`, per-turn scores, etc.), see `docs/spec/agent-ui-eval-kpi-reference.md`.

---

## Section 1 — Architecture Overview

The system is a 4-layer stack. Layer 1 (`src/gaia/metrics/`) defines the core storage and analysis primitives: `MetricType` (14-value enum), `MetricSnapshot` (frozen dataclass), `MetricsCollector` (thread-safe, SQLite-backed), `MetricsAnalyzer` (trend + anomaly detection), and `MetricsReport`. Layer 2 (`src/gaia/pipeline/`) wraps Layer 1 in a `PipelineMetricsCollector` and 7 hook classes that fire automatically at phase boundaries, loop boundaries, quality evaluation points, agent selection events, and hook execution events. Layer 3 (`src/gaia/metrics/production_monitor.py`, `benchmarks.py`) provides `ProductionMonitor` (async polling, 2 alert types) and `PipelineBenchmarker` (6 benchmark types). Layer 4 (`src/gaia/quality/`) implements `QualityScorer` over 27 categories in 6 dimensions with 5 certification levels.

All metrics flow through `MetricsCollector.record_*()` methods, which write `MetricSnapshot` rows to SQLite. The pipeline layer calls these methods automatically via hooks; application code never calls `record_*` directly for pipeline events.

```
PipelineEngine._execute_phase()
  → HookExecutor → PhaseEnterMetricsHook  → PipelineMetricsCollector.start_phase()
  → [phase runs]
  → HookExecutor → PhaseExitMetricsHook   → PipelineMetricsCollector.end_phase()
       → MetricsCollector.record_phase_duration()   [PM-09]
       → MetricsCollector.record_throughput()        [PM-07, if tokens > 0]
       → MetricsCollector.record_ttft()              [PM-08, if TTFT set]
  → LoopStartMetricsHook  → record LOOP_ITERATION_COUNT  [PM-10]
  → AgentSelectMetricsHook → record AGENT_SELECTION      [PM-13]
  → HookExecutionMetricsHook → record HOOK_EXECUTION_TIME [PM-11]
```

---

## Section 2 — Core Pipeline Metrics (PM-01 – PM-06)

Recorded by `MetricsCollector.record_*()` in `src/gaia/metrics/collector.py`.

| ID    | Name                  | Unit        | Formula                                    | Pass Threshold            | Recommendation Trigger | Source method              |
|-------|-----------------------|-------------|--------------------------------------------|---------------------------|------------------------|----------------------------|
| PM-01 | TOKEN_EFFICIENCY      | ratio (0–1) | `min(1.0, 10000 / total_tokens)`           | ≥ 0.90                    | mean < 0.70            | `record_token_usage()`     |
| PM-02 | CONTEXT_UTILIZATION   | ratio (0–1) | `tokens_used / context_window_size`        | ≥ 0.90                    | mean < 0.50            | `record_context_utilization()` |
| PM-03 | QUALITY_VELOCITY      | iterations  | count at `reached_threshold=True`          | ≤ 5 (> 5 = fail)          | mean > 3               | `record_quality_score()`   |
| PM-04 | DEFECT_DENSITY        | defects/KLOC| `defect_count / code_volume_kloc`          | ≤ 5 (> 5 = fail)          | mean > 5               | `record_defect_discovered()` |
| PM-05 | MTTR                  | hours       | `sum(resolved_at − discovered_at) / count` | ≤ 4 h (> 4 h = fail)      | mean > 4               | `record_defect_resolved()` |
| PM-06 | AUDIT_COMPLETENESS    | ratio (0–1) | `min(1.0, logged / expected)`              | ≥ 0.90                    | mean < 0.95            | `record_audit_event()`     |

---

## Section 3 — Pipeline-Native Metrics (PM-07 – PM-14)

Recorded by `PipelineMetricsCollector` and hook classes in `src/gaia/pipeline/`.

| ID    | Name                  | Unit             | Formula / Source                                        | Pass Threshold             | Source location                                  |
|-------|-----------------------|------------------|---------------------------------------------------------|----------------------------|--------------------------------------------------|
| PM-07 | TPS                   | tokens/sec       | `tokens / elapsed_seconds`                              | ≥ 10 (< 10 = fail)         | `pipeline/metrics_collector.py` — `end_phase()`  |
| PM-08 | TTFT                  | seconds          | Captured externally; set on `PhaseTiming`               | ≤ 5 s (> 5 s = fail)       | `pipeline/metrics_collector.py` — `end_phase()`  |
| PM-09 | PHASE_DURATION        | seconds          | `(phase_end − phase_start).total_seconds()`             | ≤ 300 s (> 300 s = fail)   | `pipeline/metrics_collector.py` — `end_phase()`  |
| PM-10 | LOOP_ITERATION_COUNT  | count            | Incremented by `LoopStartMetricsHook` per loop boundary | ≤ 10; matches `PipelineConfig.max_iterations` | `pipeline/metrics_hooks.py:206-211` — `LoopStartMetricsHook` |
| PM-11 | HOOK_EXECUTION_TIME   | seconds          | Elapsed seconds per individual hook invocation          | ≤ 1 s (> 1 s = fail)       | `pipeline/metrics_hooks.py` — `HookExecutionMetricsHook` |
| PM-12 | STATE_TRANSITION      | Unix timestamp   | `datetime.now(utc).timestamp()` at transition           | None (event marker)        | `pipeline/metrics_collector.py` — `record_state_transition()` |
| PM-13 | AGENT_SELECTION       | count            | Fixed value = 1.0 per routing selection event           | None (event counter)       | `pipeline/metrics_collector.py` — `record_agent_selection()` |
| PM-14 | RESOURCE_UTILIZATION  | ratio (0–1)      | `(cpu_pct + mem_pct) / 2 / 100`                         | None defined               | `pipeline/metrics_collector.py` — `record_resource_utilization()` |

---

## Section 4 — Production Health Monitoring

### 4.1 — Alert Rules (`ProductionMonitor`)

Source: `src/gaia/metrics/production_monitor.py`. Both alerts emit at level WARNING.

| ID          | Name              | Trigger condition                                        | Default threshold         |
|-------------|-------------------|----------------------------------------------------------|---------------------------|
| PM-ALERT-01 | Low Success Rate  | `loops_executed > 0 AND success_rate < 0.99`            | 0.99 (strict less-than)   |
| PM-ALERT-02 | Error Count       | `len(errors) > 10`                                       | 10 (> 10 triggers; 11 fires) |

**Note:** PM-ALERT-01 is suppressed when `loops_executed == 0`; `success_rate` returns 1.0 for idle systems. Reference: `production_monitor.py:56-58`.

### 4.2 — Real-Time Aggregate Metrics (`ProductionMonitor` fields)

| Name             | Definition                                   | Formula                                        | Source                     |
|------------------|----------------------------------------------|------------------------------------------------|----------------------------|
| `success_rate`   | Fraction of executed loops that succeeded    | `loops_successful / loops_executed` (property) | —                          |
| `avg_latency_ms` | Mean loop latency across all executed loops  | `total_latency_ms / loops_executed`            | —                          |
| `peak_memory_mb` | Maximum observed memory across all loops     | Settable field; caller updates on each loop    | —                          |
| `loops_failed`   | Count of loops that failed                   | Direct counter                                 | `production_monitor.py:41` |

### 4.3 — Health Bands (`MetricsReport.overall_health`)

| Status              | Condition               |
|---------------------|-------------------------|
| `excellent`         | score ≥ 0.95            |
| `good`              | score ≥ 0.85            |
| `acceptable`        | score ≥ 0.70            |
| `needs_improvement` | score ≥ 0.50            |
| `critical`          | score < 0.50            |

---

## Section 5 — Analyzer Risk Scoring (`MetricsAnalyzer.risk_level`)

Source: `src/gaia/metrics/` — `MetricsAnalyzer`.

**Score accumulation rules:**

| Event                                          | Points added |
|------------------------------------------------|--------------|
| Negative trend AND trend confidence > 0.70     | +1           |
| High anomaly detected                          | +2           |
| Critical anomaly detected                      | +3           |

**Risk level mapping (cumulative score):**

| Risk level | Score condition |
|------------|-----------------|
| `minimal`  | score = 0       |
| `low`      | score ≥ 1       |
| `medium`   | score ≥ 2       |
| `high`     | score ≥ 5       |

**Note:** `low` (abs_z: 2.0–2.49) and `medium` (abs_z: 2.5–2.99) anomalies are detected and reported but contribute 0 to the risk score. Reference: `analyzer.py:604-612`.

---

## Section 6 — Benchmark Suite (BF-01 – BF-06)

Source: `src/gaia/metrics/benchmarks.py` — `PipelineBenchmarker`.

| ID    | Type             | Purpose                                | Target                       | Bottleneck / fail condition                        |
|-------|------------------|----------------------------------------|------------------------------|----------------------------------------------------|
| BF-01 | LATENCY          | p50/p95/p99 latency distribution       | median < 15,000 ms           | `avg_latency_ms > 15,000`                          |
| BF-02 | THROUGHPUT       | Concurrent execution rate              | > 1,000 loops/hr             | `throughput < 1,000/hr`                            |
| BF-03 | MEMORY           | Peak memory consumption                | < 500 MB                     | `avg_memory > 500 MB`                              |
| BF-04 | TOKEN_EFFICIENCY | Token budget per feature               | < 10,000 tokens/feature      | No explicit bottleneck flag; feeds PM-01           |
| BF-05 | SCALE            | Scaling linearity under load increase  | Time grows linearly          | `time_factor > scale_factor × 1.5`                 |
| BF-06 | ENDURANCE        | Memory leak detection over time        | No sustained memory growth   | `growth > 20% AND increase > 5 MB`                 |

---

## Section 7 — Quality Scorer

Source: `src/gaia/quality/` — `QualityScorer`, `CertificationStatus`.

### 7.1 — Certification Thresholds

| Status              | Overall score | `passed` flag |
|---------------------|---------------|---------------|
| `EXCELLENT`         | ≥ 95          | True          |
| `GOOD`              | ≥ 85          | True          |
| `ACCEPTABLE`        | ≥ 75          | True          |
| `NEEDS_IMPROVEMENT` | ≥ 65          | False         |
| `FAIL`              | < 65          | False         |

Per-category floor: every individual category raw_score must be ≥ 70 to avoid flagging.

### 7.2 — Dimension Weights (Profile: `balanced`)

| Dimension               | Weight           | Category count    |
|-------------------------|------------------|-------------------|
| `code_quality`          | 25%              | 7 (CQ-01–CQ-07)   |
| `requirements_coverage` | 25%              | 4 (RC-01–RC-04)   |
| `testing`               | 20%              | 4 (TS-01–TS-04)   |
| `documentation`         | 15%              | 4 (DC-01–DC-04)   |
| `best_practices`        | 15%              | 5 (BP-01–BP-05)   |
| `additional`            | 7% (hardcoded)   | 3 (AC-01–AC-03)   |

**Note:** The dimension key in `QualityScorer.CATEGORIES` is `"requirements"`. The `balanced` profile in `weight_config.py` uses the key `"requirements_coverage"`. Both refer to the same dimension.

**Note:** The `additional` dimension is not present in the `balanced` profile; its three categories use hardcoded weights (AC-01: 3%, AC-02: 2%, AC-03: 2%).

### 7.3 — All 27 Quality Categories

| ID    | Dimension             | Category Name                  |
|-------|-----------------------|--------------------------------|
| CQ-01 | code_quality          | Syntax Validity                |
| CQ-02 | code_quality          | Code Style Consistency         |
| CQ-03 | code_quality          | Cyclomatic Complexity          |
| CQ-04 | code_quality          | DRY Principle Adherence        |
| CQ-05 | code_quality          | SOLID Principles               |
| CQ-06 | code_quality          | Error Handling                 |
| CQ-07 | code_quality          | Type Safety                    |
| RC-01 | requirements          | Feature Completeness           |
| RC-02 | requirements          | Edge Case Handling             |
| RC-03 | requirements          | Acceptance Criteria Met        |
| RC-04 | requirements          | User Story Alignment           |
| TS-01 | testing               | Unit Test Coverage             |
| TS-02 | testing               | Integration Test Coverage      |
| TS-03 | testing               | Test Quality/Assertions        |
| TS-04 | testing               | Mock/Stub Appropriateness      |
| DC-01 | documentation         | Docstrings/Comments            |
| DC-02 | documentation         | README Quality                 |
| DC-03 | documentation         | API Documentation              |
| DC-04 | documentation         | Usage Examples                 |
| BP-01 | best_practices        | Security Practices             |
| BP-02 | best_practices        | Performance Optimization       |
| BP-03 | best_practices        | Accessibility Compliance       |
| BP-04 | best_practices        | Logging/Monitoring             |
| BP-05 | best_practices        | Configuration Management       |
| AC-01 | additional            | Dependency Management          |
| AC-02 | additional            | Build/Deployment Readiness     |
| AC-03 | additional            | Backward Compatibility         |

---

## Section 8 — Quick Threshold Lookup

Single consolidated table. Every threshold, target, and alert condition across all systems. No other table in this document is needed for on-call or review use.

| System              | Metric / KPI              | Pass / Target                     | Fail / Alert condition              | Direction  |
|---------------------|---------------------------|-----------------------------------|-------------------------------------|------------|
| Core Metrics        | PM-01 TOKEN_EFFICIENCY    | ≥ 0.90                            | < 0.90 = fail; < 0.70 = recommend   | Higher is better |
| Core Metrics        | PM-02 CONTEXT_UTILIZATION | ≥ 0.90                            | < 0.90 = fail; < 0.50 = recommend   | Higher is better |
| Core Metrics        | PM-03 QUALITY_VELOCITY    | ≤ 5 iterations                    | > 5 = fail; > 3 = recommend         | Lower is better  |
| Core Metrics        | PM-04 DEFECT_DENSITY      | ≤ 5 defects/KLOC                  | > 5 = fail; > 5 = recommend         | Lower is better  |
| Core Metrics        | PM-05 MTTR                | ≤ 4 h                             | > 4 h = fail; > 4 h = recommend     | Lower is better  |
| Core Metrics        | PM-06 AUDIT_COMPLETENESS  | ≥ 0.90                            | < 0.90 = fail; < 0.95 = recommend   | Higher is better |
| Pipeline-Native     | PM-07 TPS                 | ≥ 10 tokens/sec                   | < 10 = fail                         | Higher is better |
| Pipeline-Native     | PM-08 TTFT                | ≤ 5 s                             | > 5 s = fail                        | Lower is better  |
| Pipeline-Native     | PM-09 PHASE_DURATION      | ≤ 300 s                           | > 300 s = fail                      | Lower is better  |
| Pipeline-Native     | PM-10 LOOP_ITERATION_COUNT| ≤ 10 (matches `max_iterations`)   | > 10 = fail                         | Lower is better  |
| Pipeline-Native     | PM-11 HOOK_EXECUTION_TIME | ≤ 1 s                             | > 1 s = fail                        | Lower is better  |
| Pipeline-Native     | PM-12 STATE_TRANSITION    | N/A (event marker)                | No threshold                        | Event only       |
| Pipeline-Native     | PM-13 AGENT_SELECTION     | N/A (event counter)               | No threshold                        | Event only       |
| Pipeline-Native     | PM-14 RESOURCE_UTILIZATION| No defined target                 | No threshold (gap — see Section 9)  | Lower is better  |
| Production Monitor  | PM-ALERT-01 success_rate  | ≥ 0.99                            | < 0.99 → WARNING                    | Higher is better |
| Production Monitor  | PM-ALERT-02 error count   | ≤ 10 errors                       | > 10 → WARNING                      | Lower is better  |
| Health Bands        | overall_health excellent  | score ≥ 0.95                      | —                                   | Higher is better |
| Health Bands        | overall_health good       | score ≥ 0.85                      | —                                   | Higher is better |
| Health Bands        | overall_health acceptable | score ≥ 0.70                      | —                                   | Higher is better |
| Health Bands        | overall_health needs_impr | score ≥ 0.50                      | —                                   | Higher is better |
| Health Bands        | overall_health critical   | —                                 | score < 0.50                        | Higher is better |
| Analyzer Risk       | risk_level minimal        | score = 0                         | —                                   | Lower is better  |
| Analyzer Risk       | risk_level low            | score ≥ 1                         | —                                   | Lower is better  |
| Analyzer Risk       | risk_level medium         | score ≥ 2                         | —                                   | Lower is better  |
| Analyzer Risk       | risk_level high           | —                                 | score ≥ 5                           | Lower is better  |
| Benchmark           | BF-01 LATENCY             | median < 15,000 ms                | avg > 15,000 ms = bottleneck        | Lower is better  |
| Benchmark           | BF-02 THROUGHPUT          | > 1,000 loops/hr                  | < 1,000/hr = bottleneck             | Higher is better |
| Benchmark           | BF-03 MEMORY              | < 500 MB peak                     | avg > 500 MB = bottleneck           | Lower is better  |
| Benchmark           | BF-04 TOKEN_EFFICIENCY    | < 10,000 tokens/feature           | No explicit bottleneck flag         | Lower is better  |
| Benchmark           | BF-05 SCALE               | time grows linearly               | time_factor > scale_factor × 1.5   | Lower is better  |
| Benchmark           | BF-06 ENDURANCE           | no sustained growth               | growth > 20% AND increase > 5 MB   | Lower is better  |
| Quality Scorer      | Certification EXCELLENT   | overall ≥ 95                      | —                                   | Higher is better |
| Quality Scorer      | Certification GOOD        | overall ≥ 85                      | —                                   | Higher is better |
| Quality Scorer      | Certification ACCEPTABLE  | overall ≥ 75 (passed=True)        | —                                   | Higher is better |
| Quality Scorer      | Certification NEEDS_IMPR  | overall ≥ 65 (passed=False)       | —                                   | Higher is better |
| Quality Scorer      | Certification FAIL        | —                                 | overall < 65 (passed=False)         | Higher is better |
| Quality Scorer      | Per-category floor        | raw_score ≥ 70                    | < 70 = category flagged             | Higher is better |

---

## Section 9 — Known Gaps

Items tracked here are not implemented as of branch `feature/pipeline-orchestration-v1`.

| Gap ID     | Affected KPI                          | Description                                                                                         | Priority |
|------------|---------------------------------------|-----------------------------------------------------------------------------------------------------|----------|
| GAP-PM-01  | PM-14 RESOURCE_UTILIZATION            | No alert threshold or pass/fail rule defined for resource utilization; metric is recorded but silent.| P0       |
| GAP-PM-02  | PM-12 STATE_TRANSITION                | No pass/fail rule; events are logged as timestamps only; no SLA or anomaly detection on transitions.| P1       |
| GAP-PM-03  | PM-13 AGENT_SELECTION                 | No pass/fail rule; counts selection events only; no threshold for excessive re-routing.             | P1       |
| GAP-PM-04  | PM-ALERT-03 (missing)                 | No latency SLA alert in `ProductionMonitor`; `avg_latency_ms` is tracked but never triggers a warning. | P0   |
| GAP-PM-05  | BF-04 TOKEN_EFFICIENCY bottleneck     | `PipelineBenchmarker` records token budget results but defines no bottleneck condition flag for BF-04. | P2    |
