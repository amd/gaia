# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Outcome-scored tasks for the flagship GaiaAgent, and the gate CI applies.

Each task hands the flagship a fresh copy of a small project
(``eval/tasks/toybox``), which a named setup may change first
(``task_setups.py``). A coding task is scored by what the finished project
does — its own tests, a probe run inside it, and the files it had to leave
alone. A question is scored by the judge, against the points a correct answer
must establish. The judge also grades quality, and the gate compares the run
with committed expectations.

Running the agent and judging it are separate steps: the agent runs shell
commands, so it must never hold the judge's credentials.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from gaia.agents.base.agent import Agent
from gaia.agents.base.verification import (
    check_was_executed,
    verification_check_label,
    verification_check_target,
)
from gaia.eval.task_setups import SETUPS, remove_leftovers
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

#: ``error_history`` types that mean the model backend returned an error.
_BACKEND_FAILURES = frozenset({"llm_error", "llm_streaming_error"})
#: The agent's record of a ``ConnectionError``: the backend was not there at all.
_BACKEND_UNREACHABLE = "llm_connection_error"

TESTS_TIMEOUT_S = 240
PROBE_TIMEOUT_S = 120
JUDGE_TIMEOUT_S = 300
DIFF_CAP = 20000
ANSWER_CAP = 8000
PROJECT_CAP = 12000
IGNORED = ("__pycache__", ".pytest_cache", ".git")
EXPECT_KEYS = frozenset({"tests_pass", "probe", "unchanged"})

#: Headroom a proposed expectation leaves over the run it was measured from.
#: One run per task is noisy: a single flipped task must not fail the gate.
PASS_SLACK = 1
VERIFIED_SLACK = 1
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
    #: For a question: what a correct answer must establish. The judge decides.
    must_establish: Tuple[str, ...] = ()
    genuine_answer: str = ""
    #: Plausible answers that miss the point; the judge must fail each one.
    wrong_answers: Tuple[str, ...] = ()
    #: A name from ``task_setups.SETUPS``, applied to the copy before the agent runs.
    setup: str = ""


def _project_path(path: Any) -> bool:
    """A relative, forward-slash path that stays inside the project."""
    if not isinstance(path, str) or not path or "\\" in path or ":" in path:
        return False
    rel = PurePosixPath(path)
    return not rel.is_absolute() and ".." not in rel.parts


def _parse_task(raw: Mapping[str, Any], source: Path) -> Task:
    where = f"task {raw.get('id')!r} in {source}"
    check = raw.get("check")
    if check not in ("mechanical", "stated"):
        raise ValueError(f"{where}: check must be 'mechanical' or 'stated'")
    for key in ("id", "prompt", "max_steps"):
        if not raw.get(key):
            raise ValueError(f"{where}: missing {key!r}")
    expect = dict(raw.get("expect") or {})
    unknown = set(expect) - EXPECT_KEYS
    if unknown:
        raise ValueError(f"{where}: unknown expect keys {sorted(unknown)}")
    if "unchanged" in expect and not (
        isinstance(expect["unchanged"], list)
        and expect["unchanged"]
        and all(_project_path(p) for p in expect["unchanged"])
    ):
        raise ValueError(
            f"{where}: 'unchanged' must be a list of paths relative to the project, "
            f"like 'tests/test_dates.py'; got {expect['unchanged']!r}"
        )
    setup = raw.get("setup") or ""
    if setup and setup not in SETUPS:
        raise ValueError(
            f"{where}: unknown setup {setup!r}. src/gaia/eval/task_setups.py "
            f"defines: {sorted(SETUPS)}"
        )
    points = tuple(raw.get("must_establish") or ())
    if check == "mechanical" and not expect:
        raise ValueError(f"{where}: a mechanical task needs an 'expect' block")
    if check == "stated" and expect:
        raise ValueError(
            f"{where}: a stated task is decided by the judge, so its 'expect' "
            "block would never run. Make it a mechanical task, or drop the block."
        )
    wrong = tuple(raw.get("wrong_answers") or ())
    if check == "stated" and not (points and all(points)):
        raise ValueError(f"{where}: a stated task needs 'must_establish'")
    if check == "stated" and not (raw.get("genuine_answer") and wrong):
        raise ValueError(
            f"{where}: a stated task needs a 'genuine_answer' that passes and "
            "'wrong_answers' that fail, so the judge is checked both ways"
        )
    return Task(
        id=raw["id"],
        check=check,
        prompt=raw["prompt"],
        max_steps=int(raw["max_steps"]),
        expect=expect,
        must_establish=points,
        genuine_answer=raw.get("genuine_answer", ""),
        wrong_answers=wrong,
        setup=setup,
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
    if not suites[name]:
        raise ValueError(f"suite {name!r} in {tasks_file} has no tasks")
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


def _unchanged(task: Task, workdir: Path, baseline: Path) -> Tuple[bool, str]:
    """Every ``unchanged`` file still byte-identical to the project the agent got."""
    paths = task.expect.get("unchanged") or []
    for rel in paths:
        before, after = baseline / rel, workdir / rel
        if not before.is_file():
            raise ValueError(
                f"task {task.id!r}: 'unchanged' names {rel}, which is not in the "
                "project the agent was given. Fix the path in eval/tasks/tasks.json."
            )
        if not after.is_file():
            return False, f"{rel} was deleted"
        if after.read_bytes() != before.read_bytes():
            return False, f"{rel} was changed"
    return True, f"{len(paths)} file(s) untouched"


def evaluate(task: Task, workdir: Path, baseline: Path) -> Tuple[bool, str]:
    """Check the finished *workdir* against the task; *baseline* is how it started."""
    notes: List[str] = []
    if "unchanged" in task.expect:
        ok, note = _unchanged(task, workdir, baseline)
        if not ok:
            return False, note
        notes.append(note)
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


def score(task: Task, workdir: Path, baseline: Path) -> Tuple[Optional[bool], str]:
    """A coding task is decided here; a question is decided by the judge."""
    if task.check == "stated":
        return None, "decided by the judge"
    return evaluate(task, workdir, baseline)


def prepare_workdir(task: Task, root: Path) -> Tuple[Path, Path]:
    """Copy the fixture to ``root/toybox`` and apply the task's setup.

    Returns ``(workdir, baseline)``: *baseline* is a copy of the workdir as the
    agent will find it, kept beside it rather than inside it. ``unchanged`` and
    the judge's diff compare with it, so a setup's files are never mistaken
    for the agent's work.
    """
    workdir, baseline = root / "toybox", root / "baseline"
    shutil.copytree(FIXTURE, workdir, ignore=shutil.ignore_patterns(*IGNORED))
    if task.setup:
        SETUPS[task.setup](workdir)
    shutil.copytree(workdir, baseline, ignore=shutil.ignore_patterns(*IGNORED))
    return workdir, baseline


#: Tools that write a file. A test run verifies only the edits made before it.
EDIT_TOOLS = frozenset(
    {
        "write_file",
        "write_python_file",
        "write_markdown_file",
        "edit_file",
        "edit_python_file",
        "replace_function",
        "update_gaia_md",
    }
)

#: pytest's closing summary when a test failed. A snippet or a pipe that prints
#: it can still exit 0, so the exit code alone would call the run a pass.
_FAILED_SUMMARY = re.compile(
    r"(?m)^[= ]*(?:\d+ [a-z]+, )*[1-9]\d* (?:failed|errors?)\b.* in \d+(?:\.\d+)?s\b"
)


def _tool_result(content: Any) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except ValueError:
            return content
    return content


def _test_run_passed(result: Any) -> bool:
    if Agent._is_error_result(result):
        return False
    if not isinstance(result, dict):
        return True
    output = "\n".join(str(result.get(key) or "") for key in ("stdout", "stderr"))
    return not _FAILED_SUMMARY.search(output)


def tests_verified(conversation: List[Mapping[str, Any]]) -> bool:
    """True when a pytest run passed after the agent's last file edit.

    Read from the agent's own tool record, with the check detection its
    verification line uses, so ``python -m pytest`` and pytest run through
    ``run_python`` both count. A command run more than once counts by its
    latest run. Edits made through a shell command or a snippet are not seen.
    """
    latest: Dict[str, bool] = {}
    for entry in conversation:
        if entry.get("role") != "tool":
            continue
        name, args = str(entry.get("name") or ""), entry.get("tool_args")
        result = _tool_result(entry.get("content"))
        if not check_was_executed(result):
            continue
        if name in EDIT_TOOLS:
            if not Agent._is_error_result(result):
                latest.clear()
        elif verification_check_label(name, args, result) == "pytest":
            latest[verification_check_target(name, args)] = _test_run_passed(result)
    return any(latest.values())


def _files(root: Path) -> set:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not set(path.relative_to(root).parts) & set(IGNORED)
    }


def project_snapshot(root: Path = FIXTURE) -> str:
    """The project's files, for a judge with no tools."""
    text = "\n".join(
        f"--- {rel} ---\n{(root / rel).read_text('utf-8', 'replace')}"
        for rel in sorted(_files(root))
    )
    if len(text) > PROJECT_CAP:
        return text[:PROJECT_CAP] + "\n...[project truncated]"
    return text


def workspace_diff(workdir: Path, original: Path) -> str:
    """A unified diff from *original* to *workdir*, capped for the judge."""
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
    check: str = ""
    passed: Optional[bool] = False  # None: a question the judge has yet to decide
    #: A pytest run passed after the agent's last file edit (``tests_verified``).
    verified: bool = False
    why: str = ""
    error: str = ""
    error_kind: str = ""  # "unavailable": not measured; "failed": the task failed
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
) -> Tuple[Dict[str, Any], str, str]:
    """Run the flagship once; return (outcome, error, error_kind).

    A crash is a failed task, not a failed eval. ``error_kind`` is
    ``"unavailable"`` when the model backend could not be reached at all: that
    task was not measured, and says nothing about the agent.
    """
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
    agent, outcome, error, kind = None, {}, "", ""
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
        kind = "unavailable" if isinstance(exc, ConnectionError) else "failed"
    finally:
        os.chdir(previous_cwd)
        if previous_db is None:
            os.environ.pop("GAIA_MEMORY_DB", None)
        else:
            os.environ["GAIA_MEMORY_DB"] = previous_db
    history = [
        entry
        for entry in (getattr(agent, "error_history", None) or [])
        if isinstance(entry, dict)
    ]
    unreachable = [e for e in history if e.get("type") == _BACKEND_UNREACHABLE]
    failed = [e for e in history if e.get("type") in _BACKEND_FAILURES]
    if unreachable and not error:
        error = (
            f"model backend unreachable: {str(unreachable[-1].get('error', ''))[:300]}"
        )
        kind = "unavailable"
    elif failed and not error:
        error = f"model backend failed: {str(failed[-1].get('error', ''))[:300]}"
        kind = "failed"
    return outcome, error, kind


def run_task(task: Task, model: str, task_dir: Path) -> TaskResult:
    """Give the flagship one task in a fresh project copy, then score it."""
    task_dir.mkdir(parents=True, exist_ok=True)
    result = TaskResult(id=task.id, check=task.check)
    with tempfile.TemporaryDirectory(
        prefix=f"gaia-task-{task.id}-", ignore_cleanup_errors=True
    ) as tmp:
        # Resolved, so the path in the prompt is the agent's real working dir.
        root = Path(tmp).resolve()
        workdir, baseline = prepare_workdir(task, root)
        try:
            prompt = f"You are working in {workdir}. {task.prompt}"
            started = time.time()
            outcome, error, result.error_kind = _run_agent(
                prompt, model, task.max_steps, workdir, root / "memory.db"
            )
            result.wall_seconds = round(time.time() - started, 1)
            conversation = outcome.get("conversation") or []
            answer = str(outcome.get("result") or "")
            result.steps = int(outcome.get("steps_taken") or 0)
            result.tool_calls = sum(1 for m in conversation if m.get("role") == "tool")
            result.input_tokens = int(outcome.get("input_tokens") or 0)
            result.output_tokens = int(outcome.get("output_tokens") or 0)
            result.verified = tests_verified(conversation)
            if error:
                result.error = result.why = error
            else:
                result.passed, result.why = score(task, workdir, baseline)
            (task_dir / "transcript.json").write_text(
                json.dumps(
                    {"prompt": prompt, "answer": answer, "conversation": conversation},
                    indent=1,
                    default=str,
                ),
                encoding="utf-8",
            )
            (task_dir / "workspace.diff").write_text(
                workspace_diff(workdir, baseline), encoding="utf-8"
            )
            if task.setup:
                (task_dir / "setup.diff").write_text(
                    workspace_diff(baseline, FIXTURE), encoding="utf-8"
                )
        finally:
            remove_leftovers(workdir)
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
# The judge: quality for every task, and pass or fail for questions only
# ---------------------------------------------------------------------------

RUBRIC = """You are grading attempts by an autonomous coding agent. Each attempt
below is independent: grade each on its own merits and never compare them.

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

You are given the original project and, per attempt, a diff of what the agent
changed. Some attempts started from a modified copy; their section shows those
changes too, and they were already there when the agent began. Check each
answer's claims against all of it.

An attempt marked QUESTION also needs a verdict: it is correct only if it
establishes every one of its required points, in any wording. Its reference
answer is one correct answer; the agent need not match its wording.

Return STRICT JSON and nothing else: one object keyed by attempt id, e.g.
{"a1": {"instruction_compliance": N, "work_quality": N, "reasoning": N,
        "fabrication_free": N, "one_line": "<=25 words on the deciding factor"},
 "a2": {..., "answers_correctly": true, "missing": ""}}
Add "answers_correctly" (true or false) and "missing" (the first required point
it fails to establish, or "") only for QUESTION attempts.
"""


class JudgeError(RuntimeError):
    """The judge ran but did not return a usable grade."""


@dataclass(frozen=True)
class Attempt:
    """One answer for the judge: what was asked, what came back, what changed."""

    key: str
    prompt: str
    answer: str
    diff: str
    task: Optional[Task] = None
    #: How the project this attempt started from differs from the original.
    setup_diff: str = ""

    @property
    def question(self) -> bool:
        return self.task is not None and self.task.check == "stated"


def _attempt_section(attempt: Attempt) -> str:
    kind = "QUESTION" if attempt.question else "TASK"
    parts = [
        f"=== ATTEMPT {attempt.key} ({kind}) ===",
        f"Task given to the agent:\n{attempt.prompt}",
    ]
    if attempt.setup_diff:
        parts.append(
            "Before the agent started, its copy of the project was changed like "
            f"this (not the agent's work):\n{attempt.setup_diff}"
        )
    if attempt.question:
        points = "\n".join(f"- {p}" for p in attempt.task.must_establish)
        parts += [
            f"Required points:\n{points}",
            f"Reference answer:\n{attempt.task.genuine_answer}",
        ]
    parts += [
        f"The agent's final answer:\n{attempt.answer[:ANSWER_CAP]}",
        f"Changes it made to the workspace (diff vs the project it was given):\n{attempt.diff}",
    ]
    return "\n\n".join(parts)


def _validate_grade(raw: Any, question: bool) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise JudgeError(f"grade is not an object: {raw!r}"[:200])
    grade: Dict[str, Any] = {}
    for axis in AXES:
        value = raw.get(axis)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise JudgeError(f"{axis} must be an integer 1-5, got {value!r}")
        grade[axis] = value
    if question:
        verdict = raw.get("answers_correctly")
        if not isinstance(verdict, bool):
            raise JudgeError(
                f"answers_correctly must be true or false, got {verdict!r}"
            )
        grade["answers_correctly"] = verdict
        grade["missing"] = str(raw.get("missing") or "")[:300]
    grade["one_line"] = str(raw.get("one_line", ""))[:300]
    return grade


def parse_judgement(stdout: str, attempts: List[Attempt]) -> Dict[str, Dict[str, Any]]:
    """Read one batched ``claude -p`` reply into a grade (or error) per attempt."""
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
        grades = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JudgeError(f"unparseable grades: {text[start:end + 1][:200]!r}") from exc
    if not isinstance(grades, dict):
        raise JudgeError("the judge's answer is not an object keyed by attempt id")
    share = (envelope.get("total_cost_usd") or 0) / max(len(attempts), 1)
    results: Dict[str, Dict[str, Any]] = {}
    for attempt in attempts:
        try:
            grade = _validate_grade(grades.get(attempt.key), attempt.question)
            grade["cost_usd"] = share
            results[attempt.key] = grade
        except JudgeError as exc:
            results[attempt.key] = {"error": str(exc)}
    return results


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


def judge_batch(
    attempts: List[Attempt], model: str, env: Mapping[str, str]
) -> Dict[str, Dict[str, Any]]:
    """Grade every attempt in one judge call; the project is sent once."""
    payload = "\n\n".join(
        [
            RUBRIC,
            f"=== THE ORIGINAL PROJECT ===\n{project_snapshot()}",
            *(_attempt_section(a) for a in attempts),
        ]
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
    return parse_judgement(proc.stdout, attempts)


def judge_run(
    run_dir: Path,
    model: str,
    env: Mapping[str, str],
    attempts: int = 1,
    on_progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    tasks_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Grade every task in *run_dir* in one call; a failed grade is recorded.

    ``attempts`` tries the tasks still without a grade again. A question's pass
    or fail is the judge's verdict; without one it stays undecided.
    """
    card = read_scorecard(run_dir)
    tasks = {t.id: t for t in load_suite(card["suite"], tasks_file)}
    pending = []
    for entry in card["tasks"]:
        task, task_dir = tasks[entry["id"]], run_dir / entry["id"]
        transcript = json.loads(
            (task_dir / "transcript.json").read_text(encoding="utf-8")
        )
        pending.append(
            Attempt(
                entry["id"],
                transcript["prompt"],
                transcript["answer"],
                (task_dir / "workspace.diff").read_text(encoding="utf-8"),
                task,
                (
                    (task_dir / "setup.diff").read_text(encoding="utf-8")
                    if task.setup
                    else ""
                ),
            )
        )
    grades: Dict[str, Dict[str, Any]] = {}
    for _ in range(attempts):
        if not pending:
            break
        try:
            batch = judge_batch(pending, model, env)
        except (JudgeError, subprocess.TimeoutExpired) as exc:
            batch = {
                a.key: {"error": f"{type(exc).__name__}: {exc}"[:300]} for a in pending
            }
        grades.update(batch)
        pending = [a for a in pending if "error" in grades[a.key]]
    for entry in card["tasks"]:
        entry["judge"] = grades[entry["id"]]
        verdict = entry["judge"].get("answers_correctly")
        if (
            tasks[entry["id"]].check == "stated"
            and verdict is not None
            and not entry.get("error")
        ):
            entry["passed"] = verdict
            missing = entry["judge"].get("missing")
            entry["why"] = (
                "judge: correct" if verdict else f"judge: missing {missing!r}"
            )
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
    coding = [t for t in tasks if t.get("check") == "mechanical"]
    return {
        "tasks": len(tasks),
        "passed": sum(1 for t in tasks if t["passed"] is True),
        "coding": len(coding),
        "verified": sum(1 for t in coding if t.get("verified") is True),
        "errors": sum(1 for t in tasks if t.get("error")),
        "unmeasured": sum(1 for t in tasks if t.get("error_kind") == "unavailable"),
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
    gated: bool = True  # False: reported only, until expectations set a limit


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
    min_verified = expected.get("min_verified")
    return [
        GateCheck(
            "Tasks measured",
            f"{s['tasks'] - s['unmeasured']}/{s['tasks']}",
            "all",
            s["unmeasured"] == 0,
        ),
        GateCheck(
            "Tasks passed",
            f"{s['passed']}/{s['tasks']}",
            f">= {expected['min_passed']}",
            s["passed"] >= expected["min_passed"],
            None if main.get("passed") is None else f"{main['passed']}/{s['tasks']}",
        ),
        GateCheck(
            "Changes verified by a test run",
            f"{s['verified']}/{s['coding']}",
            "not gated yet" if min_verified is None else f">= {min_verified}",
            min_verified is None or s["verified"] >= min_verified,
            (
                None
                if main.get("verified") is None
                else f"{main['verified']}/{s['coding']}"
            ),
            gated=min_verified is not None,
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
        "result": (
            "NOT MEASURED"
            if t.get("error_kind") == "unavailable"
            else (
                "ERROR"
                if t.get("error")
                else (
                    "AWAITING JUDGE"
                    if t["passed"] is None
                    else "PASS" if t["passed"] else "FAIL"
                )
            )
        ),
        "verified": t.get("verified"),
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
    if s["unmeasured"]:
        raise ValueError(
            f"{s['unmeasured']} task(s) were not measured (the model backend was "
            "unreachable); re-run before proposing expectations from this run."
        )
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
                "verified",
                "quality",
                "misreported",
                "total_tokens",
                "steps",
                "wall_seconds",
            )
        },
        "tasks": [_task_row(t) for t in card["tasks"]],
        "min_passed": max(0, s["passed"] - PASS_SLACK),
        "min_verified": max(0, s["verified"] - VERIFIED_SLACK),
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


def _yes_no(value: Optional[bool]) -> Optional[str]:
    return None if value is None else "yes" if value else "no"


def _mark(check: GateCheck) -> str:
    if not check.gated:
        return "—"
    return "✅" if check.ok else "❌"


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
        f"{s['passed']}/{s['tasks']} passed · {s['verified']}/{s['coding']} changes "
        f"verified by a test run · quality "
        f"{'—' if s['quality'] is None else s['quality']} · "
        f"{s['total_tokens']:,} tokens ({s['input_tokens']:,} in, "
        f"{s['output_tokens']:,} out) · {s['steps']} steps · {s['wall_seconds']}s",
        "",
    ]
    if checks is not None:
        lines += ["| Metric | Main | This run | Limit | |", "|---|---|---|---|---|"]
        lines += [
            f"| {c.metric} | {'—' if c.main is None else _fmt(c.main)} | "
            f"{_fmt(c.actual)} | {c.expected} | {_mark(c)} |"
            for c in checks
        ]
        lines.append("")
    lines += [
        "| Task | Result | Verified | Steps | Tokens | Seconds | Quality | Why |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in card["tasks"]:
        row, main = _task_row(t), main_tasks.get(t["id"], {})
        quality = row["quality"]
        if quality is None:
            quality = "judge failed" if (t.get("judge") or {}).get("error") else "—"
        cells = [
            _vs(row["result"], main.get("result")),
            _vs(_yes_no(row["verified"]) or "—", _yes_no(main.get("verified"))),
            _vs(row["steps"], main.get("steps")),
            _vs(row["total_tokens"], main.get("total_tokens")),
            _vs(row["wall_seconds"], main.get("wall_seconds")),
            _vs(quality, main.get("quality")),
        ]
        lines.append(
            f"| `{t['id']}` | " + " | ".join(cells) + f" | {str(t['why'])[:120]} |"
        )
    return "\n".join(lines) + "\n"
