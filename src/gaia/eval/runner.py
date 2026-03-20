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
    import urllib.error
    import urllib.request

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
                scenario_path,
                scenario_data,
                run_dir,
                self.backend_url,
                self.model,
                self.budget,
                self.timeout,
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
