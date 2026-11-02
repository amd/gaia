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

import ast
import hashlib
import re
import shlex
from dataclasses import asdict, dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

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

#: ``<tool> run`` launchers that run their first non-option word as the program.
_RUN_LAUNCHERS = frozenset({"uv", "poetry", "pipenv", "hatch", "pdm"})
#: Launchers that run their first non-option word as the program.
_BARE_LAUNCHERS = frozenset({"npx"})
#: Launcher options whose value is the next word (``uv run --with pytest x``).
_LAUNCHER_VALUE_FLAGS = frozenset(
    {
        "--with",
        "--with-editable",
        "--with-requirements",
        "--python",
        "-p",
        "--project",
        "--directory",
        "--env-file",
        "--extra",
        "--group",
        "--only-group",
        "--no-group",
        "--package",
        "--index",
        "--index-url",
        "--extra-index-url",
        "--default-index",
        "-i",
        "--find-links",
        "-f",
        "--config-file",
        "--cache-dir",
    }
)
#: Python long options whose value is the next word.
_PYTHON_LONG_VALUE_FLAGS = frozenset({"--check-hash-based-pycs"})
#: The ``py`` launcher's version selector: ``-3``, ``-3.12``, ``-3.12-32``.
_PY_VERSION_FLAG_RE = re.compile(r"-\d+(?:\.\d+)?(?:-\d+)?|-V:\S+")

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
#: go test: one ``ok`` / ``FAIL`` line per package. The duration, ``(cached)``
#: or ``[...]`` trailer is required — without it ``print("ok done")`` reads as a
#: passing run.
_GO_SUMMARY_RE = re.compile(
    r"(?m)^(?:ok|FAIL)[ \t]+\S+"
    r"(?:[ \t]+(?:\(cached\)|\d+(?:\.\d+)?s)(?:[ \t]+\[[^\]\n]*\])?"
    r"|[ \t]+\[[^\]\n]*\])"
    r"(?:[ \t]+coverage:[^\n]*)?[ \t]*$"
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


def _program_name(word: str) -> str:
    """*word*'s lowercased basename, no Windows suffix; ``python`` for any Python."""
    program = re.split(r"[/\\]", word)[-1].lower()
    program = re.sub(r"\.(?:exe|cmd|bat)$", "", program)
    if program == "py" or re.fullmatch(r"python[\d.]*w?", program):
        return "python"
    return program


def _python_module_argv(options: Sequence[str]) -> Optional[List[str]]:
    """``[module, *args]`` when interpreter *options* end in ``-m module``, else None.

    Skips interpreter flags (``-u``, ``-X dev``, ``-W error``, the ``py``
    launcher's ``-3.12``). ``-c`` runs a code string and a bare word a script,
    so neither is a module.
    """
    i = 0
    while i < len(options):
        word = options[i]
        if not word.startswith("-") or word == "-":
            return None
        if word.startswith("--"):
            i += 2 if word in _PYTHON_LONG_VALUE_FLAGS else 1
            continue
        if _PY_VERSION_FLAG_RE.fullmatch(word):
            i += 1
            continue
        takes_next = False
        for j, letter in enumerate(word[1:]):
            if letter == "m":
                module = word[j + 2 :]
                rest = list(options[i + 1 :])
                if module:
                    return [module, *rest]
                return rest or None
            if letter == "c":
                return None
            if letter in "XW":
                takes_next = j == len(word) - 2
                break
        i += 2 if takes_next else 1
    return None


def _after_launcher(words: List[str]) -> Optional[List[str]]:
    """The argv a launcher runs (``uv run --with x pytest``: ``pytest``), or None."""
    if len(words) > 1 and words[0] in _RUN_LAUNCHERS and words[1] == "run":
        i = 2
    elif words and words[0] in _BARE_LAUNCHERS:
        i = 1
    else:
        return None
    while i < len(words) and words[i].startswith("-"):
        word = words[i]
        if word == "--":
            i += 1
            break
        if words[0] == "uv" and word in ("-m", "--module"):
            return ["python", "-m", *words[i + 1 :]]
        i += 2 if word in _LAUNCHER_VALUE_FLAGS else 1
    return words[i:]


def argv_check_label(argv: Sequence[str]) -> Optional[str]:
    """Runner label when *argv* launches a check runner, else ``None``.

    The program has to BE the runner: ``pytest -q``, ``.venv/bin/python -u -m
    pytest``, ``py -m pytest``, ``uv run --with pytest pytest``, ``npm.cmd
    test``. ``echo pytest`` and ``cat pytest.ini`` are not.
    """
    if not argv:
        return None
    words = [_program_name(argv[0]), *argv[1:]]
    launched = _after_launcher(words)
    if launched is not None:
        return argv_check_label(launched)
    if words[0] == "python":
        module = _python_module_argv(words[1:])
        if module is not None:
            words = ["python", "-m", *module]
    match = _CHECK_COMMAND_RE.match(" ".join(words))
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
    label = next(
        (matched for seg in segments if (matched := argv_check_label(seg))), None
    )
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


#: In-process runner entry points, by the dotted name they are imported under.
_RUNNER_CALLS: FrozenSet[str] = frozenset(
    {
        "pytest.main",
        "unittest.main",
        "unittest.TestProgram",
        "unittest.TextTestRunner",
        "unittest.runner.TextTestRunner",
        "unittest.main.TestProgram",
    }
)

#: Calls that start a program: how they name it, and the position of the
#: argument that does. ``argv`` is an argv list or a command string, ``shell`` a
#: command string, ``module`` a module run as ``python -m``, ``path`` a script,
#: ``varargs`` the argv itself. ``execv`` is the program then an argv list whose
#: first word is only its name; ``execl`` the same with the argv spread out.
_LAUNCH_CALLS: Dict[str, Tuple[str, int]] = {
    "subprocess.run": ("argv", 0),
    "subprocess.call": ("argv", 0),
    "subprocess.check_call": ("argv", 0),
    "subprocess.check_output": ("argv", 0),
    "subprocess.Popen": ("argv", 0),
    "subprocess.getoutput": ("shell", 0),
    "subprocess.getstatusoutput": ("shell", 0),
    "os.system": ("shell", 0),
    "os.popen": ("shell", 0),
    "asyncio.create_subprocess_shell": ("shell", 0),
    "asyncio.create_subprocess_exec": ("varargs", 0),
    "runpy.run_module": ("module", 0),
    "runpy.run_path": ("path", 0),
    **{f"os.exec{s}": ("execv", 0) for s in ("v", "ve", "vp", "vpe")},
    **{f"os.exec{s}": ("execl", 0) for s in ("l", "le", "lp", "lpe")},
    **{f"os.spawn{s}": ("execv", 1) for s in ("v", "ve", "vp", "vpe")},
    **{f"os.spawn{s}": ("execl", 1) for s in ("l", "le", "lp", "lpe")},
    "os.posix_spawn": ("execv", 0),
    "os.posix_spawnp": ("execv", 0),
}
#: Calls that return the module named by their first argument.
_IMPORT_CALLS = frozenset({"__import__", "importlib.import_module"})
#: Shells that run the word after ``-c`` as a command line.
_POSIX_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
#: How deep a shell may wrap another before the reader stops looking.
_MAX_SHELL_DEPTH = 4

#: A word that stands for the Python interpreter when it starts an argv.
_PYTHON_EXPRESSIONS = frozenset({"sys.executable"})
#: Shell text between these runs as a separate command.
_SHELL_COMMAND_BREAK_RE = re.compile(r"&&|\|\||[;|&\n]")
#: ``NAME=value`` in front of a shell command sets its environment.
_LEADING_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
#: A word whose value the code does not fix.
_UNKNOWN = "\0"


def _dotted(node: ast.AST, aliases: Dict[str, str]) -> Optional[str]:
    """``a.b.c`` for a Name/Attribute chain, with its import alias resolved."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Call):
        module = _imported_module(node, aliases)
        if module is None:
            return None
        parts.append(module)
        return ".".join(reversed(parts))
    if not isinstance(node, ast.Name):
        return None
    parts.append(aliases.get(node.id, node.id))
    return ".".join(reversed(parts))


def _imported_module(call: ast.Call, aliases: Dict[str, str]) -> Optional[str]:
    """The module ``__import__('x')`` / ``importlib.import_module('x')`` returns."""
    if _dotted(call.func, aliases) not in _IMPORT_CALLS or not call.args:
        return None
    arg = call.args[0]
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return None
    if isinstance(call.func, ast.Name) and call.func.id == "__import__":
        return arg.value.split(".")[0]
    return arg.value


class _LaunchReader:
    """What a launch call in one parsed snippet would run."""

    def __init__(self, tree: ast.AST, aliases: Dict[str, str]):
        self.aliases = aliases
        # Last assignment in source order wins: ``cmd = [...]; run(cmd)``.
        self.assigned: Dict[str, ast.AST] = {}
        assigns = [
            n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))
        ]
        for node in sorted(assigns, key=lambda n: (n.lineno, n.col_offset)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if node.value is not None and len(targets) == 1:
                if isinstance(targets[0], ast.Name):
                    self.assigned[targets[0].id] = node.value

    def _resolve(self, node: ast.AST, depth: int = 0) -> ast.AST:
        while isinstance(node, ast.Name) and node.id in self.assigned and depth < 8:
            node = self.assigned[node.id]
            depth += 1
        return node

    def text(self, node: ast.AST) -> Optional[str]:
        """The string *node* evaluates to; ``_UNKNOWN`` marks parts left open."""
        node = self._resolve(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(
                v.value if isinstance(v, ast.Constant) else self.word(v.value)
                for v in node.values
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # ``sys.executable + ' -m pytest'``; a list sum is argv(), not text.
            left, right = self.text(node.left), self.text(node.right)
            if left is None and right is None:
                return None
            return self.word(node.left) + self.word(node.right)
        if isinstance(node, ast.Call):
            name = _dotted(node.func, self.aliases)
            if name == "shutil.which" and node.args:
                return self.text(node.args[0])
        return None

    def word(self, node: ast.AST) -> str:
        """One argv word or string part; ``python`` for ``sys.executable``."""
        if _dotted(self._resolve(node), self.aliases) in _PYTHON_EXPRESSIONS:
            return "python"
        text = self.text(node)
        return _UNKNOWN if text is None else text

    def argv(self, node: ast.AST) -> Optional[List[str]]:
        """*node* as an argv list, or None when it is not one."""
        node = self._resolve(node)
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self.word(item) for item in node.elts]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # ``[sys.executable, '-m', 'pytest'] + sys.argv[1:]``: the head decides.
            head = self.argv(node.left)
            if head is None:
                return None
            tail = self.argv(node.right)
            return head + (tail if tail is not None else [_UNKNOWN])
        if not isinstance(node, ast.Call):
            return None
        if _dotted(node.func, self.aliases) == "shlex.split" and node.args:
            text = self.text(node.args[0])
            return None if text is None else _split_words(text)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "split"
            and not node.args
        ):
            text = self.text(node.func.value)
            return None if text is None else text.split()
        return None


def _split_words(text: str) -> List[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _shell_command_in(words: Sequence[str]) -> Optional[str]:
    """The command line a shell *words* runs (``sh -c '...'``, ``cmd /c ...``)."""
    program = _program_name(words[0])
    if program in _POSIX_SHELLS:
        skip_next = False
        for i, word in enumerate(words[1:], 1):
            if skip_next:
                skip_next = False
                continue
            if word in ("-o", "+o"):
                skip_next = True
                continue
            if not word.startswith(("-", "+")) or word.startswith("--"):
                return None
            if word.startswith("-") and "c" in word[1:]:
                return words[i + 1] if i + 1 < len(words) else None
        return None
    switches = {
        "cmd": ("/c", "/k"),
        "powershell": ("-c", "-command", "/c", "/command"),
        "pwsh": ("-c", "-command", "/c", "/command"),
    }.get(program)
    if switches is None:
        return None
    for i, word in enumerate(words[1:], 1):
        if word.lower() in switches:
            return " ".join(words[i + 1 :]) or None
    return None


def _argv_runs_check(argv: Sequence[str], depth: int = 0) -> bool:
    """True when *argv* starts a check runner, directly or through a shell.

    An unknown program followed by interpreter flags and ``-m`` is an
    interpreter path the code computed, such as ``os.path.join(venv, 'python')``.
    """
    words = list(argv)
    if not words:
        return False
    if words[0] == _UNKNOWN and _python_module_argv(words[1:]) is not None:
        words[0] = "python"
    if argv_check_label(words) is not None:
        return True
    command = _shell_command_in(words)
    return (
        command is not None
        and depth < _MAX_SHELL_DEPTH
        and _shell_runs_check(command, depth + 1)
    )


def _shell_runs_check(text: str, depth: int = 0) -> bool:
    """True when one command in shell *text* is a check runner."""
    for command in _SHELL_COMMAND_BREAK_RE.split(text):
        words = _split_words(command)
        while words and _LEADING_ENV_RE.match(words[0]):
            words = words[1:]
        if words and _argv_runs_check(words, depth):
            return True
    return False


def _launch_runs_check(
    call: ast.Call, how: str, position: int, reader: _LaunchReader
) -> bool:
    """True when *call*, a launch of kind *how*, starts a check runner."""
    args = call.args[position:]
    if how == "varargs":
        return bool(args) and _argv_runs_check([reader.word(a) for a in args])
    if how in ("execv", "execl"):
        if not args:
            return False
        if how == "execl":
            rest = [reader.word(a) for a in args[2:]]
        else:
            argv = reader.argv(args[1]) if len(args) > 1 else None
            rest = argv[1:] if argv else []
        return _argv_runs_check([reader.word(args[0]), *rest])
    target = args[0] if args else None
    if target is None:
        target = next((k.value for k in call.keywords if k.arg == "args"), None)
    if target is None:
        return False
    if how == "module":
        module = reader.text(target)
        return module is not None and _argv_runs_check(["python", "-m", module])
    if how == "path":
        path = reader.text(target)
        return path is not None and _argv_runs_check([path])
    if how == "argv":
        argv = reader.argv(target)
        if argv is not None:
            return _argv_runs_check(argv)
    text = reader.text(target)
    return text is not None and _shell_runs_check(text)


def invokes_test_runner(source: str) -> bool:
    """True when Python *source* runs a test runner, not just prints its output.

    A runner's summary line in the output is only evidence when the code
    could have produced it: it calls ``pytest.main`` / ``unittest.main`` /
    a ``TextTestRunner`` (also through ``__import__``), or launches a process
    (``subprocess``, ``os.system``, ``os.exec*``, ``runpy``) whose program is
    itself the runner (:func:`argv_check_label`), directly or through
    ``sh -c`` / ``cmd /c``. ``subprocess.run(['cat', 'pytest.log'])`` launches
    ``cat``, so it is not. Printing a saved log does neither. Unparseable
    source never ran, so it is not a runner.
    """
    try:
        tree = ast.parse(source or "")
    except (SyntaxError, ValueError):
        return False
    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                aliases[name.asname or name.name.split(".")[0]] = (
                    name.name if name.asname else name.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            for name in node.names:
                aliases[name.asname or name.name] = f"{node.module}.{name.name}"
    for node in ast.walk(tree):
        # ``pt = __import__('pytest')`` binds a module like an import does.
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
        ):
            module = _imported_module(node.value, aliases)
            if module is not None:
                aliases[node.targets[0].id] = module
    reader = _LaunchReader(tree, aliases)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func, aliases)
        if name in _RUNNER_CALLS:
            return True
        launch = _LAUNCH_CALLS.get(name or "")
        if launch is not None and _launch_runs_check(node, *launch, reader):
            return True
    return False


def check_from_python_run(
    target: str,
    return_code: int,
    stdout: Any = "",
    stderr: Any = "",
    *,
    source: str,
) -> Optional[CheckResult]:
    """The test run a Python file or snippet performed, read from its output.

    Python can run anything, so it is a check only when *source* — the code
    that ran — invokes a runner (:func:`invokes_test_runner`) and the output
    carries that runner's own summary line. *target* identifies what ran —
    the file and its arguments, or :func:`snippet_target` — so reruns of the
    same code group together.
    """
    if not invokes_test_runner(source):
        return None
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
