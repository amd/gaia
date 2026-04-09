---
title: GAIA Loom Architecture
description: Strategic architecture document for GAIA Loom pipeline orchestration, covering the Layer 3 task completion rate gap, competitive positioning vs Manus AI and OpenClaw, and implementation roadmap.
status: Published
---

# GAIA Loom — Pipeline Orchestration Architecture and Competitive Positioning

**Version:** 1.0
**Status:** Specification — Strategic Architecture Document
**Date:** 2026-04-01
**Branch:** `feature/pipeline-orchestration-v1`
**Audience:** Engineering, Product Management, AMD Leadership, Open-Source Community

---

## 1. The Layer 3 Gap: Why Task Completion Rate Is Missing

### 1.1 The Three-Layer Metric Stack (Recap)

The `feature/pipeline-orchestration-v1` branch introduced a structured three-layer metric architecture covering everything GAIA can measure about its own pipeline execution. Each layer answers a different question for a different audience:

| Layer | Label | Question Answered | Primary Audience | Example Metrics |
|-------|-------|-------------------|------------------|-----------------|
| 1 | Infrastructure | How fast is the hardware? | Engineers, hardware marketing | TTFT, TPS (PM-07, PM-08) |
| 2 | Engineering Health | How well-engineered is the pipeline? | SRE, release gates | QualityScorer (27 categories), MTTR, AUDIT_COMPLETENESS |
| 3 | Capability | Does the agent actually succeed at tasks? | Product, competitive positioning | Task completion rate — **MISSING** |

Layer 1 and Layer 2 are implemented. Their implementation details are fully specified in `docs/spec/pipeline-metrics-kpi-reference.md` and analyzed in `docs/spec/pipeline-metrics-competitive-analysis.md`. Layer 3 is not implemented. This document specifies what it means, why it matters, and how to build it.

The Layer 3 gap was explicitly acknowledged in `docs/spec/pipeline-metrics-competitive-analysis.md`, Section 2.4:

> "Neither the pre-existing behavioral evaluator nor the new integration layer produces a standardized task completion rate on a public benchmark (GAIA benchmark, AgentBench, etc.). This is an honest gap."

And in Section 5.4:

> "This is GAIA's addressable gap in the benchmarking landscape: be the first agent framework that reports task completion rate AND efficiency (TTFT/TPS, token consumption) in a single benchmark pass."

### 1.2 What "Task Completion Rate" Means Precisely

Task completion rate is the percentage of benchmark tasks that an agent answers correctly — where "correctly" is defined by the benchmark's own ground truth and evaluation criteria, not by an internal judge.

This is distinct from GAIA's existing internal quality metrics:

| Metric | Source | Measures | Public? |
|--------|--------|----------|---------|
| `judged_pass_rate` | `src/gaia/eval/scorecard.py` | Fraction of internal GAIA scenarios where the agent passed the internal judge rubric | No — GAIA's own scenario set |
| `avg_score` | `src/gaia/eval/scorecard.py` | Average quality score across internal scenarios (scale 0–10) | No |
| `similarity_score` | `src/gaia/eval/eval.py` | TF-IDF cosine similarity to internal ground truth | No |
| Task completion rate | External benchmark (e.g., Princeton GAIA) | Fraction of standardized tasks answered correctly against public ground truth | Yes — comparable to Manus AI, h2oGPTe, etc. |

The distinction is load-bearing. GAIA's internal `judged_pass_rate` of 84% (baseline run `eval-20260320-182258`) means: "84% of our own hand-crafted scenarios passed our own judge." That is a regression testing signal, not a capability claim. Task completion rate on a public benchmark means: "our agent solved X% of the tasks that any other agent using the same benchmark can be compared against." Only the latter enables defensible external comparison.

Concretely, task completion rate requires:

1. A fixed, public task set with known ground truth answers
2. A defined evaluation criterion (exact match, fuzzy match, or human-judgment thresholded)
3. An agent execution harness that runs each task end-to-end without human intervention
4. Score computation across all tasks, broken down by difficulty level if the benchmark supports it

None of these four components currently exist in `src/gaia/eval/`. The internal eval system (`runner.py`, `scorecard.py`) partially overlaps with components 3 and 4, but it operates against GAIA's own internal scenario YAML files, not a public task set.

### 1.3 The GAIA Benchmark Name Collision (Critical Clarification)

There are two entirely separate entities that share the acronym "GAIA." This collision causes persistent confusion in any discussion of competitive benchmarking. This section resolves it definitively.

**Entity 1: AMD GAIA Framework**

- Full name: GAIA ("Generative AI Is Awesome")
- What it is: AMD's open-source Python framework for running generative AI agents locally on AMD hardware with NPU/iGPU optimization
- Location: This repository — `https://github.com/amd/gaia`
- External site: `https://amd-gaia.ai`
- What it produces: Agent implementations (`ChatAgent`, `CodeAgent`, `RoutingAgent`, etc.), pipeline orchestration, evaluation infrastructure

**Entity 2: Princeton GAIA Benchmark**

- Full name: GAIA ("General AI Assistants")
- What it is: An academic benchmark for evaluating AI agent capability on real-world tasks
- Published by: Mialon et al., Meta / HuggingFace / Princeton, with HAL paper accepted to ICLR 2026
- Location: HuggingFace — `gaia-benchmark/GAIA`
- What it produces: A scored leaderboard ranking AI agents by their ability to complete 450+ real-world tasks at three difficulty levels

These two entities share no code, no team, no organizational relationship, and no technical overlap beyond the coincidental acronym. They emerged independently.

When this document uses the phrase "submit to the GAIA benchmark," it means: submit AMD's GAIA framework's agent implementations as the test subject to run against the Princeton GAIA benchmark task set. The benchmark tests the agent; the agent is the AMD framework.

**Current leaderboard state for the Princeton GAIA Benchmark (as of 2026-04-01):**

| Agent / System | Level 1 | Level 2 | Level 3 | Notes |
|----------------|---------|---------|---------|-------|
| Manus AI | 86.5% | 70.1% | 57.7% | Self-reported during closed beta; cloud, multi-model |
| Writer's Action Agent | — | — | 61.0% | L3 leader as of early 2026; public leaderboard |
| h2oGPTe (H2O.ai) | ~75% (test set) | — | — | Test set, considered more rigorous than validation |
| OpenAI Deep Research | ~67.4% | — | — | L2 baseline at time of Manus AI announcement |
| AMD GAIA | Not submitted | Not submitted | Not submitted | **This is the gap** |

Source attributions: Manus AI scores from GetBind analysis (March 2025, blog.getbind.co); H2O.ai scores from public reporting; Writer's Action Agent from HuggingFace leaderboard. HAL (Holistic Agent Leaderboard) paper accepted to ICLR 2026 is the authoritative academic reference for the benchmark methodology.

**Note on HAL submission status:** HAL has paused accepting new benchmark submissions as of early 2026 while focusing on reliability and reproducibility improvements. The HuggingFace validation leaderboard (`gaia-benchmark/GAIA`) remains available for community submissions. The recommended path for GAIA AMD is to target the HuggingFace validation leaderboard directly, as the HAL submission pipeline may resume with updated requirements.

---

## 2. The Competitive Landscape

### 2.1 Manus AI — Cloud Power Agent

Manus AI is a general-purpose AI agent operating on cloud infrastructure with access to multi-model stacks (reported to use Claude/Anthropic and Alibaba Qwen models, among others). It achieved the first published scores above the prior state of the art on the Princeton GAIA benchmark, particularly at Level 2 (70.1%, exceeding OpenAI Deep Research's ~67.4%).

**Technical characteristics relevant to GAIA Loom positioning:**

- Infrastructure: Cloud-hosted; compute specifications are not publicly disclosed
- Model access: Multi-model — uses different models for different task types; not local
- Internet access: Full web browsing, tool use, and API calling during tasks
- Task scope: General-purpose; benchmark tasks include web research, file processing, math, reasoning
- Open-source status: No — proprietary system
- Hardware transparency: None — cloud providers and configurations undisclosed
- Benchmark verification: Self-reported during closed beta period; limited independent replication

**Why Manus AI's GAIA scores are not directly comparable to GAIA AMD's future scores:**

A cloud-hosted multi-model system with full internet access operating against the Princeton GAIA benchmark tasks is solving a different problem than a local single-model agent on consumer AMD hardware operating against the same tasks. The task set is the same; the agent infrastructure is not equivalent. Any comparison must carry explicit disclosure of these differences.

However, this does not mean GAIA AMD should avoid benchmarking against the same task set. The Princeton GAIA benchmark's value is precisely that it provides a fixed external reference point — even across structurally different agents. A GAIA AMD agent achieving 35% on Level 1 is a factual, honest datum that positions AMD's local-first agent relative to cloud systems. Transparency about the infrastructure difference is the correct response, not abstention from the benchmark.

### 2.2 OpenClaw — Viral Messaging Agent

OpenClaw is a viral open-source AI agent framework that reached 247,000+ GitHub stars within approximately 60 days of its November 2025 launch. It was created by Peter Steinberger (founder of PSPDFKit); as of February 2026, Steinberger joined OpenAI, with OpenClaw continuing under the OpenClaw Foundation.

**Technical architecture:**

OpenClaw is fundamentally a messaging-platform-native autonomous agent. Its primary interaction model is through messaging platforms — WhatsApp, Telegram, Discord, Slack, and similar services. Agent capabilities are delivered through a "skills" plugin system covering:

- Web browsing and scraping
- File system operations
- Third-party API calling
- Task automation and scheduling

OpenClaw's rapid growth reflects strong product-market fit for messaging-first automation workflows. It does not require a dedicated UI; the user's existing messaging app serves as the interface.

**Security posture:** The OpenClaw architecture has a documented prompt injection vulnerability surface (9+ CVEs identified as of early 2026). Because OpenClaw agents process arbitrary user messages through messaging platform webhooks, and because messages can be crafted to contain adversarial instructions, the attack surface is structurally broader than a local-only agent system. This is a relevant consideration for any enterprise deployment comparison.

**OpenClaw's benchmark — PinchBench:**

OpenClaw ships with PinchBench, a benchmark written in Rust covering 23 real-world task categories. PinchBench is OpenClaw's proprietary benchmark; it is not independent third-party validation. Its primary value is establishing an OpenClaw performance baseline and testing interoperability with OpenClaw's skills plugin system. Running GAIA AMD agents against PinchBench tasks (without the OpenClaw skill layer) provides a concrete comparison point in OpenClaw's own framing.

**Why OpenClaw is not a direct competitor but is a relevant comparison point:**

OpenClaw is cloud-API-dependent (it calls external LLM APIs; it does not run models locally) and is messaging-platform-first. GAIA AMD is local-first, desktop-first, and AMD-hardware-optimized. These are different deployment models serving different use cases. However, both are open-source agent frameworks targeting automation workflows — the overlapping positioning makes OpenClaw the most natural open-source comparison for GAIA AMD in public discourse.

### 2.3 GAIA Loom — Local-First Orchestration Pipeline

GAIA Loom is the proposed brand identity for AMD's GAIA pipeline orchestration architecture (`feature/pipeline-orchestration-v1`). Its positioning is distinct from both Manus AI and OpenClaw along every material axis:

**Primary differentiators:**

1. **Local execution**: GAIA Loom runs entirely on-device. No cloud dependency. No API keys required for inference. Model weights run on AMD hardware — NPU for prefill (TTFT), iGPU for decode (TPS).

2. **Hardware-transparent benchmarking**: GAIA Loom is the only agent framework positioned to report task completion rate AND hardware efficiency metrics (TTFT, TPS, token consumption) in a single benchmark pass, on disclosed AMD Ryzen AI hardware, with NPU vs. CPU baseline comparisons.

3. **Privacy-preserving**: All inference, tool use, and context remain on the user's device. No data leaves the machine during agent operation.

4. **Open-source with AMD backing**: Apache-licensed, backed by AMD engineering investment, with NPU/iGPU optimization built into the instrumentation layer from the ground up.

5. **Pipeline observability**: The three-layer metric stack (Layer 1 Infrastructure, Layer 2 Engineering Health, Layer 3 Capability — once implemented) provides more complete internal observability than any comparable open-source agent framework.

---

## 3. How to Close the Layer 3 Gap: Implementation Roadmap

### 3.1 Target Benchmarks (Ranked by Priority)

Three benchmark targets are identified. They are not mutually exclusive; the recommended sequencing prioritizes impact and implementation effort.

**Priority 1: Princeton GAIA Benchmark**

- HuggingFace dataset: `gaia-benchmark/GAIA`, configuration `2023_all`
- Validation set: 165 tasks with public ground truth (used for leaderboard)
- Test set: 300 tasks with private ground truth (requires HAL submission)
- Task types: Web research, file reading (PDF, CSV, images), code execution, multi-step reasoning, arithmetic
- Difficulty levels: L1 (simple, often single-tool), L2 (multi-step, multi-tool), L3 (complex, multi-source reasoning)
- Why first: Direct comparison to Manus AI's self-reported scores on the same task set. Maximum press and community relevance. Validation ground truth is public, enabling iteration without HAL submission.

**Priority 2: Galileo Agent Leaderboard v2 — Action Completion (AC) Metric**

- Metric: Action Completion (AC) — "Did the agent fully accomplish every user goal?"
- Scope: 5 industry verticals, multi-turn dialogues
- Leading scores as of early 2026: GPT-4.1 at 62% AC; Kimi K2 leads open-source at 0.53 AC
- Why second: The AC metric is more enterprise-narrative-aligned than academic task completion. Multi-turn dialogue coverage tests GAIA's `ChatAgent` context retention capabilities. Reaching 0.53 AC (matching the open-source leader) would be a credible open-source positioning claim.

**Priority 3: PinchBench**

- Created by: OpenClaw Foundation / kilo.ai; implemented in Rust
- Scope: 23 real-world task categories
- Why third: Direct competitive comparison to OpenClaw in OpenClaw's own benchmark framing. Lower strategic priority than Princeton GAIA (less academic credibility) but high value for open-source positioning discourse. Running GAIA agents against PinchBench tasks demonstrates cross-framework task coverage without endorsing OpenClaw's architecture.

### 3.2 Benchmark Harness Architecture

The benchmark harness is a new component that does not yet exist in the GAIA codebase. It is architecturally distinct from the existing eval system (`src/gaia/eval/runner.py`, `scorecard.py`) but can reuse the agent infrastructure (`RoutingAgent`, `ChatAgent`, `CodeAgent`) and supporting systems (RAG, MCP, tool registry).

The harness must satisfy the following constraints:

1. **Agent isolation**: Each task run must start with a clean agent state. No session or context leaks between tasks.
2. **Attachment handling**: Princeton GAIA tasks include attachments (PDFs, CSVs, images). The harness must route attachments to the appropriate GAIA subsystem (PDF to RAG, images to VLM).
3. **Answer extraction**: Agent final answers must be extracted as strings and compared to ground truth using both exact match and fuzzy match criteria defined by the benchmark.
4. **Hardware telemetry integration**: Each task run must capture TTFT and TPS via the existing `PipelineMetricsCollector` infrastructure, enabling the combined capability + efficiency report described in Section 5.4 of `docs/spec/pipeline-metrics-competitive-analysis.md`.
5. **Reproducibility**: All randomness in agent prompting and tool selection must be seeded or documented. Results must be reproducible on identical hardware.

**Proposed file locations:**

```
src/gaia/eval/harnesses/
    __init__.py
    gaia_benchmark_runner.py        # Princeton GAIA benchmark harness
    galileo_runner.py               # Galileo AC metric harness
    pinch_bench_runner.py           # PinchBench harness
    base_harness.py                 # Abstract base class: load tasks, run agent, score, report

src/gaia/eval/harnesses/adapters/
    gaia_attachment_adapter.py      # Routes PDF/CSV/image attachments to appropriate GAIA subsystem
    answer_extractor.py             # Extracts final answer string from agent response
    fuzzy_matcher.py                # Flexible answer matching (exact + normalized + semantic threshold)
```

### 3.3 Step-by-Step Implementation Plan

The implementation plan targets the Princeton GAIA Benchmark at Level 1 first (highest ground truth coverage, simplest task structure), then L2 and L3 as the harness matures.

**Phase 1: Dataset access and task loading**

1. Install the HuggingFace Datasets library: `pip install datasets`
2. Load the validation set:
   ```python
   from datasets import load_dataset
   dataset = load_dataset("gaia-benchmark/GAIA", "2023_all")
   validation = dataset["validation"]  # 165 tasks, public ground truth
   ```
3. Inspect task structure: each task has `question`, `Level`, `Final answer` (ground truth string), and `file_name` (attachment path, may be empty)
4. Implement `src/gaia/eval/harnesses/base_harness.py` with abstract methods: `load_tasks()`, `run_task(task)`, `score_answer(predicted, ground_truth)`, `generate_report(results)`

**Phase 2: Agent routing**

5. Implement `src/gaia/eval/harnesses/gaia_benchmark_runner.py` — the Princeton GAIA-specific harness subclass
6. For tasks with no attachment: route question to `RoutingAgent` (`src/gaia/agents/routing/agent.py`), which will select `ChatAgent` or another appropriate agent
7. For tasks with PDF attachments: index the PDF via the RAG SDK (`src/gaia/rag/sdk.py`) in a per-task session, then route the question to `ChatAgent` with the indexed document available
8. For tasks with image attachments: route to the VLM client (`src/gaia/llm/providers/`) using the vision-capable model path
9. For tasks requiring web information: route through MCP-enabled agent if MCP web search tool is registered; otherwise, log the task as `REQUIRES_WEB_ACCESS` and skip
10. Capture each agent's final response string using `answer_extractor.py` — extract the last agent message, strip formatting, normalize whitespace

**Phase 3: Scoring**

11. Implement exact match scoring: `predicted.strip().lower() == ground_truth.strip().lower()`
12. Implement fuzzy match scoring via `fuzzy_matcher.py`:
    - Numeric equivalence: normalize number formatting (commas, units, rounding) before comparison
    - Named entity match: if ground truth is a proper noun, check substring containment in predicted
    - The Princeton GAIA benchmark uses a relatively strict evaluation; fuzzy matching is a supplement for debugging, not a substitute for the benchmark's own evaluation criteria
13. Score per level: separate `L1_count`, `L2_count`, `L3_count` with per-level correct counts and task completion rates
14. Aggregate `overall_task_completion_rate = correct_total / attempted_total`

**Phase 4: Hardware telemetry integration**

15. Wrap each `run_task()` call with `PipelineMetricsCollector` instrumentation (already implemented in `src/gaia/pipeline/metrics_collector.py`)
16. Record per-task TTFT and TPS alongside task outcome
17. In the final report, surface:
    - Task completion rate by level (L1, L2, L3)
    - Median TTFT across all tasks
    - Median TPS across all tasks
    - AMD hardware configuration (captured from system at harness startup)
18. This produces the first report that simultaneously answers "how capable?" and "how efficient?" — the combination described as GAIA's addressable market gap in `docs/spec/pipeline-metrics-competitive-analysis.md` Section 5.4

**Phase 5: CLI integration**

19. Add `gaia eval benchmark --dataset gaia --split validation` to the CLI (`src/gaia/cli.py`)
20. Add `gaia eval benchmark --dataset galileo` and `gaia eval benchmark --dataset pinchbench` as subsequent targets
21. Results output: JSON to `eval/results/benchmark/<run_id>/` and summary Markdown to stdout

**Phase 6: Leaderboard reporting**

22. Format results according to the HuggingFace GAIA leaderboard submission requirements
23. Target submission to the HuggingFace validation leaderboard (HAL submission to follow when HAL resumes accepting new submissions)

### 3.4 Expected Results and Timeline

**Honest expectations for L1:**

GAIA AMD's first Princeton GAIA Benchmark L1 run will produce a score substantially below Manus AI's 86.5%. This is expected and is not a failure condition — it is the starting measurement. The sources of the gap are predictable:

- L1 tasks that require web access will be skipped or scored as incorrect (GAIA's local-first model means web search depends on MCP tool availability)
- L1 tasks requiring complex multi-step tool chains will expose current `RoutingAgent` limitations
- Tasks with large PDF attachments may be partially blocked by RAG chunking limitations already documented in `docs/spec/agent-ui-eval-kpis.md` (Gap G-03)

A reasonable first-run L1 target, assuming web-access tasks are excluded from the denominator: **30–50% task completion rate on exercisable L1 tasks**. This figure, reported with full hardware disclosure and scope documentation (no web access, AMD Ryzen AI Max+ 395, Qwen3.5-35B-A3B-GGUF model), is a credible and honest datum that begins the public benchmarking trajectory.

**Timeline estimate:**

| Milestone | Estimated Engineering Effort | Description |
|-----------|------------------------------|-------------|
| Phase 1–2 (harness + routing) | 2–3 weeks | Dataset loading, agent routing, answer extraction |
| Phase 3 (scoring) | 1 week | Exact match + fuzzy match, per-level breakdown |
| Phase 4 (telemetry) | 1 week | Hardware metrics integration with existing `PipelineMetricsCollector` |
| Phase 5 (CLI) | 1 week | CLI command, output formatting |
| First L1 run and result analysis | 1 week | Execution, debugging, documentation |
| **Total to first published result** | **6–7 weeks** | Including test engineering and documentation |

---

## 4. Architecture Name: LOOM

### 4.1 Naming Decision and Rationale

The pipeline orchestration architecture introduced in `feature/pipeline-orchestration-v1` requires a name distinct from "GAIA" (which refers to the full framework) and from any existing component name (`PipelineEngine`, `RoutingAgent`, etc.). The name must be:

- Memorable and phonetically unambiguous
- Semantically coherent with what the system does (orchestrate multiple agents into a pipeline)
- Free of conflicts with existing developer tools in the Python/ML ecosystem
- Compatible with AMD's existing product and brand identity
- Appropriate for open-source positioning

The recommended name is **LOOM**.

**Why the metaphor holds:**

A loom is the tool that weaves individual threads into finished fabric. In GAIA's pipeline architecture, individual agents (the threads) are orchestrated by the pipeline engine into a coherent output artifact (the fabric). The physical operation of a loom — the shuttle passes back and forth between phases, integrating each thread into the growing structure — directly maps to the recursive iterative loop architecture of GAIA's `PipelineEngine`, where multiple phases execute, evaluate quality via `QualityScorer`, and loop back when quality thresholds are not met.

The loom metaphor also captures the observability goal: watching a loom, you can see which threads are being woven, how many passes have been made, and whether the fabric is developing correctly. GAIA Loom's three-layer metric stack (TTFT, TPS, QualityScorer, task completion rate) is the instrumentation equivalent — it lets you watch the pipeline work.

**AMD heritage alignment:**

AMD's entire CPU and GPU microarchitecture is thread-scheduler-centric. Thread management, warp scheduling, and work queue orchestration are the core primitives of AMD's hardware design philosophy. "Loom" maps naturally to this lineage: a loom is, in physical terms, a thread scheduler. The naming choice is internally coherent with AMD's engineering culture.

**Mythological coherence:**

GAIA (from Greek mythology) is the primordial earth goddess — the source from which all things arise. In Greek mythology, the Moirai (the Fates) used a loom to weave the destiny of mortals. GAIA and Loom thus belong to the same mythological tradition: one is the source, the other is the mechanism through which that source's output is structured. This is not forced symbolism — it reflects the actual architectural relationship. GAIA is the framework; Loom is the orchestration mechanism inside it. GAIA produces agents; Loom weaves those agents into pipeline results.

**Loop architecture alignment:**

A loom's shuttle does not move in a straight line — it traverses back and forth across the warp threads, depositing each pass of weft and returning. This bidirectional, iterative motion is identical to how GAIA's pipeline operates: a phase executes, the quality evaluator scores the output, and if the threshold is not met, the loop iterates. The loom metaphor is mechanically accurate for the recursive refinement architecture.

### 4.2 Alternatives Considered

The following names were considered and rejected:

**FORGE**

Rejected because: AMD's internal toolchain includes a project named PyTorch Forge (an AMD fork/extension). A public open-source product named FORGE risks collision and confusion with this internal AMD project. The forge metaphor (shaping raw material into finished product) is semantically coherent but the namespace risk outweighs the benefit.

**HELIX**

Rejected because: Helix is a well-established open-source terminal text editor (`helix-editor.com`, GitHub: `helix-editor/helix`) with significant community recognition. Developer searches for "Helix" resolve to the text editor in most contexts. A second Helix in the developer tools space creates search engine dilution and potential contributor confusion.

**WEAVE**

Rejected because: Weights and Biases (W&B) ships a product named W&B Weave — their LLM evaluation and observability platform. This is a direct collision in the ML observability tooling space, which is precisely the market segment where GAIA Loom competes. Using "Weave" invites confusion with W&B Weave in every developer conversation about LLM pipeline observability.

**FLUX**

Rejected because: "Flux" is saturated in the AI tooling space. Flux is the name of a widely used open-source image generation model; it also appears in multiple ML and data pipeline contexts. The word has no disambiguation power in the current ecosystem.

**RELAY**

Rejected because: The relay metaphor implies sequential handoff (agent A hands off to agent B) rather than iterative refinement (agent A's output is evaluated and looped back). GAIA's pipeline architecture is iterative, not relay-sequential. The metaphor is architecturally wrong.

**NEXUS**

Rejected because: "Nexus" is overused in enterprise software branding (Nexus Repository by Sonatype, Nexus by Salesforce, etc.). It reads as generic corporate product naming rather than a distinctive open-source identifier.

### 4.3 Brand Identity: GAIA Loom

The full brand expression is **GAIA Loom**, combining the parent framework name with the architecture name.

**Package naming:**

- PyPI: `gaia-loom` (consistent with `amd-gaia` package naming pattern)
- Python module: `gaia.loom` (consistent with existing `gaia.pipeline`, `gaia.eval`, `gaia.metrics` module structure)
- CLI: No new top-level CLI command required; Loom is the internal name for the pipeline orchestration architecture, surfaced through existing `gaia eval benchmark`, `gaia pipeline`, and related commands

**Documentation placement:**

- Primary specification: `docs/spec/gaia-loom-architecture.md` (this document)
- Implementation guide: `docs/guides/pipeline.mdx` (already exists at `docs/guides/pipeline`)
- SDK reference: `docs/sdk/infrastructure/pipeline.mdx` (already exists at `docs/sdk/infrastructure/pipeline`)

The Loom name is an architecture identity label, not a separate installable package in the initial release. It should appear in:
- The `feature/pipeline-orchestration-v1` PR title and description
- The `docs/spec/` documentation
- The README section describing the pipeline orchestration architecture
- Any external blog posts or AMD marketing materials about the pipeline feature

### 4.4 Open-Source Positioning Statement

> GAIA Loom is the local-first multi-agent pipeline orchestration engine for AMD Ryzen AI. Where cloud agents consume tokens on remote servers, Loom weaves them into results on your hardware — with full observability at every phase.

**What this statement claims:**

- "Local-first": All inference runs on-device. This is factually accurate and is the primary architectural distinction from Manus AI and OpenClaw's cloud-API-dependent models.
- "Multi-agent pipeline orchestration": The `RoutingAgent` selects agents; the `PipelineEngine` sequences their execution; the `QualityScorer` evaluates outputs. This is an accurate description of the `feature/pipeline-orchestration-v1` architecture.
- "AMD Ryzen AI": The NPU (TTFT optimization) and iGPU (TPS optimization) integration is AMD-specific and hardware-disclosed.
- "Full observability at every phase": The three-layer metric stack (Layer 1 TTFT/TPS, Layer 2 QualityScorer/MTTR, Layer 3 task completion rate — when implemented) is the observability claim. This claim is partially true today (Layers 1 and 2 are implemented); it becomes fully true when Layer 3 is delivered.

**What this statement does not claim:**

It does not claim competitive capability parity with Manus AI. It does not claim task completion rates that are not yet measured. It does not claim hardware performance numbers without disclosing the AMD device and model. This is intentional — the positioning is based on architectural differentiation, not on numbers that have not yet been established.

---

## 5. GAIA Loom vs. The Field: Comparison Table

The following table summarizes the material differences between GAIA Loom, Manus AI, and OpenClaw. All scores and statistics are labeled with their source type (self-reported, public leaderboard, internal). No figures are presented without attribution.

| Dimension | Manus AI | OpenClaw | GAIA Loom |
|-----------|----------|----------|-----------|
| Infrastructure | Cloud; provider and specs undisclosed | Cloud APIs (LLM calls to external providers) | Local AMD NPU/iGPU; hardware disclosed |
| Primary interface | Browser-based task UI | Messaging apps (WhatsApp, Telegram, Discord, Slack) | CLI / Agent UI (desktop, browser-based) |
| Hardware awareness | None — infrastructure-agnostic | None — API-dependent | AMD-specific: TTFT optimized on NPU, TPS on iGPU |
| Open source | No — proprietary | Yes — OpenClaw Foundation (Apache) | Yes — Apache-licensed, AMD-backed |
| Model stack | Multi-model cloud (Claude + Qwen + proprietary) | External LLM API calls (model-agnostic) | Single local model per run (Qwen3-0.6B to Qwen3.5-35B) |
| Agent orchestration | Proprietary multi-model task routing | Skills plugin system | PipelineEngine + RoutingAgent + QualityScorer |
| Benchmark (capability) | Princeton GAIA: L1=86.5%, L2=70.1%, L3=57.7% (self-reported, closed beta) | PinchBench (proprietary, 23 categories) | Layer 3 not yet implemented — **this is the gap** |
| Benchmark (efficiency) | Not published | Not published | TTFT and TPS per pipeline phase (Layer 1, implemented) |
| Privacy | Data processed on cloud | Data processed on external LLM APIs | All data on-device; no external transmission |
| Security posture | Not disclosed | 9+ CVEs identified (prompt injection via messaging platform) | Local-only; no messaging-platform attack surface |
| Competitive positioning | "Most capable cloud agent" | "Most popular open-source messaging agent" | "Only local-first agent framework with AMD hardware observability" |

**Source labels:**

- Manus AI benchmark scores: GetBind analysis, March 2025 (blog.getbind.co); self-reported by Manus AI during closed beta; limited independent replication
- OpenClaw GitHub star count: community-reported, approximately November 2025–January 2026 period
- OpenClaw CVE count: security research reporting as of early 2026
- Galileo Agent Leaderboard scores (GPT-4.1 at 62% AC, Kimi K2 at 0.53 AC): Galileo public leaderboard v2
- GAIA Loom figures: internal implementation (this branch), no public benchmark submission as of 2026-04-01

---

## Appendix A: Implementation File Reference

| Component | File | Status |
|-----------|------|--------|
| Pipeline engine core | `src/gaia/pipeline/engine.py` | Implemented (this branch) |
| Pipeline metrics collector | `src/gaia/pipeline/metrics_collector.py` | Implemented (this branch) |
| Metrics hooks (7 hooks) | `src/gaia/pipeline/metrics_hooks.py` | Implemented (this branch) |
| Core metrics models | `src/gaia/metrics/models.py` | Implemented (this branch) |
| Metrics collector (SQLite-backed) | `src/gaia/metrics/collector.py` | Implemented (this branch) |
| Production monitor | `src/gaia/metrics/production_monitor.py` | Implemented (this branch) |
| Benchmark suite | `src/gaia/metrics/benchmarks.py` | Implemented (this branch) |
| Quality scorer | `src/gaia/quality/scorer.py` | Implemented (this branch) |
| Routing agent | `src/gaia/agents/routing/agent.py` | Pre-existing |
| Chat agent | `src/gaia/agents/chat/agent.py` | Pre-existing |
| RAG SDK | `src/gaia/rag/sdk.py` | Pre-existing |
| Eval runner (internal scenarios) | `src/gaia/eval/runner.py` | Pre-existing |
| Eval scorecard | `src/gaia/eval/scorecard.py` | Pre-existing |
| **Benchmark harness (base)** | `src/gaia/eval/harnesses/base_harness.py` | **Not yet implemented** |
| **Princeton GAIA harness** | `src/gaia/eval/harnesses/gaia_benchmark_runner.py` | **Not yet implemented** |
| **Galileo AC harness** | `src/gaia/eval/harnesses/galileo_runner.py` | **Not yet implemented** |
| **PinchBench harness** | `src/gaia/eval/harnesses/pinch_bench_runner.py` | **Not yet implemented** |
| **Attachment adapter** | `src/gaia/eval/harnesses/adapters/gaia_attachment_adapter.py` | **Not yet implemented** |
| **Answer extractor** | `src/gaia/eval/harnesses/adapters/answer_extractor.py` | **Not yet implemented** |
| **Fuzzy matcher** | `src/gaia/eval/harnesses/adapters/fuzzy_matcher.py` | **Not yet implemented** |

---

## Appendix B: Benchmark Dataset Reference

| Benchmark | Access | Task Count | Ground Truth | Attachment Types | Submission |
|-----------|--------|------------|-------------|------------------|------------|
| Princeton GAIA (validation) | `load_dataset("gaia-benchmark/GAIA", "2023_all")` | 165 tasks | Public (string, exact match) | PDF, CSV, images, audio | HuggingFace leaderboard |
| Princeton GAIA (test) | Same dataset | 300 tasks | Private (HAL submission) | PDF, CSV, images, audio | HAL (paused as of early 2026) |
| Galileo Agent Leaderboard v2 | Galileo API | Multi-turn dialogues, 5 industries | Private evaluation | Text-only | Galileo platform |
| PinchBench | OpenClaw GitHub | 23 task categories | Proprietary | Task-dependent | Not applicable (run locally) |
| AgentBench | `THUDM/AgentBench` on HuggingFace | 8 environments | Public per environment | Text + environment state | GitHub / paper contact |

---

## Appendix C: Relationship to Existing Specifications

This document is the third in a three-part specification sequence for the `feature/pipeline-orchestration-v1` branch:

| Specification | File | Content |
|---------------|------|---------|
| Agent UI Eval KPI Specification | `docs/spec/agent-ui-eval-kpis.md` | Internal eval system: 16 KPIs, pass/fail logic, gap registry (G-01 through G-10) |
| Pipeline Metrics KPI Quick Reference | `docs/spec/pipeline-metrics-kpi-reference.md` | Three-layer metric stack: all formulas, thresholds, 27 Quality Scorer categories |
| Pipeline Metrics Competitive Analysis | `docs/spec/pipeline-metrics-competitive-analysis.md` | TTFT/TPS deep explanation, AMD hardware context, competitive positioning vs. Manus AI |
| **GAIA Loom Architecture** | `docs/spec/gaia-loom-architecture.md` | **This document**: Layer 3 gap definition, implementation roadmap, architecture naming |

These four documents together constitute the complete specification output for Phase 1 and Phase 2 of the pipeline orchestration work. The benchmark harness implementation (Section 3 of this document) constitutes Phase 3.

---

*Branch: `feature/pipeline-orchestration-v1` | Date: 2026-04-01*

---

## 6. GAIA Loom Architecture Internals

### 6.1 YAML-Driven Agent Configuration

Every agent in the GAIA Loom pipeline is defined by a YAML file in `config/agents/`. The engine never hard-codes agent behavior; all identity, routing triggers, capability declarations, tool access, model selection, and execution constraints are read from these files at runtime by `AgentRegistry`.

**Annotated schema** (`config/agents/senior-developer.yaml`):

```yaml
agent:
  id: senior-developer          # Stable identifier used in template agent_categories and routing rules
  name: Senior Developer        # Display name
  version: 1.0.0
  category: development         # One of: planning | development | review | management
  model_id: Qwen3-0.6B-GGUF    # Per-agent model override; see model resolution chain in Section 6.6

  triggers:
    keywords:                   # AgentRegistry.select_agent() matches these against task_description
      - implement
      - develop
      - code
      - build
      - feature
    phases:                     # Phases where this agent is eligible for auto-selection
      - DEVELOPMENT
      - REFACTORING
    complexity_range:           # Agent is considered only when task complexity falls in this range
      min: 0.3
      max: 1.0

  capabilities:                 # Declared capability tags; used for required_capabilities matching
    - full-stack-development
    - api-design
    - database-design
    - testing
    - code-review
    - debugging
    - refactoring

  system_prompt: prompts/senior-developer.md   # Relative path to the markdown prompt file; loaded
                                               # by ConfigurableAgent.initialize()

  tools:                        # Tool IDs registered during agent.initialize()
    - file_read
    - file_write
    - bash_execute
    - git_operations
    - search_codebase
    - run_tests

  execution_targets:            # AMD hardware target selection
    default: cpu                # Primary target: CPU (always available)
    fallback:
      - gpu                     # Fallback chain: iGPU or discrete GPU when NPU unavailable

  constraints:                  # Enforced limits passed to ConfigurableAgent
    max_file_changes: 20
    max_lines_per_file: 500
    requires_review: true
    timeout_seconds: 600

  metadata:
    author: GAIA Team
    created: "2026-03-23"
    tags: [development, full-stack, core]
```

**Triggers and auto-selection.** `AgentRegistry.select_agent()` is called when a phase does not have an explicit agent sequence from the template. It scores candidates by matching `triggers.keywords` against the user goal string, filtering by `triggers.phases` and `complexity_range`, then ranking by `required_capabilities` overlap. The first matching agent ID is returned. When a template explicitly lists agents in `agent_categories`, `_get_agents_for_phase()` in `engine.py` returns that list directly and bypasses `select_agent()`.

**Execution targets.** The `execution_targets` block declares the preferred AMD hardware target for inference. `default: cpu` means the agent runs on CPU by default. The `fallback` list specifies the ordered sequence to try when the default target is unavailable. In future NPU-capable deployments, agents declared with `default: npu` will route their prefill pass through the AMD NPU, enabling the TTFT optimization described in `docs/spec/pipeline-metrics-kpi-reference.md` Layer 1.

**Agent catalog.** The 17 agent YAML files in `config/agents/` as of this branch, organized by category:

| Category | Agent ID | Description |
|----------|----------|-------------|
| planning | planning-analysis-strategist | Requirements decomposition and task planning |
| planning | solutions-architect | High-level architecture and system design |
| planning | api-designer | API contract design and interface specification |
| planning | database-architect | Data model and database schema design |
| development | senior-developer | Full-stack generalist; primary implementation agent |
| development | backend-specialist | Backend services, APIs, server-side logic |
| development | frontend-specialist | UI components, browser compatibility |
| development | data-engineer | Data pipelines, ETL, and data processing |
| development | devops-engineer | Infrastructure, CI/CD, deployment configuration |
| review | quality-reviewer | General code quality and requirements coverage |
| review | security-auditor | Security vulnerability detection and remediation |
| review | performance-analyst | Performance profiling and optimization |
| review | test-coverage-analyzer | Test coverage gaps and test implementation |
| review | accessibility-reviewer | WCAG compliance and accessibility defects |
| management | software-program-manager | Pipeline decision-making and iteration control |
| management | release-manager | Release readiness, versioning, changelog |
| management | technical-writer | Documentation artifacts and API documentation |

### 6.2 Pipeline Templates

Three templates ship with GAIA Loom. Each exists in two forms: as a YAML file in `config/pipeline_templates/` (loaded by `load_template_from_yaml()` in `recursive_template.py`) and as a pre-built `RecursivePipelineTemplate` instance in the `RECURSIVE_TEMPLATES` dict at the bottom of `recursive_template.py`. The engine uses `get_recursive_template(name)` to load from the in-code registry; the YAML files serve as the human-editable source of truth and as the override path for custom templates.

**Template comparison:**

| Property | generic | rapid | enterprise |
|----------|---------|-------|------------|
| `quality_threshold` | 0.90 | 0.75 | 0.95 |
| `max_iterations` | 10 | 5 | 15 |
| Phase count | 4 | 3 | 4 |
| Planning agents | planning-analysis-strategist | planning-analysis-strategist | planning-analysis-strategist, solutions-architect |
| Development agents | senior-developer | senior-developer | senior-developer |
| Quality agents | quality-reviewer | quality-reviewer | quality-reviewer, security-auditor, performance-analyst |
| Decision agents | software-program-manager | (none) | software-program-manager |
| Routing rules | 3 | 1 | 2 |
| `default_model` | Qwen3-0.6B-GGUF | Qwen3-0.6B-GGUF | Qwen3-0.6B-GGUF |

The `rapid` template omits the DECISION phase entirely, making it a 3-phase pipeline: PLANNING → DEVELOPMENT → QUALITY. This reduces overhead for prototype tasks that do not require a management-layer decision gate.

**Generic template routing rules** (all three, from `config/pipeline_templates/generic.yaml`):

| Priority | Condition | `route_to` | `loop_back` | Guidance |
|----------|-----------|-----------|-------------|---------|
| 1 | `defect_type == 'security'` | `security-auditor` | true | Address security vulnerability before proceeding |
| 2 | `defect_type == 'missing_tests'` | `DEVELOPMENT` | true | Add unit tests for new functionality |
| 3 | `quality_score < 0.75` | `PLANNING` | true | Significant rework needed - revisit requirements |

**Rapid template routing rule** (single rule, from `config/pipeline_templates/rapid.yaml`):

| Priority | Condition | `route_to` | `loop_back` |
|----------|-----------|-----------|-------------|
| 1 | `defect_severity == 'critical'` | `QUALITY` | true |

Note the distinction: the rapid template routes on `defect_severity` (not `defect_type`) and sends critical defects back to the QUALITY phase for re-evaluation, not to a specialist agent.

Routing rules are evaluated by `RecursivePipelineTemplate.evaluate_routing_rules(context)` (`recursive_template.py`), which sorts rules by priority (ascending) and returns the first matching `RoutingRule`. Rules with `route_to` set to a phase name (e.g., `DEVELOPMENT`, `PLANNING`) instruct the pipeline to re-enter that phase. Rules with `route_to` set to an agent ID instruct the `RoutingEngine` to select that specific specialist.

**YAML vs. in-code templates.** The `TemplateLoader` class (`template_loader.py`) parses multi-template YAML files of the form `templates: { name: { configuration: {...}, phases: [...], routing_rules: [...] } }`. The individual files in `config/pipeline_templates/` use the simpler flat format defined in `load_template_from_yaml()` in `recursive_template.py`. The `PipelineEngine` calls `get_recursive_template(name)` which reads from `RECURSIVE_TEMPLATES`; it falls back to `"generic"` if the requested name is not in the registry (`engine.py:242-247`). To use a custom YAML template not in the in-code registry, call `load_template_from_yaml(name)` and pass the resulting `RecursivePipelineTemplate` directly to the engine.

### 6.3 Engine Component Map

The following diagram shows the relationships between the major components inside `PipelineEngine` for a single pipeline run:

```
  PipelineContext (immutable)
  PipelineConfig  ─────────────────────────────────────────┐
                                                           │
                         ┌─────────────────────────────────▼───┐
                         │           PipelineEngine             │
                         │           engine.py                  │
                         │                                      │
                         │  PipelineStateMachine  ◄── state.py │
                         │  DecisionEngine        ◄── decision_ │
                         │                             engine.py│
                         │  QualityScorer         ◄── quality/  │
                         │                             scorer.py │
                         │  AgentRegistry         ◄── agents/   │
                         │                             registry.py│
                         │  RoutingEngine         ◄── routing_  │
                         │                             engine.py │
                         │         │                            │
                         │         ▼                            │
                         │    LoopManager ─────────── loop_     │
                         │    loop_manager.py       manager.py  │
                         │         │                            │
                         │         ▼                            │
                         │  ConfigurableAgent                   │
                         │  agents/configurable.py              │
                         │         │                            │
                         │    ┌────┴──────┐                     │
                         │    │           │                     │
                         │  Agent      system_                  │
                         │  YAML       prompt.md                │
                         │  tools[]                             │
                         │                                      │
                         │  HookExecutor ──► 8 Production Hooks │
                         │  hooks/registry.py                   │
                         │             └──► 7 Metrics Hooks     │
                         │                  metrics_hooks.py    │
                         │                       │              │
                         │                       ▼              │
                         │            PipelineMetricsCollector  │
                         │            metrics_collector.py      │
                         └──────────────────────────────────────┘
```

**Component reference table:**

| Component | File | Responsibility | Key Interface |
|-----------|------|----------------|---------------|
| `PipelineEngine` | `src/gaia/pipeline/engine.py` | Main orchestrator; coordinates all components | `initialize(context, config)`, `start()`, `execute_with_backpressure(workloads)` |
| `PipelineStateMachine` | `src/gaia/pipeline/state.py` | Thread-safe state lifecycle; transition log; chronicle | `transition(state, reason)`, `snapshot`, `chronicle` |
| `PipelineContext` | `src/gaia/pipeline/state.py` | Frozen configuration for a single pipeline run | Dataclass fields: `pipeline_id`, `user_goal`, `quality_threshold`, `max_iterations`, `concurrent_loops` |
| `PipelineSnapshot` | `src/gaia/pipeline/state.py` | Mutable runtime state: phase, quality score, artifacts, defects, chronicle | `.artifacts`, `.defects`, `.quality_score` |
| `PipelineConfig` | `src/gaia/pipeline/engine.py` | Engine-level configuration dataclass | `.template`, `.quality_threshold`, `.max_iterations`, `.concurrent_loops` |
| `RecursivePipelineTemplate` | `src/gaia/pipeline/recursive_template.py` | Template dataclass; routing rule evaluation; loop-back logic | `should_loop_back()`, `evaluate_routing_rules(context)`, `get_phase(name)` |
| `TemplateLoader` | `src/gaia/pipeline/template_loader.py` | Parses YAML template files into `RecursivePipelineTemplate` objects | `load_from_file(path)`, `load_from_string(yaml_str)`, `validate_template(template, registry)` |
| `LoopManager` | `src/gaia/pipeline/loop_manager.py` | ThreadPoolExecutor-based concurrent loop execution; pending queue when at capacity | `create_loop(config)`, `start_loop(loop_id)`, `cancel_loop(loop_id)` |
| `LoopConfig` | `src/gaia/pipeline/loop_manager.py` | Per-loop configuration: agent sequence, thresholds, timeout | Fields: `loop_id`, `phase_name`, `agent_sequence`, `quality_threshold`, `max_iterations`, `timeout_seconds` |
| `LoopState` | `src/gaia/pipeline/loop_manager.py` | Runtime state of a single loop; quality score history; defect list | `.status`, `.iteration`, `.quality_scores`, `.artifacts`, `.defects` |
| `AgentRegistry` | `src/gaia/agents/registry.py` | Scans `config/agents/*.yaml`; provides `select_agent()` and `get_agent()` | `initialize()`, `select_agent(task_description, current_phase, ...)`, `get_agent(agent_id)` |
| `ConfigurableAgent` | `src/gaia/agents/configurable.py` | Instantiates an agent from its YAML definition; registers tools; builds prompt | `initialize()`, `execute(context)` |
| `DecisionEngine` | `src/gaia/pipeline/decision_engine.py` | Evaluates quality score vs. threshold and defect list; returns `Decision` | `evaluate(phase_name, quality_score, quality_threshold, defects, iteration, max_iterations, is_final_phase)` |
| `RoutingEngine` | `src/gaia/pipeline/routing_engine.py` | Keyword-based defect type detection; specialist agent selection | `route_defect(defect)`, `route_defects(defects)`, `detect_defect_type(description)` |
| `PhaseContract` | `src/gaia/pipeline/phase_contract.py` | Input/output contracts per phase; validates prerequisites before execution | `validate_inputs(state)`, `validate_outputs(state)`, `validate_quality(state)` |
| `QualityScorer` | `src/gaia/quality/scorer.py` | Evaluates artifacts against 27 quality categories; returns `QualityReport` | `evaluate(artifact, context)` |
| `PipelineMetricsCollector` | `src/gaia/pipeline/metrics_collector.py` | Collects Layer 1 and Layer 2 metrics during execution | `start_phase(phase)`, `end_phase(phase)`, `record_quality_score(...)` |
| `HookExecutor` | `src/gaia/hooks/registry.py` | Executes registered hooks for each pipeline event | `execute_hooks(event, context)` |

### 6.4 Execution Flow: End-to-End Walk-Through

The following steps describe a single pipeline run using the `generic` template from `PipelineEngine.initialize()` through `PipelineEngine.start()` completion. Line references are to `src/gaia/pipeline/engine.py` unless otherwise noted.

**Step 1 — Initialize (`engine.py:182-282`)**

`PipelineEngine.initialize(context, config)` is called with a `PipelineContext` (frozen: pipeline_id, user_goal, quality_threshold, max_iterations, concurrent_loops) and an optional config dict.

1. `PipelineStateMachine(context)` is created; state enters `INITIALIZING`.
2. `PipelineMetricsCollector` is instantiated and associated with the pipeline_id.
3. `DecisionEngine(config)` and `QualityScorer()` are created.
4. `agents_dir` is resolved: config key → constructor arg → `config/agents/` relative to package root.
5. `AgentRegistry(agents_dir)` is created and `await registry.initialize()` scans all `.yaml` files in `agents_dir`, building an in-memory map of agent definitions.
6. `get_recursive_template("generic")` is called; it returns `GENERIC_TEMPLATE` from `RECURSIVE_TEMPLATES`. If the requested name is not found, it falls back to `"generic"` with a warning log.
7. `LoopManager(max_concurrent, agent_registry, model_id, template_model_id)` is created. The `model_id` and `template_model_id` arguments seed the model resolution chain (see Section 6.6).
8. `RoutingEngine(agent_registry)` is created.
9. If `enable_hooks` is true, `HookRegistry` and `HookExecutor` are created. `_register_default_hooks()` registers 8 production hooks and, if a metrics collector is present, 7 metrics hooks.
10. StateMachine transitions to `READY`. `_initialized = True`.

**Step 2 — Start (`engine.py:314-353`)**

`PipelineEngine.start()` checks `_initialized` and `_running`, then transitions the StateMachine to `RUNNING` and calls `_execute_pipeline()`.

**Step 3 — PLANNING phase (`engine.py:442-485`)**

`_execute_phase("PLANNING")` fires the `PHASE_ENTER` event through `HookExecutor`. `PreActionValidationHook` checks the `PhaseContract` for PLANNING phase prerequisites. `ContextInjectionHook` injects pipeline context into agent state.

`_get_agents_for_phase("PLANNING")` checks the loaded template's `agent_categories["planning"]` and returns `["planning-analysis-strategist"]`. A `LoopConfig` is built with this agent sequence, the pipeline's quality_threshold, and max_iterations. `LoopManager.create_loop(config)` registers the loop; `LoopManager.start_loop(loop_id)` submits it to the `ThreadPoolExecutor`.

Inside the thread: `_execute_loop()` calls `_execute_agent("planning-analysis-strategist", loop_state)`. The agent loads its definition from `AgentRegistry`, a `ConfigurableAgent` is instantiated and initialized (tools registered, prompt assembled), and `agent.execute(context)` runs the LLM. The resulting artifact is stored in `loop_state.artifacts`. Quality is evaluated; if threshold is met, `LoopStatus.COMPLETED`; if max iterations exceeded, `LoopStatus.FAILED`.

On return, `PHASE_EXIT` hooks fire: `PostActionValidationHook` checks phase outputs, `OutputProcessingHook` normalizes artifacts, `ChronicleHarvestHook` appends a `STATE_TRANSITION` entry to the chronicle. `StateMachine.increment_iteration()` is called.

**Step 4 — DEVELOPMENT phase**

Same pattern as Step 3 with agent sequence `["senior-developer"]`. The development loop receives the planning artifacts via `loop_state.artifacts` passed through the execution context.

**Step 5 — QUALITY phase (`engine.py:532-558`)**

`_execute_quality()` retrieves accumulated artifacts from `StateMachine.snapshot.artifacts` and calls `QualityScorer.evaluate(artifact, context)`. The scorer returns a `QualityReport` with an `overall_score` on a 0–100 scale. This is divided by 100 and stored on the StateMachine via `set_quality_score(score)`. The full quality report dict is stored as an artifact under the key `"quality_report"`.

**Step 6 — DECISION phase (`engine.py:560-610`)**

`_execute_decision()` reads `quality_score` and `iteration` from the snapshot. If defects exist, `RoutingEngine.route_defect(defect)` is called for each defect; routing decisions are stored as an artifact under `"routing_decisions"`.

`DecisionEngine.evaluate(...)` is called with `is_final_phase=True`. The engine checks:
- Critical defects matching patterns in `DEFAULT_CRITICAL_PATTERNS` → `DecisionType.PAUSE` (not implemented as loop-back; pauses for user input)
- `quality_score >= quality_threshold` → `DecisionType.COMPLETE`
- `quality_score < quality_threshold` AND `iteration < max_iterations` → `DecisionType.LOOP_BACK`
- `iteration >= max_iterations` → `DecisionType.FAIL`

If `FAIL`: `StateMachine.set_error(reason)` is called; `_execute_phase` returns `False`; `_execute_pipeline` transitions StateMachine to `FAILED`.

**Step 7 — Loop-back**

When `DecisionType.LOOP_BACK` is returned (or when `QualityGateHook` signals a threshold miss via `halt_pipeline=False` with loop signal), the iteration counter increments and `_execute_pipeline` re-enters PLANNING. `PhaseContract.validate_inputs()` runs again; optional inputs `previous_plan` and `defects` are now populated from the previous iteration's artifacts, giving the planning agent context for remediation.

**Step 8 — Completion**

When `DecisionType.COMPLETE` is returned (quality threshold met in final phase), `_execute_pipeline` exits the phase loop. StateMachine transitions to `COMPLETED`. `_completion_event.set()` unblocks any callers of `wait_for_completion()`. `ChronicleHarvestHook` appends the final `STATE_TRANSITION` event to the chronicle. `PipelineEngine.start()` returns the current `PipelineSnapshot`.

### 6.5 Hook System

Hooks are registered with `HookRegistry` and executed by `HookExecutor` at `PHASE_ENTER` and `PHASE_EXIT` events within each phase. `_register_default_hooks()` in `engine.py:284-312` registers all hooks during `initialize()`.

**Production hooks (8):**

| Hook | Class | Event | Purpose |
|------|-------|-------|---------|
| PreActionValidationHook | `gaia.hooks.production.validation_hooks` | `PHASE_ENTER` | Validates phase prerequisites using `PhaseContract.validate_inputs()`; blocking: a `halt_pipeline=True` result prevents phase execution |
| PostActionValidationHook | `gaia.hooks.production.validation_hooks` | `PHASE_EXIT` | Validates phase outputs using `PhaseContract.validate_outputs()`; logs violations as defects |
| ContextInjectionHook | `gaia.hooks.production.context_hooks` | `PHASE_ENTER` | Injects pipeline context (user_goal, iteration, defects, artifacts) into the agent execution environment via `StateMachine.inject_context()` |
| OutputProcessingHook | `gaia.hooks.production.context_hooks` | `PHASE_EXIT` | Normalizes and stores phase output artifacts; applies output schema defined in `PhaseContract.expected_outputs` |
| QualityGateHook | `gaia.hooks.production.quality_hooks` | `PHASE_EXIT` | Checks `quality_score` against `quality_threshold`; emits loop-back signal if below threshold and iteration budget remains |
| DefectExtractionHook | `gaia.hooks.production.quality_hooks` | `PHASE_EXIT` | Extracts defect list from the quality report artifact and appends defects to `StateMachine.snapshot.defects` for `RoutingEngine` consumption |
| PipelineNotificationHook | `gaia.hooks.production.quality_hooks` | `PHASE_EXIT` | Sends pipeline status notifications (logging, event emission) on phase completion or failure |
| ChronicleHarvestHook | `gaia.hooks.production.quality_hooks` | `PHASE_EXIT` | Appends a `STATE_TRANSITION` event entry to `PipelineSnapshot.chronicle`, forming the append-only audit log of all pipeline events |

**Metrics hooks (7), from `create_metrics_hook_group()` in `src/gaia/pipeline/metrics_hooks.py`:**

| Hook Class | Event | Metric Captured |
|-----------|-------|-----------------|
| `PhaseEnterMetricsHook` | `PHASE_ENTER` | Phase start timestamp; state transition record via `PipelineMetricsCollector.start_phase()` and `record_state_transition()` |
| `PhaseExitMetricsHook` | `PHASE_EXIT` | Phase duration via `PipelineMetricsCollector.end_phase()`; phase outcome (success/failed) |
| `LoopStartMetricsHook` | `LOOP_START` | Loop iteration count via `PipelineMetricsCollector.record_loop_iteration()` |
| `LoopEndMetricsHook` | `LOOP_END` | Quality score and defect count per loop iteration via `record_quality_score()` and `record_defect()` |
| `QualityEvalMetricsHook` | `QUALITY_EVAL` | Quality score at evaluation time via `record_quality_score()` |
| `AgentSelectMetricsHook` | `AGENT_SELECT` | Agent selection decision, rationale, and alternatives via `record_agent_selection()` |
| `HookExecutionMetricsHook` | `*` (all events) | Per-hook execution duration via `record_hook_execution()` |

The metrics hooks feed `PipelineMetricsCollector`, which maps collected data to the PM-01 through PM-14 metric IDs. For the full hook-to-metric-ID mapping table, see `docs/spec/pipeline-metrics-kpi-reference.md` Section 1.

### 6.6 ConfigurableAgent Execution Path

Every agent execution inside a pipeline loop follows a five-step path implemented in `LoopManager._execute_agent()` (`loop_manager.py:436-550`):

**Step 1 — Registry lookup**

```python
agent_def = self._agent_registry.get_agent(agent_id)
```

`AgentRegistry.get_agent(agent_id)` returns the `AgentDefinition` deserialized from the agent's YAML file. If the agent is not found, the method returns `None` and the loop records an error artifact.

**Step 2 — Model resolution and instantiation**

The model to use for inference is resolved through a four-level priority chain before `ConfigurableAgent` is instantiated:

| Priority | Source | Resolved from |
|----------|--------|---------------|
| 1 (highest) | Agent YAML `model_id` field | `agent_def.model_id` |
| 2 | `PipelineEngine` constructor `model_id` argument | `self._model_id` (engine-level override) |
| 3 | Template `default_model` field | `self._template_model_id` (from `RecursivePipelineTemplate.default_model`) |
| 4 (fallback) | Hardcoded default | `"Qwen3-0.6B-GGUF"` |

The resolved `model_id` is then passed to `ConfigurableAgent`:

```python
agent = ConfigurableAgent(
    definition=agent_def,
    tools_dir=Path("gaia/tools"),
    prompts_dir=Path("gaia/prompts"),
    silent_mode=True,
    model_id=resolved_model_id,
    skip_lemonade=self._skip_lemonade,
)
```

`silent_mode=True` suppresses console output during pipeline execution. `skip_lemonade=True` is used in CI and stub mode to bypass Lemonade Server initialization.

**Step 3 — Initialization**

```python
await agent.initialize()
```

`ConfigurableAgent.initialize()` performs two operations:
1. Registers each tool ID listed in `agent_def.tools` with the agent's tool registry, loading the tool implementation from `tools_dir`.
2. Reads the markdown file at `agent_def.system_prompt` (e.g., `prompts/senior-developer.md`) and assembles the full system prompt string for the LLM.

**Step 4 — Execution with injected context**

```python
context = {
    "goal":      loop_state.config.exit_criteria.get("goal", "Complete the task"),
    "phase":     loop_state.config.phase_name,
    "iteration": loop_state.iteration,
    "defects":   loop_state.defects,
    "artifacts": loop_state.artifacts,
}
result = await agent.execute(context)
```

The context dict provides the agent with full pipeline state: the user's goal, the current phase name, which iteration this is (enabling agents to recognize they are on a retry), the defect list from previous iterations (enabling targeted remediation), and the artifacts produced so far by earlier phases.

**Step 5 — Result handling**

`agent.execute(context)` returns a dict with the shape:

```python
{"success": bool, "artifact": Any, "error": Optional[str]}
```

If `success` is `True`, the artifact is stored in `loop_state.artifacts[agent_id]`. If `success` is `False`, an error entry is appended to `loop_state.defects` with the agent ID, error string, and current iteration number. The loop then moves to the next agent in the sequence or proceeds to quality evaluation after all agents in the sequence have run.
