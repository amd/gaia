# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

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

import functools
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
REAL_WORLD_CORPUS_DIR = CORPUS_DIR / "real_world"
REAL_WORLD_MANIFEST = REAL_WORLD_CORPUS_DIR / "manifest.json"

# Personas defined in eval/prompts/simulator.md.  validate_scenario enforces this list.
_KNOWN_PERSONAS = frozenset(
    {"casual_user", "power_user", "confused_user", "adversarial_user", "data_analyst"}
)

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_BACKEND = "http://localhost:4200"
DEFAULT_BUDGET = "2.00"
DEFAULT_TIMEOUT = 900  # seconds per scenario (base)
# Extra seconds reserved for claude subprocess + MCP server cold-start.
_STARTUP_OVERHEAD_S = 120
# Hard upper bound — a misconfigured scenario cannot tie up CI for more than 2 hours.
_MAX_EFFECTIVE_TIMEOUT_S = 7200


def _compute_effective_timeout(base_timeout: int, scenario_data: dict) -> int:
    """Return per-scenario timeout covering startup overhead + turns + docs."""
    num_turns = len(scenario_data.get("turns", []))
    num_docs = len(scenario_data.get("setup", {}).get("index_documents", []))
    # 90s per doc (index time) + 200s per turn (simulate+judge).  Cap prevents runaway CI.
    effective = max(base_timeout, _STARTUP_OVERHEAD_S + num_docs * 90 + num_turns * 200)
    return min(effective, _MAX_EFFECTIVE_TIMEOUT_S)


def validate_scenario(path: Path, data: dict) -> None:
    """Validate scenario YAML structure. Raises ValueError with details on failure."""
    sid = data.get("id", f"<{path.name}>")
    errors = []

    for field in ("id", "category", "setup", "turns", "persona"):
        if field not in data:
            errors.append(f"missing top-level field '{field}'")

    if "setup" in data and "index_documents" not in data.get("setup", {}):
        errors.append("setup.index_documents is missing (use empty list [] if none)")

    # Each non-empty index_documents entry must have a 'path' field for corpus file verification.
    for i, doc in enumerate(data.get("setup", {}).get("index_documents", [])):
        if isinstance(doc, dict) and "path" not in doc:
            errors.append(
                f"setup.index_documents[{i}]: missing 'path' field "
                "(required so the runner can verify the file exists before running)"
            )

    # Validate persona against the known list defined in simulator.md.
    persona = data.get("persona")
    if persona is not None:
        if not isinstance(persona, str):
            errors.append(f"persona must be a string, got {type(persona).__name__}")
        elif persona not in _KNOWN_PERSONAS:
            errors.append(
                f"persona '{persona}' is not a known persona; "
                f"use one of: {', '.join(sorted(_KNOWN_PERSONAS))}"
            )

    turns = data.get("turns", [])
    if not turns:
        errors.append("turns list is empty")

    seen_nums = set()
    for i, turn in enumerate(turns):
        prefix = f"turns[{i}]"
        if "turn" not in turn:
            errors.append(f"{prefix}: missing 'turn' number")
        else:
            n = turn["turn"]
            if n in seen_nums:
                errors.append(f"{prefix}: duplicate turn number {n}")
            seen_nums.add(n)
        if "objective" not in turn:
            errors.append(f"{prefix}: missing 'objective'")
        # A non-None ground_truth dict OR a non-empty success_criteria string is required.
        # ground_truth: null (key present, value None) counts as absent.
        has_gt = isinstance(turn.get("ground_truth"), dict)
        has_criteria = isinstance(turn.get("success_criteria"), str) and bool(
            turn.get("success_criteria", "").strip()
        )
        if not has_gt and not has_criteria:
            errors.append(
                f"{prefix}: must have at least one of 'ground_truth' (non-null dict) "
                "or 'success_criteria' (non-empty string)"
            )
        # Detect dict-format success_criteria (produced by old capture function)
        if isinstance(turn.get("success_criteria"), dict):
            errors.append(
                f"{prefix}: success_criteria must be a string, got dict — "
                "convert to a plain English description of the pass condition"
            )

    # Validate turn numbers are sequential integers starting from 1.
    # Only skip when duplicate turn numbers were already flagged (duplicates make the
    # sequential check produce a misleading error); other errors don't suppress it.
    has_dup_errors = any("duplicate turn number" in e for e in errors)
    if seen_nums and not has_dup_errors:
        expected = set(range(1, len(turns) + 1))
        if seen_nums != expected:
            errors.append(
                f"turn numbers {sorted(seen_nums)} must be sequential starting from 1 "
                f"(expected {sorted(expected)})"
            )

    if errors:
        raise ValueError(
            f"Scenario '{sid}' ({path.name}) has validation errors:\n  "
            + "\n  ".join(errors)
        )


def _documents_exist(scenario_data: dict) -> bool:
    """Return True if all pre-indexed documents listed in the scenario exist on disk.

    Checks the 'path' field of each entry in setup.index_documents against REPO_ROOT.
    Returns True for scenarios with no pre-indexed documents (empty list).
    Real-world scenarios whose files are not committed to git return False.
    """
    for doc in scenario_data.get("setup", {}).get("index_documents", []):
        if isinstance(doc, dict):
            path = doc.get("path")
            if path and not (REPO_ROOT / path).exists():
                return False
    return True


def find_scenarios(scenario_id=None, category=None):
    """Find scenario YAML files matching filters.

    Returns list of (path, data) tuples. Raises RuntimeError if any YAML is
    unparseable or fails schema validation.
    """
    scenarios = []
    for path in sorted(SCENARIOS_DIR.rglob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"Failed to parse scenario YAML {path}: {e}") from e
        try:
            validate_scenario(path, data)
        except ValueError as e:
            raise RuntimeError(str(e)) from e
        if scenario_id and data.get("id") != scenario_id:
            continue
        if category and data.get("category") != category:
            continue
        scenarios.append((path, data))
    return scenarios


def build_scenario_prompt(scenario_data, manifest_data, backend_url):
    """Build the prompt passed to `claude -p` for one scenario."""
    scenario_yaml = yaml.dump(scenario_data, default_flow_style=False)
    manifest_json = json.dumps(manifest_data, indent=2)

    corpus_root = str(CORPUS_DIR / "documents").replace("\\", "/")
    adversarial_root = str(CORPUS_DIR / "adversarial").replace("\\", "/")
    real_world_root = str(REAL_WORLD_CORPUS_DIR).replace("\\", "/")
    # Inline all three prompt files so the full rubric is always available — the claude
    # subprocess has no file-read tool and cannot access these paths from disk.
    # JSON examples below use {{ and }} as f-string escaped literal braces.
    # If you switch to .replace()-style templating, change all {{ → { and }} → }.
    simulator_content = _load_simulator_content()
    judge_turn_content = _load_judge_turn_content()
    judge_scenario_content = _load_judge_scenario_content()

    return f"""You are the GAIA Eval Agent. Test the GAIA Agent UI by simulating a realistic user and judging responses.

## SCORING RULES AND RUBRIC
{simulator_content}

## PER-TURN JUDGE INSTRUCTIONS
{judge_turn_content}

## SCENARIO-LEVEL JUDGE INSTRUCTIONS
{judge_scenario_content}

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
- Real-world documents: {real_world_root}/
- Use ABSOLUTE paths when calling index_document
- For real_world scenarios, resolve relative paths using the real-world root above

## AGENT UI
Backend: {backend_url}

## YOUR TASK

### Phase 1: Setup
1. Call system_status() — if error, return status="INFRA_ERROR"
2. Call create_session("Eval: {{scenario_id}}")
3. For each document in scenario setup.index_documents:
   Call index_document(filepath=<absolute path>, session_id=<session_id from step 2>)
   CRITICAL: Always pass the session_id so documents are linked to the session and visible to the agent.
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
3. Judge the response using the PER-TURN JUDGE INSTRUCTIONS section above

### Phase 3: Full trace
After all turns, call get_messages(session_id) for the persisted full trace.

### Phase 4: Scenario judgment
Evaluate holistically using the SCENARIO-LEVEL JUDGE INSTRUCTIONS section above

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


_SCORE_WEIGHTS = {
    "correctness": 0.25,
    "tool_selection": 0.20,
    "context_retention": 0.20,
    "completeness": 0.15,
    "efficiency": 0.10,
    "personality": 0.05,
    "error_recovery": 0.05,
}

# Significant score drop within the same pass/fail status warrants a warning
_SCORE_REGRESSION_THRESHOLD = 2.0


@functools.lru_cache(maxsize=1)
def _load_simulator_content() -> str:
    return (EVAL_DIR / "prompts" / "simulator.md").read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _load_judge_turn_content() -> str:
    return (EVAL_DIR / "prompts" / "judge_turn.md").read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _load_judge_scenario_content() -> str:
    return (EVAL_DIR / "prompts" / "judge_scenario.md").read_text(encoding="utf-8")


def recompute_turn_score(scores: dict) -> float:
    """Recompute weighted overall_score from dimension scores.

    Used to validate that the eval agent's arithmetic matches the rubric.
    Returns -1.0 if required dimensions are missing.
    """
    if not all(k in scores for k in _SCORE_WEIGHTS):
        return -1.0
    if not all(isinstance(scores[k], (int, float)) for k in _SCORE_WEIGHTS):
        return -1.0
    return sum(scores[k] * w for k, w in _SCORE_WEIGHTS.items())


def _validate_turn_scores(result: dict) -> list:
    """Check for turns where dimension scores were missing and could not be recomputed.

    This runs after the score-overwrite pass, so a discrepancy between reported
    and recomputed only remains for turns where recompute_turn_score returned -1
    (missing dimensions).  Returns warning strings for those turns.
    """
    warnings = []
    for turn in result.get("turns", []):
        scores = turn.get("scores", {})
        reported = turn.get("overall_score")
        if not isinstance(reported, (int, float)):
            continue
        computed = recompute_turn_score(scores)
        if computed < 0:
            warnings.append(
                f"Turn {turn.get('turn', '?')}: missing dimension scores — "
                f"score could not be recomputed (reported={reported:.2f})"
            )
    return warnings


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
    result = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        errors.append("'claude' CLI not found on PATH — install Claude Code CLI")

    return errors


def run_scenario_subprocess(
    _scenario_path, scenario_data, run_dir, backend_url, model, budget, timeout
):
    """Invoke claude -p for one scenario. Returns parsed result dict."""
    scenario_id = scenario_data["id"]
    manifest_data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # Merge real-world manifest facts if present, so the eval agent has ground
    # truth for all document types (standard + real-world) in a single context block.
    if REAL_WORLD_MANIFEST.exists():
        rw_manifest = json.loads(REAL_WORLD_MANIFEST.read_text(encoding="utf-8"))
        merged_docs = manifest_data.get("documents", []) + rw_manifest.get(
            "documents", []
        )
        manifest_data = {
            **manifest_data,
            "documents": merged_docs,
            "total_documents": len(merged_docs),
        }

    prompt = build_scenario_prompt(scenario_data, manifest_data, backend_url)

    result_schema = json.dumps(
        {
            "type": "object",
            "required": ["scenario_id", "status", "overall_score", "turns"],
            "properties": {
                "scenario_id": {"type": "string"},
                "status": {"type": "string"},
                "overall_score": {"type": ["number", "null"]},
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
                "overall_score": None,
                "turns": [],
                "error": proc.stderr[:500],
                "elapsed_s": elapsed,
                "cost_estimate": {"turns": 0, "estimated_usd": 0.0},
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
                        "overall_score": None,
                        "turns": [],
                        "error": f"Budget cap hit after ${cost:.3f} ({raw.get('num_turns', '?')} turns)",
                        "cost_estimate": {
                            "turns": raw.get("num_turns", 0),
                            "estimated_usd": cost,
                        },
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
                                "overall_score": None,
                                "turns": [],
                                "error": f"eval agent returned non-JSON result: {str(raw.get('result', ''))[:200]}",
                                "cost_estimate": {"turns": 0, "estimated_usd": 0.0},
                            }
                else:
                    result = raw
                # Guard: ensure required fields are present regardless of parse path
                if isinstance(result, dict) and "status" not in result:
                    print(
                        f"[WARN] {scenario_id} — eval agent JSON missing 'status' field",
                        file=sys.stderr,
                    )
                    result.setdefault("status", "ERRORED")
                    result.setdefault("overall_score", None)
                    result.setdefault("turns", [])
                result["elapsed_s"] = elapsed
                score = result.get("overall_score")
                score_str = f"{score:.1f}" if isinstance(score, (int, float)) else "n/a"
                print(
                    f"[DONE] {scenario_id} — {result.get('status')} {score_str}/10 ({elapsed:.0f}s)"
                )
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[ERROR] {scenario_id} — JSON parse error: {e}", file=sys.stderr)
                result = {
                    "scenario_id": scenario_id,
                    "status": "ERRORED",
                    "overall_score": None,
                    "turns": [],
                    "error": f"JSON parse error: {e}. stdout: {proc.stdout[:300]}",
                    "elapsed_s": elapsed,
                    "cost_estimate": {"turns": 0, "estimated_usd": 0.0},
                }

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"[TIMEOUT] {scenario_id} — exceeded {timeout}s", file=sys.stderr)
        result = {
            "scenario_id": scenario_id,
            "status": "TIMEOUT",
            "overall_score": None,
            "turns": [],
            "elapsed_s": elapsed,
            "cost_estimate": {"turns": 0, "estimated_usd": 0.0},
        }

    # Inject category from scenario YAML — eval agent doesn't include this field
    result.setdefault("category", scenario_data.get("category", "unknown"))

    # Trust dimension scores, not LLM arithmetic — overwrite per-turn overall_score
    # with the recomputed weighted sum.  Log when the LLM's value differed by > 0.25.
    for turn in result.get("turns", []):
        if isinstance(turn.get("scores"), dict):
            computed = recompute_turn_score(turn["scores"])
            if computed >= 0:
                reported = turn.get("overall_score")
                if (
                    isinstance(reported, (int, float))
                    and abs(computed - reported) > 0.25
                ):
                    print(
                        f"[WARN] {scenario_id} turn {turn.get('turn', '?')}: "
                        f"overwriting score {reported:.2f} → {computed:.2f}",
                        file=sys.stderr,
                    )
                turn["overall_score"] = round(computed, 2)
                # Recompute per-turn pass flag so trace files stay internally consistent
                t_correct = turn["scores"].get("correctness")
                turn["pass"] = bool(
                    isinstance(t_correct, (int, float))
                    and t_correct >= 4
                    and computed >= 6.0
                )

    # Recompute scenario-level overall_score as the mean of recomputed per-turn scores.
    # This ensures the scorecard's primary quality metric is fully deterministic.
    turn_scores = [
        t["overall_score"]
        for t in result.get("turns", [])
        if isinstance(t.get("overall_score"), (int, float))
    ]
    if turn_scores:
        result["overall_score"] = round(sum(turn_scores) / len(turn_scores), 2)
    elif result.get("turns"):
        # Turns exist but all have null overall_score (dimension scores missing).
        # Nullify the scenario score rather than silently propagating the LLM's value.
        print(
            f"[WARN] {scenario_id} — all turn scores are null, setting overall_score=None",
            file=sys.stderr,
        )
        result["overall_score"] = None

    # Deterministic status re-derivation: apply rubric rules to recomputed scores.
    # Corrects both PASS→FAIL and FAIL→PASS; never touches infrastructure statuses
    # (BLOCKED_BY_ARCHITECTURE, TIMEOUT, etc.).
    # Design: status is based on the mean of per-turn scores (not any-failing-turn).
    # The scenario-level judge may legitimately PASS a scenario with one weak non-critical
    # turn; the runner respects that by using the aggregate mean rather than a strict
    # all-turns-pass rule.
    if result.get("status") == "PASS" and result.get("turns"):
        fail_reason = None
        for t in result["turns"]:
            t_correctness = t.get("scores", {}).get("correctness")
            if isinstance(t_correctness, (int, float)) and t_correctness < 4:
                fail_reason = (
                    f"turn {t.get('turn', '?')} correctness={t_correctness:.0f} < 4 "
                    "(rubric: FAIL if correctness < 4)"
                )
                break
        if fail_reason is None:
            sc = result.get("overall_score")
            if isinstance(sc, (int, float)) and sc < 6.0:
                fail_reason = (
                    f"overall_score={sc:.2f} < 6.0 (rubric: FAIL if score < 6.0)"
                )
        if fail_reason:
            print(
                f"[WARN] {scenario_id} — overriding LLM status PASS→FAIL: {fail_reason}",
                file=sys.stderr,
            )
            result["status"] = "FAIL"
    elif result.get("status") == "FAIL" and result.get("turns"):
        # Correct a false FAIL: if ALL turns are scored and every turn's correctness ≥ 4
        # and overall_score ≥ 6.0, the rubric says PASS.
        # Requiring full coverage prevents upgrading scenarios where some turns had no scores
        # (e.g. eval agent timed out before scoring them — those turns may be real failures).
        turns_with_correctness = [
            t
            for t in result["turns"]
            if isinstance(t.get("scores", {}).get("correctness"), (int, float))
        ]
        sc = result.get("overall_score")
        if (
            turns_with_correctness
            and len(turns_with_correctness) == len(result["turns"])
            and all(t["scores"]["correctness"] >= 4 for t in turns_with_correctness)
            and isinstance(sc, (int, float))
            and sc >= 6.0
        ):
            print(
                f"[WARN] {scenario_id} — overriding LLM status FAIL\u2192PASS: "
                f"all turns correctness\u22654, overall_score={sc:.2f}\u22656.0",
                file=sys.stderr,
            )
            result["status"] = "PASS"

    # Guard: BLOCKED_BY_ARCHITECTURE is intentionally NOT overridden to PASS.
    # But if the eval agent applied it when all turns clearly pass the rubric, warn —
    # this indicates a hallucinated architectural block that needs human review.
    elif result.get("status") == "BLOCKED_BY_ARCHITECTURE" and result.get("turns"):
        turns_with_correctness = [
            t
            for t in result["turns"]
            if isinstance(t.get("scores", {}).get("correctness"), (int, float))
        ]
        sc = result.get("overall_score")
        if (
            turns_with_correctness
            and len(turns_with_correctness) == len(result["turns"])
            and all(t["scores"]["correctness"] >= 4 for t in turns_with_correctness)
            and isinstance(sc, (int, float))
            and sc >= 6.0
        ):
            print(
                f"[WARN] {scenario_id} — status=BLOCKED_BY_ARCHITECTURE but all turns pass "
                f"rubric criteria (correctness\u22654, overall_score={sc:.2f}\u22656.0); "
                "verify this is a genuine architectural block and not an eval agent error",
                file=sys.stderr,
            )

    # After overwrite, warn on turns where dimensions were missing (recompute returned -1)
    score_warnings = _validate_turn_scores(result)
    if score_warnings:
        result["score_warnings"] = score_warnings
        for w in score_warnings:
            print(f"[WARN] {scenario_id} score mismatch — {w}", file=sys.stderr)

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

    # Derive summary stem robustly: replace first occurrence of "scorecard" with "summary",
    # or append "_summary" if the prefix does not contain "scorecard".
    if "scorecard" in filename_prefix:
        idx = filename_prefix.index("scorecard")
        summary_stem = (
            filename_prefix[:idx]
            + "summary"
            + filename_prefix[idx + len("scorecard") :]
        )
    else:
        summary_stem = f"{filename_prefix}_summary"
    summary_path = run_dir / f"{summary_stem}.md"
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

    # Use str.replace instead of .format() to avoid KeyError when fixer.md
    # contains curly braces in code blocks or JSON examples.
    prompt = (
        fixer_template.replace(
            "{scorecard_path}", str(scorecard_path).replace("\\", "/")
        )
        .replace("{summary_path}", str(summary_path).replace("\\", "/"))
        .replace("{fix_log_path}", str(fix_log_path).replace("\\", "/"))
        .replace("{failed_scenarios}", failed_summary)
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
        raise FileNotFoundError(f"Baseline scorecard not found: {baseline_path}")
    if not current_path.exists():
        raise FileNotFoundError(f"Current scorecard not found: {current_path}")

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = json.loads(current_path.read_text(encoding="utf-8"))

    # Build per-scenario maps
    def scenario_map(sc):
        result = {}
        for s in sc.get("scenarios", []):
            sid = s.get("scenario_id")
            if sid is None:
                print(
                    "[WARN] compare_scorecards: result missing 'scenario_id', skipping",
                    file=sys.stderr,
                )
                continue
            result[sid] = s
        return result

    base_map = scenario_map(baseline)
    curr_map = scenario_map(current)

    all_ids = sorted(set(base_map) | set(curr_map))

    improved = []
    regressed = []
    score_regressed = []
    unchanged = []
    only_in_baseline = []
    only_in_current = []
    # corpus_changed: one side is SKIPPED_NO_DOCUMENT — corpus availability changed,
    # not a quality regression or improvement.  Reported separately to avoid noise.
    corpus_changed = []

    for sid in all_ids:
        if sid in base_map and sid not in curr_map:
            only_in_baseline.append(sid)
            continue
        if sid not in base_map and sid in curr_map:
            only_in_current.append(sid)
            continue

        b = base_map[sid]
        c = curr_map[sid]
        b_skipped = b.get("status") == "SKIPPED_NO_DOCUMENT"
        c_skipped = c.get("status") == "SKIPPED_NO_DOCUMENT"
        b_pass = b.get("status") == "PASS"
        c_pass = c.get("status") == "PASS"
        b_score = (
            b.get("overall_score")
            if isinstance(b.get("overall_score"), (int, float))
            else 0
        )
        c_score = (
            c.get("overall_score")
            if isinstance(c.get("overall_score"), (int, float))
            else 0
        )
        delta = c_score - b_score

        entry = {
            "scenario_id": sid,
            "baseline_status": b.get("status"),
            "current_status": c.get("status"),
            "baseline_score": b_score,
            "current_score": c_score,
            "delta": delta,
        }

        # Corpus availability change — not a quality signal
        if b_skipped or c_skipped:
            corpus_changed.append(entry)
        elif not b_pass and c_pass:
            improved.append(entry)
        elif b_pass and not c_pass:
            regressed.append(entry)
        elif b_pass and c_pass and delta <= -_SCORE_REGRESSION_THRESHOLD:
            # Still passing but significant score drop — flag separately
            score_regressed.append(entry)
        elif not b_pass and not c_pass and delta <= -_SCORE_REGRESSION_THRESHOLD:
            # Both failing but quality dropped significantly — flag separately
            score_regressed.append(entry)
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
    b_judged = b_summary.get("judged_pass_rate", 0) * 100
    c_judged = c_summary.get("judged_pass_rate", 0) * 100
    b_avg = b_summary.get("avg_score", 0)
    c_avg = c_summary.get("avg_score", 0)
    print(f"\n{'METRIC':<30} {'BASELINE':>10} {'CURRENT':>10} {'DELTA':>10}")
    print("-" * 62)
    print(
        f"{'Pass rate (all)':<30} {b_rate:>9.0f}% {c_rate:>9.0f}% {c_rate - b_rate:>+9.0f}%"
    )
    print(
        f"{'Pass rate (judged)':<30} {b_judged:>9.0f}% {c_judged:>9.0f}% {c_judged - b_judged:>+9.0f}%"
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

    if score_regressed:
        print(
            f"\n[~] SCORE REGRESSION ({len(score_regressed)} scenario(s)) — PASS but score drop ≥{_SCORE_REGRESSION_THRESHOLD}:"
        )
        for e in score_regressed:
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

    if corpus_changed:
        print(
            f"\n[~] CORPUS AVAILABILITY CHANGED ({len(corpus_changed)} scenario(s)) — "
            "SKIPPED_NO_DOCUMENT in one run; not a quality signal:"
        )
        for e in corpus_changed:
            print(
                f"    {e['scenario_id']:<40} {e['baseline_status']} → {e['current_status']}"
            )

    print(f"\n{'='*70}")
    if regressed:
        print(f"[WARN] {len(regressed)} regression(s) detected!")
    if score_regressed:
        print(
            f"[WARN] {len(score_regressed)} score regression(s) detected (still passing but score dropped ≥{_SCORE_REGRESSION_THRESHOLD})!"
        )
    if not regressed and not score_regressed and improved:
        print(
            f"[OK]   Net improvement: {len(improved)} scenario(s) fixed, 0 regressions."
        )
    elif not regressed and not score_regressed and not improved:
        print("[OK]   No status changes between runs.")
    print(f"{'='*70}\n")

    return {
        "improved": improved,
        "regressed": regressed,
        "score_regressed": score_regressed,
        "unchanged": unchanged,
        "only_in_baseline": only_in_baseline,
        "only_in_current": only_in_current,
        "corpus_changed": corpus_changed,
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
            f"Results: {passed}/{total} passed ({summary.get('pass_rate', 0)*100:.0f}% all, "
            f"{summary.get('judged_pass_rate', 0)*100:.0f}% judged)"
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
                trace_path = run_dir / "traces" / f"{sid}.json"
                if not trace_path.exists():
                    # Progress file recorded completion but trace wasn't written —
                    # previous run crashed between the two writes. Re-run the scenario.
                    print(
                        f"[WARN] {sid} in progress file but trace missing — re-running"
                    )
                    del completed[sid]
                else:
                    try:
                        results.append(
                            json.loads(trace_path.read_text(encoding="utf-8"))
                        )
                        print(f"[SKIP] {sid} -- already completed (resume mode)")
                        continue
                    except (json.JSONDecodeError, OSError):
                        # Trace file is corrupt (e.g. process killed mid-write) — re-run
                        print(f"[WARN] {sid} trace file corrupt — re-running")
                        del completed[sid]

            # Skip scenarios whose corpus documents are not on disk.
            # Real-world documents are not committed to git; skip gracefully
            # rather than failing with SETUP_ERROR or INFRA_ERROR.
            if not _documents_exist(scenario_data):
                print(
                    f"[SKIP] {sid} — corpus document(s) not on disk (real-world corpus not committed to git)"
                )
                result = {
                    "scenario_id": sid,
                    "category": scenario_data.get("category", "unknown"),
                    "status": "SKIPPED_NO_DOCUMENT",
                    "overall_score": None,
                    "turns": [],
                    "elapsed_s": 0.0,
                    "cost_estimate": {"turns": 0, "estimated_usd": 0.0},
                }
                # Write a trace so resume mode can reload this result without re-running
                traces_dir = run_dir / "traces"
                traces_dir.mkdir(exist_ok=True)
                (traces_dir / f"{sid}.json").write_text(
                    json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                results.append(result)
                completed[sid] = "SKIPPED_NO_DOCUMENT"
                progress_path.write_text(
                    json.dumps(completed, indent=2), encoding="utf-8"
                )
                continue

            effective_timeout = _compute_effective_timeout(self.timeout, scenario_data)
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

        # Clean up progress file — all scenarios complete
        if progress_path.exists():
            progress_path.unlink()

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
            pass_rate = current_scorecard.get("summary", {}).get("judged_pass_rate", 0)
            if pass_rate >= target_pass_rate:
                print(
                    f"\n[FIX] Target judged pass rate {target_pass_rate:.0%} reached ({pass_rate:.0%} actual). Stopping."
                )
                break

            failed = [
                s
                for s in current_scorecard["scenarios"]
                if s.get("status") not in ("PASS", "SKIPPED_NO_DOCUMENT")
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
                effective_timeout = _compute_effective_timeout(
                    self.timeout, scenario_data
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

            new_pass_rate = new_scorecard.get("summary", {}).get("judged_pass_rate", 0)
            print(
                f"[FIX] Iteration {iteration}: {len(improvements)} fixed, {len(regressions)} regression(s), judged pass rate {new_pass_rate:.0%}"
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
        baseline_pass = baseline_scorecard.get("summary", {}).get("judged_pass_rate", 0)
        final_pass = current_scorecard.get("summary", {}).get("judged_pass_rate", 0)
        print(f"\n{'='*60}")
        print(f"[FIX] FINAL RESULT after {iteration} iteration(s)")
        print(f"  Baseline judged pass rate: {baseline_pass:.0%}")
        print(f"  Final judged pass rate:    {final_pass:.0%}")
        print(f"  Delta:                     {(final_pass - baseline_pass):+.0%}")
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
                    "success_criteria": (
                        f"Agent response matches the captured conversation: "
                        f"{msg['content'][:120]}"
                        + ("..." if len(msg["content"]) > 120 else "")
                    ),
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
