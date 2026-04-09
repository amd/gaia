---
title: Pipeline Metrics Competitive Analysis
description: Analysis of the three-layer metric stack (Infrastructure, Engineering Health, Capability), TTFT/TPS deep dive with AMD NPU/iGPU context, and competitive positioning vs Manus AI, OpenClaw, and local LLM tools.
status: Published
---

# GAIA Pipeline Metrics: What's New, How It Works, and Competitive Context

**Date:** 2026-04-01 | **Branch:** `feature/pipeline-orchestration-v1`

This document explicitly maps which metrics are new in this branch versus pre-existing in `main`, explains the TTFT/TPS/quality scoring stack in depth with AMD hardware context, and evaluates whether these metrics enable competitive comparison with systems like Manus AI.

---

## Section 1: What Was Already There (Before This Branch)

These five metrics existed in `main` before `feature/pipeline-orchestration-v1` was opened. They are not new.

**These existed before `feature/pipeline-orchestration-v1`.**

| Metric | File | What It Measures | Method |
|---|---|---|---|
| `similarity_score` | `src/gaia/eval/eval.py:28-49` | TF-IDF cosine similarity between agent response and ground-truth answer | `TfidfVectorizer` + `cosine_similarity`; pass threshold >= 0.70 |
| `overall_rating` | `src/gaia/eval/eval.py:88-128` | Qualitative behavioral quality of agent response: correctness (40%), completeness (30%), conciseness (15%), relevance (15%) | Claude-as-judge returning excellent/good/fair/poor; normalized score >= 0.60 required to pass |
| `pass_fail` | `src/gaia/eval/eval.py:51-75` | Binary scenario outcome derived from similarity + Claude judge combined criteria | Correctness must be at least "fair"; poor correctness overrides all other passing signals |
| `elapsed_time` | `src/gaia/eval/eval.py` result dicts | Wall-clock seconds per scenario run | Python `time.time()` delta; no SLA threshold enforced |
| `cost_estimate_usd` | `src/gaia/eval/eval.py` result dicts | Estimated Claude API billing cost for the judge calls in a scenario | Hard cap at $2.00 per scenario |

These five metrics form the **behavioral evaluation layer** — they ask whether the agent's response served the user. Everything in Section 2 is additive.

---

## Section 2: What Is New — The Three-Layer Stack

### 2.1 The Metric Taxonomy: Three Distinct Layers

The 14 new `MetricType` enum values added in `src/gaia/metrics/models.py` divide cleanly into three functional layers. The layers answer different questions for different audiences.

| Layer | Label | What It Measures | Primary Audience | Externally Comparable? |
|---|---|---|---|---|
| 1 | Infrastructure | LLM throughput and latency during pipeline execution | Engineers, hardware marketing | Yes — against Ollama, LM Studio, Jan.ai, NPUBenchmark.org |
| 2 | Engineering Health | Structural quality, pipeline control overhead, audit coverage | Internal SRE, release quality gates | No — no published industry equivalent |
| 3 | Capability | Task success rate and behavioral response quality | Product, competitive benchmarking | Partially — pre-existing behavioral eval; task completion rate not yet published |

The 14 new enum values split across two implementation phases documented in the codebase:

- **Phase 1 (core 6):** `TOKEN_EFFICIENCY`, `CONTEXT_UTILIZATION`, `QUALITY_VELOCITY`, `DEFECT_DENSITY`, `MTTR`, `AUDIT_COMPLETENESS`
- **Phase 2 (pipeline-native 8):** `TPS`, `TTFT`, `PHASE_DURATION`, `LOOP_ITERATION_COUNT`, `HOOK_EXECUTION_TIME`, `STATE_TRANSITION`, `AGENT_SELECTION`, `RESOURCE_UTILIZATION`

### 2.2 Layer 1 — Infrastructure Metrics (NEW)

**Phase 1 — Token Economy:**

`TOKEN_EFFICIENCY` measures how many tokens the pipeline consumed per unit of useful output delivered. Unit: `tokens/feature` (see `models.py:139`). Higher is better. This metric exposes whether the pipeline's multi-phase prompt construction is lean or bloated — a pipeline that requires 8,000 tokens of context to generate a 200-token result has a different efficiency profile than one requiring 1,200 tokens. This is distinct from `CONTEXT_UTILIZATION`, which measures what fraction of the available context window was filled with relevant content versus padding and boilerplate. Both are efficiency signals, not quality signals.

`CONTEXT_UTILIZATION` is expressed as a percentage. A value of 0.20 means 80% of the available context window went unused — which may indicate under-use of available retrieval results, or over-conservative prompt design. A value above 0.95 may indicate the pipeline is at risk of context overflow on longer inputs.

**Phase 2 — The Performance Triad (TPS, TTFT, PHASE_DURATION):**

These three metrics form the instrumentation backbone for hardware performance claims. `TPS` and `TTFT` are covered in depth in Section 3. `PHASE_DURATION` records total wall-clock time for each named pipeline phase (e.g., PLANNING, DEVELOPMENT, REVIEW, TESTING). Its fail threshold is > 300 seconds per phase (see `models.py:419-422`). In a multi-phase pipeline, phase durations sum; a 5-phase pipeline where each phase takes 120 seconds delivers results in 10 minutes regardless of raw LLM TPS.

### 2.3 Layer 2 — Engineering Health Metrics (NEW)

**Phase 1:**

`QUALITY_VELOCITY` measures how many loop iterations a pipeline required to reach the quality threshold on a given artifact. A value of 1 means the artifact passed quality checks on the first generation attempt. A value of 5 triggers the fail condition (see `models.py:398-401`). This metric distinguishes pipelines that converge quickly from those that spin in rework cycles.

`DEFECT_DENSITY` counts defects per thousand lines of generated code (KLOC). The fail threshold is > 5 defects/KLOC (see `models.py:402-405`). This connects pipeline output to standard software engineering quality language and enables comparison against human-authored baselines.

`MTTR` (**Mean Time to Remediate**) measures the average time in hours between defect detection and successful remediation within a pipeline run. Fail threshold: > 4 hours (see `models.py:406-409`). In automated pipeline context, this captures how many iteration cycles pass before a detected defect is corrected.

`AUDIT_COMPLETENESS` measures what fraction of pipeline actions were successfully logged to the audit trail (`src/gaia/pipeline/audit_logger.py`). Expressed as a percentage; higher is better. A score of 0.70 means 30% of actions went unlogged — a compliance and debuggability problem. This metric is particularly relevant for enterprise deployments requiring full auditability of AI-generated decisions.

**Phase 2:**

`LOOP_ITERATION_COUNT` is the raw count of iterations per pipeline loop. Fail threshold: > 10 (see `models.py:423-426`). Combined with `QUALITY_VELOCITY`, this distinguishes pipelines that take many small iterations from those that take fewer but heavier passes.

`HOOK_EXECUTION_TIME` tracks how long each of the 7 pipeline hooks takes to execute (`src/gaia/pipeline/metrics_hooks.py`). Fail threshold: > 1 second per hook (see `models.py:427-430`). Hook overhead that exceeds 1 second begins to compete meaningfully with LLM generation time in fast pipelines.

`STATE_TRANSITION` records timestamps for pipeline state changes. This is an event trace metric rather than a scalar — its primary value is in post-hoc reconstruction of pipeline execution sequences for debugging.

`AGENT_SELECTION` tracks which agent was selected at each routing decision point and captures the decision metadata. This feeds analysis of whether the `RoutingAgent` (`src/gaia/agents/routing/agent.py`) is making consistent, correct choices across similar inputs.

`RESOURCE_UTILIZATION` measures CPU/memory consumption as a percentage. Fail threshold: applied via the percentage metric path in `quality_check()` (see `models.py:386-393`). On AMD Ryzen AI hardware, this metric is particularly informative because NPU workloads run on a separate compute block — high CPU utilization during inference may indicate the NPU offload path failed to activate.

### 2.4 Layer 3 — Capability Metrics

**Pre-existing behavioral evaluator (eval.py):** The Claude-as-judge system in `src/gaia/eval/eval.py` (see Section 1) remains the primary capability measurement. It answers: did the agent's response correctly and completely serve the user's intent? This is behavioral quality.

**New integration (EvalScenarioMetrics):** `src/gaia/eval/eval_metrics.py` introduces `EvalScenarioMetrics` — a dataclass that captures `duration_seconds`, `tokens_generated`, `cost_estimate_usd`, and `status` per scenario. This bridges the existing behavioral evaluator with the new infrastructure metrics, enabling correlation: for example, whether scenarios that took longer also showed lower behavioral quality scores. This is a integration layer, not a replacement for either the behavioral or infrastructure systems.

**The Layer 3 gap:** Neither the pre-existing behavioral evaluator nor the new integration layer produces a standardized task completion rate on a public benchmark (GAIA benchmark, AgentBench, etc.). This is an honest gap. Without a published task completion rate on a defined benchmark, GAIA cannot make quantitative competitive capability claims against Manus AI, H2O.ai h2oGPTe, or OpenAI Deep Research. Section 5 addresses this directly.

---

## Section 3: TTFT and TPS — A Deep Explanation

### 3.1 Time to First Token (TTFT)

**What it is.** TTFT is the wall-clock elapsed time from when a request is submitted to the LLM backend to when the first token of the response begins streaming. It is **perceived latency** — the duration during which the user interface appears frozen or waiting. Even if full response generation takes 30 seconds at high TPS, a TTFT of 0.6 seconds makes the interface feel responsive. For agentic pipelines, each phase incurs its own TTFT, so multi-phase pipelines multiply this penalty.

**How GAIA captures it.** Instrumentation lives in `PhaseTiming.record_first_token()` at `src/gaia/pipeline/metrics_collector.py:62-66`:

```python
def record_first_token(self) -> None:
    if self.started_at and not self.first_token_at:
        self.first_token_at = datetime.now(timezone.utc)
        self.ttft = (self.first_token_at - self.started_at).total_seconds()
```

`started_at` is set when the phase begins (via `PhaseTiming.start()` at `metrics_collector.py:52-54`). The formula is a simple delta: `ttft = (first_token_at - started_at).total_seconds()`. This measures end-to-end perceived latency from phase start, which includes any LLM client connection overhead — not just the model's internal time-to-first-token.

**AMD's NPU architecture makes TTFT a hardware differentiator.** In AMD's Ryzen AI hybrid execution model, inference is split by phase:

- The **NPU** (Neural Processing Unit, 50 TOPS+ on Ryzen AI Max+ 395) handles the **prefill phase** — processing the input prompt tokens through the transformer attention layers. The prefill phase is compute-bound (it processes all prompt tokens in parallel). NPU's high TOPS advantage directly translates to faster prefill completion, which is what drives TTFT.
- The **iGPU** handles the **decode phase** — autoregressive generation of one token at a time. Decode is bandwidth-bound, not compute-bound; it requires moving model weights from memory on every token step.

This means AMD's architecture has a structural advantage specifically in TTFT: the NPU was designed for exactly the parallel compute-heavy workload that prefill represents.

**Real AMD numbers.** AMD's MLPerf Client v1.0 results (published at amd.com, 2025) demonstrate:

- Ryzen AI Max+ 395 with hybrid NPU+iGPU: **TTFT < 0.7 seconds** on Phi-3.5
- Ryzen AI Max+ 395 on 7B/8B models: **TTFT approximately 1 second**

Source: [AMD MLPerf Client v1.0](https://www.amd.com/en/developer/resources/technical-articles/2025/unlocking-peak-ai-performance-with-mlperf-client-on-ryzen-ai-.html)

**Cold vs. warm caveat.** The NPU requires DMA (Direct Memory Access) weight loading before execution. On a **cold start** (first inference after system boot or model load), TTFT is measurably higher because weight transfer to NPU memory has not completed. On **warm starts** (subsequent inferences with weights resident), TTFT drops to the figures cited above. Any benchmark or monitoring system that includes cold-start TTFT alongside warm-start TTFT without labeling the distinction will produce misleading results. GAIA's current `PhaseTiming` implementation does not distinguish cold vs. warm — this should be documented as a measurement caveat.

**GAIA's threshold and its implications for agentic loops.** The `MetricSnapshot.quality_check()` method flags TTFT as a failure when it exceeds 5 seconds (see `src/gaia/metrics/models.py:415-418`). Five seconds is a conservative floor, well above AMD hardware capability. In a 5-phase pipeline on Ryzen AI Max+ 395 hardware, cumulative TTFT across all phases at < 0.7 seconds per phase totals < 3.5 seconds — below the 5-second per-phase threshold with significant headroom.

**The TPS formula's TTFT dependency.** The current `PhaseTiming.get_tps()` implementation divides `token_count` by `duration_seconds`, where `duration_seconds` includes TTFT (`metrics_collector.py:72-81`). This means **reported TPS is lower than raw generation speed**. A phase that takes 10 seconds total with a 1-second TTFT and generates 90 tokens reports 9.0 TPS, while true generation speed during the decode phase was 90 tokens / 9 seconds = 10.0 TPS. This distinction matters when communicating hardware capability externally.

### 3.2 Tokens Per Second (TPS)

**What it is.** TPS measures how many output tokens the LLM generates per second of elapsed time during a pipeline phase. It is **generation throughput** — the metric that determines how long a response takes once streaming has started. Unlike TTFT, TPS is dominated by the decode phase of inference, which is bandwidth-bound: the bottleneck is how fast the hardware can stream model weights from memory to compute units, not how many FLOPS are available.

**Hardware truth table.** Published benchmarks as of early 2026 for representative devices:

| Device | Model | TPS | Source |
|---|---|---|---|
| Ryzen AI Max+ 395 (Hybrid NPU+iGPU) | Phi-3.5 | 61 | AMD MLPerf Client v1.0 |
| Ryzen AI 9 HX 375 | Phi-3.5 | 27+ | AMD MLPerf Client v1.0 |
| Apple M2 Ultra | 7B Q4 | ~94 | Community benchmarks |
| NVIDIA RTX 5090 (discrete GPU) | 7B | 150-220+ | Community benchmarks |

AMD's iGPU is bandwidth-optimized for the decode workload. Discrete GPUs (RTX 5090) and high-bandwidth unified memory systems (Apple M2 Ultra) outperform integrated solutions at TPS on equivalently-sized models. AMD's competitive TPS story is strongest on the Ryzen AI Max+ 395's high-bandwidth LPDDR5x memory configuration.

**Why GAIA's 10 TPS floor is conservative.** The fail threshold of < 10 TPS (see `src/gaia/metrics/models.py:411-413`) is intentionally set to catch pathological cases — a pipeline falling below 10 TPS on AMD Ryzen AI hardware would indicate a hardware configuration problem, driver issue, or model that significantly exceeds available memory capacity. On GAIA's default models, AMD hardware far exceeds this floor:

- `Qwen3-0.6B-GGUF` (general tasks): A 0.6B model on NPU should deliver 100+ TPS
- `Qwen3.5-35B-A3B-GGUF` (agent tasks): A 35B MoE model is memory-intensive; TPS depends heavily on the specific hardware memory configuration

**What TPS means for agentic pipelines.** Each pipeline phase generates its own token stream. A 5-phase pipeline where each phase generates 300 tokens at 61 TPS takes approximately 24.5 seconds of pure generation time (excluding TTFT). At 27 TPS (Ryzen AI 9 HX 375), the same pipeline takes approximately 55.5 seconds. TPS choices compound: model selection (0.6B vs. 35B), memory configuration, and GPU driver state all affect every phase in a multi-phase run.

**Pipeline-TPS vs. raw-LLM-TPS.** GAIA's `PHASE_DURATION`-based TPS is a pipeline-level measurement. It includes all non-generation overhead within a phase: tool calls, retrieval operations, hook execution, state serialization. Raw LLM TPS from tools like Ollama or LM Studio measures only the model serving layer. These numbers should not be directly compared without clear labeling. GAIA's reported TPS will always be lower than or equal to the raw LLM TPS for the same model on the same hardware.

### 3.3 The TTFT/TPS Interplay in AMD's Hybrid Architecture

AMD's Ryzen AI hybrid execution model explicitly separates prefill (TTFT) from decode (TPS) onto architecturally distinct compute resources:

```
User Request
     |
     v
  [NPU — 50 TOPS+]
  Prefill Phase
  (all prompt tokens processed in parallel)
  Compute-bound → drives TTFT
     |
     v
  [iGPU — High-bandwidth memory]
  Decode Phase
  (one token generated per step, autoregressive)
  Bandwidth-bound → drives TPS
     |
     v
  Streaming Output
```

GAIA's `PipelineMetricsCollector` captures both TTFT (via `record_first_token()`) and TPS (via `get_tps()`) within the same phase timing object. This means a single GAIA pipeline run can produce both data points in one instrumentation pass — without requiring separate profiling tools.

**The competitive claim this enables:** GAIA is positioned to be the first agent framework that simultaneously reports AMD NPU-driven TTFT and iGPU-driven TPS within actual agentic pipeline execution, tied to real AMD hardware disclosure. NPUBenchmark.org ([npubenchmark.org](https://www.npubenchmark.org/)) is establishing the emerging standard for PC NPU benchmarking covering AMD Ryzen AI 300, Qualcomm Snapdragon X, and Intel Core Ultra Series 2 — and GAIA's instrumentation is architecturally aligned with what this standard measures.

**Cold/warm caveat applies here too.** A GAIA benchmark report that mixes cold-start and warm-start measurements across phases will show artificially elevated TTFT on the first phase and reduced TTFT on subsequent phases (once NPU weights are resident). Run-level TTFT averaging masks this pattern. Phase-level TTFT reporting with a cold/warm flag is the correct disclosure.

---

## Section 4: Quality Scoring — What It Measures and What It Doesn't

### 4.1 The Pre-Existing Behavioral Evaluator

`src/gaia/eval/eval.py` uses Claude as an external judge to evaluate agent responses against ground truth. It has been in `main` since before this branch.

The scoring weights:

| Criterion | Weight | What It Asks |
|---|---|---|
| Correctness | 40% | Is the factual content accurate? |
| Completeness | 30% | Were all parts of the question addressed? |
| Conciseness | 15% | Was the response appropriately brief, without padding? |
| Relevance | 15% | Did the response stay on topic? |

Pass requires: normalized weighted score >= 0.60 AND correctness rated at least "fair". A "poor" correctness rating overrides all other passing signals (see `eval.py:127-128`). This is a behavioral measurement — it characterizes whether the agent served the user's intent.

### 4.2 The New Structural QualityScorer

`src/gaia/quality/scorer.py` introduces a `QualityScorer` that evaluates generated artifacts across **27 validation categories** organized into 6 dimensions, producing a `QualityReport` with `CertificationStatus`.

The 27 categories span structural and syntactic properties of pipeline-generated artifacts: code coverage, documentation completeness, security patterns (via `src/gaia/quality/validators/security_validators.py`), test quality (via `src/gaia/quality/validators/test_validators.py`), and code style. Each category runs through a dedicated validator class; scores are aggregated into dimensional scores and then a composite `CertificationStatus`.

This is a **structural measurement** — it characterizes whether the generated artifacts are correctly formed.

### 4.3 The Critical Distinction

These two systems measure orthogonal properties. Treating either as a substitute for the other is a category error.

| Behavioral Quality (pre-existing, eval.py) | Structural Quality (new, quality/scorer.py) |
|---|---|
| Answers: Did the response serve the user? | Answers: Is the generated artifact correctly formed? |
| Method: LLM-as-judge | Method: Rule-based validators (27 categories) |
| Measures: Correctness, completeness, relevance | Measures: Code coverage, security patterns, test quality, documentation completeness |
| Input: Agent response vs. ground truth | Input: Generated code/doc artifacts |
| Audience: Product quality, user success | Audience: Engineering SLOs, release gates |
| Externally comparable: Partially (via GAIA benchmark, AgentBench) | Externally comparable: No industry standard |

A pipeline that passes all 27 structural quality checks may still fail the behavioral evaluator (structurally correct code that solves the wrong problem). A pipeline that passes the behavioral evaluator may fail structural checks (a correct but poorly tested or undocumented implementation). Both are valid and necessary — they should be reported as separate signals.

### 4.4 What Neither Measures

Neither the behavioral evaluator nor the structural QualityScorer produces a metric comparable to published agentic benchmark results:

- **GAIA benchmark task completion rate** (used by Manus AI, H2O.ai): Neither system produces this.
- **AgentBench task success rate** across 8 standardized environments: Neither system produces this.
- **Standardized scenario pass rate on a public dataset**: Neither system produces this.

GAIA's `judged_pass_rate` and `avg_score` from `src/gaia/eval/scorecard.py` (production targets: >= 0.95 pass rate, >= 8.5 avg score) are measured against GAIA's own internal scenario set, not a standardized public benchmark. Until GAIA's scenario set is validated against a public benchmark, these scores cannot be directly compared to Manus AI's reported numbers.

---

## Section 5: Competitive Evaluation — Can We Compare GAIA to Manus AI?

### 5.1 What Manus AI Actually Claims

Manus AI reported the following scores on the **GAIA benchmark** (a standardized agentic reasoning benchmark):

| Level | Task Type | Manus AI Score | Prior Best |
|---|---|---|---|
| Level 1 | Basic | 86.5% | ~67.9% |
| Level 2 | Intermediate | 70.1% | ~67.36% (OpenAI Deep Research) |
| Level 3 | Advanced | 57.7% | — |

Source: [GetBind analysis, March 2025](https://blog.getbind.co/2025/03/10/manus-ai-agent-what-does-it-mean-for-coding/)

Contextual disclosures Manus AI does not prominently headline:

- **Infrastructure:** Cloud-hosted, not local execution
- **Model stack:** Multi-model (Claude/Anthropic + Alibaba Qwen + proprietary models)
- **Verification status:** Self-reported during closed beta; independent third-party verification is limited
- **Comparison type:** These are GAIA benchmark scores — a specific public benchmark for general AI assistants — not a pipeline-engineering quality measure

H2O.ai's h2oGPTe Agent reported 75% on the GAIA benchmark test set, considered more rigorous than the validation set Manus uses for its Level 1-3 breakdown.

### 5.2 Why Direct Comparison Is Not Currently Possible

| Requirement for Fair Comparison | Manus AI Status | GAIA AMD Status |
|---|---|---|
| Standard benchmark task set | GAIA benchmark (public) | None published |
| Task success criterion | Defined by benchmark | Scenario-level, internal, not standardized |
| Hardware disclosure | Cloud (unspecified cloud provider, specs unknown) | AMD Ryzen AI (specific, disclosed) |
| Deployment scope | Cloud agent, networked tools | Local-only agent, offline-first |
| Model access | Multi-model cloud stack | Single local model per run |
| Verification | Self-reported, limited independent check | Not yet submitted to any public benchmark |

These are not equivalent systems operating in the same tier. A cloud-hosted multi-model agent with internet access completing GAIA benchmark tasks is solving a fundamentally different problem than a local agent running on consumer AMD hardware. Comparing Manus AI's GAIA Level 1 score (86.5%) to any GAIA agent score would require submitting GAIA agents to the same benchmark under equivalent conditions — which has not been done.

### 5.3 Where GAIA Can Compete

**Layer 1 — Local hardware efficiency against local tool competitors:**

The valid competitive frame for GAIA's TTFT/TPS metrics is against other **local LLM execution tools** on the same hardware class:

| Competitor | TPS Published | Task Quality Published | Hardware Disclosed |
|---|---|---|---|
| Ollama | Yes (community leaderboards, model-specific) | No (model server only) | User-reported |
| LM Studio | Yes (community benchmarks) | No (model server only) | User-reported |
| Jan.ai | Yes (community benchmarks) | No (model server only) | User-reported |
| GAIA | Yes (pipeline-level, via new metrics) | Yes (behavioral + structural, internal) | AMD-specific, disclosed |

GAIA's differentiated position here: it is the only local agent framework that ties AMD NPU TTFT and iGPU TPS measurements to **actual agentic pipeline execution** rather than raw model serving. Ollama, LM Studio, and Jan.ai publish TPS for model throughput; GAIA can publish TPS per pipeline phase with agent task context.

**AMD's credibility foundation:** AMD's MLPerf Client v1.0 submission ([AMD developer resources, 2025](https://www.amd.com/en/developer/resources/technical-articles/2025/unlocking-peak-ai-performance-with-mlperf-client-on-ryzen-ai-.html)) provides third-party-validated hardware performance numbers. GAIA's pipeline TTFT/TPS instrumentation can tie directly to these validated baselines when reporting on the same hardware.

**NPUBenchmark.org alignment:** The emerging NPUBenchmark.org standard ([npubenchmark.org](https://www.npubenchmark.org/)) is establishing cross-vendor TTFT and TPS measurement standards covering AMD Ryzen AI 300, Qualcomm Snapdragon X, and Intel Core Ultra Series 2. GAIA's `PipelineMetricsCollector` instrumentation is structurally compatible with this measurement methodology.

### 5.4 What the Landscape Confirms GAIA Should Add

The AgentBench study ([github.com/THUDM/AgentBench](https://github.com/THUDM/AgentBench)), which evaluates agents across 8 standardized environments (OS, Database, Knowledge Graph, Digital Card Game, Lateral Thinking, House-Holding, Web Shopping, Web Browsing), explicitly notes that **"many existing benchmarks pay limited attention to efficiency metrics like token consumption and execution time."**

GLM-4.5, one of AgentBench's top performers, averages 217.8 seconds and 14,000 tokens per query on TPS-Bench-Hard. No existing public agentic benchmark simultaneously reports both task completion rate and hardware efficiency metrics (TTFT, TPS, token consumption).

This is GAIA's addressable gap in the benchmarking landscape: be the first agent framework that reports task completion rate AND efficiency (TTFT/TPS, token consumption) in a single benchmark pass, on disclosed AMD hardware, with NPU vs. CPU baselines. The infrastructure to collect the efficiency half now exists in this branch. The missing piece is the task completion half.

---

## Section 6: Verdict — Which Metrics Matter for What

| Metric | Layer | Publish Internally | Publish Competitively | Comparable To |
|---|---|---|---|---|
| TTFT | 1 — Infrastructure | Yes, per phase with cold/warm label | Yes, with full hardware disclosure and CPU baseline | Ollama/LM Studio on same hardware; AMD MLPerf Client v1.0; NPUBenchmark.org |
| TPS | 1 — Infrastructure | Yes, labeled as pipeline-TPS not raw-LLM-TPS | Yes, with formula disclosure (includes TTFT in denominator) | Same local tool competitors; AMD MLPerf numbers |
| TOKEN_EFFICIENCY | 1 — Infrastructure | Yes, as prompt engineering quality signal | Cautiously — no external standard exists | No published comparable |
| CONTEXT_UTILIZATION | 1 — Infrastructure | Yes, as RAG pipeline quality signal | No — no external standard | No published comparable |
| QUALITY_VELOCITY | 2 — Engineering Health | Yes, as internal SLO | No | No published comparable |
| DEFECT_DENSITY | 2 — Engineering Health | Yes, as release quality gate | No | No published comparable |
| MTTR | 2 — Engineering Health | Yes, as internal SLO | No | No published comparable |
| AUDIT_COMPLETENESS | 2 — Engineering Health | Yes, for compliance and debuggability | No | No published comparable |
| QualityScorer (27 categories) | 2 — Engineering Health | Yes, as artifact release gate | No | No published comparable |
| Behavioral eval (eval.py) | 3 — Capability | Yes, as primary quality signal | Yes, with scenario set disclosure | Comparable methodology to judge-based evals; not equivalent to GAIA/AgentBench scores |
| Task Completion Rate | 3 — Capability | N/A — not yet measured | N/A — not yet measurable | Manus AI (86.5%), H2O.ai (75%), OpenAI Deep Research (67.36%) — **this is the gap** |

---

## Section 7: Priority Recommendations

1. **Define and publish task completion rate on a public benchmark (Layer 3 gap — highest priority).** Submit GAIA agents to the GAIA benchmark or AgentBench under disclosed AMD hardware conditions. Without this, no quantitative competitive capability claim against Manus AI, H2O.ai, or OpenAI Deep Research is defensible. The internal scenario set's `judged_pass_rate` is not a substitute.

2. **Publish TTFT and TPS with full hardware and formula disclosure.** Reports must specify: Ryzen AI variant (Max+ 395 vs. 9 HX 375), model (Qwen3-0.6B vs. Qwen3.5-35B), warm vs. cold start condition, and that pipeline-TPS includes TTFT in the denominator. Undisclosed comparisons will invite justified criticism. Align reporting format with NPUBenchmark.org's emerging standard.

3. **Use all Layer 2 metrics as internal SLO targets, not external claims.** `QUALITY_VELOCITY`, `DEFECT_DENSITY`, `MTTR`, `AUDIT_COMPLETENESS`, and the 27-category `QualityScorer` have no published industry equivalents. Their value is as internal engineering quality gates and release criteria, not competitive differentiators. Publishing them externally without context will cause confusion.

4. **Do not compare GAIA to Manus AI on capability; compare GAIA to Ollama/LM Studio on hardware efficiency.** The cloud vs. local distinction makes capability score comparison structurally invalid. GAIA's defensible competitive position is: the only local agent framework that ties AMD NPU TTFT and iGPU TPS to real agentic pipeline execution, on disclosed AMD Ryzen AI hardware, with third-party-credible MLPerf baselines.

5. **Position GAIA as the framework that unifies hardware efficiency proof with pipeline quality evidence.** The AgentBench finding that "many benchmarks pay limited attention to efficiency metrics" is a genuine market gap. GAIA's new `src/gaia/metrics/` and `src/gaia/pipeline/metrics_collector.py` infrastructure provides the efficiency measurement half. Adding standardized task completion measurement on public benchmarks would make GAIA uniquely positioned: the only agent framework reporting both halves simultaneously, on consumer AMD hardware, with full transparency on model and hardware configuration.

---

## Appendix: Source File Index

| Claim | File | Lines |
|---|---|---|
| MetricType enum (14 new values) | `src/gaia/metrics/models.py` | 69-88 |
| Phase 1 / Phase 2 category comment | `src/gaia/metrics/models.py` | 80 |
| TTFT formula implementation | `src/gaia/pipeline/metrics_collector.py` | 62-66 |
| TPS formula implementation | `src/gaia/pipeline/metrics_collector.py` | 72-81 |
| TPS fail threshold (< 10) | `src/gaia/metrics/models.py` | 411-413 |
| TTFT fail threshold (> 5s) | `src/gaia/metrics/models.py` | 415-418 |
| PHASE_DURATION fail threshold (> 300s) | `src/gaia/metrics/models.py` | 419-422 |
| QUALITY_VELOCITY fail threshold (> 5 iterations) | `src/gaia/metrics/models.py` | 398-401 |
| DEFECT_DENSITY fail threshold (> 5/KLOC) | `src/gaia/metrics/models.py` | 402-405 |
| MTTR fail threshold (> 4 hours) | `src/gaia/metrics/models.py` | 406-409 |
| QualityScorer (27 categories, 6 dimensions) | `src/gaia/quality/scorer.py` | 1-60 |
| Pre-existing TF-IDF similarity | `src/gaia/eval/eval.py` | 28-49 |
| Pre-existing Claude-as-judge weights | `src/gaia/eval/eval.py` | 88-93 |
| Correctness override logic | `src/gaia/eval/eval.py` | 127-128 |
| EvalScenarioMetrics (new integration) | `src/gaia/eval/eval_metrics.py` | 23-48 |
| Hook classes (7 hooks) | `src/gaia/pipeline/metrics_hooks.py` | full file |
| Audit logger | `src/gaia/pipeline/audit_logger.py` | full file |

---

*Branch: `feature/pipeline-orchestration-v1` | Generated: 2026-04-01*
