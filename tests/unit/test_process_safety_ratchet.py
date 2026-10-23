# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Ratchet: the bug shapes behind most recent hang, orphan and silent-success
fixes may not grow.

Nearly every recent fix shipped a mocked regression test, yet CI caught almost
none of the original bugs: each test guards the one call site it was written
for, and the next site with the same shape ships untested. This scan counts the
shapes themselves across ``src/gaia`` and the hub agent packages.

Rules (each counted per file, outside test directories):

* ``subprocess_no_timeout`` — ``subprocess.run`` / ``call`` / ``check_call`` /
  ``check_output`` with no ``timeout=`` (or ``timeout=None``). A child that stops making progress
  freezes the caller forever (ffmpeg #5383, MCP stdio #5346, pip lock #5381).
* ``http_no_timeout`` — ``requests.<verb>`` or ``urlopen`` with no ``timeout=``
  (or ``timeout=None``).
  Both wait forever by default (web fetch #5384). ``httpx`` is not counted: it
  defaults to a 5 s timeout.
* ``kill_by_name`` — ``taskkill /im``, ``pkill`` or ``killall`` in a command.
  Killing by image name hits processes GAIA didn't start (the user's ngrok
  #5376, every PowerPoint #5345, the developer's own Lemonade #5439).
* ``swallowed_exception`` — a handler that catches ``Exception`` or
  ``BaseException`` (bare, by name, or in a tuple) whose whole body is ``pass``,
  ``continue`` or a placeholder return (``None``, a constant, ``-1``, an empty
  ``[]`` / ``{}`` / ``()``). It turns a
  failure into a quiet success (#5112, #5113, #5114, #5432) and breaks the
  repo's fail-loudly rule.

Today's sites are counted in ``process_safety_allowlist.txt``. The test fails
when a file gains a site, and when a file loses one without the allowlist being
lowered, so the counts only go down. A call that passes ``**kwargs`` is not
counted: the timeout may be inside them.

Not covered, so a clean scan is not proof of no hang: ``Popen(...).wait()`` /
``.communicate()`` without a timeout, ``requests.Session`` methods,
``functools.partial(subprocess.run, ...)``, and kill commands built in a
variable rather than written at the call.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ALLOWLIST = Path(__file__).with_name("process_safety_allowlist.txt")
_SKIP_PARTS = {
    "tests",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
}
_SUBPROCESS_BLOCKING = {"run", "call", "check_call", "check_output"}
_REQUESTS_VERBS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "request",
}
_BROAD = {"Exception", "BaseException"}
RULES = (
    "subprocess_no_timeout",
    "http_no_timeout",
    "kill_by_name",
    "swallowed_exception",
)

Site = Tuple[str, str]  # (relative file, rule)


def _scanned_files() -> List[Path]:
    roots = [
        _REPO / "src" / "gaia",
        *sorted((_REPO / "hub" / "agents").glob("*/python")),
    ]
    files = []
    for root in roots:
        for path in root.rglob("*.py"):
            rel = path.relative_to(_REPO)
            if _SKIP_PARTS.intersection(rel.parts) or path.name.startswith("test_"):
                continue
            files.append(path)
    return sorted(files)


class _Aliases:
    """Names a module binds to ``subprocess``, ``requests`` and their functions."""

    def __init__(self, tree: ast.AST):
        self.modules: Dict[str, str] = {}  # local name -> real module
        self.functions: Dict[str, Tuple[str, str]] = {}  # local -> (module, func)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.modules[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    self.functions[alias.asname or alias.name] = (
                        node.module,
                        alias.name,
                    )

    def resolve(self, func: ast.AST) -> Optional[Tuple[str, str]]:
        """``(module, function)`` a call target names, or None."""
        if isinstance(func, ast.Name):
            return self.functions.get(func.id)
        if isinstance(func, ast.Attribute):
            value = func.value
            if isinstance(value, ast.Name) and value.id in self.modules:
                return self.modules[value.id], func.attr
            if isinstance(value, ast.Attribute):
                # urllib.request.urlopen
                parts = [func.attr]
                while isinstance(value, ast.Attribute):
                    parts.append(value.attr)
                    value = value.value
                if isinstance(value, ast.Name):
                    root = self.modules.get(value.id, value.id)
                    parts.append(root)
                    dotted = ".".join(reversed(parts))
                    module, _, name = dotted.rpartition(".")
                    return module, name
        return None


def _keywords(call: ast.Call) -> Set[Optional[str]]:
    """Keyword names, leaving out ``timeout=None``, which waits forever."""
    return {
        kw.arg
        for kw in call.keywords
        if not (
            kw.arg == "timeout"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is None
        )
    }


def _string_parts(node: ast.AST) -> Iterable[str]:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value


def _is_kill_by_name(call: ast.Call) -> bool:
    command = call.args[0] if call.args else None
    for kw in call.keywords:
        if kw.arg == "args":
            command = kw.value
    if command is None:
        return False
    words = [w.lower() for s in _string_parts(command) for w in s.split()]
    if not words:
        return False
    if words[0] in {"pkill", "killall"}:
        return True
    return "taskkill" in words and "/im" in words


def _is_broad(kind: Optional[ast.AST]) -> bool:
    if kind is None:
        return True
    if isinstance(kind, ast.Tuple):
        return any(_is_broad(elt) for elt in kind.elts)
    return (isinstance(kind, ast.Name) and kind.id in _BROAD) or (
        isinstance(kind, ast.Attribute) and kind.attr in _BROAD
    )


def _is_placeholder(value: Optional[ast.AST]) -> bool:
    if value is None or isinstance(value, ast.Constant):
        return True
    if isinstance(value, ast.UnaryOp):
        return isinstance(value.operand, ast.Constant)
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return not value.elts
    return isinstance(value, ast.Dict) and not value.keys


def _is_swallow(handler: ast.ExceptHandler) -> bool:
    if not _is_broad(handler.type) or len(handler.body) != 1:
        return False
    stmt = handler.body[0]
    if isinstance(stmt, (ast.Pass, ast.Continue)):
        return True
    return isinstance(stmt, ast.Return) and _is_placeholder(stmt.value)


def sites_in_source(source: str, filename: str = "<src>") -> List[Tuple[str, int]]:
    """``(rule, line)`` for every counted site in *source*."""
    tree = ast.parse(source, filename=filename)
    aliases = _Aliases(tree)
    found: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _is_swallow(node):
            found.append(("swallowed_exception", node.lineno))
            continue
        if not isinstance(node, ast.Call):
            continue
        target = aliases.resolve(node.func)
        if target is None:
            continue
        module, name = target
        kws = _keywords(node)
        unbounded = "timeout" not in kws and None not in kws
        if module == "subprocess":
            if name in _SUBPROCESS_BLOCKING | {"Popen"} and _is_kill_by_name(node):
                found.append(("kill_by_name", node.lineno))
            if name in _SUBPROCESS_BLOCKING and unbounded:
                found.append(("subprocess_no_timeout", node.lineno))
        elif module == "os" and name == "system" and _is_kill_by_name(node):
            found.append(("kill_by_name", node.lineno))
        elif module == "requests" and name in _REQUESTS_VERBS and unbounded:
            found.append(("http_no_timeout", node.lineno))
        elif name == "urlopen" and unbounded and len(node.args) < 3:
            found.append(("http_no_timeout", node.lineno))
    return found


def scan() -> Tuple[Counter, Dict[Site, List[int]]]:
    counts: Counter = Counter()
    lines: Dict[Site, List[int]] = {}
    for path in _scanned_files():
        rel = path.relative_to(_REPO).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            found = sites_in_source(source, str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise AssertionError(
                f"{rel}: cannot be parsed, so the process-safety ratchet cannot "
                f"vouch for it ({exc})."
            ) from exc
        for rule, line in found:
            counts[(rel, rule)] += 1
            lines.setdefault((rel, rule), []).append(line)
    return counts, lines


def _load_allowlist() -> Counter:
    allowed: Counter = Counter()
    for number, raw in enumerate(
        _ALLOWLIST.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            site, count = line.rsplit(" ", 1)
            rel, rule = site.rsplit(":", 1)
            allowed[(rel, rule)] = int(count)
        except ValueError as exc:
            raise AssertionError(
                f"{_ALLOWLIST.name}:{number}: expected '<file>:<rule> <count>', "
                f"got {raw!r}"
            ) from exc
        if rule not in RULES:
            raise AssertionError(f"{_ALLOWLIST.name}:{number}: unknown rule {rule!r}")
    return allowed


_FIXES = {
    "subprocess_no_timeout": "pass timeout= and handle subprocess.TimeoutExpired",
    "http_no_timeout": "pass timeout= (seconds) to the request",
    "kill_by_name": "stop the process by the PID GAIA recorded when it started it",
    "swallowed_exception": "catch the specific exception and log or re-raise it",
}


def test_no_new_process_safety_sites():
    counts, lines = scan()
    allowed = _load_allowlist()

    grown = {site: n for site, n in counts.items() if n > allowed.get(site, 0)}
    assert not grown, (
        "New hang / orphan / silent-success site(s):\n"
        + "\n".join(
            f"  {rel} ({rule}, lines {sorted(lines[(rel, rule)])}): {n} site(s), "
            f"{allowed.get((rel, rule), 0)} allowed — {_FIXES[rule]}"
            for (rel, rule), n in sorted(grown.items())
        )
        + f"\nDo not raise the count in tests/unit/{_ALLOWLIST.name}."
    )

    shrunk = {site: n for site, n in allowed.items() if counts.get(site, 0) < n}
    assert not shrunk, (
        "Sites were removed. Lower the allowlist so they cannot come back. In "
        f"tests/unit/{_ALLOWLIST.name}:\n"
        + "\n".join(
            f"  {rel}:{rule} {n} -> "
            + (
                str(counts[(rel, rule)])
                if counts.get((rel, rule))
                else "delete the line"
            )
            for (rel, rule), n in sorted(shrunk.items())
        )
    )


def test_no_kill_by_name_anywhere():
    counts, lines = scan()
    hits = {site: lines[site] for site in counts if site[1] == "kill_by_name"}
    assert not hits, (
        "Killing a process by image name hits processes GAIA didn't start "
        "(#5345, #5376, #5439):\n"
        + "\n".join(f"  {rel}: lines {ls}" for (rel, _), ls in sorted(hits.items()))
    )


@pytest.mark.parametrize(
    "source, expected",
    [
        ("import subprocess\nsubprocess.run(['x'])", ["subprocess_no_timeout"]),
        ("import subprocess as sp\nsp.check_output('x')", ["subprocess_no_timeout"]),
        ("from subprocess import run\nrun(['x'])", ["subprocess_no_timeout"]),
        ("from subprocess import run as r\nr(['x'])", ["subprocess_no_timeout"]),
        ("import subprocess\nsubprocess.run(['x'], timeout=5)", []),
        ("import subprocess\nsubprocess.run(['x'], **kw)", []),
        (
            "import subprocess\nsubprocess.run(['x'], timeout=None)",
            ["subprocess_no_timeout"],
        ),
        ("import subprocess\nsubprocess.Popen(['x'])", []),
        ("import requests\nrequests.get(u)", ["http_no_timeout"]),
        ("import requests\nrequests.post(u, timeout=3)", []),
        ("import httpx\nhttpx.get(u)", []),
        ("import urllib.request\nurllib.request.urlopen(u)", ["http_no_timeout"]),
        ("from urllib.request import urlopen\nurlopen(u)", ["http_no_timeout"]),
        ("from urllib.request import urlopen\nurlopen(u, None, 5)", []),
        (
            "import subprocess\nsubprocess.run(['taskkill', '/F', '/IM', 'x.exe'], timeout=5)",
            ["kill_by_name"],
        ),
        (
            "import subprocess\nsubprocess.Popen('pkill -f ngrok', shell=True)",
            ["kill_by_name"],
        ),
        ("import os\nos.system('killall POWERPNT')", ["kill_by_name"]),
        (
            "import subprocess\nsubprocess.Popen(args=['pkill', 'ngrok'])",
            ["kill_by_name"],
        ),
        (
            "import subprocess\nsubprocess.run(['taskkill', '/PID', p, '/T'], timeout=5)",
            [],
        ),
        ("try:\n  x()\nexcept Exception:\n  pass", ["swallowed_exception"]),
        (
            "def f():\n  try:\n    x()\n  except:\n    return None",
            ["swallowed_exception"],
        ),
        (
            "for a in b:\n  try:\n    x()\n  except BaseException:\n    continue",
            ["swallowed_exception"],
        ),
        ("try:\n  x()\nexcept (OSError, Exception):\n  pass", ["swallowed_exception"]),
        (
            "def f():\n  try:\n    x()\n  except Exception:\n    return -1",
            ["swallowed_exception"],
        ),
        (
            "def f():\n  try:\n    x()\n  except Exception:\n    return []",
            ["swallowed_exception"],
        ),
        (
            "def f():\n  try:\n    x()\n  except Exception:\n    return {}",
            ["swallowed_exception"],
        ),
        ("def f():\n  try:\n    x()\n  except Exception:\n    return [y]", []),
        ("try:\n  x()\nexcept OSError:\n  pass", []),
        ("try:\n  x()\nexcept (OSError, ValueError):\n  pass", []),
        ("try:\n  x()\nexcept Exception:\n  log(); raise", []),
        (
            "def f():\n  try:\n    x()\n  except Exception as e:\n    return {'error': str(e)}",
            [],
        ),
    ],
)
def test_scanner_recognises_each_shape(source, expected):
    assert [rule for rule, _ in sites_in_source(source)] == expected
