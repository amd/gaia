# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a test, lint, or build run actually reported, as a fact on its result.

The tool that runs a check knows, at the moment it returns, whether it was a
check and whether it passed: it has the argv, the exit code, and the runner's
full output. It used to throw that away into a text blob, and every consumer —
the verification footer, the answer seam — re-derived it with its own guess.

Now a tool that runs commands sets :data:`CHECK_RESULT_KEY` on every result it
returns for a call that actually ran: a :class:`CheckResult` when the call was
a check, ``None`` when it was not. Consumers read that. Only a result without
the key — a tool that predates it, a hub agent, an MCP server — is still
classified from its text, by the fallback in ``verification.py``.

Pure and dependency-free: tools, the agent loop, and hub agents all import it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, FrozenSet, Optional, Sequence, Tuple

#: Result key a command-running tool sets on every call that ran:
#: ``CheckResult.to_dict()`` for a check, ``None`` for anything else.
CHECK_RESULT_KEY = "check_result"

#: What a check is evidence for. Only ``"test"`` backs a claim about tests.
CHECK_KINDS: FrozenSet[str] = frozenset({"test", "lint", "typecheck", "build"})

_TEXT_FIELDS = ("label", "target", "kind", "summary")


@dataclass(frozen=True)
class CheckResult:
    """One check run, as reported by the tool that ran it."""

    #: Runner name: ``"pytest"``, ``"unittest"``, ``"npm run test"``, ``"ruff"``.
    label: str
    #: What it ran against, so a narrow rerun is not mistaken for the suite.
    target: str
    #: One of :data:`CHECK_KINDS`.
    kind: str
    passed: bool
    #: The runner's own summary line, verbatim; empty when it printed none.
    summary: str

    def __post_init__(self) -> None:
        if self.kind not in CHECK_KINDS:
            raise ValueError(
                f"CheckResult kind must be one of {sorted(CHECK_KINDS)}, "
                f"got {self.kind!r} (label {self.label!r})"
            )

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe form, for the tool's result dict."""
        return asdict(self)

    @classmethod
    def from_result(cls, result: Any) -> Optional["CheckResult"]:
        """The check a tool result reports, or ``None`` when it reports none.

        ``None`` covers both a result that declared "not a check" and one that
        says nothing — :func:`declares_check` tells them apart. A result that
        carries the key but not a valid check is a producer bug, and raises:
        reading past it would put a made-up fact in the record.
        """
        if not declares_check(result) or result[CHECK_RESULT_KEY] is None:
            return None
        payload = result[CHECK_RESULT_KEY]
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("passed"), bool)
            or not all(isinstance(payload.get(f), str) for f in _TEXT_FIELDS)
        ):
            raise ValueError(
                f"Malformed {CHECK_RESULT_KEY!r} on a tool result: {payload!r}. "
                "Build it with CheckResult(...).to_dict() from "
                "gaia.agents.base.checks."
            )
        return cls(passed=payload["passed"], **{f: payload[f] for f in _TEXT_FIELDS})


def declares_check(result: Any) -> bool:
    """True when the tool itself said whether this call was a check."""
    return isinstance(result, dict) and CHECK_RESULT_KEY in result


def attach_check(
    result: Dict[str, Any], check: Optional[CheckResult]
) -> Dict[str, Any]:
    """*result* with its check declared — ``None`` means "ran, not a check"."""
    return {**result, CHECK_RESULT_KEY: check.to_dict() if check else None}


# ---------------------------------------------------------------------------
# Recognising a check runner
# ---------------------------------------------------------------------------

_CHECK_COMMAND_RE = re.compile(
    r"\b("
    r"pytest|py\.test|tox|nox"
    r"|python\s+-m\s+(?:pytest|unittest)"
    r"|npm\s+(?:run\s+)?(?:test|lint|build|typecheck)"
    r"|yarn\s+(?:test|lint|build)"
    r"|pnpm\s+(?:run\s+)?(?:test|lint|build)"
    r"|go\s+(?:test|vet|build)"
    r"|cargo\s+(?:test|clippy|check|build)"
    r"|dotnet\s+(?:test|build)"
    r"|mvn\s+(?:test|verify)"
    r"|make\s+(?:test|check|lint|build)"
    r"|ctest|jest|vitest|mocha"
    r"|ruff|flake8|pylint|mypy|pyright|eslint|tsc|shellcheck"
    r"|util[/\\]lint\.py"
    r")\b",
    re.IGNORECASE,
)

#: Launchers that run the next word as the program.
_LAUNCHER_RE = re.compile(
    r"(?:(?:uv|poetry|pipenv|hatch|pdm)\s+run\s+|npx\s+)?", re.IGNORECASE
)

_LABEL_ALIASES = {
    "python -m pytest": "pytest",
    "py.test": "pytest",
    "python -m unittest": "unittest",
}

# Kind by label word. A label naming none of these counts as a test: calling a
# lint a test only lets a claim through, calling a test a lint accuses an
# honest answer.
_KIND_WORDS: Tuple[Tuple[str, FrozenSet[str]], ...] = (
    (
        "lint",
        frozenset(
            {"lint", "lint.py", "vet", "clippy", "ruff", "flake8", "pylint"}
            | {"eslint", "shellcheck"}
        ),
    ),
    ("typecheck", frozenset({"typecheck", "mypy", "pyright", "tsc"})),
    ("build", frozenset({"build"})),
)

#: A pytest summary line: ``== 3 failed, 10 passed in 1.2s ==``, with or
#: without the rule, including pytest-subtests' ``19 subtests passed``.
_PYTEST_SUMMARY_RE = re.compile(
    r"(?m)^=*[ \t]*(?:\d+ (?:subtests? )?"
    r"(?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warning|warnings)"
    r"(?:, )?)+ in \d+(?:\.\d+)?s(?: \(.*\))?[ \t]*=*[ \t]*$"
)
#: At least one test ran — a summary of only skips and warnings is not a run.
_PYTEST_RAN_RE = re.compile(
    r"\b[1-9]\d* (?:subtests? )?(?:passed|failed|error|errors|xfailed|xpassed)\b"
)
_UNITTEST_SUMMARY_RE = re.compile(
    r"(?m)^Ran [1-9]\d* tests? in \d+(?:\.\d+)?s\s*\n\s*"
    r"(?:OK(?: \(.*\))?|FAILED \(.*\))[ \t]*$"
)
#: jest: ``Tests:       1 failed, 9 passed, 10 total``.
_JEST_SUMMARY_RE = re.compile(
    r"(?m)^[ \t]*Tests:[ \t]+(?:\d+ (?:failed|passed|skipped|todo|total)(?:, )?)+[ \t]*$"
)
#: vitest: ``Tests  1 failed | 9 passed (10)``.
_VITEST_SUMMARY_RE = re.compile(
    r"(?m)^[ \t]*Tests[ \t]+(?:\d+ (?:failed|passed|skipped|todo)(?: \| )?)+"
    r" \(\d+\)[ \t]*$"
)
#: mocha: ``10 passing (52ms)``, then ``1 pending`` and ``2 failing`` lines.
_MOCHA_SUMMARY_RE = re.compile(
    r"(?m)^[ \t]*\d+ passing \(\d+(?:\.\d+)?m?s\)[ \t]*$"
    r"(?:\n[ \t]*\d+ pending[ \t]*$)?(?:\n[ \t]*[1-9]\d* failing[ \t]*$)?"
)
#: go test: one ``ok`` / ``FAIL`` line per package.
_GO_SUMMARY_RE = re.compile(
    r"(?m)^(?:ok|FAIL)[ \t]+\S+(?:[ \t]+(?:\(cached\)|\d+(?:\.\d+)?s))?"
    r"(?:[ \t]+\[[^\]\n]*\])?(?:[ \t]+coverage:[^\n]*)?[ \t]*$"
)
_GO_RAN_RE = re.compile(r"^(?:ok|FAIL)\b(?![^\n]*\[no tests to run\])")
#: cargo test: one ``test result:`` line per target (unit, integration, doc).
_CARGO_SUMMARY_RE = re.compile(
    r"(?m)^test result: (?:ok|FAILED)\. \d+ passed; \d+ failed; \d+ ignored;"
    r" \d+ measured; \d+ filtered out(?:; finished in \d+(?:\.\d+)?s)?[ \t]*$"
)
_COUNTED_RUN_RE = re.compile(r"\b[1-9]\d* (?:passed|failed|passing|failing)\b")
#: A summary that counts a failure, in any runner's words.
_REPORTED_FAILURE_RE = re.compile(
    r"\b[1-9]\d* (?:subtests? )?(?:failed|error|errors|failing)\b"
    r"|\bFAILED \(|^FAIL\b|\btest result: FAILED\b"
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True)
class _Runner:
    label: str
    summary: "re.Pattern[str]"
    ran: "re.Pattern[str]"
    #: The runner reports per package or target, so one failure fails the run.
    per_target: bool = False


_RUNNERS: Tuple[_Runner, ...] = (
    _Runner("pytest", _PYTEST_SUMMARY_RE, _PYTEST_RAN_RE),
    _Runner("unittest", _UNITTEST_SUMMARY_RE, re.compile(r"^Ran [1-9]")),
    _Runner("jest", _JEST_SUMMARY_RE, _COUNTED_RUN_RE),
    _Runner("vitest", _VITEST_SUMMARY_RE, _COUNTED_RUN_RE),
    _Runner("mocha", _MOCHA_SUMMARY_RE, _COUNTED_RUN_RE),
    _Runner("go test", _GO_SUMMARY_RE, _GO_RAN_RE, per_target=True),
    _Runner("cargo test", _CARGO_SUMMARY_RE, _COUNTED_RUN_RE, per_target=True),
)


def _label(match: "re.Match[str]") -> str:
    label = " ".join(match.group(0).split()).lower()
    return _LABEL_ALIASES.get(label, label)


def check_kind(label: str) -> str:
    """The :data:`CHECK_KINDS` entry for a check *label*."""
    words = set(re.split(r"[^a-z0-9.]+", (label or "").lower()))
    for kind, markers in _KIND_WORDS:
        if words & markers:
            return kind
    return "test"


def command_check_label(command: str) -> Optional[str]:
    """Runner label for a runner named ANYWHERE in *command*, else ``None``.

    The text-only guess, for results whose tool declared nothing: it also
    matches ``cat pytest.ini``. A producer that has the argv uses
    :func:`argv_check_label` instead.
    """
    match = _CHECK_COMMAND_RE.search(command or "")
    return _label(match) if match else None


def argv_check_label(argv: Sequence[str]) -> Optional[str]:
    """Runner label when *argv* launches a check runner, else ``None``.

    The program has to BE the runner: ``pytest -q``, ``.venv/bin/python -m
    pytest``, ``uv run pytest``. ``echo pytest`` and ``cat pytest.ini`` are not.
    """
    if not argv:
        return None
    program = re.split(r"[/\\]", argv[0])[-1].lower()
    program = re.sub(r"\.exe$", "", program)
    program = re.sub(r"^python[\d.]*$", "python", program)
    text = " ".join([program, *argv[1:]])
    launcher = _LAUNCHER_RE.match(text)
    start = launcher.end() if launcher else 0
    match = _CHECK_COMMAND_RE.match(text, start)
    return _label(match) if match else None


def runner_summary(output: str) -> Optional[Tuple[str, str]]:
    """``(label, summary_line)`` for the last test-runner summary in *output*.

    Knows pytest, unittest, jest, vitest, mocha, ``go test`` and ``cargo test``
    by the summary each writes itself. The summary printed last wins — a
    snippet may run the suite more than once — except for runners that report
    per package or target, where any failing line decides the run.
    """
    text = _ANSI_RE.sub("", output or "")
    found: Optional[Tuple[str, str]] = None
    found_at = -1
    for runner in _RUNNERS:
        matches = [
            m for m in runner.summary.finditer(text) if runner.ran.search(m.group(0))
        ]
        if not matches or matches[-1].end() <= found_at:
            continue
        decisive = matches[-1]
        if runner.per_target:
            failed = [m for m in matches if _REPORTED_FAILURE_RE.search(m.group(0))]
            decisive = failed[-1] if failed else decisive
        found_at = matches[-1].end()
        found = (runner.label, " ".join(decisive.group(0).split()).strip(" ="))
    return found


def summary_reports_failure(summary: str) -> bool:
    """True when a runner's summary line counts a failure or an error."""
    return bool(_REPORTED_FAILURE_RE.search(_ANSI_RE.sub("", summary or "")))


def _output(stdout: Any, stderr: Any) -> str:
    return "\n".join(s for s in (stdout, stderr) if isinstance(s, str))


def check_from_command(
    command: str,
    segments: Sequence[Sequence[str]],
    return_code: Optional[int],
    stdout: Any = "",
    stderr: Any = "",
) -> Optional[CheckResult]:
    """The check a shell command ran, judged by its exit code AND its summary.

    *segments* are the argv of each pipeline stage; the first stage that
    launches a runner names the check. Both signals decide ``passed``, because
    ``pytest | tail`` exits with tail's status — a summary that counts a
    failure is a failure whatever the pipeline returned. ``return_code`` is
    ``None`` when the run never finished (timed out).
    """
    label = next((found for seg in segments if (found := argv_check_label(seg))), None)
    if label is None:
        return None
    found = runner_summary(_output(stdout, stderr))
    summary = found[1] if found else ""
    return CheckResult(
        label=label,
        target=" ".join(command.split()),
        kind=check_kind(label),
        passed=return_code == 0 and not summary_reports_failure(summary),
        summary=summary,
    )


def check_from_python_run(
    target: str, return_code: int, stdout: Any = "", stderr: Any = ""
) -> Optional[CheckResult]:
    """The test run a Python file or snippet performed, read from its output.

    Python can run anything, so only a runner's own summary line makes it a
    check. *target* identifies what ran — the file and its arguments, or
    :func:`snippet_target` — so reruns of the same code group together.
    """
    found = runner_summary(_output(stdout, stderr))
    if found is None:
        return None
    label, summary = found
    return CheckResult(
        label=label,
        target=target,
        kind="test",
        passed=return_code == 0 and not summary_reports_failure(summary),
        summary=summary,
    )


def snippet_target(code: str) -> str:
    """A short stable target for an inline snippet, without echoing its source."""
    normalized = " ".join((code or "").split()).encode("utf-8")
    return f"snippet:{hashlib.sha256(normalized).hexdigest()[:12]}"
