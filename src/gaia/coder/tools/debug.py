#  Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""DebugToolsMixin — the eight debug sub-loop tools (§5.9).

§5.9 of ``docs/plans/coder-agent.mdx`` treats debugging as a first-class
capability: a self-contained sub-loop with its own tool mixin. This module
is the Python side of that — the eight tools the loop uses to drive
``reproduce → bisect → hypothesise → probe → localise_bug → propose_fix →
postmortem``.

All eight tools are registered via :meth:`DebugToolsMixin.register_debug_tools`
and integrate with the hybrid memory store (``failure_patterns`` topic,
§6.8.1) for recall of prior similar failures.

The discipline rules that gate ``propose_fix`` (§5.9: "no fix before
repro", "no fix before root cause", "three hypotheses minimum") live in
:mod:`gaia.coder.debug_loop` on the sub-loop state machine — this module
only provides the raw primitives. Tests for the mixin live in
``tests/coder/test_debug_tools.py`` and cover each of the eight tools
plus the mixin-registration smoke test.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Sequence, TypedDict

from gaia.agents.base.tools import tool
from gaia.coder.stores import memory as memory_store

logger = logging.getLogger(__name__)

#: Default timeout for subprocess commands driven by the debug tools
#: (``repro_attempt``, ``run_with_tracing``, etc.). 5 minutes — long enough
#: for slow test suites, short enough that a hung probe surfaces quickly.
DEFAULT_DEBUG_TIMEOUT_S: int = 300

#: Minimum repro attempts §5.9 requires before declaring "can't repro".
#: "3 of 3" in the spec — a reproducing failure is the starting gate.
DEFAULT_REPRO_ATTEMPTS: int = 3

#: Default flake-check sample size. §5.9: "run N times; flake rate > 10%
#: → flaky_tests topic write, not debug".
DEFAULT_FLAKE_ATTEMPTS: int = 5
FLAKE_RATE_THRESHOLD: float = 0.10


# ---------------------------------------------------------------------------
# Typed results
# ---------------------------------------------------------------------------


class ReproResult(TypedDict):
    """Result shape for :func:`repro_attempt`."""

    reproduced: bool
    actual_output: str
    match_score: float
    returncode: int
    attempts: int
    attempts_reproduced: int
    duration_ms: int


class BisectResult(TypedDict):
    """Result shape for :func:`git_bisect`."""

    culprit_sha: Optional[str]
    log: str
    tested_refs: List[str]
    returncode: int


class TraceResult(TypedDict):
    """Result shape for :func:`run_with_tracing`."""

    returncode: int
    stdout: str
    stderr: str
    trace_output: str
    duration_ms: int


class BehaviorDiff(TypedDict):
    """Result shape for :func:`diff_behavior`."""

    good_ref: str
    bad_ref: str
    good_output: str
    bad_output: str
    diff: str


class PatternHit(TypedDict):
    """One row from :func:`query_failure_patterns`."""

    memory_id: str
    stack_hash: str
    root_cause: str
    fix_pr_url: Optional[str]
    similarity: float
    confidence: int


class FlakeResult(TypedDict):
    """Result shape for :func:`flake_check`."""

    test_fqn: str
    attempts: int
    passed: int
    failed: int
    flake_rate: float
    is_flaky: bool


class MinimizeResult(TypedDict):
    """Result shape for :func:`minimize_repro`."""

    minimized: str
    original_length: int
    minimized_length: int
    iterations: int


class InstrumentResult(TypedDict):
    """Result shape for :func:`add_instrumented_trace`."""

    branch: str
    file: str
    line: int
    applied_message: str
    revert_handle: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    command: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    timeout: int = DEFAULT_DEBUG_TIMEOUT_S,
) -> "subprocess.CompletedProcess[str]":
    """Run a subprocess with defaults appropriate for debug tooling.

    Fail-loudly on :class:`subprocess.TimeoutExpired` (re-raised) so a
    hung probe is surfaced rather than silently succeeding. Capture both
    streams as text for downstream diffing.
    """
    return subprocess.run(  # noqa: S603 — explicit command list; inputs are tool args
        list(command),
        cwd=str(cwd) if cwd else None,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _similarity(a: str, b: str) -> float:
    """Return a 0-1 similarity score between two strings.

    Cheap token-overlap similarity — enough to decide "did the repro output
    match the expected failure signature?" without depending on
    ``difflib``'s slower routines. 1.0 = identical, 0.0 = no overlap.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    tokens_a = set(re.findall(r"\w+", a.lower()))
    tokens_b = set(re.findall(r"\w+", b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    inter = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(inter) / len(union)


def _stable_ms() -> int:
    """Monotonic milliseconds — used for duration fields across result dicts."""
    return int(time.monotonic() * 1000)


def _shell_split(command: str | Sequence[str]) -> List[str]:
    """Normalise ``command`` to a list, shelling-splitting strings."""
    if isinstance(command, str):
        return shlex.split(command)
    return list(command)


# ---------------------------------------------------------------------------
# The eight tools — pure functions first, then the mixin binds them to @tool.
# ---------------------------------------------------------------------------


def repro_attempt(
    command: str | Sequence[str],
    expected_failure_signature: str,
    *,
    attempts: int = DEFAULT_REPRO_ATTEMPTS,
    cwd: Optional[Path] = None,
    timeout: int = DEFAULT_DEBUG_TIMEOUT_S,
) -> ReproResult:
    """Run ``command`` up to ``attempts`` times, checking for the signature.

    §5.9 step 1: "create a reliable repro. A flaky failure you can't repro
    on demand is unfixable." We declare ``reproduced=True`` only when the
    signature matches on every successful-to-fail attempt AND the match
    score is ≥ 0.5 on at least one of them. Returns the merged output of
    the last attempt so the hypothesise step has something to chew on.
    """
    if attempts < 1:
        raise ValueError(f"repro_attempt: attempts must be >= 1 (got {attempts})")
    start = _stable_ms()
    argv = _shell_split(command)
    best_output = ""
    best_score = 0.0
    best_rc = 0
    hits = 0
    for i in range(attempts):
        completed = _run(argv, cwd=cwd, timeout=timeout)
        merged = f"{completed.stdout}\n{completed.stderr}"
        score = _similarity(expected_failure_signature, merged)
        logger.debug(
            "repro_attempt %d/%d: rc=%d score=%.2f",
            i + 1,
            attempts,
            completed.returncode,
            score,
        )
        if score > best_score:
            best_score = score
            best_output = merged
            best_rc = completed.returncode
        if score >= 0.5 and completed.returncode != 0:
            hits += 1
    reproduced = hits >= attempts and best_score >= 0.5
    return {
        "reproduced": reproduced,
        "actual_output": best_output,
        "match_score": best_score,
        "returncode": best_rc,
        "attempts": attempts,
        "attempts_reproduced": hits,
        "duration_ms": _stable_ms() - start,
    }


def git_bisect(
    good_ref: str,
    bad_ref: str,
    repro_command: str | Sequence[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = DEFAULT_DEBUG_TIMEOUT_S * 4,
    git_runner: Optional[Callable[..., "subprocess.CompletedProcess[str]"]] = None,
) -> BisectResult:
    """Automated ``git bisect`` driver — returns the culprit SHA on success.

    Internally runs ``git bisect start / good / bad / run <cmd>`` and
    parses the output. The caller is expected to provide a ``repro_command``
    that exits non-zero on the bad side and zero on the good side — the
    standard bisect contract.

    ``git_runner`` is an injection point for tests: they replace subprocess
    calls with a deterministic stub. Production passes ``None`` and we use
    :func:`_run`.
    """
    runner = git_runner or _run
    argv_repro = _shell_split(repro_command)

    tested_refs: List[str] = []
    full_log: List[str] = []

    def _git(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
        proc = runner(["git", *args], cwd=cwd, timeout=timeout)
        full_log.append(
            f"$ git {' '.join(args)} (rc={proc.returncode})\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
        return proc

    start = _git(["bisect", "start"])
    if start.returncode != 0:
        return {
            "culprit_sha": None,
            "log": "\n".join(full_log),
            "tested_refs": tested_refs,
            "returncode": start.returncode,
        }
    _git(["bisect", "bad", bad_ref])
    _git(["bisect", "good", good_ref])

    run_cmd = ["bisect", "run", *argv_repro]
    run_proc = _git(run_cmd)

    # Parse the classic bisect output line:
    #   "<sha> is the first bad commit"
    culprit: Optional[str] = None
    for line in run_proc.stdout.splitlines():
        m = re.match(r"^([0-9a-fA-F]{7,40}) is the first bad commit", line.strip())
        if m:
            culprit = m.group(1)
            break
    # Capture which refs were tested for the audit trail.
    for line in run_proc.stdout.splitlines():
        m = re.search(r"\[([0-9a-fA-F]{7,40})\]", line)
        if m:
            tested_refs.append(m.group(1))

    _git(["bisect", "reset"])

    return {
        "culprit_sha": culprit,
        "log": "\n".join(full_log),
        "tested_refs": tested_refs,
        "returncode": run_proc.returncode,
    }


def add_instrumented_trace(
    file: str,
    line: int,
    message: str,
    *,
    cwd: Optional[Path] = None,
    branch_prefix: str = "auto/gaia-coder-probe",
) -> InstrumentResult:
    """Insert a targeted ``logger.debug()`` at ``file:line`` on a scratch branch.

    §5.9 "probe" step. Creates a new branch from the current HEAD, patches
    the file in-place, and returns the branch name + a revert handle so
    ``propose_fix`` can refuse to run until the instrumentation is cleaned
    up.

    The inserted line is always ``logger.debug(<message>)`` at the original
    line's indentation level — not a bare ``print()`` — because the agent's
    own linting rejects debug prints (§8 Pass 1). The caller removes the
    trace by checking out the base branch (the ``revert_handle`` is the
    base SHA).
    """
    repo_root = Path(cwd) if cwd else Path.cwd()
    target = repo_root / file
    if not target.exists():
        raise FileNotFoundError(f"add_instrumented_trace: no such file {target}")

    base_sha_proc = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    if base_sha_proc.returncode != 0:
        raise RuntimeError(
            f"add_instrumented_trace: could not resolve HEAD under {repo_root}: "
            f"{base_sha_proc.stderr.strip()}"
        )
    base_sha = base_sha_proc.stdout.strip()
    branch = f"{branch_prefix}-{uuid.uuid4().hex[:8]}"

    checkout = _run(["git", "checkout", "-b", branch], cwd=repo_root)
    if checkout.returncode != 0:
        raise RuntimeError(
            f"add_instrumented_trace: `git checkout -b {branch}` failed: "
            f"{checkout.stderr.strip()}"
        )

    text = target.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if line < 1 or line > len(lines) + 1:
        raise ValueError(
            f"add_instrumented_trace: line {line} is out of range (1..{len(lines) + 1})"
        )
    # Preserve indentation from the target line (or previous line if EOF).
    template_line = lines[line - 1] if line <= len(lines) else lines[-1]
    indent_match = re.match(r"^(\s*)", template_line)
    indent = indent_match.group(1) if indent_match else ""
    # Inline the logging lookup so the probe is safe even when the target
    # file does not bind ``logger`` at module scope. Cf. #828 auto-review.
    inserted = (
        f"{indent}__import__('logging').getLogger(__name__)"
        f".debug({json.dumps(message)})  # gaia-coder probe\n"
    )
    new_lines = lines[: line - 1] + [inserted] + lines[line - 1 :]
    target.write_text("".join(new_lines), encoding="utf-8")

    return {
        "branch": branch,
        "file": file,
        "line": line,
        "applied_message": message,
        "revert_handle": base_sha,
    }


def run_with_tracing(
    command: str | Sequence[str],
    trace_flags: Optional[Sequence[str]] = None,
    *,
    cwd: Optional[Path] = None,
    timeout: int = DEFAULT_DEBUG_TIMEOUT_S,
) -> TraceResult:
    """Execute ``command`` with Python / Node tracing env vars enabled.

    Sets ``PYTHONFAULTHANDLER=1`` and ``PYTHONDEVMODE=1`` by default —
    cheap, forces more diagnostics out of :mod:`sys` and :mod:`warnings`.
    Extra tags passed via ``trace_flags`` are appended to the command:

    * ``python-dev`` → prepend ``python -X dev`` if the command starts
      with ``python``.
    * ``node-inspect`` → set ``NODE_OPTIONS=--inspect``.
    """
    argv = _shell_split(command)
    env: dict[str, str] = {
        "PYTHONFAULTHANDLER": "1",
        "PYTHONDEVMODE": "1",
    }
    flags = list(trace_flags or [])
    if "node-inspect" in flags:
        env["NODE_OPTIONS"] = "--inspect"
    if "python-dev" in flags and argv and argv[0] in ("python", "python3"):
        argv = [argv[0], "-X", "dev", *argv[1:]]

    start = _stable_ms()
    completed = _run(argv, cwd=cwd, env=env, timeout=timeout)
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "trace_output": completed.stderr,  # faulthandler writes to stderr
        "duration_ms": _stable_ms() - start,
    }


def diff_behavior(
    good_ref: str,
    bad_ref: str,
    harness_script: str | Sequence[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = DEFAULT_DEBUG_TIMEOUT_S,
) -> BehaviorDiff:
    """Run ``harness_script`` on ``good_ref`` and ``bad_ref``; return a diff.

    §5.9 tool: used when bisection is too coarse (e.g. failures coupled to
    a build artifact). We ``git stash`` the working tree, check out each
    ref, run the harness, collect stdout+stderr, then restore HEAD via
    ``git switch -`` and pop the stash.

    The diff is computed with :mod:`difflib.unified_diff` so the caller
    can render it as a patch-style summary.
    """
    import difflib

    repo_root = Path(cwd) if cwd else Path.cwd()
    argv = _shell_split(harness_script)

    # Capture the original ref BEFORE the first switch. ``git switch -`` only
    # returns to the previous ref, which after two detached switches is the
    # first detached ref, not the caller's original HEAD. Cf. #828 auto-review.
    sym = _run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repo_root)
    if sym.returncode == 0 and sym.stdout.strip():
        original_head = sym.stdout.strip()
        original_is_branch = True
    else:
        original_head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
        original_is_branch = False

    # Stash first to keep the working tree clean.
    stash = _run(
        ["git", "stash", "push", "--include-untracked", "-m", "gaia-coder-debug"],
        cwd=repo_root,
    )
    stashed = stash.returncode == 0 and "No local changes" not in (
        stash.stdout + stash.stderr
    )

    def _run_on(ref: str) -> str:
        co = _run(["git", "switch", "--detach", ref], cwd=repo_root)
        if co.returncode != 0:
            raise RuntimeError(
                f"diff_behavior: could not switch to {ref}: {co.stderr.strip()}"
            )
        proc = _run(argv, cwd=repo_root, timeout=timeout)
        return f"{proc.stdout}\n{proc.stderr}"

    try:
        good_output = _run_on(good_ref)
        bad_output = _run_on(bad_ref)
    finally:
        if original_is_branch:
            _run(["git", "switch", original_head], cwd=repo_root)
        else:
            _run(["git", "switch", "--detach", original_head], cwd=repo_root)
        if stashed:
            _run(["git", "stash", "pop"], cwd=repo_root)

    diff = "".join(
        difflib.unified_diff(
            good_output.splitlines(keepends=True),
            bad_output.splitlines(keepends=True),
            fromfile=f"output@{good_ref}",
            tofile=f"output@{bad_ref}",
            n=3,
        )
    )
    return {
        "good_ref": good_ref,
        "bad_ref": bad_ref,
        "good_output": good_output,
        "bad_output": bad_output,
        "diff": diff,
    }


def query_failure_patterns(
    error_signature: str,
    *,
    memory_conn: sqlite3.Connection,
    limit: int = 5,
) -> List[PatternHit]:
    """Look up prior failures with a similar error signature.

    Wraps :func:`gaia.coder.stores.memory.list_rows` filtered to the
    ``failure_patterns`` topic and ranks by token-similarity against
    ``error_signature``. Until FAISS lands in Phase 10 this is the
    retrieval path the memory topic exposes — it is intentionally
    conservative (exact-token recall) so we do not return spurious
    matches.
    """
    all_rows = memory_store.list_rows(memory_conn, filter={"topic": "failure_patterns"})
    hits: List[PatternHit] = []
    for row in all_rows:
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError:
            continue
        stack_hash = str(payload.get("stack_hash", ""))
        recorded_signature = str(payload.get("error_signature", ""))
        score = max(
            _similarity(error_signature, recorded_signature),
            _similarity(error_signature, stack_hash),
        )
        if score <= 0.0:
            continue
        hits.append(
            {
                "memory_id": row.id,
                "stack_hash": stack_hash,
                "root_cause": str(payload.get("root_cause", "")),
                "fix_pr_url": payload.get("fix_pr_url"),
                "similarity": score,
                "confidence": int(row.confidence),
            }
        )
    hits.sort(key=lambda h: h["similarity"], reverse=True)
    return hits[:limit]


def flake_check(
    test_fqn: str,
    *,
    attempts: int = DEFAULT_FLAKE_ATTEMPTS,
    cwd: Optional[Path] = None,
    timeout: int = DEFAULT_DEBUG_TIMEOUT_S,
    runner: Optional[Callable[[str, int, Optional[Path]], int]] = None,
) -> FlakeResult:
    """Run a single test N times; compute the flake rate.

    ``test_fqn`` is the pytest FQN (e.g. ``tests/coder/test_foo.py::test_bar``).
    ``runner`` is an injection point for tests — it accepts
    ``(test_fqn, attempt_index, cwd)`` and returns the process exit code.
    Production omits it and we run ``pytest <fqn> -x`` under ``_run``.

    Flake rate above :data:`FLAKE_RATE_THRESHOLD` flips ``is_flaky`` on;
    the caller writes to the ``flaky_tests`` topic rather than pursuing
    debug.
    """
    if attempts < 1:
        raise ValueError(f"flake_check: attempts must be >= 1 (got {attempts})")

    def _default_runner(fqn: str, _idx: int, workdir: Optional[Path]) -> int:
        # ``_idx`` is required by the runner contract (flake_check also
        # accepts test-injected runners that key off the attempt number);
        # the default implementation ignores it.
        return _run(
            ["pytest", fqn, "-x", "--no-header", "-q"],
            cwd=workdir,
            timeout=timeout,
        ).returncode

    use_runner = runner or _default_runner
    passed = 0
    failed = 0
    for i in range(attempts):
        rc = use_runner(test_fqn, i, cwd)
        if rc == 0:
            passed += 1
        else:
            failed += 1
    flake_rate = 0.0
    if attempts > 0 and 0 < passed < attempts:
        # A test that neither always passes nor always fails is flaky.
        flake_rate = min(passed, failed) / attempts
    return {
        "test_fqn": test_fqn,
        "attempts": attempts,
        "passed": passed,
        "failed": failed,
        "flake_rate": flake_rate,
        "is_flaky": flake_rate > FLAKE_RATE_THRESHOLD,
    }


def minimize_repro(
    command: str | Sequence[str],
    repro_input: str,
    *,
    reproducer: Optional[Callable[[str], bool]] = None,
    cwd: Optional[Path] = None,
    timeout: int = DEFAULT_DEBUG_TIMEOUT_S,
    max_iterations: int = 32,
) -> MinimizeResult:
    """Binary-search ``repro_input`` down to a minimal version that still repros.

    §5.9 tool: the "2000-char input becomes a 20-char one" case. The loop
    drops half of the input at each step and verifies the result still
    reproduces via ``reproducer``. When ``reproducer`` is None we fall back
    to running ``command`` with ``repro_input`` piped to stdin — production
    usage.

    The caller is responsible for ensuring the reproducer is deterministic.
    """
    if not repro_input:
        return {
            "minimized": "",
            "original_length": 0,
            "minimized_length": 0,
            "iterations": 0,
        }

    def _default_reproducer(candidate: str) -> bool:
        argv = _shell_split(command)
        proc = subprocess.run(  # noqa: S603
            argv,
            input=candidate,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode != 0

    check = reproducer or _default_reproducer
    if not check(repro_input):
        raise ValueError(
            "minimize_repro: the original input does not reproduce the failure; "
            "nothing to minimise."
        )

    current = repro_input
    iterations = 0
    while iterations < max_iterations:
        iterations += 1
        if len(current) <= 1:
            break
        mid = len(current) // 2
        left, right = current[:mid], current[mid:]
        if check(left):
            current = left
            continue
        if check(right):
            current = right
            continue
        break
    return {
        "minimized": current,
        "original_length": len(repro_input),
        "minimized_length": len(current),
        "iterations": iterations,
    }


# ---------------------------------------------------------------------------
# Mixin registration
# ---------------------------------------------------------------------------


class DebugToolsMixin:
    """Register the eight §5.9 debug tools on an agent.

    The mixin is stateless — each tool is a @tool-decorated closure that
    delegates to the pure helpers above. Call
    :meth:`register_debug_tools` during agent bootstrap; it returns the
    list of registered tool names so the smoke test can assert the
    eight-tool contract.
    """

    def register_debug_tools(self) -> List[str]:
        """Register the debug tools and return their names in §5.9 order."""
        registered: List[str] = []

        @tool
        def repro_attempt_tool(
            command: str,
            expected_failure_signature: str,
            attempts: int = DEFAULT_REPRO_ATTEMPTS,
            cwd: Optional[str] = None,
        ) -> ReproResult:
            """Run ``command`` up to ``attempts`` times, matching the signature."""
            return repro_attempt(
                command,
                expected_failure_signature,
                attempts=attempts,
                cwd=Path(cwd) if cwd else None,
            )

        registered.append("repro_attempt")

        @tool
        def git_bisect_tool(
            good_ref: str,
            bad_ref: str,
            repro_command: str,
            cwd: Optional[str] = None,
        ) -> BisectResult:
            """Drive ``git bisect`` between ``good_ref`` and ``bad_ref``."""
            return git_bisect(
                good_ref,
                bad_ref,
                repro_command,
                cwd=Path(cwd) if cwd else None,
            )

        registered.append("git_bisect")

        @tool
        def add_instrumented_trace_tool(
            file: str,
            line: int,
            message: str,
            cwd: Optional[str] = None,
        ) -> InstrumentResult:
            """Insert a ``logger.debug`` probe at file:line on a scratch branch."""
            return add_instrumented_trace(
                file,
                line,
                message,
                cwd=Path(cwd) if cwd else None,
            )

        registered.append("add_instrumented_trace")

        @tool
        def run_with_tracing_tool(
            command: str,
            trace_flags: Optional[List[str]] = None,
            cwd: Optional[str] = None,
        ) -> TraceResult:
            """Run ``command`` with Python/Node tracing enabled."""
            return run_with_tracing(
                command,
                trace_flags=trace_flags,
                cwd=Path(cwd) if cwd else None,
            )

        registered.append("run_with_tracing")

        @tool
        def diff_behavior_tool(
            good_ref: str,
            bad_ref: str,
            harness_script: str,
            cwd: Optional[str] = None,
        ) -> BehaviorDiff:
            """Run ``harness_script`` on each ref; return a unified diff."""
            return diff_behavior(
                good_ref,
                bad_ref,
                harness_script,
                cwd=Path(cwd) if cwd else None,
            )

        registered.append("diff_behavior")

        @tool
        def query_failure_patterns_tool(
            error_signature: str,
            memory_db_path: str,
            limit: int = 5,
        ) -> List[PatternHit]:
            """Memory lookup against the ``failure_patterns`` topic."""
            conn = memory_store.open_store(Path(memory_db_path))
            try:
                return query_failure_patterns(
                    error_signature, memory_conn=conn, limit=limit
                )
            finally:
                conn.close()

        registered.append("query_failure_patterns")

        @tool
        def flake_check_tool(
            test_fqn: str,
            attempts: int = DEFAULT_FLAKE_ATTEMPTS,
            cwd: Optional[str] = None,
        ) -> FlakeResult:
            """Run ``test_fqn`` ``attempts`` times and compute flake rate."""
            return flake_check(
                test_fqn,
                attempts=attempts,
                cwd=Path(cwd) if cwd else None,
            )

        registered.append("flake_check")

        @tool
        def minimize_repro_tool(
            command: str,
            repro_input: str,
            max_iterations: int = 32,
            cwd: Optional[str] = None,
        ) -> MinimizeResult:
            """Binary-search ``repro_input`` down to a minimal reproducer."""
            return minimize_repro(
                command,
                repro_input,
                max_iterations=max_iterations,
                cwd=Path(cwd) if cwd else None,
            )

        registered.append("minimize_repro")

        logger.info(
            "DebugToolsMixin.register_debug_tools: registered %d tools",
            len(registered),
        )
        return registered


__all__ = [
    "BehaviorDiff",
    "BisectResult",
    "DEFAULT_DEBUG_TIMEOUT_S",
    "DEFAULT_FLAKE_ATTEMPTS",
    "DEFAULT_REPRO_ATTEMPTS",
    "DebugToolsMixin",
    "FLAKE_RATE_THRESHOLD",
    "FlakeResult",
    "InstrumentResult",
    "MinimizeResult",
    "PatternHit",
    "ReproResult",
    "TraceResult",
    "add_instrumented_trace",
    "diff_behavior",
    "flake_check",
    "git_bisect",
    "minimize_repro",
    "query_failure_patterns",
    "repro_attempt",
    "run_with_tracing",
]
