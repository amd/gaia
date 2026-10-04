# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Ratchet: no new code may hard-code the GAIA home directory (#4442).

A path built from ``Path.home() / ".gaia"`` or ``"~/.gaia/..."`` ignores
``GAIA_HOME``, so a portable or per-user install leaks state into the real home
directory. Today's sites are counted per file in ``gaia_home_allowlist.txt``;
this test fails when a file gains a site, and when a file loses one without the
allowlist being lowered — so the migration to ``gaia.config.gaia_home()`` can
only shrink the list.

Two shapes are counted, both outside docstrings:

* ``home_join`` — ``".gaia"`` as a path segment: an operand of ``/``, an
  argument to ``os.path.join`` / ``ntpath.join`` / ``.joinpath``, or a
  non-first argument to a bare ``join`` / ``joinpath`` / ``Path`` /
  ``PurePath`` call (``Path(Path.home(), ".gaia")``).
* ``tilde_path`` — a string that *is* a home path, i.e. starts with
  ``~/.gaia`` or ``~\\.gaia`` (``expanduser("~/.gaia/x")``, a default value).
  Prose that merely mentions ``~/.gaia`` mid-sentence is not counted, except
  in an f-string, where text right after a ``{...}`` starting ``~/.gaia`` is.

``src/gaia/config.py`` is exempt: it is the resolver.
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

_REPO = Path(__file__).resolve().parents[2]
_ALLOWLIST = Path(__file__).with_name("gaia_home_allowlist.txt")
_EXEMPT = {"src/gaia/config.py"}
_SKIP_PARTS = {
    "tests",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
}
_JOIN_FUNCS = {"join", "joinpath"}
_BARE_JOIN_FUNCS = _JOIN_FUNCS | {"Path", "PurePath"}

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
            if _SKIP_PARTS.intersection(rel.parts) or rel.as_posix() in _EXEMPT:
                continue
            files.append(path)
    return sorted(files)


def _is_gaia_segment(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value == ".gaia" or node.value.startswith((".gaia/", ".gaia\\")))
    )


def _is_tilde_path(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(("~/.gaia", "~\\.gaia"))
    )


def _docstring_nodes(tree: ast.AST) -> set:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                ids.add(id(body[0].value))
    return ids


def _sites_in(path: Path) -> List[Tuple[str, int]]:
    """``(rule, line)`` for every hard-coded GAIA-home site in *path*."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise AssertionError(
            f"{path}: cannot be parsed, so the GAIA_HOME ratchet cannot vouch "
            f"for it ({exc})."
        ) from exc
    docstrings = _docstring_nodes(tree)
    found: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if _is_gaia_segment(node.right):
                found.append(("home_join", node.lineno))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _JOIN_FUNCS:
                segments = node.args
            elif isinstance(func, ast.Name) and func.id in _BARE_JOIN_FUNCS:
                # A leading ".gaia" is relative to the cwd, not the home dir.
                segments = node.args[1:]
            else:
                segments = []
            found.extend(
                ("home_join", arg.lineno) for arg in segments if _is_gaia_segment(arg)
            )
        elif _is_tilde_path(node) and id(node) not in docstrings:
            found.append(("tilde_path", node.lineno))
    return found


def scan() -> Tuple[Counter, Dict[Site, List[int]]]:
    counts: Counter = Counter()
    lines: Dict[Site, List[int]] = {}
    for path in _scanned_files():
        rel = path.relative_to(_REPO).as_posix()
        for rule, line in _sites_in(path):
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
    return allowed


def _format(counts: Counter) -> str:
    return "\n".join(
        f"{rel}:{rule} {counts[(rel, rule)]}" for rel, rule in sorted(counts)
    )


def test_no_new_hard_coded_gaia_home_paths():
    counts, lines = scan()
    allowed = _load_allowlist()

    grown = {site: n for site, n in counts.items() if n > allowed.get(site, 0)}
    assert not grown, (
        "New hard-coded GAIA home path(s) — these ignore GAIA_HOME (#4442):\n"
        + "\n".join(
            f"  {rel} ({rule}, lines {sorted(lines[(rel, rule)])}): "
            f"{n} site(s), {allowed.get((rel, rule), 0)} allowed"
            for (rel, rule), n in sorted(grown.items())
        )
        + "\nBuild the path from gaia.config.gaia_home() instead, e.g. "
        '`gaia_home() / "memory.db"`. Do not raise the count in '
        f"tests/unit/{_ALLOWLIST.name}."
    )

    shrunk = {site: n for site, n in allowed.items() if counts.get(site, 0) < n}
    assert not shrunk, (
        "Hard-coded GAIA home sites were removed. Lower the "
        f"allowlist so they cannot come back. In tests/unit/{_ALLOWLIST.name}:\n"
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


def test_scanner_catches_each_shape(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""Docstring mentioning "~/.gaia/x" is ignored."""\n'
        "import os\n"
        "from pathlib import Path\n"
        'a = Path.home() / ".gaia" / "memory.db"\n'
        'b = os.path.join(os.path.expanduser("~"), ".gaia", "tui")\n'
        'c = Path.home().joinpath(".gaia/x")\n'
        'd = os.path.expanduser("~/.gaia/schedules.toml")\n'
        'e = "Settings live in ~/.gaia, see the docs"\n'
        'f = Path(".gaia/cache")\n'
        'g = Path(Path.home(), ".gaia")\n'
        'h = join(home, ".gaia", "tui")\n'
        'i = PurePath(home, ".gaia")\n'
        'j = join(".gaia", "x")\n',
        encoding="utf-8",
    )
    assert sorted(_sites_in(sample)) == [
        ("home_join", 4),
        ("home_join", 5),
        ("home_join", 6),
        ("home_join", 10),
        ("home_join", 11),
        ("home_join", 12),
        ("tilde_path", 7),
    ]


if __name__ == "__main__":  # pragma: no cover
    # Prints the current counts in allowlist format, for reviewing a migration.
    sys.stdout.write(_format(scan()[0]) + "\n")
