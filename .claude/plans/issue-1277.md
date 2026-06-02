---
type: plan
source-issue: 1277
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
branch: tmi/issue-1277-perf-metrics
reflection_iterations: 1
agents_used: [planning, execution, validation]
---

# Issue #1277 — Performance metrics output (TTFT, throughput, latency, NPU)

## Goal
Log/export end-to-end perf metrics (TTFT, throughput, pipeline latency, NPU
utilization) and ship a configurable perf-threshold gate that *can* assert the
Strix Halo bars (TTFT <5s, throughput >10 tok/s, 50-email <5 min, peak mem <8 GB)
— in **report mode** by default, mirroring #1278's quality gate. Each metric is
explicitly marked **gating** vs **reported**.

## Stacked on #1278
Branch cut from `tmi/issue-1278-quality-metrics` (BASE_OK + PERF_PRESENT verified).
`benchmark.py` already carries #1278's *quality* blocks; I add *perf* blocks
alongside them — additive, never touching the quality machinery.

## Scope boundary (critical — build the code, flag the hardware)
- **BUILD locally (deterministic, no Lemonade / no live model):**
  - Extend `performance.py`: TTFT/throughput/latency/tokens already extracted;
    add **peak-memory** + **NPU-utilization** best-effort capture, and the
    `PerfThresholds` / `load_perf_thresholds` / `evaluate_perf_gate` gate
    (report mode by default, marks each metric gating-vs-reported, computes
    pass/fail + breaches WITHOUT hard-failing).
  - Add perf blocks to `benchmark.py` alongside #1278's quality blocks.
  - Commit `tests/fixtures/email/perf_gate_thresholds.json` manifest.
  - Unit tests driving extractor + gate from fixture metric arrays.
- **FLAG as hardware checkpoints (do NOT run locally):**
  - **NPU-utilization capture** needs Lemonade NPU telemetry on the Ryzen AI box.
    Captured as a best-effort field: `null`/"unavailable" off-NPU, populated when
    Lemonade exposes it. Fail-loud only if explicitly required; else "reported,
    not gating" off-hardware.
  - **Actual Strix Halo bar run** (real TTFT/tps/50-email/peak-mem) — nightly
    self-hosted (#1112).

## Gating vs reported (the AC's explicit marking)
The manifest marks each metric. Default committed posture:
- `ttft_max_s` (5.0) — **gating** when enforced
- `throughput_min_tps` (10.0) — **gating** when enforced
- `pipeline_max_s` (300.0, 50-email <5 min) — **gating** when enforced
- `peak_memory_max_gb` (8.0) — **gating** when enforced
- `npu_utilization` — **reported** (telemetry not on every box; never gating
  off-hardware). NPU has no committed bar; it is observed-only.

`enforce` ships **false** (report mode) just like #1278: the gate computes and
logs, CI (#1112) does NOT block until the bars are ratified on hardware. #1112
flips `enforce` in the manifest (data, not code).

## NPU semantics
- Lemonade today exposes only *static* NPU detection (`amd_npu.available`,
  `name`, `driver_version`, `power_mode`) via `/system-info` — NO real-time
  utilization %. So `extract_npu_utilization()` reads a best-effort
  `npu_utilization_percent` (or `npu_utilization`) key from a stats/system-info
  dict, returning a structured record: `available` flag + `utilization_percent`
  (`None` when absent) + `source` + `detail`. Off-NPU it is gracefully
  "unavailable" — never raises unless `require=True`.

## What #1112 consumes
- `tests/fixtures/email/perf_gate_thresholds.json` — the ONE committed source of
  the perf bars + `enforce` switch.
- `benchmark.load_default_perf_thresholds()` / `default_perf_thresholds_path()` —
  the discovery entry points (mirror the quality equivalents).
- `summarize_benchmark(..., perf_thresholds=...)` adds a `perf_gate` block whose
  `should_fail` is the hook CI keys off (report mode → always False).

## TDD steps
1. FAILING unit tests in `tests/unit/eval/test_performance_extractor.py`:
   - peak-memory extraction from fixture stats arrays
   - NPU "unavailable" when telemetry absent; populated when present; loud only
     when required
   - `evaluate_perf_gate` marks each metric gating-vs-reported, flags a synthetic
     breach (TTFT 9s) but `should_fail=False` in report mode; `True` when enforced
   - `load_perf_thresholds` loud on missing/malformed
2. Implement in `performance.py` + `benchmark.py` + manifest.
3. Green; lint; self-review.

## Lane boundary
OWN: `performance.py`, `benchmark.py` (perf blocks only), `perf_gate_thresholds.json`,
`test_performance_extractor.py`, `test_email_bench_throughput.py` (extend if needed).
Do NOT touch `quality_metrics.py`, `statistics.py`, or any email-agent/api/connectors source.

## Eval trigger
NO. Read-only eval harness; no LLM-affecting product path changed.
