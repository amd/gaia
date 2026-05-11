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

import contextlib
import errno
import functools
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# fcntl is POSIX-only — on Windows the eval lock degrades to a no-op (the
# Lemonade race the lock guards against doesn't happen on a contributor's
# Windows box, where Lemonade Server isn't typically running concurrent evals).
if sys.platform == "win32":
    fcntl = None  # type: ignore[assignment]
else:
    import fcntl  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).parent.parent.parent.parent
EVAL_DIR = REPO_ROOT / "eval"
SCENARIOS_DIR = EVAL_DIR / "scenarios"
CORPUS_DIR = EVAL_DIR / "corpus"
RESULTS_DIR = EVAL_DIR / "results"
MCP_CONFIG = EVAL_DIR / "mcp-config.json"
MANIFEST = CORPUS_DIR / "manifest.json"
REAL_WORLD_CORPUS_DIR = CORPUS_DIR / "real_world"

# ── Single-runner lock ────────────────────────────────────────────────────
#
# Why this exists: ``gaia eval agent`` drives Lemonade Server, which has a
# **single-tenant LLM slot** (one model loaded at a time, one ``ctx_size``
# in effect). When two eval runs fire concurrently against the same
# Lemonade — e.g. an agent in ``--fix`` mode shelling out parallel
# category invocations, or a user kicking off a manual run on top of a
# script — they race-evict each other's models out of that slot, and
# the user sees nondeterministic ``n_ctx=4096`` overflow errors,
# ``model_load_error: llama-server failed to start`` failures, and
# spurious ``BLOCKED_BY_ARCHITECTURE`` results that have nothing to do
# with the agent under test.
#
# We enforce one-at-a-time execution by holding an advisory lock on
# ``/tmp/gaia-eval-agent.lock`` for the lifetime of ``AgentEvalRunner.run()``.
# fcntl.flock + LOCK_EX | LOCK_NB gives us "fail fast if held by another
# process" semantics. We write our PID into the lock file so the error
# message is actionable, and we tolerate stale locks by checking whether
# the holder PID is alive.
#
# Escape hatch: ``GAIA_EVAL_NO_LOCK=1`` skips the lock — useful for the
# unit-test suite or for callers that genuinely manage Lemonade out of
# band.
_LOCK_FILE = Path(tempfile.gettempdir()) / "gaia-eval-agent.lock"
_LOCK_ENV_BYPASS = "GAIA_EVAL_NO_LOCK"


def _is_pid_alive(pid: int) -> bool:
    """Return True iff *pid* corresponds to a running process."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # We don't own the process, but it exists.
        return True


@contextlib.contextmanager
def _acquire_eval_lock():
    """Hold a process-wide advisory lock for the duration of an eval run.

    Raises ``SystemExit(2)`` with an actionable error message when the
    lock is already held by another live process.  Stale locks (holder
    PID has exited) are reclaimed automatically.
    """
    if os.environ.get(_LOCK_ENV_BYPASS) == "1":
        yield
        return

    # Windows: fcntl is unavailable — degrade to a no-op (same shape as the
    # OSError-on-/tmp short-circuit below).
    if fcntl is None:
        yield
        return

    # Open / create the lock file. Mode 0o644 so it survives across users
    # without weird permission games.
    try:
        fd = os.open(str(_LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        # If we can't even create the lockfile (read-only /tmp etc.),
        # skip locking and let the run proceed — better degraded than dead.
        print(
            f"[WARN] Could not create eval lock at {_LOCK_FILE}: {exc}. "
            "Skipping concurrency guard.",
            file=sys.stderr,
        )
        yield
        return

    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise
            # Lock is held — read holder PID + age and decide.
            try:
                with open(_LOCK_FILE, "r", encoding="utf-8") as fh:
                    holder_pid_str = fh.read().strip()
                holder_pid = int(holder_pid_str) if holder_pid_str else -1
            except (ValueError, OSError):
                holder_pid = -1

            held_age_s = (
                time.time() - _LOCK_FILE.stat().st_mtime if _LOCK_FILE.exists() else 0
            )

            if holder_pid > 0 and not _is_pid_alive(holder_pid):
                # Stale lock — holder is dead. Try once more to grab it.
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    print(
                        "[ERROR] Eval lock is held but holder PID "
                        f"{holder_pid} is gone, and re-acquire still failed. "
                        f"Manually delete {_LOCK_FILE} and retry.",
                        file=sys.stderr,
                    )
                    os.close(fd)
                    sys.exit(2)
            else:
                # A live eval run is in progress somewhere — refuse loudly.
                print(
                    "[ERROR] Another `gaia eval agent` run is already in "
                    f"progress (PID {holder_pid}, started ~{int(held_age_s)}s ago).\n"
                    "        Lemonade Server's single LLM slot can't safely "
                    "host two evals at once — they race-evict each other's "
                    "models and you'll see bogus n_ctx=4096 errors.\n"
                    f"        Wait for PID {holder_pid} to finish, or "
                    f"`kill {holder_pid}` if it's stuck.\n"
                    "        Override (NOT recommended): "
                    f"set {_LOCK_ENV_BYPASS}=1 in the environment.",
                    file=sys.stderr,
                )
                os.close(fd)
                sys.exit(2)

        # We hold the lock — record our PID so future failers can see it.
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
            os.fsync(fd)
        except OSError:
            # PID-write failure is non-fatal; the lock itself is already held.
            pass

        try:
            yield
        finally:
            # Best-effort cleanup. Releasing the flock happens implicitly
            # when fd is closed; we also wipe our PID so a reader doesn't
            # blame our (dead) process for a future stale-lock encounter.
            try:
                os.ftruncate(fd, 0)
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


REAL_WORLD_MANIFEST = REAL_WORLD_CORPUS_DIR / "manifest.json"

# User-local directories for custom scenarios and corpus.
# Auto-discovered if they exist; also addable via --scenario-dir / --corpus-dir.
USER_SCENARIOS_DIR = Path.home() / ".gaia" / "eval" / "scenarios"
USER_CORPUS_DIR = Path.home() / ".gaia" / "eval" / "corpus"

# Personas defined in eval/prompts/simulator.md.  validate_scenario enforces this list.
# Custom personas are allowed — these are documented defaults.
_KNOWN_PERSONAS = frozenset(
    {
        "casual_user",
        "power_user",
        "confused_user",
        "adversarial_user",
        "data_analyst",
        "home_user",
        "small_business_owner",
        "student",
        "creative_professional",
    }
)

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_BACKEND = "http://localhost:4200"
DEFAULT_BUDGET = "2.00"
DEFAULT_TIMEOUT = 900  # seconds per scenario (base)
# Extra seconds reserved for claude subprocess + MCP server cold-start.
_STARTUP_OVERHEAD_S = (
    240  # 4 min startup (MCP init + prompt ingestion, higher on Windows)
)
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

    # Validate persona: must be a non-empty string.
    # The 5 built-in personas (casual_user, power_user, etc.) are documented defaults,
    # but any non-empty string is accepted to support custom persona descriptions.
    persona = data.get("persona")
    if persona is not None:
        if not isinstance(persona, str):
            errors.append(f"persona must be a string, got {type(persona).__name__}")
        elif not persona.strip():
            errors.append("persona must be a non-empty string")
        elif persona not in _KNOWN_PERSONAS:
            logger.info(
                "Scenario '%s' uses custom persona '%s' (not in built-in set: %s)",
                sid,
                persona,
                ", ".join(sorted(_KNOWN_PERSONAS)),
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
        gt = turn.get("ground_truth")
        has_gt = isinstance(gt, dict) and bool(gt)
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


def find_scenarios(scenario_id=None, category=None, extra_dirs=None, tags=None):
    """Find scenario YAML files matching filters.

    Args:
        scenario_id: Only return the scenario with this ID.
        category: Only return scenarios in this category.
        extra_dirs: List of additional directories to scan for scenario YAML files.
            Scenarios from extra_dirs override built-in scenarios with the same ID.
        tags: List of tags to filter by. If specified, only scenarios whose ``tags``
            field contains at least one of these tags are returned (OR logic).

    Returns list of (path, data) tuples. Raises RuntimeError if any YAML is
    unparseable or fails schema validation.
    """
    # Collect all directories to scan: built-in first, then user-local, then extra.
    dirs_to_scan = [SCENARIOS_DIR]

    # Auto-discover user-local scenarios directory
    if USER_SCENARIOS_DIR.is_dir():
        dirs_to_scan.append(USER_SCENARIOS_DIR)
        logger.info("Auto-discovered user scenarios dir: %s", USER_SCENARIOS_DIR)

    # Append any extra directories from --scenario-dir
    if extra_dirs:
        for d in extra_dirs:
            p = Path(d)
            if p.is_dir():
                dirs_to_scan.append(p)
                logger.info("Adding extra scenario dir: %s", p)
            else:
                logger.warning("Scenario directory does not exist, skipping: %s", p)

    # Scan all directories; later entries override earlier ones (by scenario ID).
    scenarios_by_id = {}  # id -> (path, data)
    for scan_dir in dirs_to_scan:
        for path in sorted(scan_dir.rglob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception as e:
                raise RuntimeError(f"Failed to parse scenario YAML {path}: {e}") from e
            try:
                validate_scenario(path, data)
            except ValueError as e:
                raise RuntimeError(str(e)) from e

            sid = data.get("id")
            if sid in scenarios_by_id:
                prev_path = scenarios_by_id[sid][0]
                logger.info(
                    "Scenario '%s' from %s overrides built-in at %s",
                    sid,
                    path,
                    prev_path,
                )
            scenarios_by_id[sid] = (path, data)

    # Apply filters
    scenarios = []
    for sid, (path, data) in sorted(scenarios_by_id.items(), key=lambda x: x[0]):
        if scenario_id and data.get("id") != scenario_id:
            continue
        if category and data.get("category") != category:
            continue
        # Tag filtering: scenario must have at least one matching tag (OR logic)
        if tags:
            scenario_tags = set(data.get("tags", []))
            if not scenario_tags.intersection(tags):
                continue
        scenarios.append((path, data))
    return scenarios


def build_scenario_prompt(
    scenario_data, manifest_data, backend_url, keep_sessions=False, agent_type=None
):  # pylint: disable=unused-argument
    """Build the prompt passed to `claude -p` for one scenario.

    Sessions are always preserved (never deleted) so they remain available
    for manual inspection in the Agent UI.  The *keep_sessions* parameter
    is retained for backward compatibility with existing callers but has
    no effect.

    When *agent_type* is set, the simulator is instructed to create sessions
    bound to that agent registration ID (e.g. "gaia-lite"). Without it, the
    backend uses its default agent.
    """
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

    # When the runner targets a specific agent (e.g. gaia-lite), inject an
    # explicit instruction so the eval simulator creates sessions bound to
    # that agent_type. The MCP create_session tool accepts an optional
    # agent_type kwarg that maps to the REST POST /api/sessions body.
    if agent_type:
        agent_type_instructions = (
            f"\n## TARGET AGENT\n"
            f'This eval run targets agent_type="{agent_type}". '
            f"When creating sessions in Phase 1, you MUST pass "
            f'agent_type="{agent_type}" to create_session so each scenario '
            f"runs against the intended agent. Example call: "
            f'create_session(title="Eval: <scenario_id>", agent_type="{agent_type}").\n'
        )
        create_session_call = (
            f'create_session(title="Eval: {{scenario_id}}", '
            f'agent_type="{agent_type}")'
        )
    else:
        agent_type_instructions = ""
        create_session_call = 'create_session("Eval: {{scenario_id}}")'

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
{agent_type_instructions}
## YOUR TASK

### Phase 1: Setup
1. Call system_status() — if error, return status="INFRA_ERROR"
2. Call {create_session_call}
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

### Phase 5: Preserve session
Do NOT call delete_session. Leave the session intact so it can be reviewed in the Agent UI after the eval completes.

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
      "reasoning": "...",
      "performance": {{
        "tokens_per_second": 39.9 or null,
        "time_to_first_token": 1.163 or null,
        "input_tokens": 616 or null,
        "output_tokens": 320 or null,
        "flags": []
      }}
    }}
  ],
  "performance_summary": {{
    "avg_tokens_per_second": N.N or null,
    "avg_time_to_first_token": N.NNN or null,
    "total_input_tokens": N,
    "total_output_tokens": N,
    "flags": []
  }},
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
    # Clamp each dimension to [0, 10] — a hallucinating eval agent could return
    # out-of-range values that would inflate/deflate the weighted score.
    return sum(max(0, min(10, scores[k])) * w for k, w in _SCORE_WEIGHTS.items())


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


def _aggregate_performance(result: dict, scenario_id: str) -> None:
    """Aggregate per-turn performance data into a scenario-level performance_summary.

    Mutates *result* in place.  Performance data is informational — missing data
    does not affect pass/fail and is expected when using non-Lemonade providers.
    """
    tps_values = []
    ttft_values = []
    total_input = 0
    total_output = 0
    all_flags: set = set()

    for turn in result.get("turns", []):
        perf = turn.get("performance")
        if not isinstance(perf, dict):
            continue

        tps = perf.get("tokens_per_second")
        if isinstance(tps, (int, float)) and tps > 0:
            tps_values.append(tps)

        ttft = perf.get("time_to_first_token")
        if isinstance(ttft, (int, float)) and ttft > 0:
            ttft_values.append(ttft)

        inp = perf.get("input_tokens")
        if isinstance(inp, (int, float)):
            total_input += int(inp)

        out = perf.get("output_tokens")
        if isinstance(out, (int, float)):
            total_output += int(out)

        flags = perf.get("flags")
        if isinstance(flags, list):
            all_flags.update(flags)

    if tps_values or ttft_values or total_input or total_output:
        avg_tps = sum(tps_values) / len(tps_values) if tps_values else None
        avg_ttft = sum(ttft_values) / len(ttft_values) if ttft_values else None
        result["performance_summary"] = {
            "avg_tokens_per_second": round(avg_tps, 1) if avg_tps else None,
            "avg_time_to_first_token": round(avg_ttft, 3) if avg_ttft else None,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "flags": sorted(all_flags),
        }
        if avg_tps:
            print(
                f"[PERF] {scenario_id} — {avg_tps:.1f} tok/s avg, "
                f"{total_input}→{total_output} tokens",
                file=sys.stderr,
            )
    else:
        result["performance_summary"] = None


def preflight_check(backend_url, scenarios=None):
    """Check prerequisites before running scenarios.

    Args:
        backend_url: Agent UI backend URL.
        scenarios: Optional iterable of ``(path, scenario_data)`` tuples — when
            provided, also verifies any category-specific prerequisites (e.g.
            memory scenarios need ``GAIA_MEMORY_ADMIN=1`` on the backend so
            the eval simulator can reset state between runs).

    Returns a list of error strings; empty list means preflight passed.
    """
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
    claude_bin = shutil.which("claude")
    if not claude_bin:
        errors.append("'claude' CLI not found on PATH — install Claude Code CLI")
    else:
        try:
            result = subprocess.run(
                [claude_bin, "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                errors.append(
                    "'claude' CLI not found on PATH — install Claude Code CLI"
                )
        except FileNotFoundError:
            errors.append("'claude' CLI not found on PATH — install Claude Code CLI")

    # Memory-category preflight — required only when memory scenarios are queued.
    # Memory scenarios call memory_clear(scope=all) to reset state between turns;
    # that endpoint is gated by GAIA_MEMORY_ADMIN=1 on the backend process.
    # Without this, every memory scenario would silently inherit state from the
    # previous run and produce flaky pass/fail results.
    has_memory_scenarios = scenarios is not None and any(
        (sd or {}).get("category") == "memory" for _path, sd in scenarios
    )
    if has_memory_scenarios:
        memory_admin_error = _probe_memory_admin(backend_url)
        if memory_admin_error:
            errors.append(memory_admin_error)

    return errors


def _probe_memory_admin(backend_url: str) -> Optional[str]:
    """Verify the backend has ``GAIA_MEMORY_ADMIN=1`` set.

    Probes ``POST /api/memory/admin/seed`` with an empty items list — the
    endpoint short-circuits to ``{"count": 0, "ids": []}`` without writing
    anything when admin is enabled, or returns 403 with an actionable
    message when it isn't.

    Returns ``None`` on success, or an error string ready for the preflight
    error list.
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{backend_url}/api/memory/admin/seed",
        data=b'{"items":[]}',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            if r.status == 200:
                return None
            return (
                f"Memory admin probe returned unexpected HTTP {r.status} "
                f"from {backend_url}/api/memory/admin/seed"
            )
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return (
                "Memory scenarios require GAIA_MEMORY_ADMIN=1 on the backend, "
                "but the backend at "
                f"{backend_url} returned 403 from /api/memory/admin/seed. "
                "Restart the Agent UI backend with that env var set, e.g.:\n"
                "    GAIA_MEMORY_ADMIN=1 GAIA_MEMORY_MCP_ALWAYS=1 gaia chat --ui"
            )
        return (
            f"Memory admin probe failed with HTTP {e.code} from {backend_url}: "
            f"{e.reason}"
        )
    except urllib.error.URLError as e:
        return (
            f"Memory admin probe could not reach {backend_url}: {e}. "
            "Is the Agent UI backend running?"
        )


def _load_merged_manifest(extra_corpus_dirs=None):
    """Load and merge corpus manifest from built-in + user-local + extra dirs.

    Args:
        extra_corpus_dirs: Optional list of additional corpus directories, each
            expected to contain a ``manifest.json``.

    Returns:
        Merged manifest dict with combined document lists.
    """
    manifest_data = json.loads(MANIFEST.read_text(encoding="utf-8"))

    # Merge real-world manifest facts if present
    if REAL_WORLD_MANIFEST.exists():
        try:
            rw_manifest = json.loads(REAL_WORLD_MANIFEST.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[WARN] Could not load real-world manifest {REAL_WORLD_MANIFEST}: {exc}",
                file=sys.stderr,
            )
            rw_manifest = {}
        merged_docs = manifest_data.get("documents", []) + rw_manifest.get(
            "documents", []
        )
        manifest_data = {
            **manifest_data,
            "documents": merged_docs,
            "total_documents": len(merged_docs),
        }

    # Auto-discover user-local corpus manifest
    user_manifest = USER_CORPUS_DIR / "manifest.json"
    if user_manifest.is_file():
        try:
            user_data = json.loads(user_manifest.read_text(encoding="utf-8"))
            extra_docs = user_data.get("documents", [])
            manifest_data["documents"] = manifest_data.get("documents", []) + extra_docs
            manifest_data["total_documents"] = len(manifest_data["documents"])
            logger.info(
                "Merged %d document(s) from user corpus: %s",
                len(extra_docs),
                USER_CORPUS_DIR,
            )
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"[WARN] Could not load user corpus manifest {user_manifest}: {exc}",
                file=sys.stderr,
            )

    # Merge extra corpus directories from --corpus-dir
    if extra_corpus_dirs:
        for corpus_dir_str in extra_corpus_dirs:
            corpus_dir_path = Path(corpus_dir_str)
            extra_manifest = corpus_dir_path / "manifest.json"
            if extra_manifest.is_file():
                try:
                    extra_data = json.loads(extra_manifest.read_text(encoding="utf-8"))
                    extra_docs = extra_data.get("documents", [])
                    manifest_data["documents"] = (
                        manifest_data.get("documents", []) + extra_docs
                    )
                    manifest_data["total_documents"] = len(manifest_data["documents"])
                    logger.info(
                        "Merged %d document(s) from extra corpus: %s",
                        len(extra_docs),
                        corpus_dir_path,
                    )
                except (json.JSONDecodeError, OSError) as exc:
                    print(
                        f"[WARN] Could not load corpus manifest {extra_manifest}: {exc}",
                        file=sys.stderr,
                    )
            else:
                logger.warning(
                    "No manifest.json found in corpus dir: %s", corpus_dir_path
                )

    return manifest_data


def run_scenario_subprocess(
    _scenario_path,
    scenario_data,
    run_dir,
    backend_url,
    model,
    budget,
    timeout,
    keep_sessions=False,
    extra_corpus_dirs=None,
    agent_type=None,
):
    """Invoke claude -p for one scenario. Returns parsed result dict."""
    scenario_id = scenario_data["id"]
    manifest_data = _load_merged_manifest(extra_corpus_dirs=extra_corpus_dirs)

    prompt = build_scenario_prompt(
        scenario_data,
        manifest_data,
        backend_url,
        keep_sessions=keep_sessions,
        agent_type=agent_type,
    )

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

    claude_bin = shutil.which("claude") or "claude"

    # ``--bare`` gives a clean subprocess (skips hooks, CLAUDE.md auto-load,
    # plugin sync, etc.) but per the Claude Code CLI docs it ALSO restricts
    # Anthropic auth to ``ANTHROPIC_API_KEY`` / ``apiKeyHelper`` only —
    # OAuth and keychain are never read. So on a subscription-only setup
    # (the common case for Claude Code Max users), ``--bare`` makes the
    # eval fail-fast with ``Not logged in · Please run /login`` because
    # the SDK can't see the user's existing OAuth session. We opt in to
    # ``--bare`` only when an explicit API key is available; otherwise we
    # let the subprocess inherit the parent's OAuth credentials.
    cmd = [claude_bin, "-p"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        cmd.append("--bare")
    cmd.extend(
        [
            "--no-session-persistence",  # don't save eval sessions to disk
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
    )

    print(f"\n[RUN] {scenario_id} — invoking claude -p ...", flush=True)
    start = time.time()

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
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
                # Guard: eval agent must return a JSON object — arrays or scalars
                # are not processable.  Wrap them in an ERRORED result dict.
                if not isinstance(result, dict):
                    result = {
                        "scenario_id": scenario_id,
                        "status": "ERRORED",
                        "overall_score": None,
                        "turns": [],
                        "error": f"Eval agent returned non-object JSON: {type(result).__name__}",
                        "cost_estimate": {"turns": 0, "estimated_usd": 0.0},
                    }
                # Guard: ensure required fields are present regardless of parse path.
                # Always inject the runner-known scenario_id — eval agent output may omit
                # it (partial JSON) or contain a wrong value; the runner's sid is authoritative.
                if isinstance(result, dict) and "status" not in result:
                    print(
                        f"[WARN] {scenario_id} — eval agent JSON missing 'status' field",
                        file=sys.stderr,
                    )
                    result.setdefault("status", "ERRORED")
                    result.setdefault("overall_score", None)
                    result.setdefault("turns", [])
                if isinstance(result, dict):
                    result["scenario_id"] = scenario_id
                result["elapsed_s"] = elapsed
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

    # Validate and aggregate per-turn performance data into scenario-level summary.
    # Performance data is informational — missing data is expected when using
    # non-Lemonade providers and does not affect pass/fail.
    _aggregate_performance(result, scenario_id)

    # Latency validation — flag excessive TTFT as a UX concern
    _MAX_TTFT_S = 30.0  # fail threshold for time-to-first-token
    perf_summary = result.get("performance_summary") or {}
    avg_ttft = perf_summary.get("avg_time_to_first_token")
    if isinstance(avg_ttft, (int, float)) and avg_ttft > _MAX_TTFT_S:
        result.setdefault("latency_warning", []).append(
            f"avg TTFT {avg_ttft:.1f}s exceeds {_MAX_TTFT_S}s threshold"
        )
        print(
            f"[WARN] {scenario_id} — slow TTFT: {avg_ttft:.1f}s "
            f"(threshold: {_MAX_TTFT_S}s)",
            file=sys.stderr,
        )

    # Write trace file
    traces_dir = run_dir / "traces"
    traces_dir.mkdir(exist_ok=True)
    trace_path = traces_dir / f"{scenario_id}.json"
    trace_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Print final [DONE] line here — AFTER all score/status overrides — so the
    # displayed status always reflects the fully-corrected result, not the raw
    # LLM output which may still show the pre-override status.
    elapsed_final = result.get("elapsed_s", 0)
    score_final = result.get("overall_score")
    score_str_final = (
        f"{score_final:.1f}" if isinstance(score_final, (int, float)) else "n/a"
    )
    print(
        f"[DONE] {scenario_id} — {result.get('status')} {score_str_final}/10 ({elapsed_final:.0f}s)"
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

        # Check for time regressions: compare per-scenario elapsed seconds
        b_elapsed = b.get("elapsed_s") or 0
        c_elapsed = c.get("elapsed_s") or 0
        time_regress = False
        if isinstance(b_elapsed, (int, float)) and b_elapsed > 0:
            try:
                if c_elapsed > (b_elapsed * 2):
                    time_regress = True
            except Exception:
                time_regress = False
        if time_regress:
            entry["baseline_elapsed_s"] = round(float(b_elapsed), 2)
            entry["current_elapsed_s"] = round(float(c_elapsed), 2)
            entry["time_regressed"] = True

        # Corpus availability change — not a quality signal
        if b_skipped or c_skipped:
            corpus_changed.append(entry)
        elif entry.get("time_regressed"):
            # Treat time regressions as score regressions to surface in CI
            score_regressed.append(entry)
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
        extra_scenario_dirs=None,
        extra_corpus_dirs=None,
        tags=None,
        output_format=None,
        agent_type=None,
    ):
        self.backend_url = backend_url
        self.model = model
        self.budget = budget_per_scenario
        self.timeout = timeout_per_scenario
        self.results_dir = Path(results_dir) if results_dir else RESULTS_DIR
        self.extra_scenario_dirs = extra_scenario_dirs or []
        self.extra_corpus_dirs = extra_corpus_dirs or []
        self.tags = tags or []
        self.output_format = output_format
        self.agent_type = agent_type

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
        keep_sessions=False,
    ):
        """Run eval scenarios. Returns scorecard dict.

        When fix_mode=True, after the initial eval run the runner will:
          B) invoke Claude Code to fix failing scenarios
          C) re-run only previously-failed scenarios
          D) compare before/after and report improvements/regressions
        repeating B-D up to max_fix_iterations or until target_pass_rate is met.

        Holds a process-wide advisory lock for the lifetime of the run
        (audit-only mode skips this — no Lemonade contention there).
        See ``_acquire_eval_lock`` for the rationale.
        """

        if audit_only:
            from gaia.eval.audit import run_audit

            result = run_audit()
            print(json.dumps(result, indent=2))
            return result

        with _acquire_eval_lock():
            return self._run_locked(
                scenario_id=scenario_id,
                category=category,
                fix_mode=fix_mode,
                max_fix_iterations=max_fix_iterations,
                target_pass_rate=target_pass_rate,
                keep_sessions=keep_sessions,
            )

    def _run_locked(
        self,
        scenario_id=None,
        category=None,
        fix_mode=False,
        max_fix_iterations=3,
        target_pass_rate=0.90,
        keep_sessions=False,
    ):
        """Internal entry — holds the eval lock; only called by ``run()``."""

        # Find scenarios
        scenarios = find_scenarios(
            scenario_id=scenario_id,
            category=category,
            extra_dirs=self.extra_scenario_dirs,
            tags=self.tags if self.tags else None,
        )
        if not scenarios:
            filter_desc = f"id={scenario_id}, category={category}"
            if self.tags:
                filter_desc += f", tags={self.tags}"
            print(
                f"[ERROR] No scenarios found ({filter_desc})",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"[INFO] Found {len(scenarios)} scenario(s)")
        if self.agent_type:
            print(f"[INFO] Targeting agent_type='{self.agent_type}'")

        # Pre-flight
        errors = preflight_check(self.backend_url, scenarios=scenarios)
        if errors:
            print("[ERROR] Pre-flight check failed:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            sys.exit(1)

        # Create run dir
        run_id = f"eval-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
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
            # Per-scenario agent_type from YAML overrides CLI --agent-type
            scenario_agent_type = scenario_data.get("agent_type", self.agent_type)
            result = run_scenario_subprocess(
                scenario_path,
                scenario_data,
                run_dir,
                self.backend_url,
                self.model,
                self.budget,
                effective_timeout,
                keep_sessions=keep_sessions,
                extra_corpus_dirs=(
                    self.extra_corpus_dirs if self.extra_corpus_dirs else None
                ),
                agent_type=scenario_agent_type,
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
            "agent_type": self.agent_type,
        }
        scorecard = aggregate_scorecard(results, run_id, run_dir, config)

        # Write JUnit XML if requested
        if self.output_format == "junit":
            from gaia.eval.scorecard import write_junit_xml

            junit_path = run_dir / "results.xml"
            junit_path.write_text(write_junit_xml(scorecard), encoding="utf-8")
            print(f"[INFO] JUnit XML written to: {junit_path}")

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
                scenario_agent_type = scenario_data.get("agent_type", self.agent_type)
                result = run_scenario_subprocess(
                    scenario_path,
                    scenario_data,
                    run_dir,
                    self.backend_url,
                    self.model,
                    self.budget,
                    effective_timeout,
                    keep_sessions=keep_sessions,
                    extra_corpus_dirs=(
                        self.extra_corpus_dirs if self.extra_corpus_dirs else None
                    ),
                    agent_type=scenario_agent_type,
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
