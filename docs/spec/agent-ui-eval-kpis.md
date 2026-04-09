---
title: Agent UI Eval KPI Specification
description: Complete specification for the GAIA Agent UI Eval automated benchmark system including all 16 KPIs, pass/fail logic, gap registry, and production readiness thresholds.
status: Published
---

# GAIA Agent UI Eval — KPI Specification

**Version:** 1.1
**Status:** Draft — Stage 3 Pipeline Output (Final)
**Date:** 2026-03-31
**Audience:** Engineering, QA, Product Management, AMD Leadership

---

## 1. Executive Summary

The GAIA Agent UI Eval system is a fully automated benchmark that exercises the GAIA chat agent across multi-turn conversation scenarios, scoring each agent response across seven quality dimensions using Claude as an LLM judge. Its primary purpose is to provide a reproducible, cost-bounded quality gate that detects regressions before release and surfaces root causes—categorized as architecture, prompt, tool description, or RAG pipeline defects—that engineers can act on directly. This specification defines every KPI produced by the system, their precise computation formulas, pass thresholds, file locations, and known gaps requiring remediation before the system is suitable as a production gating signal.

---

## 2. Scope

### 2.1 In Scope

**System 1 — Agent UI Eval** (primary benchmark, this specification)

| Component | Location |
|---|---|
| Scenario definitions (YAML) | `eval/scenarios/` |
| Corpus documents | `eval/corpus/` |
| Corpus manifest (ground truth facts) | `eval/corpus/manifest.json` |
| Per-turn judge prompt | `eval/prompts/judge_turn.md` |
| Scenario-level judge prompt | `eval/prompts/judge_scenario.md` |
| Simulator prompt | `eval/prompts/simulator.md` |
| Eval runner (subprocess orchestrator) | `src/gaia/eval/runner.py` |
| Scorecard builder | `src/gaia/eval/scorecard.py` |
| Baseline result | `eval/results/baseline.json` |
| CLI entry point | `gaia eval agent` |

**System 2 — Legacy RAG Eval** (tertiary, covered in Section 3.4)

| Component | Location |
|---|---|
| Evaluator class (TF-IDF + Claude) | `src/gaia/eval/eval.py` |
| Batch runner | `src/gaia/eval/batch_experiment.py` |
| Groundtruth manager | `src/gaia/eval/groundtruth.py` |

### 2.2 Out of Scope

- Pipeline orchestration KPIs from `src/gaia/pipeline/` and `src/gaia/metrics/` (separate specification)
- Hardware performance benchmarks (tokens/second, NPU utilization)
- End-to-end latency SLAs (identified as Gap G-02)
- Security penetration testing or adversarial red-teaming beyond the included adversarial corpus documents

---

## 3. KPI Reference

### 3.1 Per-Turn Quality KPIs

These seven dimensions are scored by the Claude judge LLM (model: `claude-sonnet-4-6`) after each agent response turn. Each dimension score is an integer on [0, 10].

---

#### KPI-01: correctness

| Attribute | Value |
|---|---|
| **Full name** | Per-turn factual correctness |
| **Definition** | Factual accuracy of the agent response relative to the ground truth specified in the scenario YAML (`ground_truth.expected_answer` or `ground_truth.expected_behavior`). |
| **Formula** | LLM judge assigns integer score 0–10. Exact match = 10; minor omissions = 7; partial = 4; wrong or hallucinated = 0. Pre-check failures (garbled output, raw JSON leak, non-answer, tool-call artifact only) force correctness = 0 regardless of content. |
| **Null expected_answer rule** | When `expected_answer` is null, the ground truth asserts no specific answer exists. Agent saying "I don't know" or "the document doesn't mention this" scores up to 10. Inventing a specific answer scores 0. |
| **Unit** | Integer [0, 10] |
| **Hard threshold** | Two-layer enforcement: (1) Judge prompt instruction (`eval/prompts/judge_turn.md:41`) directs the judge to assign correctness = 0 when the response is a hallucinated, fabricated, or completely wrong answer. (2) Runner code (`src/gaia/eval/runner.py:640-685`) enforces `correctness < 4` as the deterministic turn-FAIL gate. The runner check `correctness < 4` subsumes `correctness == 0` — both trigger FAIL — but the `correctness == 0` rule is a judge prompt instruction, not a separate deterministic runner check at that specific line. |
| **Weight in overall** | 25% |
| **File:line** | `eval/prompts/judge_turn.md:28,41`, formula in `src/gaia/eval/runner.py:293-301` |
| **Known issues** | None at dimension level; scenario-level flakiness affects correctness variance (see Gap G-01). |

---

#### KPI-02: tool_selection

| Attribute | Value |
|---|---|
| **Full name** | Per-turn tool selection quality |
| **Definition** | Whether the agent chose the correct tool(s) in the correct order to fulfill the turn objective. |
| **Formula** | 10 = optimal (correct tool, minimal calls); 7 = correct tool but extra redundant calls; 4 = wrong tool initially but recovered; 0 = wrong or missing tool entirely. |
| **Unit** | Integer [0, 10] |
| **Hard threshold** | None; contributes to overall_score. |
| **Weight in overall** | 20% |
| **File:line** | `eval/prompts/judge_turn.md:30` |
| **Known issues** | `smart_discovery` scenario (score 2.75) documents a structural capability gap where `search_file` only scanned Windows user folders, not project subdirectories. Fixed in rerun 2. |

---

#### KPI-03: context_retention

| Attribute | Value |
|---|---|
| **Full name** | Per-turn context retention |
| **Definition** | Whether the agent correctly carried forward information from prior turns (file paths, established facts, user preferences). |
| **Formula** | 10 = perfect use of prior context; 7 = mostly retained; 4 = missed key info; 0 = completely ignored prior context. Cap at 4 if agent re-asks for information already established in a prior turn. If a prior turn failed, judge against ground truth, not the failed prior response. |
| **Unit** | Integer [0, 10] |
| **Hard threshold** | None; contributes to overall_score. |
| **Weight in overall** | 20% |
| **File:line** | `eval/prompts/judge_turn.md:30` |
| **Known issues** | Cross-session index contamination (bug confirmed in scenarios `honest_limitation` T3, `csv_analysis`) causes the agent to list documents from prior sessions as if they were available. Partially fixed by session isolation (Fix 3). |

---

#### KPI-04: completeness

| Attribute | Value |
|---|---|
| **Full name** | Per-turn answer completeness |
| **Definition** | Whether the agent addressed all parts of the user's question. |
| **Formula** | 10 = all parts answered; 7 = mostly complete; 4 = partial; 0 = did not answer the question. |
| **Unit** | Integer [0, 10] |
| **Hard threshold** | None; contributes to overall_score. |
| **Weight in overall** | 15% |
| **File:line** | `eval/prompts/judge_turn.md:31` |
| **Known issues** | VERBOSE_NO_ANSWER failure mode (scenario `concise_response` Turn 2): agent produced 84 words asking clarifying questions instead of answering from an already-linked document. Addressed by verbosity prompt fix (Fix 2). |

---

#### KPI-05: efficiency

| Attribute | Value |
|---|---|
| **Full name** | Per-turn tool efficiency |
| **Definition** | Ratio of actual tool calls to the minimum optimal number of tool calls required to answer the turn correctly. |
| **Formula** | 10 = optimal path; 7 = 1-2 extra calls; 4 = many redundant calls; 0 = tool loop (3+ identical consecutive calls with no progress). |
| **Unit** | Integer [0, 10] |
| **Hard threshold** | None; contributes to overall_score. |
| **Weight in overall** | 10% |
| **File:line** | `eval/prompts/judge_turn.md:32` |
| **Known issues** | Path truncation bug causes the agent to burn 3-5 extra tool calls per turn in affected scenarios before recovering. |

---

#### KPI-06: personality

| Attribute | Value |
|---|---|
| **Full name** | Per-turn personality and directness |
| **Definition** | Whether the agent responded with confidence, directness, and without sycophantic hedging. |
| **Formula** | 10 = concise and direct; 7 = neutral/functional; 4 = generic AI hedging; 0 = sycophantic (agreeing with wrong user premises, excessive compliments). |
| **Unit** | Integer [0, 10] |
| **Hard threshold** | None; contributes to overall_score. |
| **Weight in overall** | 5% |
| **File:line** | `eval/prompts/judge_turn.md:33` |
| **Known issues** | `no_sycophancy` T3 showed over-correction: agent added "not as stated in your message" when the user's statement was already correct. Minor phrasing issue, not a structural defect. |

---

#### KPI-07: error_recovery

| Attribute | Value |
|---|---|
| **Full name** | Per-turn error recovery |
| **Definition** | Whether the agent handled tool failures, empty results, or unexpected states gracefully rather than giving up or hallucinating. |
| **Formula** | 10 = graceful (explained situation, tried alternative); 7 = recovered after one retry; 4 = partial recovery; 0 = gave up without attempting recovery. |
| **Unit** | Integer [0, 10] |
| **Hard threshold** | None; contributes to overall_score. |
| **Weight in overall** | 5% |
| **File:line** | `eval/prompts/judge_turn.md:34` |
| **Known issues** | Agent no-adaptation bug: when Turn N search fails, agent repeated the identical failed strategy in Turn N+1 without escalation. Affected `smart_discovery` and `search_empty_fallback`. |

---

### 3.2 Turn-Level Derived KPI

#### KPI-08: turn_overall_score

| Attribute | Value |
|---|---|
| **Full name** | Per-turn overall score (weighted composite) |
| **Definition** | Weighted sum of the seven dimension scores for one turn. |
| **Formula** | `overall_score = correctness×0.25 + tool_selection×0.20 + context_retention×0.20 + completeness×0.15 + efficiency×0.10 + personality×0.05 + error_recovery×0.05` |
| **Implementation note** | The runner **overwrites** the LLM judge's self-reported `overall_score` with a deterministic recomputation using the formula above (`src/gaia/eval/runner.py:594-606`). If the LLM-reported value differs by more than 0.25, a warning is emitted to stderr. This prevents LLM arithmetic errors from polluting the scorecard. |
| **Unit** | Float [0.0, 10.0], rounded to 2 decimal places |
| **Pass threshold** | ≥ 6.0 AND correctness ≥ 4 |
| **File:line** | `src/gaia/eval/runner.py:322-334` (`recompute_turn_score`), `src/gaia/eval/runner.py:293-301` (`_SCORE_WEIGHTS`) |
| **Known issues** | None in the deterministic recomputation path. |

---

### 3.3 Scenario-Level KPIs

#### KPI-09: scenario_overall_score

| Attribute | Value |
|---|---|
| **Full name** | Scenario overall score |
| **Definition** | Mean of all per-turn `overall_score` values for the scenario. Recomputed deterministically by the runner from dimension scores. |
| **Formula** | `scenario_overall_score = mean(turn_overall_score[1..N])` |
| **Unit** | Float [0.0, 10.0], rounded to 2 decimal places |
| **Pass threshold** | ≥ 6.0 |
| **File:line** | `src/gaia/eval/runner.py:616-623` |
| **Known issues** | A scenario with one very weak non-critical turn and strong other turns may produce an aggregate score ≥ 6.0 and PASS despite a partial failure. The scenario-level judge (`eval/prompts/judge_scenario.md`) has discretion to confirm or override this. |

---

#### KPI-10: scenario_status

| Attribute | Value |
|---|---|
| **Full name** | Scenario execution status |
| **Definition** | Categorical outcome of the scenario execution after all judge and runner logic is applied. |
| **Values** | `PASS`, `FAIL`, `BLOCKED_BY_ARCHITECTURE`, `TIMEOUT`, `BUDGET_EXCEEDED`, `INFRA_ERROR`, `SETUP_ERROR`, `SKIPPED_NO_DOCUMENT`, `ERRORED` |
| **Determination** | See Section 5 (Pass/Fail Logic) |
| **File:line** | `src/gaia/eval/runner.py:633-709`, `src/gaia/eval/scorecard.py:19-43` |
| **Known issues** | `BLOCKED_BY_ARCHITECTURE` can be hallucinated by the eval LLM when architectural constraints do not actually prevent success. The runner emits a warning but does not override this status to PASS automatically (`src/gaia/eval/runner.py:690-709`). |

---

#### KPI-11: root_cause

| Attribute | Value |
|---|---|
| **Full name** | Failure root cause category |
| **Definition** | When status is FAIL or BLOCKED_BY_ARCHITECTURE, the single most likely cause of failure. |
| **Values** | `architecture`, `prompt`, `tool_description`, `rag_pipeline`, or `null` (when PASS) |
| **Formula** | LLM judge assessment per `eval/prompts/judge_scenario.md`. Also associated with a `recommended_fix` object specifying target file and description of the change needed. |
| **File:line** | `eval/prompts/judge_scenario.md:13-21` |
| **Known issues** | Root cause attribution is qualitative and subject to LLM judgment variance. |

---

#### KPI-12: elapsed_s

| Attribute | Value |
|---|---|
| **Full name** | Scenario wall-clock elapsed time |
| **Definition** | Total seconds from scenario subprocess start to result return, including claude subprocess startup, MCP tool calls, and LLM inference. |
| **Formula** | `time.time()` delta in `src/gaia/eval/runner.py:456,557` |
| **Unit** | Seconds (float) |
| **Pass threshold** | None formally defined. Scenarios TIMEOUT if `elapsed_s` exceeds the computed per-scenario ceiling (base 900s + 90s/doc + 200s/turn, capped at 7200s). |
| **File:line** | `src/gaia/eval/runner.py:46-59` (`_compute_effective_timeout`), `src/gaia/eval/runner.py:557` |
| **Known issues** | No latency SLA target established (Gap G-02). Startup overhead of 120s (`_STARTUP_OVERHEAD_S`) is a conservative buffer for claude CLI cold-start. |

---

#### KPI-13: cost_estimate_usd

| Attribute | Value |
|---|---|
| **Full name** | Per-scenario estimated judge cost |
| **Definition** | Estimated USD cost of the Claude judge API calls for one scenario. Local LLM (Qwen3 on AMD GPU) incurs zero cost; cost is entirely from Claude-as-judge. |
| **Formula** | Populated by the eval LLM agent in its JSON output. Empirically observed across the 25-scenario baseline run (`eval-20260320-182258`): total $3.15, average $0.126/scenario (range: $0.00–$0.33). Single-turn scenarios may cost as little as $0.03–$0.08; multi-turn scenarios with document indexing cost $0.15–$0.33. Scenarios where the LLM judge self-reported $0.00 likely failed before billing the judge call. |
| **Unit** | USD (float) |
| **Hard cap** | `$2.00` per scenario (`DEFAULT_BUDGET = "2.00"` in `src/gaia/eval/runner.py:46`). Budget exhaustion produces `BUDGET_EXCEEDED` status. |
| **File:line** | `src/gaia/eval/runner.py:46`, `src/gaia/eval/scorecard.py:107-110` |
| **Known issues** | Cost estimate is self-reported by the eval LLM and not verified against actual API billing. |

---

### 3.4 Scorecard-Level KPIs

#### KPI-14: judged_pass_rate

| Attribute | Value |
|---|---|
| **Full name** | Judged pass rate |
| **Definition** | The primary production quality metric. Fraction of scenarios that received a quality judgment (PASS, FAIL, or BLOCKED_BY_ARCHITECTURE) that resulted in PASS. Infrastructure failures (TIMEOUT, BUDGET_EXCEEDED, INFRA_ERROR, SETUP_ERROR, ERRORED, SKIPPED_NO_DOCUMENT) are excluded from both numerator and denominator. |
| **Formula** | `judged_pass_rate = passed / count(status in {PASS, FAIL, BLOCKED_BY_ARCHITECTURE})` |
| **Unit** | Float [0.0, 1.0] |
| **Production thresholds** | Alpha: ≥ 0.70; Beta: ≥ 0.85; Production: ≥ 0.95 |
| **Baseline (2026-03-20)** | 21/25 = 0.84 (`eval-20260320-182258`). Note: baseline.json categories are collapsed to `unknown` (infrastructure gap). |
| **File:line** | `src/gaia/eval/scorecard.py:148-152` |
| **Known issues** | If infrastructure failures dominate a run, `judged_pass_rate` can appear deceptively high while overall quality is unverified. Monitoring infrastructure failure counts separately is required. |

---

#### KPI-15: avg_score

| Attribute | Value |
|---|---|
| **Full name** | Average scenario score |
| **Definition** | Mean of per-scenario `overall_score` values for all judged scenarios. FAIL scores are capped at 5.99 for averaging to prevent a borderline FAIL from inflating the mean above the PASS threshold. |
| **Formula** | `avg_score = mean(min(overall_score, 5.99) if status==FAIL else overall_score for each judged scenario)` |
| **Unit** | Float [0.0, 10.0], rounded to 2 decimal places |
| **Production thresholds** | Alpha: ≥ 7.0; Beta: ≥ 8.0; Production: ≥ 8.5 |
| **Baseline (2026-03-20)** | 8.61 |
| **File:line** | `src/gaia/eval/scorecard.py:49-59` |
| **Known issues** | The 5.99 cap is an approximation; a FAIL scenario scoring 5.50 and one scoring 2.80 are both capped and appear equivalent in the mean. Raw scores are preserved in individual scenario records. |

---

#### KPI-16: by_category

| Attribute | Value |
|---|---|
| **Full name** | Per-category scorecard breakdown |
| **Definition** | Breakdown of pass/fail/blocked/infra/skipped counts and average score for each scenario category. |
| **Categories** | `rag_quality`, `context_retention`, `tool_selection`, `error_recovery`, `adversarial`, `personality`, `honest_limitation`, `multi_step`, `captured`, `real_world`, `vision`, `web_system` |
| **Baseline category scores (23-scenario run, 2026-03-20)** | rag_quality: 2/6 PASS, 6.96 avg; context_retention: 5/5 PASS, 9.23 avg; tool_selection: 2/3 PASS, 7.16 avg; error_recovery: 2/3 PASS, 7.58 avg; adversarial: 3/3 PASS, 8.10 avg; personality: 1/2 PASS, 8.53 avg |
| **File:line** | `src/gaia/eval/scorecard.py:62-105` |
| **Known issues** | In `eval/results/baseline.json`, all scenarios are bucketed as `unknown` because category was not injected into baseline results. The runner now injects category from the YAML at `src/gaia/eval/runner.py:588`. |

> **Warning — Infrastructure Gap affecting KPI-16:** All 25 scenarios in `eval/results/baseline.json` carry `category = "unknown"` because the baseline was generated before the runner's category injection fix. As a result, the `compare_scorecards()` function's `by_category` regression detection is currently non-functional when comparing any new run against the baseline: the baseline's single `"unknown"` bucket cannot be matched against specific categories such as `rag_quality` or `context_retention`. Per-category regression analysis requires regenerating the baseline with the current runner (`src/gaia/eval/runner.py:588`) or manually patching `baseline.json` with the correct categories. The per-category scores shown in this spec (Section 3.4 KPI-16 row, Section 5 Slide 5) are derived from a separate 23-scenario analysis run, not from the baseline.json file directly.

---

### 3.5 Legacy RAG Eval KPIs (Tertiary)

These KPIs are produced by `src/gaia/eval/eval.py` (`Evaluator` class) for the older code-generation and summarization evaluation workflows. They are distinct from the Agent UI Eval system.

#### KPI-L1: similarity_score

| Attribute | Value |
|---|---|
| **Full name** | TF-IDF cosine similarity |
| **Formula** | `cosine_similarity(TfidfVectorizer(ground_truth), TfidfVectorizer(response))` |
| **Unit** | Float [0.0, 1.0] |
| **Pass threshold** | ≥ 0.70 |
| **File:line** | `src/gaia/eval/eval.py:28-49` |

#### KPI-L2: overall_rating (qualitative)

| Attribute | Value |
|---|---|
| **Full name** | Claude qualitative rating |
| **Values** | `excellent`, `good`, `fair`, `poor` |
| **Formula** | Weighted score: correctness 40%, completeness 30%, conciseness 15%, relevance 15%. Normalized score ≥ 0.60 AND correctness ≥ "fair" required for qualitative pass. Correctness = "poor" is an automatic overall fail. |
| **File:line** | `src/gaia/eval/eval.py:88-128` |

---

## 4. Evaluation Dimensions

The following six reporting dimensions group related KPIs for root cause diagnosis and dashboard reporting. These reporting groupings are distinct from the seven per-turn scoring dimensions (KPI-01 through KPI-07) and from the five quality dimensions used by the pipeline quality scorer in `src/gaia/quality/` (see note below).

> **Note on quality scorer dimensions:** The pipeline quality scorer (`src/gaia/quality/`) uses `PROFILES["balanced"]` with five dimensions (correctness, completeness, clarity, relevance, coherence) summing to 1.00. Any stale reference to a sixth scoring dimension with 7% weight in that code's docstrings does not reflect the active configuration. This spec does not rely on the pipeline quality scorer; its seven dimensions (KPI-01–KPI-07) are defined exclusively in `eval/prompts/judge_turn.md`.

### Dimension A: Response Quality

**KPIs:** correctness (KPI-01), completeness (KPI-04), personality (KPI-06)

Measures whether the agent's response is factually accurate, fully addresses the user's question, and does so without sycophancy or unnecessary hedging. This is the primary signal for prompt and system instruction quality. Failures here typically trace to root cause `prompt` in the judge's assessment.

### Dimension B: RAG Accuracy

**KPIs:** correctness (KPI-01), tool_selection (KPI-02), efficiency (KPI-05)

Measures whether the agent correctly retrieves information from indexed documents and cites it accurately. Failures trace to root cause `rag_pipeline` (chunking, retrieval, indexing scope) or `tool_description` (agent does not know when to use which retrieval tool). The `rag_quality` scenario category is the primary test coverage for this dimension.

**Current state:** Weakest dimension. `rag_quality` category averaged 6.96/10 across 6 scenarios in the 23-scenario run, with only 2/6 PASS. Primary failures: CSV aggregation (structural chunking limitation), cross-section synthesis.

### Dimension C: MCP Session Health

**KPIs:** context_retention (KPI-03), scenario_status (KPI-10)

Measures whether the Agent UI MCP session correctly maintains document associations, history, and tool registrations across turns. Failures typically trace to root cause `architecture` (session persistence bugs, history truncation, tool deregistration between turns).

**Known architectural parameters:** `history_pairs = 5` (confirmed by `eval/results/phase1/architecture_audit.json`). Beyond 5 turn pairs, older history is dropped. The `conversation_summary` scenario (6 turns) confirmed this boundary works correctly.

### Dimension D: Latency and Performance

**KPIs:** elapsed_s (KPI-12), cost_estimate_usd (KPI-13)

Measures scenario execution time and judge API cost. Currently informational only — no formal SLA is defined (Gap G-02). Observed range: 3-8 minutes per scenario. Full 25-scenario run completed in approximately 45 minutes.

### Dimension E: Reliability and Flakiness

**KPIs:** judged_pass_rate (KPI-14), avg_score (KPI-15) across multiple runs

Measures whether scenarios produce stable, reproducible results across independent runs. The current system has no dedicated flakiness KPI (Gap G-01). Three scenarios are known to oscillate significantly across runs (see Section 7, Gap G-01 for documentation).

### Dimension F: Security and Hallucination Detection

**KPIs:** correctness (KPI-01), scenario_status (KPI-10) when root_cause = null and score = 0

The `hallucination_resistance` scenario explicitly tests whether the agent fabricates facts not present in indexed documents. It passed with 9.625/10 in the initial run and 8.75/10 in the rerun. The adversarial corpus (`eval/corpus/adversarial/`) includes `empty.txt`, `unicode_test.txt`, and `duplicate_sections.md` to test boundary conditions.

---

## 5. Pass/Fail Logic

### 5.1 Per-Turn Decision Tree

The following rules are applied in order. The first matching rule determines the outcome.

```
1. PRE-CHECK: if response is garbled, raw JSON, non-answer, or tool-call artifact only
   → correctness = 0, completeness = 0, tool_selection = 0; continue to Step 2

2. AUTOMATIC ZERO (applies only when expected_answer is a non-null string):
   - Wrong number (> 5% deviation from ground truth)
   - Wrong named entity
   - Lazy refusal (said "can't find" without calling a query tool first)
   - Hallucinated source (fact "from document" contradicts ground truth)
   → correctness = 0; continue to Step 3

3. SCORE ALL 7 DIMENSIONS (0–10 per dimension)

4. COMPUTE weighted overall_score:
   overall_score = correctness×0.25 + tool_selection×0.20 +
                   context_retention×0.20 + completeness×0.15 +
                   efficiency×0.10 + personality×0.05 + error_recovery×0.05

5. RUNNER OVERWRITE: runner recomputes overall_score deterministically from dimensions.
   LLM-reported score is discarded if it differs by > 0.25 (warning emitted).

6. TURN PASS/FAIL (two-layer architecture):
   LAYER 1 — Judge prompt instruction (eval/prompts/judge_turn.md:41):
     The judge prompt instructs the LLM judge to assign correctness = 0 for hallucinated,
     fabricated, or completely wrong answers. This is a scoring instruction, not runner code.

   LAYER 2 — Runner deterministic enforcement (src/gaia/eval/runner.py:640-685):
     a. FAIL if correctness < 4  [this subsumes correctness == 0; both trigger FAIL]
     b. FAIL if overall_score < 6.0
     c. PASS otherwise

   Note: correctness == 0 is NOT a separate deterministic runner check distinct from
   correctness < 4. The runner enforces a single threshold of correctness < 4 at lines
   640-685. The specific correctness == 0 behavior is implemented as a judge prompt
   instruction only (eval/prompts/judge_turn.md:41), not as additional runner code.
```

Sources: `eval/prompts/judge_turn.md:5-44`, `src/gaia/eval/runner.py:640-685`

### 5.2 Scenario-Level Status Derivation

After all turns are scored, the scenario-level judge assesses holistically. The runner then applies deterministic overrides:

```
INFRASTRUCTURE STATUSES (runner-set, not overridden):
  - INFRA_ERROR: Agent UI health check failed
  - SETUP_ERROR: Document indexing returned 0 chunks (non-adversarial)
  - TIMEOUT: Scenario exceeded computed timeout ceiling
  - BUDGET_EXCEEDED: Claude API budget cap hit ($2.00)
  - ERRORED: Eval agent subprocess crashed or returned unparseable JSON
  - SKIPPED_NO_DOCUMENT: Corpus file not on disk (real-world scenarios)

JUDGE-SET STATUSES (subject to runner override):
  - BLOCKED_BY_ARCHITECTURE: Architecture constraint demonstrably prevents success
    (runner warns but does NOT override to PASS even if turn scores suggest otherwise)
  - PASS → runner may override to FAIL if:
      any turn correctness < 4, OR scenario overall_score < 6.0
  - FAIL → runner may override to PASS if:
      ALL turns have correctness scores, ALL correctness ≥ 4, AND overall_score ≥ 6.0

FINAL STATUS: first matching rule wins (infrastructure statuses take precedence)
```

Sources: `src/gaia/eval/runner.py:633-709`, `eval/prompts/judge_scenario.md:16-28`

### 5.3 Scorecard Aggregation

```
judged_pass_rate = passed / count(results where status in _JUDGED_STATUSES)
                   where _JUDGED_STATUSES = {PASS, FAIL, BLOCKED_BY_ARCHITECTURE}
                   (infrastructure failures excluded from denominator)

Edge case note: The denominator includes any result whose status is in _JUDGED_STATUSES
even if overall_score is null (src/gaia/eval/scorecard.py:148-152). A scenario that was
judged but returned a null score still appears in the denominator, potentially deflating
pass rate. This situation arises when the eval agent returns a status judgment without
a numeric score.

avg_score = mean(
  min(overall_score, 5.99) if status == FAIL
  else overall_score
  for all scenarios where status in {PASS, FAIL, BLOCKED_BY_ARCHITECTURE}
  and overall_score is not null
)
```

Source: `src/gaia/eval/scorecard.py:16-160`

---

## 6. Data Flow

```
eval/scenarios/**/*.yaml        eval/corpus/manifest.json
         |                               |
         v                               v
   find_scenarios()            build_scenario_prompt()
   validate_scenario()               |
         |                            |
         +----------------------------+
                      |
                      v
          run_scenario_subprocess()
                      |
                      v
           claude -p <prompt>
           --mcp-config eval/mcp-config.json
           --model claude-sonnet-4-6
           --max-budget-usd 2.00
           --dangerously-skip-permissions
                      |
                      v
           [Eval Agent (Claude subprocess)]
           Phase 1: system_status() → create_session()
                    → index_document(session_id=...)
           Phase 2: send_message() per turn
                    → score each turn (judge_turn.md rubric)
           Phase 3: get_messages() (full trace)
           Phase 4: scenario judgment (judge_scenario.md)
           Phase 5: delete_session()
           Phase 6: return JSON to stdout
                      |
                      v
           [Runner: parse and validate JSON]
           - recompute_turn_score() for each turn
           - overwrite overall_score with deterministic value
           - recompute per-turn pass flag
           - recompute scenario overall_score (mean of turns)
           - apply PASS→FAIL or FAIL→PASS overrides
           - write trace to eval/results/<run_id>/traces/<scenario_id>.json
                      |
                      v
           aggregate_scorecard()
           → build_scorecard() → scorecard.json
           → write_summary_md() → summary.md
```

**Key invariants enforced by the runner:**
1. The eval agent's arithmetic is never trusted; all scores are recomputed from dimension values.
2. Category is injected from YAML metadata, not from the eval agent's output.
3. `scenario_id` is always set from the runner's known value, overwriting any eval agent value.
4. A progress file (`.progress.json`) enables interrupted runs to resume without repeating completed scenarios.

---

## 7. Gap Registry

The following missing KPIs and capabilities have been identified. Priority ratings: P0 = production blocker; P1 = required before Beta graduation; P2 = required before Production graduation; P3 = nice to have.

### G-01: No Flakiness / Variance KPI (P0)

**Description:** The system executes each scenario once per run. There is no mechanism to detect whether a scenario's PASS/FAIL status is stable across multiple independent runs. Three scenarios have been documented as flaky through manual multi-run observation:

| Scenario | Observed scores across runs | Pattern |
|---|---|---|
| `negation_handling` | 9.8 → 8.4 → 4.62 (FAIL) → 8.63 (PASS) | Oscillating PASS/FAIL |
| `vague_request_clarification` | 9.5 (PASS) → 4.5 (FAIL) → 8.15 (PASS) | Oscillating PASS/FAIL |
| `conversation_summary` | Multiple PASS runs, one observed regression | Occasional FAIL |

**Proposed remediation (from `eval/ARCHITECTURE_ANALYSIS.md §2.6`):** Add `--runs N` flag to `gaia eval agent` to execute each scenario N times (default: 3 passes per the proposed `MultiPassEvaluator`). Compute `flakiness_score = FAIL_count / N` and `pass_rate = PASS_count / N` per scenario. Classify using these rules:

- `STABLE_PASS`: median score >= pass_threshold AND min score >= (pass_threshold - 1.0)
- `FLAKY_PASS`: median score >= pass_threshold BUT min score < (pass_threshold - 1.0)
- `FLAKY_FAIL`: passes sometimes but fails on median score
- `STABLE_FAIL`: median score < pass_threshold (consistent failure)

Require zero STABLE_FAIL scenarios for Production graduation.

**Impact:** Without this, a single-pass run showing 84% pass rate may not reflect the actual reliable pass rate. LLM non-determinism alone accounts for ±8 percentage points of variance across runs (observed: 76%–84% across three full runs of 25 scenarios).

---

### G-02: No Latency SLA (P1)

**Description:** No maximum acceptable `elapsed_s` per scenario or per full run is defined. The system has a timeout ceiling but no target. Observed range: 3-8 minutes/scenario. A full 25-scenario run takes approximately 45 minutes.

**Proposed remediation:** Define per-scenario latency budget (e.g., ≤ 5 minutes for ≤ 3 turns, ≤ 10 minutes for ≥ 4 turns). Report percentage of scenarios exceeding budget. Add scorecard-level `p90_elapsed_s` metric.

---

### G-03: No chunk_count in Scorecard (P1)

**Description:** When documents are indexed, the `chunk_count` returned by `index_document` is logged in run traces but is not aggregated into the scorecard. The `table_extraction` failure (5.17/10) was directly caused by a 26KB/500-row CSV being indexed into only 2 chunks, giving the agent less than 10% data visibility. Without tracking `chunk_count`, this class of failure is invisible at the scorecard level.

**Proposed remediation:** Add `chunk_count` per document to each scenario result dict. Aggregate as `min_chunk_count` and `avg_chunk_count` in the scorecard. Flag scenarios where `chunk_count < 5` for a document with more than 10KB as a potential data coverage risk.

---

### G-04: No Real-World Corpus Coverage Metric (P1)

**Description:** The scenario suite (`eval/scenarios/real_world/`) includes 19 real-world document scenarios (Alphabet 10-K, RFC 7231, NIST CSF 2.0, etc.), but these scenarios produce `SKIPPED_NO_DOCUMENT` in CI because the actual PDF/XLSX files are not committed to the repository. There is no metric tracking what fraction of the total scenario suite is exercisable in a given environment.

**Proposed remediation:** Add `exercisable_scenario_fraction = (total - skipped) / total` to the scorecard. Define a minimum threshold (e.g., ≥ 80% exercisable) for a run to be considered complete. Document the real-world document acquisition process in `docs/reference/dev.mdx`.

---

### G-05: No Cross-Run Trend Tracking (P2)

**Description:** Each run produces an independent scorecard. The `--compare` flag enables manual comparison between two scorecards, but there is no persistent time-series store of run results. It is not possible to observe trends such as gradual score drift, category-level regressions, or cost creep over time without manual analysis.

**Proposed remediation:** Emit structured `metrics.jsonl` (one line per run) to a persistent path (`eval/results/metrics.jsonl`). Include `run_id`, `timestamp`, `judged_pass_rate`, `avg_score`, `by_category`, `total_cost_usd`, and `exercisable_scenario_fraction`. Build a lightweight HTML report from this file (or integrate with an existing dashboard).

---

### G-06: Judge Model Version Not Pinned in Scorecard (P2)

**Description:** The `config` section of the scorecard records the judge model name (`claude-sonnet-4-6`) but not the exact model version/snapshot. If the judge model is updated, scores from before and after the update are not directly comparable, but this is not flagged in the comparison output.

**Proposed remediation:** Record judge model API version string in scorecard config. Add a warning to `compare_scorecards()` when the judge model versions differ between baseline and current.

---

### G-07: No Automated Regression Gate in CI (P2)

**Description:** The eval system runs manually (`gaia eval agent`). There is no GitHub Actions workflow that runs the eval suite on PR merge to main and blocks if `judged_pass_rate` drops below a threshold.

**Proposed remediation:** Add `.github/workflows/agent-ui-eval.yml` that runs the eval suite on a schedule (nightly) and optionally on PR (with cost budget). Use `--compare eval/results/baseline.json` to detect regressions. Fail the workflow if any scenario regresses from PASS to FAIL.

---

### G-08: Eval Agent Persona Coverage (P3)

**Description:** Five personas are defined in `eval/prompts/simulator.md` (`casual_user`, `power_user`, `confused_user`, `adversarial_user`, `data_analyst`), but the majority of current scenarios use `casual_user` or `power_user`. There is no scorecard metric tracking pass rates broken down by persona.

**Proposed remediation:** Add `by_persona` breakdown to the scorecard, mirroring the existing `by_category` structure.

---

### G-09: No Tool Call Count in Trace (P3)

**Description:** The `agent_tools` field in each turn records which tools were called but not how many total calls were made or whether any calls were repeated. The efficiency score (KPI-05) depends on this, but it is assessed subjectively by the judge LLM without a ground truth tool count.

**Proposed remediation:** Add `tool_call_count` and `unique_tool_count` to each turn result. Compare against the scenario YAML's optional `expected_tools` list.

---

### G-10: No Corpus Fact Coverage Metric (P3)

**Description:** `eval/corpus/manifest.json` contains structured facts for each corpus document (e.g., `$14.2M Q3 revenue`, `Sarah Chen = $70,000 Q1 sales`). There is no metric tracking what percentage of these facts are tested by at least one scenario turn. Low fact coverage means critical agent behaviors may be untested.

**Proposed remediation:** Build a fact-to-scenario mapping at test collection time. Add `fact_coverage_pct` to the scorecard. Target ≥ 80% fact coverage before Beta graduation.

---

## 8. Production Readiness Thresholds

The following thresholds define graduation criteria for the GAIA Agent UI from development to production use. All thresholds apply to a single-pass run of the full scenario suite unless otherwise noted.

### Alpha (Internal Testing)

| KPI | Threshold |
|---|---|
| `judged_pass_rate` | ≥ 70% |
| `avg_score` | ≥ 7.0 |
| Infrastructure failures | < 20% of scenarios |
| `STABLE_FAIL` scenarios | No requirement (G-01 not yet implemented; classification requires multi-pass runs) |

**Current state:** PASS (84% judged pass rate, 8.61 avg score as of 2026-03-20)

### Beta (External Preview)

| KPI | Threshold |
|---|---|
| `judged_pass_rate` | ≥ 85% |
| `avg_score` | ≥ 8.0 |
| `STABLE_FAIL` scenarios | Zero (requires G-01 implementation) |
| `exercisable_scenario_fraction` | ≥ 60% (requires G-04 implementation) |
| No category with `avg_score` < 6.0 | Required |

**Current state:** Borderline. Single-pass rate at 84% (1 pp below threshold). Known flaky scenarios (G-01) may push below threshold when multi-run flakiness is measured. `rag_quality` category at 6.96 avg is close to the 6.0 floor.

### Production

| KPI | Threshold |
|---|---|
| `judged_pass_rate` | ≥ 95% |
| `avg_score` | ≥ 8.5 |
| `STABLE_FAIL` scenarios | Zero |
| `FLAKY_FAIL` scenarios | Zero |
| `exercisable_scenario_fraction` | ≥ 80% |
| No category with `avg_score` < 7.0 | Required |
| Automated CI regression gate | Required (G-07) |
| Judge model version pinned | Required (G-06) |

**Current state:** Not ready. Three known STABLE_FAIL-candidate scenarios (`smart_discovery`, `table_extraction`, `search_empty_fallback` in their pre-fix states) and three known flaky scenarios prevent graduation.

---

## Appendix A: Scenario Category Inventory (as of 2026-03-31)

> **Source:** Direct file count from `eval/scenarios/` directory. Total on disk: 54 YAML files.

| Category | Count | Notes |
|---|---|---|
| `rag_quality` | 7 | Includes `budget_query` added after baseline run |
| `context_retention` | 4 | Strong performer (9.23 avg) |
| `tool_selection` | 4 | Includes `multi_step_plan` |
| `error_recovery` | 3 | |
| `adversarial` | 3 | `empty_file`, `large_document`, `topic_switch` |
| `personality` | 3 | Includes `honest_limitation` |
| `captured` | 2 | From real user sessions |
| `real_world` | 19 | Require document acquisition; SKIPPED_NO_DOCUMENT in CI |
| `vision` | 3 | VLM-dependent; require additional system capabilities |
| `web_system` | 6 | Require system tool access |
| **Total** | **54** | 25 exercisable in the baseline CI run (pre-`budget_query`); 26 with `budget_query` added |

## Appendix B: Key File Reference

| File | Role |
|---|---|
| `eval/prompts/judge_turn.md` | Per-turn scoring rubric (normative) |
| `eval/prompts/judge_scenario.md` | Scenario-level judgment instructions (normative) |
| `eval/prompts/simulator.md` | User persona definitions (normative) |
| `src/gaia/eval/runner.py` | Eval orchestrator; contains `_SCORE_WEIGHTS`, `recompute_turn_score`, all status override logic |
| `src/gaia/eval/scorecard.py` | Scorecard builder; contains `judged_pass_rate` formula, FAIL score cap |
| `eval/results/baseline.json` | Reference scorecard: 25 scenarios, 21 PASS, 84%, 8.61 avg |
| `eval/corpus/manifest.json` | Structured facts for all corpus documents (ground truth source) |
| `eval/mcp-config.json` | MCP server config for Agent UI tool access |
