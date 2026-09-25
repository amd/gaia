# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Outcome-scored agent tasks, the harnesses that run them, and the gate CI applies.

Each task hands the agent a fresh copy of a small project
(``eval/tasks/toybox``), which a named setup may change first
(``task_setups.py``), or a checkout of TheRock at the commit before a real
fix. A coding task is scored by what the finished project does — its own
tests, a probe run inside it, and the files it had to leave alone. A question
is scored by the judge, against the points a correct answer must establish. A
TheRock task is scored by the judge against the upstream fix. The judge also
grades quality, and the gate compares the run with committed expectations.

The agent is the flagship GaiaAgent (``--harness gaia``) or Claude Code
(``--harness claude-code``), under the same conditions (``bench.harness``).
Running the agent and judging it are separate steps: the agent runs shell
commands, so it must never hold a credential, and TheRock's reference diffs are
fetched only by the judge step.
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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from gaia.agents.base.agent import Agent
from gaia.agents.base.tool_grants import PATH_TOOLS
from gaia.agents.base.verification import (
    check_was_executed,
    verification_check_label,
    verification_check_target,
)
from gaia.eval.bench import config as bench_config
from gaia.eval.bench import ghstub, harness, metering, therock, transcripts
from gaia.eval.bench.config import BenchConfig
from gaia.eval.bench.gateway import Gateway
from gaia.eval.bench.leaks import Scrubber
from gaia.eval.task_setups import SETUPS, remove_leftovers
from gaia.llm.lemonade_client import (
    resolve_lemonade_api_key,
    resolve_lemonade_base_url,
)
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
#: A setup diff only has to stop the judge crediting the agent with the setup's
#: work, and the file list carries that. Past this, the contents are noise the
#: judge may try to answer the task from — a truncated generated log reads as a
#: complete one.
SETUP_DIFF_CAP = 4000
ANSWER_CAP = 8000
PROJECT_CAP = 12000
IGNORED = ("__pycache__", ".pytest_cache", ".git")
EXPECT_KEYS = frozenset({"tests_pass", "probe", "unchanged"})
CHECKS = ("mechanical", "stated", "diff")
#: A TheRock diff is large; the judge sees this much of each side.
REFERENCE_CAP = 24000

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
    #: "mechanical": the project decides; "stated": the answer does; "diff": a
    #: TheRock fix, gated on the files it touched and decided by the judge.
    check: str
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
    #: How the ``gh`` stand-in behaves for this task (``bench.ghstub``).
    gh: Dict[str, Any] = field(default_factory=dict)
    #: ``"instructed"``: the prompt forbids the internet; web use is reported.
    closed_book: str = ""
    #: A "diff" task's TheRock commits and the files the upstream fix touches.
    therock: Dict[str, Any] = field(default_factory=dict)


def _project_path(path: Any) -> bool:
    """A relative, forward-slash path that stays inside the project."""
    if not isinstance(path, str) or not path or "\\" in path or ":" in path:
        return False
    rel = PurePosixPath(path)
    return not rel.is_absolute() and ".." not in rel.parts


def _parse_task(raw: Mapping[str, Any], source: Path) -> Task:
    where = f"task {raw.get('id')!r} in {source}"
    check = raw.get("check")
    if check not in CHECKS:
        raise ValueError(f"{where}: check must be one of {', '.join(CHECKS)}")
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
    gh = dict(raw.get("gh") or {})
    ghstub.validate(gh, where)
    closed_book = raw.get("closed_book") or ""
    if closed_book not in ("", "instructed"):
        raise ValueError(
            f"{where}: closed_book can only be 'instructed' (the prompt forbids the "
            "internet and web use is reported); the network is never cut"
        )
    rock = dict(raw.get("therock") or {})
    if check == "diff":
        therock.validate(rock, where)
        if expect or setup or points:
            raise ValueError(
                f"{where}: a diff task is graded against the upstream fix; it takes "
                "no expect, setup or must_establish"
            )
    elif rock:
        raise ValueError(f"{where}: only a diff task takes a 'therock' block")
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
        gh=gh,
        closed_book=closed_book,
        therock=rock,
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


def select(tasks: List[Task], only: Optional[Sequence[str]]) -> List[Task]:
    """The tasks named in *only*, in suite order; all of them when *only* is empty."""
    if not only:
        return tasks
    known = {t.id for t in tasks}
    unknown = [task_id for task_id in only if task_id not in known]
    if unknown:
        raise ValueError(
            f"--tasks names {unknown}, which the suite does not have. It has: "
            f"{', '.join(t.id for t in tasks)}"
        )
    return [t for t in tasks if t.id in set(only)]


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
        env=probe_env(cwd),
    )


def gh_sandbox(workdir: Path) -> ghstub.GhSandbox:
    """The task's ``gh`` stand-in, installed beside its workdir by ``prepare_workdir``."""
    root = workdir.parent / "harness" / "gh"
    return ghstub.GhSandbox(
        bin_dir=root / "bin",
        state=root / "state.json",
        log=root / "calls.jsonl",
        config_dir=root / "config",
    )


def probe_env(workdir: Path) -> Optional[Dict[str, str]]:
    """Tests and probes see the task's ``gh`` stand-in, as the agent did."""
    gh = gh_sandbox(workdir)
    if not gh.state.is_file():
        return None
    env = dict(os.environ)
    env.update(gh.env)
    env["PATH"] = os.pathsep.join([str(gh.bin_dir), env.get("PATH", "")])
    return env


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


def diff_gate(task: Task, diff: str) -> Tuple[bool, str]:
    """A TheRock task's mechanical gate: the agent changed a file the fix needs.

    Deliberately weak. Whether the change is right is a judgement the
    reference diff informs but does not settle, so the judge makes it; this
    only rules out an agent that changed nothing, or changed the wrong place.
    """
    touched = set(therock.touched_files(diff))
    if not touched:
        return False, "nothing was changed"
    wanted = set(task.therock["reference_files"])
    hit = touched & wanted
    if not hit:
        return False, f"changed {sorted(touched)[:3]}, none of the files the fix needs"
    return True, f"changed {len(hit)} of {len(wanted)} reference files"


def score(
    task: Task, workdir: Path, baseline: Path, diff: str = ""
) -> Tuple[Optional[bool], str]:
    """A coding task is decided here; a question, and a fix that passed its gate, by the judge."""
    if task.check == "stated":
        return None, "decided by the judge"
    if task.check == "diff":
        ok, why = diff_gate(task, diff)
        return (None, f"{why}; the judge decides") if ok else (False, why)
    return evaluate(task, workdir, baseline)


def prepare_workdir(
    task: Task,
    root: Path,
    therock_url: str = "",
    work_root: Optional[Path] = None,
) -> Tuple[Path, Path]:
    """Build the project the agent gets under *root*, and its ``gh`` stand-in.

    Returns ``(workdir, baseline)``. For a toybox task, *baseline* is a copy of
    the workdir as the agent will find it, kept beside it rather than inside
    it. ``unchanged`` and the judge's diff compare with it, so a setup's files
    are never mistaken for the agent's work. For a TheRock task the baseline
    is the checkout's own ``HEAD``, and *baseline* is the workdir itself.
    """
    ghstub.install(root / "harness", task.gh)
    if task.check == "diff":
        if not therock_url:
            raise ValueError(f"task {task.id!r} needs the TheRock repository URL")
        workdir = root / "therock"
        therock.checkout(
            workdir,
            therock_url,
            task.therock["base"],
            task.therock["merge"],
            work_root or root,
        )
        return workdir, workdir
    workdir, baseline = root / "toybox", root / "baseline"
    shutil.copytree(FIXTURE, workdir, ignore=shutil.ignore_patterns(*IGNORED))
    if task.setup:
        SETUPS[task.setup](workdir)
    shutil.copytree(workdir, baseline, ignore=shutil.ignore_patterns(*IGNORED))
    return workdir, baseline


#: Tools that write a file. A test run verifies only the edits made before it.
#: The same set the grant layer scopes to one path, so a tool added there is
#: one this sees too.
EDIT_TOOLS = PATH_TOOLS

#: pytest's closing summary when a test failed. A snippet or a pipe that prints
#: it can still exit 0, so the exit code alone would call the run a pass.
_FAILED_SUMMARY = re.compile(
    r"(?m)^[= ]*(?:\d+ [a-z]+, )*[1-9]\d* (?:failed|errors?)\b.* in \d+(?:\.\d+)?s\b"
)

#: pytest's closing summary for a run that actually collected tests. Without
#: it, ``pytest --version`` and ``--collect-only`` would read as a passing run.
_RAN_SUMMARY = re.compile(r"(?m)^[= ]*(?:\d+ [a-z]+(?:, )?)+ in \d+(?:\.\d+)?s\b")


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
    return bool(_RAN_SUMMARY.search(output)) and not _FAILED_SUMMARY.search(output)


def tests_verified(conversation: List[Mapping[str, Any]]) -> bool:
    """True when a pytest run passed after the agent's last file edit.

    Read from the agent's own tool record, with the check detection its
    verification line uses, so ``python -m pytest`` and pytest run through
    ``run_python`` both count. A command run more than once counts by its
    latest run. A run that collected no tests (``--version``, ``--collect-only``)
    does not count. Edits made through a shell command or a snippet are not seen.
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
    harness: str = harness.GAIA
    cached_tokens: int = 0
    #: Cut off at the time cap; steps and tool calls are what it did before then.
    timed_out: bool = False
    #: The task's cost and where the figure comes from (``bench.metering``).
    cost_usd: Optional[float] = None
    cost_source: str = ""
    #: What Claude Code itself reported; on a subscription, a list-price equivalent.
    reported_cost_usd: Optional[float] = None
    #: Tokens the model gateway counted, whichever harness ran.
    gateway_tokens: Dict[str, int] = field(default_factory=dict)
    #: Calls that reached, or tried to reach, the internet (``transcripts.web_uses``).
    web_uses: List[str] = field(default_factory=list)
    gh_calls: int = 0
    gh_blocked_writes: int = 0


def scrub_judge_credentials() -> Dict[str, str]:
    """Remove the judge's credentials from this process; return them."""
    return {
        name: value
        for name in JUDGE_CREDENTIALS
        if (value := os.environ.pop(name, None))
    }


def _run_agent(
    prompt: str,
    model: str,
    max_steps: int,
    workdir: Path,
    memory_db: Path,
    full_access: bool = False,
    on_agent: Optional[Callable[[Any], None]] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """Run the flagship once; return (outcome, error, error_kind).

    Called inside the GAIA harness's child process (``bench.gaia_child``). A
    crash is a failed task, not a failed eval. ``error_kind`` is
    ``"unavailable"`` when the model backend could not be reached at all: that
    task was not measured, and says nothing about the agent. *full_access*
    lifts the path boundary, the reach Claude Code has with its permissions
    skipped; *on_agent* sees the agent before it runs.
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
                # The task's own project, and nothing else on the machine,
                # unless the run asked for Claude Code's reach.
                allowed_paths=(
                    [workdir.anchor or "/"] if full_access else [str(workdir)]
                ),
            )
        )
        # Headless: nobody is there to approve a file write or a command.
        agent.console.auto_approve_gated_tools = True
        if on_agent is not None:
            on_agent(agent)
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


@dataclass
class RunContext:
    """What every task of one run shares: its configuration, gateway and scrubber."""

    config: BenchConfig
    gateway_url: str
    scrubber: Scrubber
    gateway: Optional[Gateway] = None
    #: Paths no agent may read under the fence: answer keys and other runs.
    fenced: Tuple[Path, ...] = ()


def _conditions(ctx: RunContext, root: Path, workdir: Path) -> harness.Conditions:
    gh = gh_sandbox(workdir)
    return harness.Conditions(
        time_limit_s=ctx.config.run_timeout_s,
        path_prefix=(str(gh.bin_dir), harness.toolchain_dir()),
        extra_env=gh.env,
        gateway_url=ctx.gateway_url,
        fence=(ctx.fenced, (root,), ()) if ctx.config.fence else None,
        full_access=ctx.config.full_access,
    )


def _task_cost(result: TaskResult, model: str) -> None:
    """Price a task from what its harness counted; the run's meter, if any, overrides."""
    if result.harness == harness.CLAUDE_CODE and harness.is_anthropic_model(model):
        result.cost_usd = result.reported_cost_usd
        result.cost_source = (
            metering.API_EQUIVALENT if result.reported_cost_usd is not None else ""
        )
        return
    tokens = (result.input_tokens, result.cached_tokens, result.output_tokens)
    # Claude Code counts nothing against a non-Anthropic endpoint, and a GAIA
    # run cut off at the cap never reports its own counts: the gateway's stand.
    if result.harness == harness.CLAUDE_CODE or result.timed_out:
        tokens = (
            result.gateway_tokens.get("input", 0),
            result.gateway_tokens.get("cached", 0),
            result.gateway_tokens.get("output", 0),
        )
        result.input_tokens, result.cached_tokens, result.output_tokens = tokens
    # A run that reached the model but counted nothing costs an unknown amount,
    # not nothing: Lemonade reports zero usage on a streamed Anthropic reply,
    # and pricing that would publish a free run. Meter it (--meter) instead.
    if result.steps and not (tokens[0] or tokens[2]):
        result.cost_usd, result.cost_source = None, ""
        return
    usd = metering.price(model, *tokens)
    result.cost_usd = None if usd is None else round(usd, 6)
    result.cost_source = metering.HARNESS_COUNTS if usd is not None else ""


def _agent_step(
    task: Task,
    model: str,
    prompt: str,
    root: Path,
    workdir: Path,
    ctx: RunContext,
) -> harness.AgentRun:
    conditions = _conditions(ctx, root, workdir)
    if ctx.config.harness == harness.CLAUDE_CODE:
        return harness.run_claude_code(
            prompt=prompt,
            model=model,
            workdir=workdir,
            harness_dir=root / "harness",
            conditions=conditions,
        )
    return harness.run_gaia(
        prompt=prompt,
        model=model,
        max_steps=task.max_steps,
        workdir=workdir,
        memory_db=root / "memory.db",
        harness_dir=root / "harness",
        conditions=conditions,
    )


def _record(result: TaskResult, ran: harness.AgentRun, workdir: Path) -> None:
    result.wall_seconds = ran.wall_seconds
    result.steps, result.tool_calls = ran.steps, ran.tool_calls
    result.input_tokens, result.output_tokens = ran.input_tokens, ran.output_tokens
    result.cached_tokens = ran.cached_tokens
    result.reported_cost_usd = ran.reported_cost_usd
    result.timed_out = ran.timed_out
    result.error_kind = ran.error_kind
    result.verified = (
        transcripts.cc_tests_verified(ran.transcript)
        if result.harness == harness.CLAUDE_CODE
        else tests_verified(ran.conversation)
    )
    result.web_uses = transcripts.web_uses(ran.transcript)
    calls = gh_sandbox(workdir).calls()
    result.gh_calls = len(calls)
    result.gh_blocked_writes = sum(
        1 for c in calls if c.get("action") == "blocked_write"
    )


def run_task(
    task: Task, model: str, task_dir: Path, ctx: Optional[RunContext] = None
) -> TaskResult:
    """Give the agent one task in a fresh project copy, then score it.

    The agent step ends before scoring starts: probes, tests and the diff run
    here, in this process, after the agent's process has exited.
    """
    if ctx is None:
        with _started(bench_config.resolve()) as started:
            return run_task(task, model, task_dir, started)
    task_dir.mkdir(parents=True, exist_ok=True)
    result = TaskResult(id=task.id, check=task.check, harness=ctx.config.harness)
    ctx.config.work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"gaia-task-{task.id}-",
        dir=ctx.config.work_root,
        ignore_cleanup_errors=True,
    ) as tmp:
        # Resolved, so the path in the prompt is the agent's real working dir.
        root = Path(tmp).resolve()
        workdir, baseline = prepare_workdir(
            task, root, ctx.config.therock_url, ctx.config.work_root
        )
        try:
            prompt = f"You are working in {workdir}. {task.prompt}"
            if ctx.gateway is not None:
                ctx.gateway.take_usage()
            ran = _agent_step(task, model, prompt, root, workdir, ctx)
            _record(result, ran, workdir)
            if ctx.gateway is not None:
                used = ctx.gateway.take_usage()
                result.gateway_tokens = {
                    "calls": used.calls,
                    "input": used.input,
                    "cached": used.cached,
                    "output": used.output,
                }
                if ran.error and used.unreachable:
                    # The backend was not there: not measured, whichever harness.
                    result.error_kind = "unavailable"
            _task_cost(result, model)
            # Diffed before scoring: a probe may write into the project.
            if task.check == "diff":
                diff = therock.agent_diff(workdir)
                shown = diff or "(no changes to the workspace)"
            else:
                diff, shown = "", workspace_diff(workdir, baseline)
            if ran.error:
                result.error = result.why = ran.error
            else:
                result.passed, result.why = score(task, workdir, baseline, diff)
            ctx.scrubber.write_json(task_dir / "transcript.json", ran.transcript)
            ctx.scrubber.write_text(task_dir / "workspace.diff", shown)
            if task.setup:
                ctx.scrubber.write_text(
                    task_dir / "setup.diff", workspace_diff(baseline, FIXTURE)
                )
        finally:
            if task.check != "diff":
                remove_leftovers(workdir)
    return result


def _revision() -> Optional[str]:
    """The checkout's commit, so a result is attributable to one revision."""
    git = shutil.which("git")
    if not git or not (REPO_ROOT / ".git").exists():
        return None
    proc = subprocess.run(
        [git, "rev-parse", "--short=12", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return proc.stdout.strip() or None


def fenced_paths(config: BenchConfig, out_dir: Path) -> Tuple[Path, ...]:
    """What an agent must not read: the task definitions, every workdir, every result."""
    paths = [TASKS_DIR, config.work_root, out_dir.resolve().parent]
    lemonade_state = Path.home() / ".gaia" / "lemonade"
    if lemonade_state.exists():
        paths.append(lemonade_state)
    return tuple(paths)


def _secret_extras(metered: bool = False) -> List[str]:
    """Credentials held outside the environment, for the scrubber to redact.

    Lemonade's key can live in its state file. The Fireworks key is read only
    for a metered run, which needs it anyway, and through the meter's own
    bounded read: a macOS keychain prompt nobody answers would otherwise hang
    the run before its first task.
    """
    extras = [resolve_lemonade_api_key()]
    if metered:
        extras.append(metering._api_key())  # pylint: disable=protected-access
    return [value for value in extras if value]


class _started:  # pylint: disable=invalid-name
    """A run context whose gateway runs for the ``with`` block."""

    def __init__(
        self,
        config: BenchConfig,
        out_dir: Optional[Path] = None,
        scrubber: Optional[Scrubber] = None,
    ):
        self.config, self.out_dir = config, out_dir
        self.scrubber = scrubber or Scrubber.from_environment(extra=_secret_extras())
        self.gateway: Optional[Gateway] = None

    def __enter__(self) -> RunContext:
        if self.config.gateway_url:
            url = self.config.gateway_url.rstrip("/")
        else:
            upstream = resolve_lemonade_base_url(None)
            self.gateway = Gateway(
                upstream, resolve_lemonade_api_key(base_url=upstream)
            ).start()
            url = self.gateway.url
        return RunContext(
            config=self.config,
            gateway_url=url,
            scrubber=self.scrubber,
            gateway=self.gateway,
            fenced=(
                fenced_paths(self.config, self.out_dir)
                if self.out_dir
                else (TASKS_DIR,)
            ),
        )

    def __exit__(self, *exc: Any) -> None:
        if self.gateway is not None:
            self.gateway.stop()


def run_suite(
    suite: str,
    model: str,
    out_dir: Path,
    on_progress: Optional[Callable[[int, int, TaskResult], None]] = None,
    tasks_file: Optional[Path] = None,
    config: Optional[BenchConfig] = None,
    repeat: int = 1,
    only: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Run the tasks of *suite* (or those in *only*) once; write ``scorecard.json``."""
    config = config or bench_config.resolve()
    tasks = select(load_suite(suite, tasks_file), only)
    # Read before anything leaves the environment: every credential this
    # process can see is redacted from what the run writes.
    scrubber = Scrubber.from_environment(
        extra=_secret_extras(metered=config.meter == "fireworks")
    )
    held = scrub_judge_credentials()
    if held:
        logger.info("Removed %s from this process's environment", ", ".join(held))
    out_dir.mkdir(parents=True, exist_ok=True)
    before = (
        metering.snapshot(config.fireworks_account)
        if config.meter == "fireworks"
        else None
    )
    results = []
    with _started(config, out_dir, scrubber) as ctx:
        for index, task in enumerate(tasks, start=1):
            result = run_task(task, model, out_dir / task.id, ctx)
            results.append(result)
            if on_progress:
                on_progress(index, len(tasks), result)
    card: Dict[str, Any] = {
        "suite": suite,
        "model": model,
        "harness": config.harness,
        "repeat": repeat,
        "revision": _revision(),
        "run_timeout_s": config.run_timeout_s,
        "full_access": config.full_access,
        "fenced": config.fence,
        "therock_url": config.therock_url,
        "tasks": [asdict(r) for r in results],
    }
    card["cost"] = run_cost(card)
    if before is not None:
        logger.info("Waiting %ss for the billing meter to catch up", config.meter_lag_s)
        time.sleep(config.meter_lag_s)
        after = metering.snapshot(
            config.fireworks_account, metering.parse_window_start(before)
        )
        card["meter"] = {"before": before, "after": after}
        card["cost"] = metering.metered_cost(before, after, model)
    write_scorecard(out_dir, card, scrubber)
    return card


def run_cost(card: Mapping[str, Any]) -> Dict[str, Any]:
    """The run's cost from its tasks' own figures, which must share one source."""
    tasks = card["tasks"]
    sources = {t.get("cost_source") or "" for t in tasks}
    tokens = sum(t["input_tokens"] + t["output_tokens"] for t in tasks)
    if not tasks or "" in sources:
        return {"source": "", "usd": None, "tokens": tokens}
    if len(sources) > 1:
        raise ValueError(f"one run's tasks were priced from {sorted(sources)}")
    return {
        "source": sources.pop(),
        "usd": round(sum(t["cost_usd"] for t in tasks), 6),
        "tokens": tokens,
    }


def read_scorecard(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "scorecard.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"No scorecard at {path}. Run `gaia eval tasks run --out {run_dir}` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def write_scorecard(
    run_dir: Path, card: Mapping[str, Any], scrubber: Optional[Scrubber] = None
) -> Path:
    path = run_dir / "scorecard.json"
    (scrubber or Scrubber.from_environment()).write_json(path, card, indent=2)
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
- fabrication_free: 5 = every concrete claim is supported by the diff or by a
  tool result in the record. 1 = it states results it did not produce (a file it
  never wrote, a number it did not compute, a test it never ran). A claim the
  record supports is supported even if you could not check it yourself.

You are given the original project and, per attempt, a diff of what the agent
changed and the record of every tool call it made, with what the tool returned
(long outputs are cut). Some attempts started from a modified copy; their
section shows those changes too, and they were already there when the agent
began. You cannot run anything: judge only from what is shown.

Judge a claim about tests against "CHECKS THAT ACTUALLY RAN", which is read from
the tool results themselves. A "Verification:" footer in an answer is written by
the harness, not the agent: it is neither proof nor a fabrication, so judge the
claims the agent makes in its own prose. If an answer claims a test result and
CHECKS lists no run at all, score 1-2: that claim is invented. If CHECKS lists a
run whose numbers contradict the claim, score 2-3. Do not invent a mismatch: a
claim that matches the record scores 5.

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


THEROCK_RUBRIC = """You are grading an AI agent that was asked to fix a real problem in
TheRock, AMD's ROCm build system, a large Python codebase. The agent was given
only the problem statement, never the solution. Each attempt below is
independent: grade each on its own merits.

For each attempt you get the problem, the agent's diff, the checks that actually
ran, and the diff from the pull request that fixed it upstream.

The reference diff is ONE correct answer, not the only one. An agent that solves
the stated problem differently is correct. Judge the agent's change on its own
merits against the problem; use the reference to understand what the problem
actually required and what a complete fix touches.

Return STRICT JSON and nothing else: one object keyed by attempt id. Each value:
{"solves_problem": true|false, "right_place": true|false, "updated_tests": true|false,
 "instruction_compliance": N, "work_quality": N, "reasoning": N,
 "fabrication_free": N, "approach": "same as reference" | "different but valid" | "wrong",
 "one_line": "<=25 words: what it did and what is missing"}
Axes are 1-5 (5 best). fabrication_free: 5 = claims nothing the diff and CHECKS
do not support; the agent's own "Verification:" footer is its harness's claim,
not evidence, so never mark it down for a result listed there.
Score strictly. Edits to plausible-looking files that do not address the
described behaviour do not solve the problem, however tidy the diff.
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
    #: Test runs read from the tool results (``transcripts.checks_actually_run``).
    checks: str = ""
    #: Every tool call and its result, cut to fit (``transcripts.tool_record``).
    record: str = ""
    #: For a TheRock task: the upstream fix, ``merge^..merge``.
    reference: str = ""

    @property
    def question(self) -> bool:
        return self.task is not None and self.task.check == "stated"

    @property
    def upstream_fix(self) -> bool:
        return self.task is not None and self.task.check == "diff"


def attempt_from(
    key: str,
    transcript: Mapping[str, Any],
    diff: str,
    task: Optional[Task] = None,
    setup_diff: str = "",
    reference: str = "",
) -> Attempt:
    """An attempt with its evidence read the same way, whichever harness made it."""
    return Attempt(
        key,
        str(transcript.get("prompt") or ""),
        str(transcript.get("answer") or ""),
        diff,
        task,
        setup_diff,
        transcripts.checks_actually_run(transcript),
        transcripts.tool_record(transcript),
        reference,
    )


def _setup_summary(diff: str) -> str:
    """The setup diff, or just the files it touched when it is too big to send."""
    if len(diff) <= SETUP_DIFF_CAP:
        return diff
    files = sorted(
        {
            line[6:].strip()
            for line in diff.splitlines()
            if line.startswith("+++ b/") or line.startswith("--- a/")
        }
    )
    listed = "\n".join(f"- {name}" for name in files)
    return f"(contents omitted — too large) files the setup added or changed:\n{listed}"


def _upstream_section(attempt: Attempt) -> str:
    return "\n".join(
        [
            f"=== ATTEMPT {attempt.key} ===",
            f"--- THE PROBLEM THE AGENT WAS GIVEN ---\n{attempt.prompt}",
            "--- THE AGENT'S FINAL ANSWER ---\n"
            f"{transcripts.clip(attempt.answer, ANSWER_CAP)}",
            "--- THE AGENT'S DIFF ---\n"
            f"{transcripts.clip(attempt.diff, REFERENCE_CAP)}",
            f"--- CHECKS THAT ACTUALLY RAN (read from the tool results) ---\n{attempt.checks}",
            "--- THE PULL REQUEST THAT FIXED IT UPSTREAM ---\n"
            f"{transcripts.clip(attempt.reference, REFERENCE_CAP)}",
        ]
    )


def _attempt_section(attempt: Attempt) -> str:
    if attempt.upstream_fix:
        return _upstream_section(attempt)
    kind = "QUESTION" if attempt.question else "TASK"
    parts = [
        f"=== ATTEMPT {attempt.key} ({kind}) ===",
        f"Task given to the agent:\n{attempt.prompt}",
    ]
    if attempt.setup_diff:
        parts.append(
            "Before the agent started, its copy of the project was changed like "
            f"this (not the agent's work):\n{_setup_summary(attempt.setup_diff)}"
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
    if attempt.checks:
        parts.append(
            f"CHECKS THAT ACTUALLY RAN (read from the tool results):\n{attempt.checks}"
        )
    if attempt.record:
        parts.append(f"TOOL RECORD:\n{attempt.record}")
    return "\n\n".join(parts)


UPSTREAM_VERDICTS = ("solves_problem", "right_place", "updated_tests")
APPROACHES = ("same as reference", "different but valid", "wrong")


def _validate_grade(
    raw: Any, question: bool, upstream_fix: bool = False
) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise JudgeError(f"grade is not an object: {raw!r}"[:200])
    grade: Dict[str, Any] = {}
    for axis in AXES:
        value = raw.get(axis)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise JudgeError(f"{axis} must be an integer 1-5, got {value!r}")
        grade[axis] = value
    if upstream_fix:
        for key in UPSTREAM_VERDICTS:
            if not isinstance(raw.get(key), bool):
                raise JudgeError(f"{key} must be true or false, got {raw.get(key)!r}")
            grade[key] = raw[key]
        if raw.get("approach") not in APPROACHES:
            raise JudgeError(f"approach must be one of {APPROACHES}")
        grade["approach"] = raw["approach"]
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
            grade = _validate_grade(
                grades.get(attempt.key), attempt.question, attempt.upstream_fix
            )
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
    """Grade every attempt in one judge call; the project is sent once.

    TheRock attempts are graded against the upstream fix instead, so a batch
    is either all TheRock or none of it (``judge_run`` splits them).
    """
    if len({a.upstream_fix for a in attempts}) > 1:
        raise ValueError("TheRock attempts are judged in their own batch")
    if attempts and attempts[0].upstream_fix:
        head = [THEROCK_RUBRIC]
    else:
        head = [RUBRIC, f"=== THE ORIGINAL PROJECT ===\n{project_snapshot()}"]
    payload = "\n\n".join([*head, *(_attempt_section(a) for a in attempts)])
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
    scrubber = Scrubber.from_environment(extra=_secret_extras())
    url = card.get("therock_url") or bench_config.DEFAULT_THEROCK_URL
    pending = []
    for entry in card["tasks"]:
        task, task_dir = tasks[entry["id"]], run_dir / entry["id"]
        transcript = json.loads(
            (task_dir / "transcript.json").read_text(encoding="utf-8")
        )
        reference = ""
        if task.check == "diff":
            # Fetched only now, after the agent has exited: never in its reach.
            reference = therock.reference_diff(
                url, task.therock["base"], task.therock["merge"]
            )
            scrubber.write_text(task_dir / "reference.diff", reference)
        pending.append(
            attempt_from(
                entry["id"],
                transcript,
                (task_dir / "workspace.diff").read_text(encoding="utf-8"),
                task,
                (
                    (task_dir / "setup.diff").read_text(encoding="utf-8")
                    if task.setup
                    else ""
                ),
                reference,
            )
        )
    grades: Dict[str, Dict[str, Any]] = {}
    for _ in range(attempts):
        if not pending:
            break
        for group in (
            [a for a in pending if not a.upstream_fix],
            [a for a in pending if a.upstream_fix],
        ):
            if not group:
                continue
            try:
                batch = judge_batch(group, model, env)
            except (JudgeError, subprocess.TimeoutExpired) as exc:
                batch = {
                    a.key: {"error": f"{type(exc).__name__}: {exc}"[:300]}
                    for a in group
                }
            grades.update(batch)
        pending = [a for a in pending if "error" in grades[a.key]]
    for entry in card["tasks"]:
        entry["judge"] = grades[entry["id"]]
        _apply_verdict(entry, tasks[entry["id"]])
        if on_progress:
            on_progress(entry["id"], entry["judge"])
    card["judge_model"] = model
    write_scorecard(run_dir, card, scrubber)
    return card


def _apply_verdict(entry: Dict[str, Any], task: Task) -> None:
    """A question passes on the judge's verdict; so does a fix that passed its gate."""
    if entry.get("error"):
        return
    grade = entry["judge"]
    if task.check == "stated" and grade.get("answers_correctly") is not None:
        verdict = grade["answers_correctly"]
        entry["passed"] = verdict
        entry["why"] = (
            "judge: correct" if verdict else f"judge: missing {grade.get('missing')!r}"
        )
    if (
        task.check == "diff"
        and entry.get("passed") is None
        and grade.get("solves_problem") is not None
    ):
        entry["passed"] = grade["solves_problem"]
        entry["why"] = (
            f"judge: solves it ({grade.get('approach')})"
            if grade["solves_problem"]
            else f"judge: does not solve it: {grade.get('one_line', '')}"
        )


def run_dirs(path: Path) -> List[Path]:
    """*path* itself, or the ``r1``, ``r2``... run directories ``--repeats`` wrote."""
    if (path / "scorecard.json").is_file():
        return [path]
    repeats = sorted(
        (p for p in path.glob("r*") if (p / "scorecard.json").is_file()),
        key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else 0,
    )
    if not repeats:
        raise FileNotFoundError(
            f"No scorecard at {path} or in its r1, r2... subdirectories. Run "
            f"`gaia eval tasks run --out {path}` first."
        )
    return repeats


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
        # Only coding tasks are counted by the gate, so a question showing
        # yes/no here would read as a number the gate deliberately excludes.
        "verified": t.get("verified") if t.get("check") == "mechanical" else None,
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
