# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Run a task end to end and record everything that happened.

The agent loop is GAIA's own — :meth:`Agent.process_query` — not a
reimplementation of it. Tools really execute, files really change, and the
verifier really runs. That is the whole point: a harness that simulates the
agent measures the simulation.

**Isolation.** Each attempt gets a fresh sandbox directory and runs in its own
process (see :mod:`._child`). Pinning ``allowed_paths`` to the workspace is not
sufficient on its own: it gates reads and writes but not *search*, and the first
smoke run walked out of the workspace into the developer's real GAIA checkout
and answered about that instead. Containment therefore comes from redirecting
``HOME``, ``USERPROFILE`` and ``GAIA_HOME`` into the sandbox, which only a
subprocess can do because those are read during import. That also gives each
attempt a cold ``~/.gaia`` — shared, the second arm would inherit the first
arm's warm memory and indexes.

**What gets recorded.** Correctness comes from the verifier's exit code. Around
that we keep the whole episode, because a task that passed in fourteen steps
with six failed tool calls is a different result from one that passed in three:

============================  ====================================================
correctness                   verifier exit code, plus its output
steps                         how many loop iterations the agent used
tool calls                    every tool, in order, with argument keys
tool failures                 calls whose result reported an error
wasted work                   repeated identical calls, and reads of absent files
tokens                        in and out; see ``BENCH_ENV`` for which are
                              backend-reported and which are locally counted
latency                       wall clock, split into time in the LLM vs in tools
context growth                real input tokens of every successive LLM call,
                              and the cache-hit ratio each one got
recovery                      the agent's error history
files                         what changed on disk, hashed before and after
============================  ====================================================

The artefacts the agent produced are saved alongside, so output *quality* can be
judged later without re-running anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .suite import Task

#: A task that has not finished in this long is not going to. The cap exists so
#: one wedged run cannot stall a suite; it is recorded as a timeout, never
#: silently reported as a failure to accomplish the task.
DEFAULT_TIMEOUT_S = 900

#: Verifiers are shell one-liners and must not outlive their task.
VERIFY_TIMEOUT_S = 120

#: Artefacts larger than this are hashed and sized but not copied into the
#: episode — a judge cannot read a 2 MB file usefully anyway.
MAX_ARTEFACT_CHARS = 20000

#: Environment every arm runs under, recorded in ``run_meta.json`` so a result
#: can never be read without seeing the conditions that produced it.
#:
#: ``GAIA_AUTO_APPROVE_TOOLS`` is required, not a convenience. Tools like
#: ``write_file`` are confirmation-gated and a benchmark has no terminal to
#: approve on, so without it every writing task fails for a reason that has
#: nothing to do with the model — the first smoke run answered a question
#: correctly and then scored zero because it could not save the answer.
#:
#: ``GAIA_PROJECT_MAP_AUTO_INDEX`` is off because background indexing reaches
#: for a local embedding server that a gateway arm does not run, spending
#: seconds and logging failures that belong to neither the task nor the model.
#:
#: ``GAIA_TURN_LOG`` switches on the agent's own per-turn recorder, which is the
#: only honest source for two things this benchmark needs. It measures the real
#: input size of every LLM call, so context growth is measured rather than
#: approximated from tool-output sizes — a proxy that was found to understate
#: the true figure by 4.6×. And it counts tokens locally, so the arms on the
#: OpenAI-shaped path get token figures at all; the client only accumulates
#: backend-reported usage on the Claude path.
#: Its path is set per-attempt in :func:`child_env` and deliberately lands
#: *outside* the workspace — see :data:`INSTRUMENTATION` for why.
BENCH_ENV = {
    "GAIA_AUTO_APPROVE_TOOLS": "1",
    "GAIA_PROJECT_MAP_AUTO_INDEX": "0",
}

#: Files this harness produces, which must never be mistaken for the agent's
#: work. The turn log was briefly written into the workspace and the damage was
#: immediate and silent: it counted as a created file, and at 12,000 characters
#: it consumed the judge's entire per-submission budget, so on one task the
#: judge scored an arm on a log file and never saw the 263-character source file
#: it was supposed to review. Instrumentation now lands in the sandbox root, and
#: this set is belt-and-braces in case anything else leaks in.
INSTRUMENTATION = {"turns.jsonl"}


@dataclass
class Episode:
    """Everything observed during one attempt at one task."""

    task: str
    use_case: str
    track: str
    arm: str

    # --- correctness: the verifier's word, not a judge's -------------------
    accomplished: bool = False
    verify_exit_code: Optional[int] = None
    verify_output: str = ""

    # --- what the agent did ------------------------------------------------
    steps: int = 0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_failures: int = 0
    repeated_calls: int = 0
    distinct_tools: List[str] = field(default_factory=list)

    # --- what it cost ------------------------------------------------------
    tokens_in: int = 0
    tokens_out: int = 0
    wall_clock_s: float = 0.0
    #: Input tokens of each successive LLM call — how context actually grew.
    input_tokens_by_call: List[int] = field(default_factory=list)
    #: Fraction of input the backend served from cache, per call.
    cache_hit_by_call: List[float] = field(default_factory=list)
    #: Fixed cost paid on every call before any conversation: system prompt
    #: plus tool schemas. In a short task this dominates everything else.
    fixed_prefill_tokens: int = 0
    seconds_in_llm: float = 0.0
    seconds_in_tools: float = 0.0
    #: Real dollars, where the harness reports them. Claude Code does; the GAIA
    #: arms run on an internal gateway that bills separately, so a zero here
    #: means "not reported by this harness", never "free".
    cost_usd: float = 0.0
    #: Tools the harness refused to run. A denial is not a model failure and
    #: must not be read as one — it is the harness scoring itself.
    permission_denials: List[str] = field(default_factory=list)
    #: Steps and answer size per turn, for multi-turn tasks. One entry means a
    #: single-turn task; several show whether the agent kept making progress
    #: after being told to continue, or restarted from nothing.
    turns: List[Dict[str, Any]] = field(default_factory=list)

    # --- how it coped ------------------------------------------------------
    errors: List[Dict[str, Any]] = field(default_factory=list)
    timed_out: bool = False
    crashed: Optional[str] = None

    # --- what it produced --------------------------------------------------
    files_created: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    artefacts: Dict[str, str] = field(default_factory=dict)
    final_answer: str = ""


def _hash_tree(root: Path) -> Dict[str, str]:
    """Content hash of every file under *root*, keyed by relative path."""
    out: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if (
            p.is_file()
            and "__pycache__" not in p.parts
            and ".pytest_cache" not in p.parts
        ):
            out[p.relative_to(root).as_posix()] = hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
    return out


def _bash() -> str:
    exe = shutil.which("bash")
    if not exe:
        raise RuntimeError(
            "No `bash` on PATH. Task verifiers are shell commands and need one "
            "(Git Bash ships with Git for Windows). Install it or run the suite "
            "from a shell where `bash` resolves."
        )
    return exe


def _git_init(workspace: Path) -> None:
    """Make the workspace a repo with the setup files already committed.

    Tasks about repository mechanics need a repo that is in the state the
    prompt describes — artefacts *tracked*, so untracking them is a real
    request. Identity and hooks are set locally so the run cannot depend on,
    or be broken by, the developer's global git config.
    """
    run = lambda *a: subprocess.run(  # noqa: E731,S603 - fixed argv, no shell
        ["git", *a], cwd=str(workspace), capture_output=True, text=True, check=True
    )
    run("init", "-q", "-b", "main")
    run("config", "user.email", "bench@example.com")
    run("config", "user.name", "bench")
    run("config", "core.hooksPath", "/dev/null")
    run("add", "-A", "-f")
    run("commit", "-q", "-m", "initial state")


def _read_episode_from_result(ep: Episode, result: Dict[str, Any]) -> None:
    """Mine the agent's own return value for the step-by-step record."""
    ep.steps = int(result.get("steps_taken") or 0)
    ep.tokens_in = int(result.get("input_tokens") or 0)
    ep.tokens_out = int(result.get("output_tokens") or 0)
    ep.final_answer = str(result.get("result") or "")[:4000]
    ep.errors = list(result.get("error_history") or [])[:50]
    ep.turns = list(result.get("turns") or [])

    seen: set = set()
    for entry in result.get("conversation") or []:
        role = entry.get("role")
        content = entry.get("content")
        if role == "tool":
            args = entry.get("tool_args") or {}
            name = entry.get("name") or "?"
            # Argument *keys* only. Values carry file contents and user text;
            # this record is read by people and shipped to a judge.
            signature = (name, tuple(sorted(args)), str(args)[:200])
            ep.tool_calls.append(
                {
                    "tool": name,
                    "arg_keys": sorted(args),
                    "failed": _looks_failed(content),
                }
            )
            if _looks_failed(content):
                ep.tool_failures += 1
            if signature in seen:
                ep.repeated_calls += 1
            seen.add(signature)

    # A harness that reports its own tool sequence (Claude Code) supplies it
    # directly; GAIA's is reconstructed from the conversation above.
    for call in result.get("tool_calls") or []:
        ep.tool_calls.append(call)
        if call.get("failed"):
            ep.tool_failures += 1
    ep.cost_usd = float(result.get("cost_usd") or 0.0)
    ep.permission_denials = list(result.get("permission_denials") or [])
    ep.distinct_tools = sorted({c["tool"] for c in ep.tool_calls})

    # The agent's own per-turn record. It measures the real input size of every
    # LLM call, which is the honest way to report context growth; deriving it
    # from tool-output sizes understates the truth by several times.
    turn = result.get("turn_metrics") or {}
    ep.fixed_prefill_tokens = int(
        (turn.get("prompt") or {}).get("fixed_prefill_tokens", 0) or 0
    )
    for call in turn.get("llm_calls") or []:
        ep.input_tokens_by_call.append(int(call.get("input_tokens_local") or 0))
        ep.cache_hit_by_call.append(round(float(call.get("cache_hit_ratio") or 0.0), 3))
    totals = turn.get("totals") or {}
    ep.seconds_in_llm = float(totals.get("llm_s") or 0.0)
    ep.seconds_in_tools = float(totals.get("tool_s") or 0.0)
    # The Claude path is the only one where the client accumulates
    # backend-reported usage; fall back to the recorder's local count so an
    # open-weights arm reports tokens instead of a misleading zero.
    if not ep.tokens_in:
        ep.tokens_in = int(totals.get("input_tokens_local") or 0)
    if not ep.tokens_out:
        ep.tokens_out = int(totals.get("output_tokens_server") or 0)


#: How long to wait for the pipes to drain after the tree has been killed.
#: Only reached when a grandchild ignored the kill, so it is a backstop, not a
#: budget.
_DRAIN_AFTER_KILL_S = 20


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill *proc* and everything it spawned.

    ``Popen.kill`` signals one process. An agent run spawns grandchildren, and
    they inherit the stdout/stderr handles — so killing only the parent leaves
    the pipes open and the reader blocked.
    """
    if os.name == "nt":
        subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def _run_child(argv, cwd, env, timeout_s: int):
    """Run *argv*, enforcing *timeout_s* against the whole process tree.

    ``subprocess.run(timeout=...)`` does not do this. It kills the direct
    child and then blocks draining pipes that a surviving grandchild still
    holds open — so the timeout does not end the wait. One wedged task in a
    benchmark run sat for **92 minutes** against a 900-second limit, burning
    the whole batch's schedule and recording zero tokens, and the episode it
    produced was indistinguishable from an agent that simply did nothing.

    Returns ``(returncode, stdout, stderr)``; a timeout returns ``None`` for
    the return code after raising, so callers see ``TimeoutExpired``.
    """
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **({} if os.name == "nt" else {"start_new_session": True}),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=_DRAIN_AFTER_KILL_S)
        except subprocess.TimeoutExpired:
            # The tree outlived its own kill. Abandoning the pipes loses the
            # tail of a run that already failed; blocking here loses the batch.
            pass
        raise
    return proc.returncode, stdout, stderr


def _looks_failed(content: Any) -> bool:
    """Did this tool result report an error?

    Tools return a status field when they fail; a bare string result is treated
    as success rather than pattern-matched for the word "error", which would
    misclassify a grep hit on an error message as a failed call.
    """
    if isinstance(content, dict):
        if content.get("status") == "error" or content.get("success") is False:
            return True
        return bool(content.get("error"))
    return False


def repo_root() -> Path:
    """The checkout this module lives in.

    Derived from ``__file__`` rather than an environment variable, so the runner
    always measures the branch it was invoked from. The alternative silently
    measures whichever copy happens to be pip-installed — which on a machine
    with several worktrees is rarely the one under test.
    """
    return Path(__file__).resolve().parents[4]


def child_env(sandbox: Path) -> Dict[str, str]:
    """Environment for one attempt, with the agent's world pointed inside *sandbox*.

    ``allowed_paths`` gates reads and writes but **not** search: ``find_files``
    defaults to ``scope="smart"``, which walks the current directory, then
    "home common locations", then previously indexed directories. In the first
    smoke run that carried the agent straight out of the task workspace and into
    the developer's real GAIA checkout.

    Redirecting ``HOME``/``USERPROFILE`` moves those locations inside the
    sandbox. ``GAIA_HOME`` does the same for ``~/.gaia``, which is where memory,
    indexes and caches live — shared, the second arm would inherit the first
    arm's warm state and the comparison would stop being fair.
    """
    env = dict(os.environ)
    env.update(BENCH_ENV)
    home = sandbox / "home"
    (home / ".gaia").mkdir(parents=True, exist_ok=True)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        GAIA_HOME=str(home / ".gaia"),
        PYTHONPATH=str(repo_root() / "src"),
        PYTHONIOENCODING="utf-8",
        # Absolute, and outside the workspace. A relative path resolves against
        # the agent's working directory, which puts the harness's own log in
        # among the agent's output.
        GAIA_TURN_LOG=str(sandbox / "turns.jsonl"),
    )
    return env


def run_task(
    task: Task,
    arm: str,
    model: str,
    transport: str,
    out_dir: Path,
    max_steps: int = 0,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    keep_workspace: bool = False,
    harness: str = "gaia",
) -> Episode:
    """Provision, run, verify, and record one attempt. Never raises."""
    # A long-horizon task needs minutes; a timeout tuned for a 5-step task would
    # score it as a failure of speed rather than of capability.
    timeout_s = task.timeout_s or timeout_s
    ep = Episode(task=task.key, use_case=task.use_case, track=task.track, arm=arm)
    sandbox = Path(tempfile.mkdtemp(prefix=f"gaia-task-{task.key}-"))
    workspace = sandbox / "workspace"
    workspace.mkdir()

    try:
        for rel, body in task.setup.items():
            dest = workspace / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # newline="" suppresses the platform translation that would write
            # CRLF on Windows. The workspace must be byte-identical wherever it
            # runs, or a task that pins a file's hash can never pass and a
            # cross-platform result is not comparable.
            dest.write_text(body, encoding="utf-8", newline="")
        if task.git_init:
            _git_init(workspace)
        before = _hash_tree(workspace)

        job = sandbox / "job.json"
        raw = sandbox / "result.json"
        job.write_text(
            json.dumps(
                {
                    "repo": str(repo_root()),
                    "workspace": str(workspace),
                    "prompt": task.prompt,
                    "follow_ups": list(task.follow_ups),
                    "model": model,
                    "transport": transport,
                    "max_steps": max_steps,
                    "base_url": os.environ.get("GAIA_TASK_BASE_URL"),
                }
            ),
            encoding="utf-8",
        )

        t0 = time.time()
        try:
            if harness == "claude-code":
                from . import claude_code

                payload = claude_code.run(
                    {
                        "workspace": str(workspace),
                        "prompt": task.prompt,
                        "follow_ups": list(task.follow_ups),
                        "model": model,
                        "base_env": dict(os.environ),
                    },
                    sandbox,
                    timeout_s,
                )
                raw.write_text(json.dumps(payload, default=str), encoding="utf-8")
            else:
                returncode, _stdout, stderr = _run_child(
                    [
                        sys.executable,
                        "-m",
                        "gaia.factory.tasks._child",
                        str(job),
                        str(raw),
                    ],
                    cwd=str(workspace),
                    env=child_env(sandbox),
                    timeout_s=timeout_s,
                )
                if not raw.exists():
                    ep.crashed = (
                        f"child exited {returncode} without writing a "
                        f"result; stderr tail: {stderr[-500:]}"
                    )
            if raw.exists():
                payload = json.loads(raw.read_text(encoding="utf-8"))
                if payload.get("ok"):
                    _read_episode_from_result(ep, payload)
                else:
                    # A crash is a real outcome and must read as one — not as a
                    # quiet zero. Keeping the traceback is what distinguishes a
                    # harness bug from a model failure when the score is read
                    # back weeks later.
                    ep.crashed = payload.get("error", "unknown")
                    (out_dir / f"{task.key}.{arm}.traceback.txt").write_text(
                        payload.get("traceback", ""), encoding="utf-8"
                    )
        except subprocess.TimeoutExpired:
            ep.timed_out = True
            ep.crashed = f"exceeded {timeout_s}s and was killed"
        ep.wall_clock_s = round(time.time() - t0, 1)

        after = _hash_tree(workspace)
        ep.files_created = sorted(set(after) - set(before) - INSTRUMENTATION)
        ep.files_modified = sorted(
            k
            for k in set(after) & set(before)
            if after[k] != before[k] and k not in INSTRUMENTATION
        )

        for rel in ep.files_created + ep.files_modified:
            p = workspace / rel
            text = p.read_text(encoding="utf-8", errors="replace")
            ep.artefacts[rel] = (
                text
                if len(text) <= MAX_ARTEFACT_CHARS
                else text[:MAX_ARTEFACT_CHARS] + f"\n…[{len(text)} chars total]"
            )

        proc = subprocess.run(  # noqa: S603 - verifier is repo-authored, not user input
            [_bash(), "-c", task.verify],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=VERIFY_TIMEOUT_S,
        )
        ep.verify_exit_code = proc.returncode
        ep.accomplished = proc.returncode == 0
        ep.verify_output = (proc.stdout + proc.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        ep.verify_output = f"verifier exceeded {VERIFY_TIMEOUT_S}s"
        ep.verify_exit_code = -1
    finally:
        if keep_workspace:
            ep.artefacts["__workspace__"] = str(workspace)
        else:
            shutil.rmtree(sandbox, ignore_errors=True)
    return ep


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", required=True, help="label for this (harness, model)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--transport", default="anthropic", choices=("anthropic", "openai"))
    ap.add_argument(
        "--harness",
        default="gaia",
        choices=("gaia", "claude-code"),
        help="which agent system runs the task; claude-code is the reference arm",
    )
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tasks", default="", help="comma-separated task keys")
    ap.add_argument("--tracks", default="")
    ap.add_argument("--max-steps", type=int, default=0, help="0 = no limit")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--keep-workspace", action="store_true")
    a = ap.parse_args()

    from .suite import BY_KEY, tasks_for

    if a.tasks:
        selected = [BY_KEY[k] for k in a.tasks.split(",")]
    else:
        selected = tasks_for(tracks=tuple(t for t in a.tracks.split(",") if t))

    os.environ.update(BENCH_ENV)
    a.out.mkdir(parents=True, exist_ok=True)
    episodes: List[Episode] = []
    t0 = time.time()

    for i, task in enumerate(selected, 1):
        print(
            f"[{i}/{len(selected)}] {task.key} ({task.use_case})… ", end="", flush=True
        )
        ep = run_task(
            task,
            a.arm,
            a.model,
            a.transport,
            a.out,
            max_steps=a.max_steps,
            timeout_s=a.timeout,
            keep_workspace=a.keep_workspace,
            harness=a.harness,
        )
        episodes.append(ep)
        verdict = "PASS" if ep.accomplished else ("CRASH" if ep.crashed else "fail")
        print(
            f"{verdict} in {ep.steps} steps, {len(ep.tool_calls)} calls, "
            f"{ep.wall_clock_s}s"
        )

    (a.out / "episodes.json").write_text(
        json.dumps([asdict(e) for e in episodes], indent=1), encoding="utf-8"
    )
    passed = sum(e.accomplished for e in episodes)
    (a.out / "run_meta.json").write_text(
        json.dumps(
            {
                "arm": a.arm,
                "model": a.model,
                "harness": a.harness,
                "transport": a.transport,
                "tasks": len(episodes),
                "accomplished": passed,
                "pass_rate": (
                    round(100 * passed / len(episodes), 1) if episodes else 0.0
                ),
                "crashed": sum(bool(e.crashed) for e in episodes),
                "wall_clock_s": round(time.time() - t0, 1),
                "tokens_in": sum(e.tokens_in for e in episodes),
                "tokens_out": sum(e.tokens_out for e in episodes),
                "cost_usd": round(sum(e.cost_usd for e in episodes), 4),
                "max_steps": a.max_steps,
                "bench_env": BENCH_ENV,
                "python": sys.version.split()[0],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{passed}/{len(episodes)} accomplished — {a.out}")


if __name__ == "__main__":
    main()
