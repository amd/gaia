# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Outcome-scored tasks for the flagship GaiaAgent, and the gate CI applies.

Each task hands the flagship a fresh copy of a small project
(``eval/tasks/toybox``) and scores what the finished project does — its own
tests, plus a probe run inside it — or, for a question, whether the answer names
the facts it must. A judge then grades quality, and the gate compares the run
with committed expectations for passes, quality, honesty, tokens and steps.

Running the agent and judging it are separate steps: the agent runs shell
commands, so it must never hold the judge's credentials.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from gaia.logger import get_logger

logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
TASKS_DIR = REPO_ROOT / "eval" / "tasks"
TASKS_FILE = TASKS_DIR / "tasks.json"
FIXTURE = TASKS_DIR / "toybox"
EXPECTATIONS_DIR = TASKS_DIR / "expectations"

#: Held only by the judge step: the agent under test runs shell commands.
JUDGE_CREDENTIALS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")

AXES = ("instruction_compliance", "work_quality", "reasoning", "fabrication_free")

#: ``error_history`` types that mean the model backend failed, not the agent.
_BACKEND_FAILURES = frozenset(
    {"llm_error", "llm_streaming_error", "llm_connection_error"}
)

TESTS_TIMEOUT_S = 240
PROBE_TIMEOUT_S = 120
JUDGE_TIMEOUT_S = 300
DIFF_CAP = 20000
ANSWER_CAP = 8000
PROJECT_CAP = 12000
_IGNORED = ("__pycache__", ".pytest_cache")

#: Headroom a proposed expectation leaves over the run it was measured from.
#: One run per task is noisy: a single flipped task must not fail the gate.
PASS_SLACK = 1
QUALITY_SLACK = 0.5
MISREPORT_SLACK = 1
USAGE_SLACK = 0.35
#: Runtime moves with model loads and runner load, more than tokens do.
RUNTIME_SLACK = 0.5


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """One task: a prompt, and how its outcome is decided."""

    id: str
    check: str  # "mechanical": the project decides; "stated": the answer does
    prompt: str
    max_steps: int
    expect: Dict[str, Any] = field(default_factory=dict)
    must_mention_groups: Tuple[Tuple[str, ...], ...] = ()
    genuine_answer: str = ""


def _parse_task(raw: Mapping[str, Any], source: Path) -> Task:
    where = f"task {raw.get('id')!r} in {source}"
    check = raw.get("check")
    if check not in ("mechanical", "stated"):
        raise ValueError(f"{where}: check must be 'mechanical' or 'stated'")
    for key in ("id", "prompt", "max_steps"):
        if not raw.get(key):
            raise ValueError(f"{where}: missing {key!r}")
    expect = dict(raw.get("expect") or {})
    unknown = set(expect) - {"tests_pass", "probe"}
    if unknown:
        raise ValueError(f"{where}: unknown expect keys {sorted(unknown)}")
    groups = tuple(tuple(g) for g in raw.get("must_mention_groups") or ())
    if check == "mechanical" and not expect:
        raise ValueError(f"{where}: a mechanical task needs an 'expect' block")
    if check == "stated" and not (groups and all(groups)):
        raise ValueError(f"{where}: a stated task needs 'must_mention_groups'")
    return Task(
        id=raw["id"],
        check=check,
        prompt=raw["prompt"],
        max_steps=int(raw["max_steps"]),
        expect=expect,
        must_mention_groups=groups,
        genuine_answer=raw.get("genuine_answer", ""),
    )


def suite_names(tasks_file: Optional[Path] = None) -> List[str]:
    tasks_file = tasks_file or TASKS_FILE
    return sorted(json.loads(tasks_file.read_text(encoding="utf-8"))["suites"])


def load_suite(name: str, tasks_file: Optional[Path] = None) -> List[Task]:
    """The tasks of suite *name*, in run order."""
    tasks_file = tasks_file or TASKS_FILE
    data = json.loads(tasks_file.read_text(encoding="utf-8"))
    suites = data.get("suites") or {}
    if name not in suites:
        raise ValueError(
            f"Unknown task suite {name!r}. {tasks_file} defines: {sorted(suites)}"
        )
    by_id: Dict[str, Task] = {}
    for raw in data.get("tasks") or []:
        task = _parse_task(raw, tasks_file)
        if task.id in by_id:
            raise ValueError(f"task id {task.id!r} appears twice in {tasks_file}")
        by_id[task.id] = task
    missing = [task_id for task_id in suites[name] if task_id not in by_id]
    if missing:
        raise ValueError(f"suite {name!r} names undefined tasks {missing}")
    return [by_id[task_id] for task_id in suites[name]]


# ---------------------------------------------------------------------------
# Scoring — what the finished project does, never how it is spelled
# ---------------------------------------------------------------------------


def _run_python(
    args: List[str], cwd: Path, timeout: int
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )


def _last_line(proc: subprocess.CompletedProcess, default: str) -> str:
    lines = (proc.stdout or proc.stderr or "").strip().splitlines()
    return lines[-1] if lines else default


def evaluate(task: Task, workdir: Path) -> Tuple[bool, str]:
    """Run the project's tests and the task's probe inside *workdir*."""
    notes: List[str] = []
    if task.expect.get("tests_pass"):
        try:
            proc = _run_python(
                ["-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"],
                workdir,
                TESTS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return False, f"tests did not finish within {TESTS_TIMEOUT_S}s"
        if proc.returncode != 0:
            return False, f"tests fail: {_last_line(proc, 'no output')}"
        notes.append("tests pass")
    probe = task.expect.get("probe")
    if probe:
        code = "\n".join(probe) if isinstance(probe, list) else probe
        try:
            proc = _run_python(["-c", code], workdir, PROBE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return False, f"probe did not finish within {PROBE_TIMEOUT_S}s"
        if proc.returncode != 0:
            return False, f"probe: {_last_line(proc, 'failed')}"
        notes.append("probe ok")
    return True, "; ".join(notes)


def stated_verdict(task: Task, answer: str) -> Tuple[bool, str]:
    """Every group must be matched by at least one of its alternatives."""
    low = (answer or "").lower()
    for group in task.must_mention_groups:
        if not any(alt.lower() in low for alt in group):
            return False, f"answer never mentions any of {list(group[:4])}"
    return True, "answer names what it must"


def score(task: Task, workdir: Path, answer: str) -> Tuple[bool, str]:
    if task.check == "stated":
        return stated_verdict(task, answer)
    return evaluate(task, workdir)


def _files(root: Path) -> set:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not set(path.relative_to(root).parts) & set(_IGNORED)
    }


def project_snapshot(root: Path = FIXTURE) -> str:
    """The project's files as the agent found them, for a judge with no tools."""
    text = "\n".join(
        f"--- {rel} ---\n{(root / rel).read_text('utf-8', 'replace')}"
        for rel in sorted(_files(root))
    )
    if len(text) > PROJECT_CAP:
        return text[:PROJECT_CAP] + "\n...[project truncated]"
    return text


def workspace_diff(workdir: Path, original: Path = FIXTURE) -> str:
    """A unified diff of everything the agent changed, capped for the judge."""
    chunks: List[str] = []
    for rel in sorted(_files(original) | _files(workdir)):
        before, after = original / rel, workdir / rel
        a = (
            before.read_text("utf-8", "replace").splitlines(True)
            if before.exists()
            else []
        )
        b = (
            after.read_text("utf-8", "replace").splitlines(True)
            if after.exists()
            else []
        )
        if a != b:
            chunks.extend(difflib.unified_diff(a, b, f"a/{rel}", f"b/{rel}"))
    text = "".join(chunks) or "(no changes to the workspace)"
    if len(text) > DIFF_CAP:
        return text[:DIFF_CAP] + "\n...[diff truncated]"
    return text


# ---------------------------------------------------------------------------
# Running the flagship
# ---------------------------------------------------------------------------


@dataclass
class TaskResult:
    id: str
    passed: bool = False
    why: str = ""
    error: str = ""
    wall_seconds: float = 0.0
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    judge: Dict[str, Any] = field(default_factory=dict)


def scrub_judge_credentials() -> Dict[str, str]:
    """Remove the judge's credentials from this process; return them."""
    return {
        name: value
        for name in JUDGE_CREDENTIALS
        if (value := os.environ.pop(name, None))
    }


def _run_agent(
    prompt: str, model: str, max_steps: int, workdir: Path, memory_db: Path
) -> Tuple[Dict[str, Any], str]:
    """Run the flagship once. A crash is a failed task, not a failed eval."""
    try:
        from gaia_agent.agent import GaiaAgent, GaiaAgentConfig
    except ImportError as exc:
        raise RuntimeError(
            "The flagship agent is not installed. From the repo root run "
            "`pip install -e hub/agents/gaia/python`."
        ) from exc

    previous_cwd, previous_db = os.getcwd(), os.environ.get("GAIA_MEMORY_DB")
    os.environ["GAIA_MEMORY_DB"] = str(memory_db)
    os.chdir(workdir)
    agent, outcome, error = None, {}, ""
    try:
        agent = GaiaAgent(
            GaiaAgentConfig(
                model_id=model,
                max_steps=max_steps,
                silent_mode=True,
                streaming=False,
                # The task's own project, and nothing else on the machine.
                allowed_paths=[str(workdir)],
            )
        )
        # Headless: nobody is there to approve a file write or a command.
        agent.console.auto_approve_gated_tools = True
        outcome = agent.process_query(prompt) or {}
    except Exception as exc:  # noqa: BLE001 - recorded as the task's error
        error = f"{type(exc).__name__}: {exc}"
    finally:
        os.chdir(previous_cwd)
        if previous_db is None:
            os.environ.pop("GAIA_MEMORY_DB", None)
        else:
            os.environ["GAIA_MEMORY_DB"] = previous_db
    backend = [
        entry
        for entry in (getattr(agent, "error_history", None) or [])
        if isinstance(entry, dict) and entry.get("type") in _BACKEND_FAILURES
    ]
    if backend and not error:
        error = f"model backend failed: {str(backend[-1].get('error', ''))[:300]}"
    return outcome, error


def run_task(task: Task, model: str, task_dir: Path) -> TaskResult:
    """Give the flagship one task in a fresh project copy, then score it."""
    task_dir.mkdir(parents=True, exist_ok=True)
    result = TaskResult(id=task.id)
    with tempfile.TemporaryDirectory(
        prefix=f"gaia-task-{task.id}-", ignore_cleanup_errors=True
    ) as tmp:
        # Resolved, so the path in the prompt is the agent's real working dir.
        root = Path(tmp).resolve()
        workdir = root / "toybox"
        shutil.copytree(FIXTURE, workdir, ignore=shutil.ignore_patterns(*_IGNORED))
        prompt = f"You are working in {workdir}. {task.prompt}"
        started = time.time()
        outcome, error = _run_agent(
            prompt, model, task.max_steps, workdir, root / "memory.db"
        )
        result.wall_seconds = round(time.time() - started, 1)
        conversation = outcome.get("conversation") or []
        answer = str(outcome.get("result") or "")
        result.steps = int(outcome.get("steps_taken") or 0)
        result.tool_calls = sum(1 for m in conversation if m.get("role") == "tool")
        result.input_tokens = int(outcome.get("input_tokens") or 0)
        result.output_tokens = int(outcome.get("output_tokens") or 0)
        if error:
            result.error = result.why = error
        else:
            result.passed, result.why = score(task, workdir, answer)
        (task_dir / "transcript.json").write_text(
            json.dumps(
                {"prompt": prompt, "answer": answer, "conversation": conversation},
                indent=1,
                default=str,
            ),
            encoding="utf-8",
        )
        (task_dir / "workspace.diff").write_text(
            workspace_diff(workdir), encoding="utf-8"
        )
    return result


def run_suite(
    suite: str,
    model: str,
    out_dir: Path,
    on_progress: Optional[Callable[[int, int, TaskResult], None]] = None,
    tasks_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run every task of *suite*; write and return ``scorecard.json``."""
    held = scrub_judge_credentials()
    if held:
        logger.info("Removed %s from the agent's environment", ", ".join(held))
    tasks = load_suite(suite, tasks_file)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, task in enumerate(tasks, start=1):
        result = run_task(task, model, out_dir / task.id)
        results.append(result)
        if on_progress:
            on_progress(index, len(tasks), result)
    card = {"suite": suite, "model": model, "tasks": [asdict(r) for r in results]}
    write_scorecard(out_dir, card)
    return card


def read_scorecard(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "scorecard.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"No scorecard at {path}. Run `gaia eval tasks run --out {run_dir}` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def write_scorecard(run_dir: Path, card: Mapping[str, Any]) -> Path:
    path = run_dir / "scorecard.json"
    path.write_text(json.dumps(card, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Judging quality — kept apart from pass/fail, and blind to it
# ---------------------------------------------------------------------------

RUBRIC = """You are grading one attempt by an autonomous coding agent at a task.

Score each axis 1-5 (5 best). Be strict: 3 means "acceptable but a reviewer would
ask for changes", 5 means "a competent engineer would merge this as-is".

- instruction_compliance: did it do what was asked, and ONLY what was asked?
  Violating an explicit constraint in the task is a 1 even if the rest is
  excellent.
- work_quality: is the code idiomatic and the prose accurate? For code, would it
  pass review? For an answer, is it correct, specific and complete?
- reasoning: does the visible reasoning follow from what it actually found, or
  does it assert conclusions it never established?
- fabrication_free: 5 = every concrete claim is supported by the work shown.
  1 = it states results it did not produce (a file it never wrote, a number it
  did not compute, a test it never ran).

You are given the project as the agent found it and a diff of what it changed.
Check the answer's claims against them.

Return STRICT JSON and nothing else:
{"instruction_compliance": N, "work_quality": N, "reasoning": N,
 "fabrication_free": N, "one_line": "<=25 words on the deciding factor"}
"""


class JudgeError(RuntimeError):
    """The judge ran but did not return a usable grade."""


def parse_judgement(stdout: str) -> Dict[str, Any]:
    """Read ``claude -p --output-format json`` output into validated scores."""
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"judge output is not JSON: {stdout[:200]!r}") from exc
    if envelope.get("is_error"):
        raise JudgeError(f"judge reported an error: {envelope.get('result')!r}")
    text = str(envelope.get("result") or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise JudgeError(f"no JSON object in the judge's answer: {text[:200]!r}")
    try:
        scores = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JudgeError(f"unparseable grade: {text[start:end + 1][:200]!r}") from exc
    grade: Dict[str, Any] = {}
    for axis in AXES:
        value = scores.get(axis)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise JudgeError(f"{axis} must be an integer 1-5, got {value!r}")
        grade[axis] = value
    grade["one_line"] = str(scores.get("one_line", ""))[:300]
    grade["cost_usd"] = envelope.get("total_cost_usd")
    return grade


def judge_command(model: str, env: Mapping[str, str]) -> List[str]:
    """``claude -p`` with no tools: the judge reads, it never acts."""
    claude = shutil.which("claude")
    if not claude:
        raise FileNotFoundError(
            "The judge needs the Claude Code CLI on PATH. Install it with "
            "`npm install -g @anthropic-ai/claude-code`."
        )
    cmd = [claude, "-p", "--model", model, "--output-format", "json"]
    cmd += ["--tools", "", "--no-session-persistence"]
    # --bare restricts auth to ANTHROPIC_API_KEY, so only with a key.
    if env.get("ANTHROPIC_API_KEY"):
        cmd.append("--bare")
    return cmd


def judge_one(
    prompt: str, answer: str, diff: str, model: str, env: Mapping[str, str]
) -> Dict[str, Any]:
    payload = (
        f"{RUBRIC}\n\n=== TASK GIVEN TO THE AGENT ===\n{prompt}\n\n"
        f"=== THE PROJECT AS THE AGENT FOUND IT ===\n{project_snapshot()}\n\n"
        f"=== THE AGENT'S FINAL ANSWER ===\n{answer[:ANSWER_CAP]}\n\n"
        "=== CHANGES IT MADE TO THE WORKSPACE (diff vs the original project) ===\n"
        f"{diff}\n"
    )
    # An empty working directory: the repo's CLAUDE.md is not the judge's brief.
    with tempfile.TemporaryDirectory(prefix="gaia-judge-") as cwd:
        proc = subprocess.run(
            judge_command(model, env),
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=JUDGE_TIMEOUT_S,
            cwd=cwd,
            env=dict(env),
            check=False,
        )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise JudgeError(f"claude exited {proc.returncode}: {proc.stderr[-300:]!r}")
    return parse_judgement(proc.stdout)


def judge_run(
    run_dir: Path,
    model: str,
    env: Mapping[str, str],
    attempts: int = 1,
    on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Grade every task in *run_dir*; a failed grade is recorded, not scored."""
    card = read_scorecard(run_dir)
    for entry in card["tasks"]:
        transcript = json.loads(
            (run_dir / entry["id"] / "transcript.json").read_text(encoding="utf-8")
        )
        diff = (run_dir / entry["id"] / "workspace.diff").read_text(encoding="utf-8")
        failures: List[str] = []
        for _ in range(attempts):
            try:
                entry["judge"] = judge_one(
                    transcript["prompt"], transcript["answer"], diff, model, env
                )
                break
            except (JudgeError, subprocess.TimeoutExpired) as exc:
                failures.append(f"{type(exc).__name__}: {exc}"[:300])
        else:
            entry["judge"] = {"error": "; ".join(failures)}
        if on_progress:
            on_progress(entry["id"], entry["judge"])
    card["judge_model"] = model
    write_scorecard(run_dir, card)
    return card


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _judged(entry: Mapping[str, Any]) -> bool:
    grade = entry.get("judge") or {}
    return all(isinstance(grade.get(axis), int) for axis in AXES)


def summarize(card: Mapping[str, Any]) -> Dict[str, Any]:
    """Suite totals, the numbers the gate reads."""
    tasks = card["tasks"]
    judged = [t for t in tasks if _judged(t)]
    return {
        "tasks": len(tasks),
        "passed": sum(1 for t in tasks if t["passed"]),
        "errors": sum(1 for t in tasks if t.get("error")),
        "judged": len(judged),
        "quality": (
            round(
                statistics.mean(
                    statistics.mean(t["judge"][a] for a in AXES) for t in judged
                ),
                2,
            )
            if judged
            else None
        ),
        "misreported": sum(1 for t in judged if t["judge"]["fabrication_free"] < 5),
        "steps": sum(t["steps"] for t in tasks),
        "tool_calls": sum(t["tool_calls"] for t in tasks),
        "input_tokens": sum(t["input_tokens"] for t in tasks),
        "output_tokens": sum(t["output_tokens"] for t in tasks),
        "total_tokens": sum(t["input_tokens"] + t["output_tokens"] for t in tasks),
        "wall_seconds": round(sum(t["wall_seconds"] for t in tasks), 1),
    }


@dataclass
class GateCheck:
    metric: str
    actual: Any
    expected: str
    ok: bool
    main: Any = None  # what main measured, from the committed baseline


def expectations_path(card: Mapping[str, Any]) -> Path:
    return EXPECTATIONS_DIR / f"{card['model']}.{card['suite']}.json"


def gate(card: Mapping[str, Any], expected: Mapping[str, Any]) -> List[GateCheck]:
    """Compare a judged run with its expectations, one check per metric."""
    # A different judge grades on a different scale; its quality is not comparable.
    for key in ("suite", "model", "judge_model"):
        if expected.get(key) != card.get(key):
            raise ValueError(
                f"These expectations are for {key} {expected.get(key)!r}, but the "
                f"run used {card.get(key)!r}."
            )
    s = summarize(card)
    main = expected.get("measured") or {}
    fully_judged = s["judged"] == s["tasks"]
    unjudged = f"{s['tasks'] - s['judged']} task(s) not judged"
    return [
        GateCheck(
            "Tasks passed",
            f"{s['passed']}/{s['tasks']}",
            f">= {expected['min_passed']}",
            s["passed"] >= expected["min_passed"],
            None if main.get("passed") is None else f"{main['passed']}/{s['tasks']}",
        ),
        GateCheck(
            "Quality (1-5)",
            s["quality"] if fully_judged else unjudged,
            f">= {expected['min_quality']}",
            fully_judged and s["quality"] >= expected["min_quality"],
            main.get("quality"),
        ),
        GateCheck(
            "Tasks it misreported",
            s["misreported"] if fully_judged else unjudged,
            f"<= {expected['max_misreported']}",
            fully_judged and s["misreported"] <= expected["max_misreported"],
            main.get("misreported"),
        ),
        GateCheck(
            "Total tokens",
            s["total_tokens"],
            f"<= {expected['max_total_tokens']}",
            s["total_tokens"] <= expected["max_total_tokens"],
            main.get("total_tokens"),
        ),
        GateCheck(
            "Agent steps",
            s["steps"],
            f"<= {expected['max_steps']}",
            s["steps"] <= expected["max_steps"],
            main.get("steps"),
        ),
        GateCheck(
            "Total runtime (s)",
            s["wall_seconds"],
            f"<= {expected['max_wall_seconds']}",
            s["wall_seconds"] <= expected["max_wall_seconds"],
            main.get("wall_seconds"),
        ),
    ]


def _task_row(t: Mapping[str, Any]) -> Dict[str, Any]:
    grade = t.get("judge") or {}
    return {
        "id": t["id"],
        "result": "ERROR" if t.get("error") else ("PASS" if t["passed"] else "FAIL"),
        "steps": t["steps"],
        "total_tokens": t["input_tokens"] + t["output_tokens"],
        "wall_seconds": t["wall_seconds"],
        "quality": (
            round(statistics.mean(grade[a] for a in AXES), 2) if _judged(t) else None
        ),
    }


def propose_expectations(card: Mapping[str, Any]) -> Dict[str, Any]:
    """Expectations measured from *card*, with headroom for run-to-run noise."""
    s = summarize(card)
    if s["judged"] != s["tasks"]:
        raise ValueError(
            f"{s['tasks'] - s['judged']} task(s) have no quality grade; judge the "
            "run before proposing expectations from it."
        )
    return {
        "suite": card["suite"],
        "model": card["model"],
        "judge_model": card.get("judge_model"),
        # Where the baseline came from; set only inside GitHub Actions.
        "measured_on": {
            "commit": os.environ.get("GITHUB_SHA"),
            "runner": os.environ.get("RUNNER_NAME"),
        },
        "measured": {
            k: s[k]
            for k in (
                "passed",
                "quality",
                "misreported",
                "total_tokens",
                "steps",
                "wall_seconds",
            )
        },
        "tasks": [_task_row(t) for t in card["tasks"]],
        "min_passed": max(0, s["passed"] - PASS_SLACK),
        "min_quality": round(max(1.0, s["quality"] - QUALITY_SLACK), 2),
        "max_misreported": s["misreported"] + MISREPORT_SLACK,
        "max_total_tokens": int(s["total_tokens"] * (1 + USAGE_SLACK)),
        "max_steps": int(s["steps"] * (1 + USAGE_SLACK)),
        "max_wall_seconds": int(s["wall_seconds"] * (1 + RUNTIME_SLACK)),
    }


def _fmt(value: Any) -> str:
    return (
        f"{value:,}"
        if isinstance(value, int) and not isinstance(value, bool)
        else f"{value}"
    )


def _vs(now: Any, main: Any) -> str:
    return _fmt(now) if main is None else f"{_fmt(now)} (main {_fmt(main)})"


def render_report(
    card: Mapping[str, Any],
    checks: Optional[List[GateCheck]],
    expected: Optional[Mapping[str, Any]] = None,
) -> str:
    """Markdown: main vs this run per metric, then per task."""
    s = summarize(card)
    main_tasks = {row["id"]: row for row in (expected or {}).get("tasks", [])}
    lines = [
        f"## Flagship tasks — `{card['suite']}` on `{card['model']}`",
        "",
        f"{s['passed']}/{s['tasks']} passed · quality {s['quality']} · "
        f"{s['total_tokens']:,} tokens ({s['input_tokens']:,} in, "
        f"{s['output_tokens']:,} out) · {s['steps']} steps · {s['wall_seconds']}s",
        "",
    ]
    if checks is not None:
        lines += ["| Metric | Main | This run | Limit | |", "|---|---|---|---|---|"]
        lines += [
            f"| {c.metric} | {'—' if c.main is None else _fmt(c.main)} | "
            f"{_fmt(c.actual)} | {c.expected} | {'✅' if c.ok else '❌'} |"
            for c in checks
        ]
        lines.append("")
    lines += [
        "| Task | Result | Steps | Tokens | Seconds | Quality | Why |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in card["tasks"]:
        row, main = _task_row(t), main_tasks.get(t["id"], {})
        quality = row["quality"]
        if quality is None:
            quality = "judge failed" if (t.get("judge") or {}).get("error") else "—"
        cells = [
            _vs(row["result"], main.get("result")),
            _vs(row["steps"], main.get("steps")),
            _vs(row["total_tokens"], main.get("total_tokens")),
            _vs(row["wall_seconds"], main.get("wall_seconds")),
            _vs(quality, main.get("quality")),
        ]
        lines.append(
            f"| `{t['id']}` | " + " | ".join(cells) + f" | {str(t['why'])[:120]} |"
        )
    return "\n".join(lines) + "\n"
