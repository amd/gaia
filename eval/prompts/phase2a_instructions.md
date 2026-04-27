# Phase 2A — Build Eval Infrastructure

Build all Phase 2 infrastructure files. Do NOT run any scenarios yet.

## Working directory
All relative paths are from: `C:\Users\14255\Work\gaia4`

---

## STEP 1 — Create scenario directories

Run:
```
mkdir -p eval/scenarios/context_retention
mkdir -p eval/scenarios/rag_quality
mkdir -p eval/scenarios/tool_selection
mkdir -p eval/scenarios/error_recovery
mkdir -p eval/scenarios/adversarial
mkdir -p eval/scenarios/personality
```

---

## STEP 2 — Write 5 critical scenario YAML files

### 2a. eval/scenarios/rag_quality/simple_factual_rag.yaml
```yaml
id: simple_factual_rag
name: "Simple Factual RAG"
category: rag_quality
severity: critical
description: |
  Direct fact lookup from a financial report.
  Agent must index the document and answer questions from it.

persona: power_user

setup:
  index_documents:
    - corpus_doc: acme_q3_report
      path: "eval/corpus/documents/acme_q3_report.md"

turns:
  - turn: 1
    objective: "Ask about Q3 revenue"
    ground_truth:
      doc_id: acme_q3_report
      fact_id: q3_revenue
      expected_answer: "$14.2 million"
    success_criteria: "Agent states Q3 revenue was $14.2 million"

  - turn: 2
    objective: "Ask about year-over-year growth"
    ground_truth:
      doc_id: acme_q3_report
      fact_id: yoy_growth
      expected_answer: "23% increase from Q3 2024's $11.5 million"
    success_criteria: "Agent mentions 23% growth and/or $11.5M baseline"

  - turn: 3
    objective: "Ask about CEO outlook for Q4"
    ground_truth:
      doc_id: acme_q3_report
      fact_id: ceo_outlook
      expected_answer: "Projected 15-18% growth driven by enterprise segment expansion"
    success_criteria: "Agent mentions 15-18% projected growth"

expected_outcome: |
  Agent correctly retrieves and reports facts from the Q3 financial report.
```

### 2b. eval/scenarios/rag_quality/hallucination_resistance.yaml
```yaml
id: hallucination_resistance
name: "Hallucination Resistance"
category: rag_quality
severity: critical
description: |
  Agent must admit when information is NOT in the indexed document.
  The document does NOT contain employee count — agent must say so.

persona: data_analyst

setup:
  index_documents:
    - corpus_doc: acme_q3_report
      path: "eval/corpus/documents/acme_q3_report.md"

turns:
  - turn: 1
    objective: "Ask about Q3 revenue (known fact - warm up)"
    ground_truth:
      doc_id: acme_q3_report
      fact_id: q3_revenue
      expected_answer: "$14.2 million"
    success_criteria: "Agent correctly states $14.2 million"

  - turn: 2
    objective: "Ask about employee count (NOT in the document)"
    ground_truth:
      doc_id: acme_q3_report
      fact_id: employee_count
      expected_answer: null
      note: "NOT in document — agent must say it doesn't know"
    success_criteria: "Agent admits it cannot find employee count in the document. FAIL if agent guesses or invents a number."

expected_outcome: |
  Agent correctly admits employee count is not in the document.
  Agent should NOT hallucinate or guess a number.
```

### 2c. eval/scenarios/context_retention/pronoun_resolution.yaml
```yaml
id: pronoun_resolution
name: "Pronoun Resolution"
category: context_retention
severity: critical
description: |
  User asks follow-up questions using pronouns ("it", "that policy").
  Agent must retain context and resolve references without re-querying.

persona: casual_user

setup:
  index_documents:
    - corpus_doc: employee_handbook
      path: "eval/corpus/documents/employee_handbook.md"

turns:
  - turn: 1
    objective: "Ask about PTO policy for new employees"
    ground_truth:
      doc_id: employee_handbook
      fact_id: pto_days
      expected_answer: "15 days"
    success_criteria: "Agent states first-year employees get 15 PTO days"

  - turn: 2
    objective: "Ask follow-up using pronoun: 'what about remote work - does it have a policy too?'"
    ground_truth:
      doc_id: employee_handbook
      fact_id: remote_work
      expected_answer: "Up to 3 days/week with manager approval. Fully remote requires VP approval."
    success_criteria: "Agent understands 'it' refers to the handbook and answers remote work policy"

  - turn: 3
    objective: "Ask 'does that policy apply to contractors too?' using pronoun"
    ground_truth:
      doc_id: employee_handbook
      fact_id: contractor_benefits
      expected_answer: "No — benefits are for full-time employees only"
    success_criteria: "Agent correctly states contractors are NOT eligible. FAIL if agent says contractors are eligible."

expected_outcome: |
  Agent maintains context across turns and resolves pronouns correctly.
```

### 2d. eval/scenarios/context_retention/cross_turn_file_recall.yaml
```yaml
id: cross_turn_file_recall
name: "Cross-Turn File Recall"
category: context_retention
severity: critical
description: |
  User indexes a document in Turn 1, then asks about its content in Turn 2
  without re-mentioning the document name. Agent must recall what was indexed.

persona: casual_user

setup:
  index_documents:
    - corpus_doc: product_comparison
      path: "eval/corpus/documents/product_comparison.html"

turns:
  - turn: 1
    objective: "Ask agent to list what documents are available/indexed"
    ground_truth: null
    success_criteria: "Agent lists the product comparison document or indicates a document has been indexed"

  - turn: 2
    objective: "Ask about pricing without naming the file: 'how much do the two products cost?'"
    ground_truth:
      doc_id: product_comparison
      fact_ids: [price_a, price_b]
      expected_answer: "StreamLine $49/month, ProFlow $79/month"
    success_criteria: "Agent correctly states both prices from the indexed document"

  - turn: 3
    objective: "Follow-up with pronoun: 'which one is better value for money?'"
    ground_truth:
      doc_id: product_comparison
    success_criteria: "Agent answers based on indexed document context, not hallucinated facts"

expected_outcome: |
  Agent recalls the indexed document across turns and answers without re-indexing.
```

### 2e. eval/scenarios/tool_selection/smart_discovery.yaml
```yaml
id: smart_discovery
name: "Smart Discovery"
category: tool_selection
severity: critical
description: |
  No documents are pre-indexed. User asks about PTO policy.
  Agent must: search for relevant file → find employee_handbook.md → index it → answer.

persona: power_user

setup:
  index_documents: []  # No pre-indexed documents

turns:
  - turn: 1
    objective: "Ask about PTO policy with no documents indexed"
    ground_truth:
      doc_id: employee_handbook
      fact_id: pto_days
      expected_answer: "15 days"
    success_criteria: |
      Agent discovers and indexes employee_handbook.md (or similar HR document),
      then correctly answers: first-year employees get 15 PTO days.
      FAIL if agent says 'no documents available' without trying to find them.

  - turn: 2
    objective: "Ask follow-up: 'what about the remote work policy?'"
    ground_truth:
      doc_id: employee_handbook
      fact_id: remote_work
      expected_answer: "Up to 3 days/week with manager approval"
    success_criteria: "Agent answers from already-indexed document without re-indexing"

expected_outcome: |
  Agent proactively discovers and indexes the employee handbook, then answers accurately.
```

---

## STEP 3 — Write eval prompt files

### 3a. eval/prompts/simulator.md

Write this file:
```
# GAIA Eval Agent — Simulator + Judge System Prompt

You are the GAIA Eval Agent. You test the GAIA Agent UI by:
1. Acting as a realistic user (simulator)
2. Judging the agent's responses (judge)

You have access to the Agent UI MCP server (gaia-agent-ui). Use its tools to drive conversations.

## PERSONAS

- casual_user: Short messages, uses pronouns ("that file", "the one you showed me"), occasionally vague.
- power_user: Precise requests, names specific files, multi-step asks.
- confused_user: Wrong terminology, unclear requests, then self-corrects.
- adversarial_user: Edge cases, rapid topic switches, impossible requests.
- data_analyst: Asks about numbers, comparisons, aggregations.

## SIMULATION RULES

- Sound natural — typos OK, overly formal is not
- Use pronouns and references to test context retention
- If agent asked a clarifying question, answer it naturally
- If agent got something wrong, push back
- Stay in character for the assigned persona
- Generate the actual user message to send (not a description of it)

## JUDGING DIMENSIONS (score each 0-10)

- correctness (weight 25%): Factual accuracy vs ground truth. 10=exact, 7=mostly right, 4=partial, 0=wrong/hallucinated
- tool_selection (weight 20%): Right tools chosen. 10=optimal, 7=correct+extra calls, 4=wrong but recovered, 0=completely wrong
- context_retention (weight 20%): Used info from prior turns. 10=perfect recall, 7=mostly, 4=missed key info, 0=ignored prior turns
- completeness (weight 15%): Fully answered. 10=complete, 7=mostly, 4=partial, 0=didn't answer
- efficiency (weight 10%): Steps vs optimal. 10=optimal, 7=1-2 extra, 4=many extra, 0=tool loop
- personality (weight 5%): GAIA voice — direct, witty, no sycophancy. 10=great, 7=neutral, 4=generic AI, 0=sycophantic
- error_recovery (weight 5%): Handles failures. 10=graceful, 7=recovered after retry, 4=partial, 0=gave up

## OVERALL SCORE FORMULA

overall = correctness*0.25 + tool_selection*0.20 + context_retention*0.20
        + completeness*0.15 + efficiency*0.10 + personality*0.05 + error_recovery*0.05

PASS if overall_score >= 6.0 AND no critical failure.

## FAILURE CATEGORIES

- wrong_answer: Factually incorrect
- hallucination: Claims not supported by any document or context
- context_blindness: Ignores info from previous turns
- wrong_tool: Uses clearly inappropriate tool
- gave_up: Stops trying after error/empty result
- tool_loop: Calls same tool repeatedly without progress
- no_fallback: First approach fails, no alternatives tried
- personality_violation: Sycophantic, verbose, or off-brand
```

### 3b. eval/prompts/judge_turn.md

Write this file:
```
# Per-Turn Judge Instructions

After each agent response, evaluate:

1. Did the agent correctly answer the question? Compare to ground truth if provided.
2. Did the agent use the right tools? Were there unnecessary calls?
3. Did the agent use information from previous turns?
4. Was the answer complete?
5. Was the path to the answer efficient?
6. Did the agent sound natural (not sycophantic, not overly verbose)?
7. If any tool failed, did the agent recover gracefully?

Score each dimension 0-10 per the weights in simulator.md.

Output format:
{
  "scores": {
    "correctness": N,
    "tool_selection": N,
    "context_retention": N,
    "completeness": N,
    "efficiency": N,
    "personality": N,
    "error_recovery": N
  },
  "overall_score": N.N,
  "pass": true/false,
  "failure_category": null or "category_name",
  "reasoning": "1-2 sentence explanation"
}
```

### 3c. eval/prompts/judge_scenario.md

Write this file:
```
# Scenario-Level Judge Instructions

After all turns are complete, evaluate the scenario holistically:

1. Did the agent complete the overall task?
2. Was the conversation coherent across turns?
3. What is the root cause of any failures?
4. What specific code change would fix the issue?

Categories:
- architecture: Requires changes to _chat_helpers.py, agent persistence, history
- prompt: Requires changes to system prompt in agent.py
- tool_description: Requires updating tool docstrings
- rag_pipeline: Requires changes to how documents are indexed or retrieved

Output format:
{
  "scenario_complete": true/false,
  "root_cause": null or "description",
  "recommended_fix": null or {
    "target": "architecture|prompt|tool_description|rag_pipeline",
    "file": "path/to/file.py",
    "description": "specific change to make"
  }
}
```

---

## STEP 4 — Write src/gaia/eval/runner.py

Write this file with the following content:

```python
"""
AgentEvalRunner — runs eval scenarios via `claude -p` subprocess.
Each scenario is one claude subprocess invocation that:
  - reads the scenario YAML + corpus manifest
  - drives a conversation via Agent UI MCP tools
  - judges each turn
  - returns structured JSON to stdout

Usage:
  from gaia.eval.runner import AgentEvalRunner
  runner = AgentEvalRunner()
  runner.run()
"""

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent.parent
EVAL_DIR = REPO_ROOT / "eval"
SCENARIOS_DIR = EVAL_DIR / "scenarios"
CORPUS_DIR = EVAL_DIR / "corpus"
RESULTS_DIR = EVAL_DIR / "results"
MCP_CONFIG = EVAL_DIR / "mcp-config.json"
MANIFEST = CORPUS_DIR / "manifest.json"

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_BACKEND = "http://localhost:4200"
DEFAULT_BUDGET = "0.50"
DEFAULT_TIMEOUT = 300  # seconds per scenario


def find_scenarios(scenario_id=None, category=None):
    """Find scenario YAML files matching filters."""
    scenarios = []
    for path in sorted(SCENARIOS_DIR.rglob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if scenario_id and data.get("id") != scenario_id:
                continue
            if category and data.get("category") != category:
                continue
            scenarios.append((path, data))
        except Exception as e:
            print(f"[WARN] Failed to parse {path}: {e}", file=sys.stderr)
    return scenarios


def build_scenario_prompt(scenario_data, manifest_data, backend_url):
    """Build the prompt passed to `claude -p` for one scenario."""
    scenario_yaml = yaml.dump(scenario_data, default_flow_style=False)
    manifest_json = json.dumps(manifest_data, indent=2)

    corpus_root = str(CORPUS_DIR / "documents").replace("\\", "/")
    adversarial_root = str(CORPUS_DIR / "adversarial").replace("\\", "/")

    return f"""You are the GAIA Eval Agent. Test the GAIA Agent UI by simulating a realistic user and judging responses.

Read eval/prompts/simulator.md for your system prompt and scoring rules.

## SCENARIO
```yaml
{scenario_yaml}
```

## CORPUS MANIFEST (ground truth)
```json
{manifest_json}
```

## DOCUMENT PATHS
- Main documents: {corpus_root}/
- Adversarial docs: {adversarial_root}/
- Use ABSOLUTE paths when calling index_document

## AGENT UI
Backend: {backend_url}

## YOUR TASK

### Phase 1: Setup
1. Call system_status() — if error, return status="INFRA_ERROR"
2. Call create_session("Eval: {{scenario_id}}")
3. For each document in scenario setup.index_documents:
   Call index_document with absolute path
   If chunk_count=0 or error, return status="SETUP_ERROR"

### Phase 2: Simulate + Judge
For each turn in the scenario:
1. Generate a realistic user message matching the turn objective and persona
2. Call send_message(session_id, user_message)
3. Judge the response per eval/prompts/judge_turn.md

### Phase 3: Full trace
After all turns, call get_messages(session_id) for the persisted full trace.

### Phase 4: Scenario judgment
Evaluate holistically per eval/prompts/judge_scenario.md

### Phase 5: Cleanup
Call delete_session(session_id)

### Phase 6: Return result
Return a single JSON object to stdout with this structure:
{{
  "scenario_id": "...",
  "status": "PASS|FAIL|BLOCKED_BY_ARCHITECTURE|INFRA_ERROR|SETUP_ERROR|TIMEOUT|ERRORED",
  "overall_score": 0-10,
  "turns": [
    {{
      "turn": 1,
      "user_message": "...",
      "agent_response": "...",
      "agent_tools": ["tool1"],
      "scores": {{"correctness": 0-10, "tool_selection": 0-10, "context_retention": 0-10,
                  "completeness": 0-10, "efficiency": 0-10, "personality": 0-10, "error_recovery": 0-10}},
      "overall_score": 0-10,
      "pass": true,
      "failure_category": null,
      "reasoning": "..."
    }}
  ],
  "root_cause": null,
  "recommended_fix": null,
  "cost_estimate": {{"turns": N, "estimated_usd": 0.00}}
}}
"""


def preflight_check(backend_url):
    """Check prerequisites before running scenarios."""
    import urllib.request
    import urllib.error

    errors = []

    # Check Agent UI health
    try:
        with urllib.request.urlopen(f"{backend_url}/api/health", timeout=5) as r:
            if r.status != 200:
                errors.append(f"Agent UI returned HTTP {r.status}")
    except urllib.error.URLError as e:
        errors.append(f"Agent UI not reachable at {backend_url}: {e}")

    # Check corpus manifest
    if not MANIFEST.exists():
        errors.append(f"Corpus manifest not found: {MANIFEST}")

    # Check MCP config
    if not MCP_CONFIG.exists():
        errors.append(f"MCP config not found: {MCP_CONFIG}")

    # Check claude CLI
    result = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    if result.returncode != 0:
        errors.append("'claude' CLI not found on PATH — install Claude Code CLI")

    return errors


def run_scenario_subprocess(scenario_path, scenario_data, run_dir, backend_url, model, budget, timeout):
    """Invoke claude -p for one scenario. Returns parsed result dict."""
    scenario_id = scenario_data["id"]
    manifest_data = json.loads(MANIFEST.read_text(encoding="utf-8"))

    prompt = build_scenario_prompt(scenario_data, manifest_data, backend_url)

    result_schema = json.dumps({
        "type": "object",
        "required": ["scenario_id", "status", "overall_score", "turns"],
        "properties": {
            "scenario_id": {"type": "string"},
            "status": {"type": "string"},
            "overall_score": {"type": "number"},
            "turns": {"type": "array"},
            "root_cause": {},
            "recommended_fix": {},
            "cost_estimate": {"type": "object"},
        }
    })

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--json-schema", result_schema,
        "--mcp-config", str(MCP_CONFIG),
        "--strict-mcp-config",
        "--model", model,
        "--permission-mode", "auto",
        "--max-budget-usd", budget,
    ]

    print(f"\n[RUN] {scenario_id} — invoking claude -p ...", flush=True)
    start = time.time()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(REPO_ROOT),
        )
        elapsed = time.time() - start

        if proc.returncode != 0:
            print(f"[ERROR] {scenario_id} — exit code {proc.returncode}", file=sys.stderr)
            print(proc.stderr[:500], file=sys.stderr)
            result = {
                "scenario_id": scenario_id,
                "status": "ERRORED",
                "overall_score": 0,
                "turns": [],
                "error": proc.stderr[:500],
                "elapsed_s": elapsed,
            }
        else:
            # Parse JSON from stdout
            try:
                # claude --output-format json wraps result; extract the content
                raw = json.loads(proc.stdout)
                # The result might be wrapped in {"result": {...}} or direct
                if isinstance(raw, dict) and "result" in raw:
                    result = raw["result"] if isinstance(raw["result"], dict) else json.loads(raw["result"])
                else:
                    result = raw
                result["elapsed_s"] = elapsed
                print(f"[DONE] {scenario_id} — {result.get('status')} {result.get('overall_score', 0):.1f}/10 ({elapsed:.0f}s)")
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[ERROR] {scenario_id} — JSON parse error: {e}", file=sys.stderr)
                result = {
                    "scenario_id": scenario_id,
                    "status": "ERRORED",
                    "overall_score": 0,
                    "turns": [],
                    "error": f"JSON parse error: {e}. stdout: {proc.stdout[:300]}",
                    "elapsed_s": elapsed,
                }

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"[TIMEOUT] {scenario_id} — exceeded {timeout}s", file=sys.stderr)
        result = {
            "scenario_id": scenario_id,
            "status": "TIMEOUT",
            "overall_score": 0,
            "turns": [],
            "elapsed_s": elapsed,
        }

    # Write trace file
    traces_dir = run_dir / "traces"
    traces_dir.mkdir(exist_ok=True)
    trace_path = traces_dir / f"{scenario_id}.json"
    trace_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    return result


def aggregate_scorecard(results, run_id, run_dir, config):
    """Build scorecard.json + summary.md from all scenario results."""
    from gaia.eval.scorecard import build_scorecard, write_summary_md
    scorecard = build_scorecard(run_id, results, config)
    scorecard_path = run_dir / "scorecard.json"
    scorecard_path.write_text(json.dumps(scorecard, indent=2, ensure_ascii=False), encoding="utf-8")

    summary_path = run_dir / "summary.md"
    summary_path.write_text(write_summary_md(scorecard), encoding="utf-8")

    return scorecard


class AgentEvalRunner:
    def __init__(
        self,
        backend_url=DEFAULT_BACKEND,
        model=DEFAULT_MODEL,
        budget_per_scenario=DEFAULT_BUDGET,
        timeout_per_scenario=DEFAULT_TIMEOUT,
        results_dir=None,
    ):
        self.backend_url = backend_url
        self.model = model
        self.budget = budget_per_scenario
        self.timeout = timeout_per_scenario
        self.results_dir = Path(results_dir) if results_dir else RESULTS_DIR

    def run(self, scenario_id=None, category=None, audit_only=False):
        """Run eval scenarios. Returns scorecard dict."""

        if audit_only:
            from gaia.eval.audit import run_audit
            result = run_audit()
            print(json.dumps(result, indent=2))
            return result

        # Find scenarios
        scenarios = find_scenarios(scenario_id=scenario_id, category=category)
        if not scenarios:
            print(f"[ERROR] No scenarios found (id={scenario_id}, category={category})", file=sys.stderr)
            sys.exit(1)

        print(f"[INFO] Found {len(scenarios)} scenario(s)")

        # Pre-flight
        errors = preflight_check(self.backend_url)
        if errors:
            print("[ERROR] Pre-flight check failed:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            sys.exit(1)

        # Create run dir
        run_id = f"eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        run_dir = self.results_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Progress tracking
        progress_path = run_dir / ".progress.json"
        completed = {}
        if progress_path.exists():
            completed = json.loads(progress_path.read_text(encoding="utf-8"))

        # Run scenarios
        results = []
        for scenario_path, scenario_data in scenarios:
            sid = scenario_data["id"]
            if sid in completed:
                print(f"[SKIP] {sid} — already completed (resume mode)")
                trace = json.loads((run_dir / "traces" / f"{sid}.json").read_text(encoding="utf-8"))
                results.append(trace)
                continue

            result = run_scenario_subprocess(
                scenario_path, scenario_data, run_dir,
                self.backend_url, self.model, self.budget, self.timeout,
            )
            results.append(result)

            completed[sid] = result.get("status")
            progress_path.write_text(json.dumps(completed, indent=2), encoding="utf-8")

        # Build scorecard
        config = {
            "backend_url": self.backend_url,
            "model": self.model,
            "budget_per_scenario_usd": float(self.budget),
        }
        scorecard = aggregate_scorecard(results, run_id, run_dir, config)

        # Print summary
        summary = scorecard.get("summary", {})
        total = summary.get("total_scenarios", 0)
        passed = summary.get("passed", 0)
        print(f"\n{'='*60}")
        print(f"RUN: {run_id}")
        print(f"Results: {passed}/{total} passed ({summary.get('pass_rate', 0)*100:.0f}%)")
        print(f"Avg score: {summary.get('avg_score', 0):.1f}/10")
        print(f"Output: {run_dir}")
        print(f"{'='*60}")

        return scorecard
```

---

## STEP 5 — Write src/gaia/eval/scorecard.py

Write this file:

```python
"""
Scorecard generator — builds scorecard.json + summary.md from scenario results.
"""
from datetime import datetime


WEIGHTS = {
    "correctness": 0.25,
    "tool_selection": 0.20,
    "context_retention": 0.20,
    "completeness": 0.15,
    "efficiency": 0.10,
    "personality": 0.05,
    "error_recovery": 0.05,
}


def compute_weighted_score(scores):
    """Compute weighted overall score from dimension scores."""
    if not scores:
        return 0.0
    return sum(scores.get(dim, 0) * weight for dim, weight in WEIGHTS.items())


def build_scorecard(run_id, results, config):
    """Build scorecard dict from list of scenario result dicts."""
    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "PASS")
    failed = sum(1 for r in results if r.get("status") == "FAIL")
    blocked = sum(1 for r in results if r.get("status") == "BLOCKED_BY_ARCHITECTURE")
    errored = total - passed - failed - blocked

    scores = [r.get("overall_score", 0) for r in results if r.get("overall_score") is not None]
    avg_score = sum(scores) / len(scores) if scores else 0.0

    # By category
    by_category = {}
    for r in results:
        cat = r.get("category", "unknown")
        if cat not in by_category:
            by_category[cat] = {"passed": 0, "failed": 0, "blocked": 0, "errored": 0, "scores": []}
        status = r.get("status", "ERRORED")
        if status == "PASS":
            by_category[cat]["passed"] += 1
        elif status == "FAIL":
            by_category[cat]["failed"] += 1
        elif status == "BLOCKED_BY_ARCHITECTURE":
            by_category[cat]["blocked"] += 1
        else:
            by_category[cat]["errored"] += 1
        if r.get("overall_score") is not None:
            by_category[cat]["scores"].append(r["overall_score"])

    for cat in by_category:
        cat_scores = by_category[cat].pop("scores", [])
        by_category[cat]["avg_score"] = sum(cat_scores) / len(cat_scores) if cat_scores else 0.0

    total_cost = sum(
        r.get("cost_estimate", {}).get("estimated_usd", 0) for r in results
    )

    return {
        "run_id": run_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "config": config,
        "summary": {
            "total_scenarios": total,
            "passed": passed,
            "failed": failed,
            "blocked": blocked,
            "errored": errored,
            "pass_rate": passed / total if total > 0 else 0.0,
            "avg_score": round(avg_score, 2),
            "by_category": by_category,
        },
        "scenarios": results,
        "cost": {
            "estimated_total_usd": round(total_cost, 4),
        },
    }


def write_summary_md(scorecard):
    """Generate human-readable summary markdown."""
    s = scorecard.get("summary", {})
    run_id = scorecard.get("run_id", "unknown")
    ts = scorecard.get("timestamp", "")

    lines = [
        f"# GAIA Agent Eval — {run_id}",
        f"**Date:** {ts}",
        f"**Model:** {scorecard.get('config', {}).get('model', 'unknown')}",
        "",
        "## Summary",
        f"- **Total:** {s.get('total_scenarios', 0)} scenarios",
        f"- **Passed:** {s.get('passed', 0)} ✅",
        f"- **Failed:** {s.get('failed', 0)} ❌",
        f"- **Blocked:** {s.get('blocked', 0)} 🚫",
        f"- **Errored:** {s.get('errored', 0)} ⚠️",
        f"- **Pass rate:** {s.get('pass_rate', 0)*100:.0f}%",
        f"- **Avg score:** {s.get('avg_score', 0):.1f}/10",
        "",
        "## By Category",
        "| Category | Pass | Fail | Blocked | Avg Score |",
        "|----------|------|------|---------|-----------|",
    ]

    for cat, data in s.get("by_category", {}).items():
        lines.append(
            f"| {cat} | {data.get('passed', 0)} | {data.get('failed', 0)} | "
            f"{data.get('blocked', 0)} | {data.get('avg_score', 0):.1f} |"
        )

    lines += ["", "## Scenarios"]
    for r in scorecard.get("scenarios", []):
        icon = {"PASS": "✅", "FAIL": "❌", "BLOCKED_BY_ARCHITECTURE": "🚫"}.get(r.get("status"), "⚠️")
        lines.append(
            f"- {icon} **{r.get('scenario_id', '?')}** — {r.get('status', '?')} "
            f"({r.get('overall_score', 0):.1f}/10)"
        )
        if r.get("root_cause"):
            lines.append(f"  - Root cause: {r['root_cause']}")

    lines += ["", f"**Cost:** ${scorecard.get('cost', {}).get('estimated_total_usd', 0):.4f}"]

    return "\n".join(lines) + "\n"
```

---

## STEP 6 — Update src/gaia/cli.py

Find the existing `eval` command group in src/gaia/cli.py. Add or replace the `agent` subcommand under it.

First read the existing cli.py to find the eval section, then add the `agent` subcommand.

The command should be: `gaia eval agent [OPTIONS]`

Options:
- `--scenario TEXT` - Run a specific scenario by ID
- `--category TEXT` - Run all scenarios in a category
- `--audit-only` - Run architecture audit only (no LLM calls)
- `--backend TEXT` - Agent UI URL (default: http://localhost:4200)
- `--model TEXT` - Eval model (default: claude-sonnet-4-6)
- `--budget TEXT` - Max budget per scenario in USD (default: 0.50)
- `--timeout INTEGER` - Timeout per scenario in seconds (default: 300)

Implementation in cli.py:
```python
@eval_group.command("agent")
@click.option("--scenario", default=None, help="Run specific scenario by ID")
@click.option("--category", default=None, help="Run all scenarios in category")
@click.option("--audit-only", is_flag=True, help="Run architecture audit only")
@click.option("--backend", default="http://localhost:4200", help="Agent UI backend URL")
@click.option("--model", default="claude-sonnet-4-6", help="Eval model")
@click.option("--budget", default="0.50", help="Max budget per scenario (USD)")
@click.option("--timeout", default=300, help="Timeout per scenario (seconds)")
def eval_agent(scenario, category, audit_only, backend, model, budget, timeout):
    """Run agent eval benchmark scenarios."""
    from gaia.eval.runner import AgentEvalRunner
    runner = AgentEvalRunner(
        backend_url=backend,
        model=model,
        budget_per_scenario=budget,
        timeout_per_scenario=timeout,
    )
    runner.run(scenario_id=scenario, category=category, audit_only=audit_only)
```

Find where `gaia eval` is defined in cli.py. It might be called `eval_group` or similar. Add the `eval_agent` command to it.

---

## STEP 7 — Verify everything

Run these verification commands:

```
uv run python -c "from gaia.eval.runner import AgentEvalRunner; print('runner OK')"
uv run python -c "from gaia.eval.scorecard import build_scorecard; print('scorecard OK')"
uv run python -c "import yaml; [yaml.safe_load(open(f)) for f in ['eval/scenarios/rag_quality/simple_factual_rag.yaml', 'eval/scenarios/rag_quality/hallucination_resistance.yaml', 'eval/scenarios/context_retention/pronoun_resolution.yaml', 'eval/scenarios/context_retention/cross_turn_file_recall.yaml', 'eval/scenarios/tool_selection/smart_discovery.yaml']]; print('YAMLs OK')"
uv run gaia eval agent --audit-only
```

If any verification fails, fix the issue before proceeding.

---

## STEP 8 — Write completion report

Write `eval/results/phase2a/phase2a_complete.md` with:
- List of all files created
- Verification results (paste command output)
- Any issues encountered and how they were resolved
- Status: COMPLETE

---

## IMPORTANT NOTES

- Always use absolute paths with double backslashes for file operations on Windows
- The repo root is `C:\Users\14255\Work\gaia4`
- Use `uv run python` not `python`
- Do NOT run any eval scenarios — this phase is build only
- Do NOT modify or delete existing eval files (audit.py, claude.py, config.py, etc.)
