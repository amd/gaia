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
import subprocess
import sys
import time
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
DEFAULT_BUDGET = "2.00"
DEFAULT_TIMEOUT = 900  # seconds per scenario (base)


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
   Call index_document with absolute path from DOCUMENT PATHS above
   If chunk_count=0 or error AND scenario category != "adversarial": return status="SETUP_ERROR"
   For adversarial scenarios: 0 chunks is expected — continue

### Phase 2: Simulate + Judge
IMPORTANT RULES:
- Generate EXACTLY the turns listed in the scenario. Do NOT add extra turns.
- After judging all turns, IMMEDIATELY return the JSON result. Do NOT loop.
- For adversarial scenarios (category="adversarial"): agent failure/empty responses are EXPECTED behaviors. Judge once and terminate.
- If agent gives a confusing response, judge it as-is and move on. Do NOT retry send_message.

For each turn in the scenario:
1. Generate a realistic user message matching the turn objective and persona.
   If the objective mentions a file path like "eval/corpus/adversarial/X", use the ABSOLUTE path from DOCUMENT PATHS.
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
    result = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        errors.append("'claude' CLI not found on PATH — install Claude Code CLI")

    return errors


def run_scenario_subprocess(
    _scenario_path, scenario_data, run_dir, backend_url, model, budget, timeout
):
    """Invoke claude -p for one scenario. Returns parsed result dict."""
    scenario_id = scenario_data["id"]
    manifest_data = json.loads(MANIFEST.read_text(encoding="utf-8"))

    prompt = build_scenario_prompt(scenario_data, manifest_data, backend_url)

    result_schema = json.dumps(
        {
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
            },
        }
    )

    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--json-schema",
        result_schema,
        "--mcp-config",
        str(MCP_CONFIG),
        "--strict-mcp-config",
        "--model",
        model,
        "--dangerously-skip-permissions",
        "--max-budget-usd",
        budget,
    ]

    print(f"\n[RUN] {scenario_id} — invoking claude -p ...", flush=True)
    start = time.time()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(REPO_ROOT),
            check=False,
        )
        elapsed = time.time() - start

        if proc.returncode != 0:
            print(
                f"[ERROR] {scenario_id} — exit code {proc.returncode}", file=sys.stderr
            )
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
                if not proc.stdout:
                    raise json.JSONDecodeError("Empty stdout", "", 0)
                # claude --output-format json wraps result; extract the content
                raw = json.loads(proc.stdout)
                # With --json-schema, structured result is in raw["structured_output"]
                # Without --json-schema, result is in raw["result"] (string or dict)
                if (
                    isinstance(raw, dict)
                    and raw.get("subtype") == "error_max_budget_usd"
                ):
                    # Budget exhausted before eval agent could return structured output
                    cost = raw.get("total_cost_usd", 0)
                    result = {
                        "scenario_id": scenario_id,
                        "status": "BUDGET_EXCEEDED",
                        "overall_score": 0,
                        "turns": [],
                        "error": f"Budget cap hit after ${cost:.3f} ({raw.get('num_turns', '?')} turns)",
                    }
                elif (
                    isinstance(raw, dict)
                    and "structured_output" in raw
                    and raw["structured_output"]
                ):
                    result = raw["structured_output"]
                elif isinstance(raw, dict) and "result" in raw:
                    if isinstance(raw["result"], dict):
                        result = raw["result"]
                    else:
                        try:
                            result = json.loads(raw["result"])
                        except (json.JSONDecodeError, TypeError):
                            result = {
                                "scenario_id": scenario_id,
                                "status": "ERRORED",
                                "overall_score": 0,
                                "turns": [],
                                "error": f"eval agent returned non-JSON result: {str(raw.get('result', ''))[:200]}",
                            }
                else:
                    result = raw
                result["elapsed_s"] = elapsed
                print(
                    f"[DONE] {scenario_id} — {result.get('status')} {result.get('overall_score', 0):.1f}/10 ({elapsed:.0f}s)"
                )
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
    trace_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return result


def aggregate_scorecard(results, run_id, run_dir, config, filename_prefix="scorecard"):
    """Build scorecard.json + summary.md from all scenario results."""
    from gaia.eval.scorecard import build_scorecard, write_summary_md

    scorecard = build_scorecard(run_id, results, config)
    scorecard_path = run_dir / f"{filename_prefix}.json"
    scorecard_path.write_text(
        json.dumps(scorecard, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    summary_path = run_dir / f"{filename_prefix.replace('scorecard', 'summary')}.md"
    summary_path.write_text(write_summary_md(scorecard), encoding="utf-8")

    return scorecard


# ---------------------------------------------------------------------------
# Fixer prompt template — used by --fix mode to invoke Claude Code for
# automated repair of failing eval scenarios.
# ---------------------------------------------------------------------------
FIXER_PROMPT = """You are the GAIA Agent Fixer. Read the eval scorecard and fix failing scenarios.

## INPUT
- Scorecard: {scorecard_path}
- Summary: {summary_path}

## RULES
1. Fix ARCHITECTURE issues first (in _chat_helpers.py, agent.py base classes)
   - these unblock BLOCKED_BY_ARCHITECTURE scenarios
2. Then fix PROMPT issues (in agent.py system prompt, tool descriptions)
   - these fix FAILED scenarios
3. Make minimal, targeted changes -- do NOT rewrite entire files
4. Do NOT commit changes -- leave for human review
5. Write a fix log to {fix_log_path}:
   [{{"file": "...", "change": "...", "targets_scenario": "...", "rationale": "..."}}]

## PRIORITY ORDER
Fix failures in this order:
1. Critical severity first
2. Architecture fixes before prompt fixes
3. Failures that affect multiple scenarios before single-scenario fixes

## FAILED SCENARIOS
{failed_scenarios}
"""


def run_fix_iteration(scorecard, run_dir, iteration):
    """Invoke Claude Code to fix failing scenarios. Returns fix log entry."""
    import shutil

    # Load fixer prompt from file if available, fall back to inline FIXER_PROMPT
    fixer_prompt_path = EVAL_DIR / "prompts" / "fixer.md"
    fixer_template = (
        fixer_prompt_path.read_text(encoding="utf-8")
        if fixer_prompt_path.exists()
        else FIXER_PROMPT
    )

    scorecard_path = run_dir / "scorecard.json"
    summary_path = run_dir / "summary.md"
    fix_log_path = run_dir / "fix_log.json"

    failed = [s for s in scorecard["scenarios"] if s.get("status") != "PASS"]
    failed_summary = json.dumps(
        [
            {
                "scenario_id": s.get("scenario_id", "unknown"),
                "status": s.get("status", "UNKNOWN"),
                "overall_score": s.get("overall_score", 0),
                "root_cause": s.get("root_cause", ""),
                "recommended_fix": s.get("recommended_fix", ""),
            }
            for s in failed
        ],
        indent=2,
    )

    prompt = fixer_template.format(
        scorecard_path=str(scorecard_path).replace("\\", "/"),
        summary_path=str(summary_path).replace("\\", "/"),
        fix_log_path=str(fix_log_path).replace("\\", "/"),
        failed_scenarios=failed_summary,
    )

    claude_cmd = shutil.which("claude") or "claude"
    cmd = [claude_cmd, "-p", prompt, "--dangerously-skip-permissions"]

    print(f"[FIX] Invoking Claude Code fixer (iteration {iteration})...")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            cwd=str(REPO_ROOT),
            check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        print(f"[FIX] Claude Code fixer completed (exit={proc.returncode})")

        # Load fix_log if written by the fixer
        if fix_log_path.exists():
            try:
                fix_log = json.loads(fix_log_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                fix_log = [{"note": "fix_log.json exists but is not valid JSON"}]
        else:
            fix_log = [
                {"note": "No fix_log.json written by fixer", "output": output[:500]}
            ]

        return {
            "iteration": iteration,
            "fixer_exit_code": proc.returncode,
            "fixes": fix_log,
            "fixer_output_preview": output[:1000],
        }
    except subprocess.TimeoutExpired:
        print("[FIX] Fixer timed out after 600s", file=sys.stderr)
        return {
            "iteration": iteration,
            "error": "Fixer timed out after 600s",
            "fixes": [],
        }
    except Exception as e:
        print(f"[FIX] Fixer error: {e}", file=sys.stderr)
        return {"iteration": iteration, "error": str(e), "fixes": []}


def compare_scorecards(baseline_path, current_path):
    """Compare two scorecard.json files and print a regression/improvement report.

    Args:
        baseline_path: Path to the baseline scorecard.json (str or Path)
        current_path:  Path to the current/new scorecard.json (str or Path)

    Returns:
        dict with keys: improved, regressed, unchanged, only_in_baseline, only_in_current
    """
    baseline_path = Path(baseline_path)
    current_path = Path(current_path)

    if not baseline_path.exists():
        print(f"[ERROR] Baseline scorecard not found: {baseline_path}", file=sys.stderr)
        sys.exit(1)
    if not current_path.exists():
        print(f"[ERROR] Current scorecard not found: {current_path}", file=sys.stderr)
        sys.exit(1)

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = json.loads(current_path.read_text(encoding="utf-8"))

    # Build per-scenario maps
    def scenario_map(sc):
        return {s["scenario_id"]: s for s in sc.get("scenarios", [])}

    base_map = scenario_map(baseline)
    curr_map = scenario_map(current)

    all_ids = sorted(set(base_map) | set(curr_map))

    improved = []
    regressed = []
    unchanged = []
    only_in_baseline = []
    only_in_current = []

    for sid in all_ids:
        if sid in base_map and sid not in curr_map:
            only_in_baseline.append(sid)
            continue
        if sid not in base_map and sid in curr_map:
            only_in_current.append(sid)
            continue

        b = base_map[sid]
        c = curr_map[sid]
        b_pass = b.get("status") == "PASS"
        c_pass = c.get("status") == "PASS"
        b_score = b.get("overall_score", 0)
        c_score = c.get("overall_score", 0)
        delta = c_score - b_score

        entry = {
            "scenario_id": sid,
            "baseline_status": b.get("status"),
            "current_status": c.get("status"),
            "baseline_score": b_score,
            "current_score": c_score,
            "delta": delta,
        }

        if not b_pass and c_pass:
            improved.append(entry)
        elif b_pass and not c_pass:
            regressed.append(entry)
        else:
            unchanged.append(entry)

    # ---- Print report ----
    b_summary = baseline.get("summary", {})
    c_summary = current.get("summary", {})

    print(f"\n{'='*70}")
    print("SCORECARD COMPARISON")
    print(f"  Baseline : {baseline_path}")
    print(f"  Current  : {current_path}")
    print(f"{'='*70}")

    # Summary row
    b_rate = b_summary.get("pass_rate", 0) * 100
    c_rate = c_summary.get("pass_rate", 0) * 100
    b_avg = b_summary.get("avg_score", 0)
    c_avg = c_summary.get("avg_score", 0)
    print(f"\n{'METRIC':<30} {'BASELINE':>10} {'CURRENT':>10} {'DELTA':>10}")
    print("-" * 62)
    print(
        f"{'Pass rate':<30} {b_rate:>9.0f}% {c_rate:>9.0f}% {c_rate - b_rate:>+9.0f}%"
    )
    print(f"{'Avg score':<30} {b_avg:>10.1f} {c_avg:>10.1f} {c_avg - b_avg:>+10.1f}")
    print(
        f"{'Scenarios':<30} {b_summary.get('total_scenarios', 0):>10} {c_summary.get('total_scenarios', 0):>10}"
    )

    if improved:
        print(f"\n[+] IMPROVED ({len(improved)} scenario(s)) — FAIL → PASS:")
        for e in improved:
            print(
                f"    {e['scenario_id']:<40} {e['baseline_score']:.1f} → {e['current_score']:.1f} ({e['delta']:+.1f})"
            )

    if regressed:
        print(f"\n[!] REGRESSED ({len(regressed)} scenario(s)) — PASS → FAIL:")
        for e in regressed:
            print(
                f"    {e['scenario_id']:<40} {e['baseline_score']:.1f} → {e['current_score']:.1f} ({e['delta']:+.1f})"
            )

    if unchanged:
        # Split into score-changed vs truly same
        score_changed = [e for e in unchanged if abs(e["delta"]) >= 0.1]
        truly_same = [e for e in unchanged if abs(e["delta"]) < 0.1]
        if score_changed:
            print(
                f"\n[~] SCORE CHANGED, STATUS SAME ({len(score_changed)} scenario(s)):"
            )
            for e in score_changed:
                print(
                    f"    {e['scenario_id']:<40} {e['baseline_status']:<5} {e['baseline_score']:.1f} → {e['current_score']:.1f} ({e['delta']:+.1f})"
                )
        if truly_same:
            print(f"\n[=] UNCHANGED ({len(truly_same)} scenario(s)):")
            for e in truly_same:
                print(
                    f"    {e['scenario_id']:<40} {e['baseline_status']:<5} {e['baseline_score']:.1f}"
                )

    if only_in_baseline:
        print(
            f"\n[-] ONLY IN BASELINE ({len(only_in_baseline)} scenario(s)) — removed or renamed:"
        )
        for sid in only_in_baseline:
            print(f"    {sid}")

    if only_in_current:
        print(
            f"\n[+] ONLY IN CURRENT ({len(only_in_current)} scenario(s)) — new scenarios:"
        )
        for sid in only_in_current:
            print(f"    {sid}")

    print(f"\n{'='*70}")
    if regressed:
        print(f"[WARN] {len(regressed)} regression(s) detected!")
    elif improved:
        print(
            f"[OK]   Net improvement: {len(improved)} scenario(s) fixed, 0 regressions."
        )
    else:
        print("[OK]   No status changes between runs.")
    print(f"{'='*70}\n")

    return {
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "only_in_baseline": only_in_baseline,
        "only_in_current": only_in_current,
    }


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

    def _print_summary(self, scorecard, run_id, run_dir):
        """Print a one-block eval summary to stdout."""
        summary = scorecard.get("summary", {})
        total = summary.get("total_scenarios", 0)
        passed = summary.get("passed", 0)
        print(f"\n{'='*60}")
        print(f"RUN: {run_id}")
        print(
            f"Results: {passed}/{total} passed ({summary.get('pass_rate', 0)*100:.0f}%)"
        )
        print(f"Avg score: {summary.get('avg_score', 0):.1f}/10")
        print(f"Output: {run_dir}")
        print(f"{'='*60}")

    def run(
        self,
        scenario_id=None,
        category=None,
        audit_only=False,
        fix_mode=False,
        max_fix_iterations=3,
        target_pass_rate=0.90,
    ):
        """Run eval scenarios. Returns scorecard dict.

        When fix_mode=True, after the initial eval run the runner will:
          B) invoke Claude Code to fix failing scenarios
          C) re-run only previously-failed scenarios
          D) compare before/after and report improvements/regressions
        repeating B-D up to max_fix_iterations or until target_pass_rate is met.
        """

        if audit_only:
            from gaia.eval.audit import run_audit

            result = run_audit()
            print(json.dumps(result, indent=2))
            return result

        # Find scenarios
        scenarios = find_scenarios(scenario_id=scenario_id, category=category)
        if not scenarios:
            print(
                f"[ERROR] No scenarios found (id={scenario_id}, category={category})",
                file=sys.stderr,
            )
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

        # ---- Phase A: Run initial eval ----
        results = []
        for scenario_path, scenario_data in scenarios:
            sid = scenario_data["id"]
            if sid in completed:
                print(f"[SKIP] {sid} -- already completed (resume mode)")
                trace = json.loads(
                    (run_dir / "traces" / f"{sid}.json").read_text(encoding="utf-8")
                )
                results.append(trace)
                continue

            # Scale timeout: base + 200s per pre-indexed doc + 200s per turn
            num_turns = len(scenario_data.get("turns", []))
            num_docs = len(scenario_data.get("setup", {}).get("index_documents", []))
            effective_timeout = max(
                self.timeout, num_docs * 200 + num_turns * 200 + 200
            )
            result = run_scenario_subprocess(
                scenario_path,
                scenario_data,
                run_dir,
                self.backend_url,
                self.model,
                self.budget,
                effective_timeout,
            )
            results.append(result)

            completed[sid] = result.get("status")
            progress_path.write_text(json.dumps(completed, indent=2), encoding="utf-8")

        # Build baseline scorecard
        config = {
            "backend_url": self.backend_url,
            "model": self.model,
            "budget_per_scenario_usd": float(self.budget),
        }
        scorecard = aggregate_scorecard(results, run_id, run_dir, config)

        # Print summary
        self._print_summary(scorecard, run_id, run_dir)

        if not fix_mode:
            return scorecard

        # ---- Fix mode loop (Phases B -> C -> D, repeated) ----
        iteration = 0
        current_scorecard = scorecard
        baseline_scorecard = scorecard
        fix_history = []

        # Build a scenario lookup for re-running failed ones
        scenario_lookup = {data["id"]: (path, data) for path, data in scenarios}

        while iteration < max_fix_iterations:
            pass_rate = current_scorecard.get("summary", {}).get("pass_rate", 0)
            if pass_rate >= target_pass_rate:
                print(
                    f"\n[FIX] Target pass rate {target_pass_rate:.0%} reached ({pass_rate:.0%} actual). Stopping."
                )
                break

            failed = [
                s for s in current_scorecard["scenarios"] if s.get("status") != "PASS"
            ]
            if not failed:
                print("\n[FIX] All scenarios passing. Done.")
                break

            iteration += 1
            print(
                f"\n[FIX] === Iteration {iteration}/{max_fix_iterations} -- fixing {len(failed)} failure(s) ==="
            )

            # Phase B: Run fixer
            fix_result = run_fix_iteration(current_scorecard, run_dir, iteration)
            fix_history.append(fix_result)

            # Phase C: Re-run only previously-failed scenarios
            failed_ids = {s.get("scenario_id") for s in failed}
            rerun_results = []
            for sid in failed_ids:
                if sid not in scenario_lookup:
                    print(
                        f"[WARN] Scenario {sid} not found in lookup, skipping rerun",
                        file=sys.stderr,
                    )
                    continue
                scenario_path, scenario_data = scenario_lookup[sid]
                num_turns = len(scenario_data.get("turns", []))
                num_docs = len(
                    scenario_data.get("setup", {}).get("index_documents", [])
                )
                effective_timeout = max(
                    self.timeout, num_docs * 200 + num_turns * 200 + 200
                )
                result = run_scenario_subprocess(
                    scenario_path,
                    scenario_data,
                    run_dir,
                    self.backend_url,
                    self.model,
                    self.budget,
                    effective_timeout,
                )
                rerun_results.append(result)

            # Merge: keep passing scenarios from current scorecard, replace with rerun results
            rerun_map = {r.get("scenario_id"): r for r in rerun_results}
            merged = []
            for s in current_scorecard["scenarios"]:
                sid = s.get("scenario_id")
                if sid in rerun_map:
                    merged.append(rerun_map[sid])
                else:
                    merged.append(s)

            # Phase D: Compare before/after
            fix_run_id = f"{run_id}_fix{iteration}"
            new_scorecard = aggregate_scorecard(
                merged,
                fix_run_id,
                run_dir,
                config,
                filename_prefix=f"scorecard_fix{iteration}",
            )

            # Detect regressions (previously passing scenario now fails)
            prev_passing = {
                s.get("scenario_id")
                for s in current_scorecard["scenarios"]
                if s.get("status") == "PASS"
            }
            now_failing = {
                s.get("scenario_id")
                for s in new_scorecard["scenarios"]
                if s.get("status") != "PASS"
            }
            regressions = prev_passing & now_failing

            improvements = [
                r
                for r in rerun_results
                if r.get("status") == "PASS" and r.get("scenario_id") in failed_ids
            ]

            new_pass_rate = new_scorecard.get("summary", {}).get("pass_rate", 0)
            print(
                f"[FIX] Iteration {iteration}: {len(improvements)} fixed, {len(regressions)} regression(s), pass rate {new_pass_rate:.0%}"
            )
            if regressions:
                print(f"[FIX] REGRESSIONS: {', '.join(sorted(regressions))}")

            self._print_summary(new_scorecard, fix_run_id, run_dir)
            current_scorecard = new_scorecard

        # Write fix_log.json with full history
        fix_log_path = run_dir / "fix_history.json"
        fix_log_path.write_text(
            json.dumps(fix_history, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Final comparison: baseline vs current
        baseline_pass = baseline_scorecard.get("summary", {}).get("pass_rate", 0)
        final_pass = current_scorecard.get("summary", {}).get("pass_rate", 0)
        print(f"\n{'='*60}")
        print(f"[FIX] FINAL RESULT after {iteration} iteration(s)")
        print(f"  Baseline pass rate: {baseline_pass:.0%}")
        print(f"  Final pass rate:    {final_pass:.0%}")
        print(f"  Delta:              {(final_pass - baseline_pass):+.0%}")
        print(f"  Fix history:        {fix_log_path}")
        print("  Changes are NOT committed -- review before merging.")
        print(f"{'='*60}")

        return current_scorecard


# ---------------------------------------------------------------------------
# --generate-corpus: regenerate corpus documents and validate manifest
# ---------------------------------------------------------------------------


def generate_corpus():
    """Regenerate corpus documents and validate manifest.json.

    Steps:
    1. Re-run CSV generator (gen_sales_csv_v2.py) with deterministic seed
    2. Scan corpus/documents/ and corpus/adversarial/ for all files
    3. Validate manifest.json facts are still reachable
    4. Print a summary report
    """
    print("[CORPUS] Starting corpus regeneration...")

    # Step 1: Regenerate CSV via gen_sales_csv_v2.py
    gen_scripts = [
        CORPUS_DIR / "gen_sales_csv_v2.py",
        CORPUS_DIR / "gen_sales_csv.py",
    ]
    ran_generator = False
    for script in gen_scripts:
        if script.exists():
            print(f"[CORPUS] Running CSV generator: {script.name}")
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(CORPUS_DIR),
                check=False,
            )
            if result.returncode == 0:
                print("[CORPUS] CSV generator OK")
                ran_generator = True
            else:
                print(
                    f"[CORPUS] CSV generator failed (exit {result.returncode}):",
                    file=sys.stderr,
                )
                print(result.stderr[:300], file=sys.stderr)
            break

    if not ran_generator:
        print(
            "[CORPUS] No CSV generator found — skipping CSV regeneration",
            file=sys.stderr,
        )

    # Step 2: Scan corpus directories
    docs_dir = CORPUS_DIR / "documents"
    adv_dir = CORPUS_DIR / "adversarial"
    all_files = []
    for d in [docs_dir, adv_dir]:
        if d.exists():
            for f in sorted(d.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    size = f.stat().st_size
                    all_files.append((f.relative_to(CORPUS_DIR), size))

    print(f"\n[CORPUS] Files found ({len(all_files)}):")
    for rel, size in all_files:
        print(f"  {str(rel):<45} {size:>8,} bytes")

    # Step 3: Validate manifest
    if not MANIFEST.exists():
        print(
            f"\n[CORPUS] WARNING: manifest.json not found at {MANIFEST}",
            file=sys.stderr,
        )
        return

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    doc_list = manifest.get("documents", [])
    adv_list = manifest.get("adversarial_documents", [])
    total_facts = sum(len(d.get("facts", [])) for d in doc_list)

    print(
        f"\n[CORPUS] Manifest: {len(doc_list)} documents, {len(adv_list)} adversarial, {total_facts} facts"
    )

    # Check every manifest file exists on disk
    missing = []
    for doc in doc_list:
        fn = doc.get("filename", "")
        if not (docs_dir / fn).exists():
            missing.append(fn)
    for doc in adv_list:
        fn = doc.get("filename", "")
        if not (adv_dir / fn).exists():
            missing.append(fn)

    if missing:
        print(f"[CORPUS] WARNING: {len(missing)} manifest file(s) missing from disk:")
        for fn in missing:
            print(f"  MISSING: {fn}")
    else:
        print("[CORPUS] All manifest files present on disk [OK]")

    print(f"\n[CORPUS] Done. Corpus at: {CORPUS_DIR}")


# ---------------------------------------------------------------------------
# --capture-session: convert a real Agent UI conversation to a YAML scenario
# ---------------------------------------------------------------------------

GAIA_DB_PATH = Path.home() / ".gaia" / "chat" / "gaia_chat.db"


def capture_session(session_id, output_dir=None, db_path=None):
    """Convert an Agent UI session from the database into a YAML scenario file.

    Args:
        session_id: UUID of the session to capture
        output_dir: Directory to write the YAML (default: eval/scenarios/captured/)
        db_path: Path to gaia_chat.db (default: ~/.gaia/chat/gaia_chat.db)

    Returns:
        Path to the written YAML file
    """
    import re
    import sqlite3

    db = Path(db_path) if db_path else GAIA_DB_PATH
    if not db.exists():
        print(f"[ERROR] Agent UI database not found: {db}", file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Load session
    cur.execute(
        "SELECT id, title, created_at FROM sessions WHERE id = ?", (session_id,)
    )
    session = cur.fetchone()
    if not session:
        # Try partial match on ID prefix
        cur.execute(
            "SELECT id, title, created_at FROM sessions WHERE id LIKE ?",
            (f"{session_id}%",),
        )
        session = cur.fetchone()
    if not session:
        print(f"[ERROR] Session '{session_id}' not found in database", file=sys.stderr)
        print("[INFO] Available sessions:", file=sys.stderr)
        cur.execute(
            "SELECT id, title, created_at FROM sessions ORDER BY created_at DESC LIMIT 10"
        )
        for row in cur.fetchall():
            print(
                f"  {row['id'][:8]}... {row['title']!r} ({row['created_at'][:10]})",
                file=sys.stderr,
            )
        con.close()
        sys.exit(1)

    session_id_full = session["id"]
    title = session["title"] or "captured_session"

    # Load messages (user + assistant only)
    cur.execute(
        "SELECT role, content, agent_steps FROM messages WHERE session_id = ? ORDER BY id",
        (session_id_full,),
    )
    messages = [dict(r) for r in cur.fetchall()]

    # Load indexed documents for this session
    cur.execute(
        """SELECT d.filepath, d.filename FROM documents d
           JOIN session_documents sd ON sd.document_id = d.id
           WHERE sd.session_id = ?""",
        (session_id_full,),
    )
    docs = [dict(r) for r in cur.fetchall()]
    con.close()

    # Build scenario ID from title
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40]
    scenario_id = f"captured_{slug}"

    # Build turns from message pairs
    turns = []
    turn_num = 0
    user_msg = None
    for msg in messages:
        if msg["role"] == "user":
            user_msg = msg["content"]
        elif msg["role"] == "assistant" and user_msg is not None:
            turn_num += 1
            # Extract tool names from agent_steps JSON if present
            tools_used = []
            if msg.get("agent_steps"):
                try:
                    steps = json.loads(msg["agent_steps"])
                    if isinstance(steps, list):
                        for step in steps:
                            name = (
                                step.get("tool")
                                or step.get("name")
                                or step.get("tool_name")
                            )
                            if name and name not in tools_used:
                                tools_used.append(name)
                except (json.JSONDecodeError, TypeError):
                    pass

            turns.append(
                {
                    "turn": turn_num,
                    "objective": f"[REVIEW] {str(user_msg)[:120]}",
                    "user_message": user_msg,
                    "expected_tools": tools_used or None,
                    "success_criteria": {
                        "must_contain": [],
                        "agent_response_preview": msg["content"][:200]
                        + ("..." if len(msg["content"]) > 200 else ""),
                    },
                }
            )
            user_msg = None

    if not turns:
        print(
            f"[ERROR] No user/assistant message pairs found in session {session_id_full[:8]}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build document list (relative to corpus dir if possible, else absolute)
    index_docs = []
    for doc in docs:
        fp = doc["filepath"] or doc["filename"]
        if fp:
            index_docs.append(fp.replace("\\", "/"))

    # Build YAML scenario
    scenario = {
        "id": scenario_id,
        "category": "captured",
        "description": f"Captured from session: {title}",
        "persona": "A user who had this real conversation with GAIA.",
        "setup": {
            "index_documents": index_docs,
        },
        "turns": [
            {
                "turn": t["turn"],
                "objective": t["objective"],
                "user_message": t["user_message"],
                **(
                    {"expected_tools": t["expected_tools"]}
                    if t["expected_tools"]
                    else {}
                ),
                "success_criteria": t["success_criteria"],
            }
            for t in turns
        ],
        "captured_from": {
            "session_id": session_id_full,
            "title": title,
            "captured_at": datetime.now().isoformat(),
        },
    }

    # Write YAML
    out_dir = Path(output_dir) if output_dir else SCENARIOS_DIR / "captured"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{scenario_id}.yaml"
    out_path.write_text(
        yaml.dump(
            scenario, default_flow_style=False, allow_unicode=True, sort_keys=False
        ),
        encoding="utf-8",
    )

    print(f"[CAPTURE] Wrote scenario: {out_path}")
    print(f"  Session: {title!r} ({session_id_full[:8]}...)")
    print(f"  Turns: {len(turns)}  Documents: {len(index_docs)}")
    print(
        "[NOTE] Review the YAML before running — update 'objective' and 'success_criteria' fields."
    )
    return out_path
