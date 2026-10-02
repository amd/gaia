# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What else in the project an edit to a Python function reaches.

An edit that changes a function's signature breaks its callers elsewhere, and
an edit to a method often needs the same change in the other classes that
define it. Both are found by searching the whole project, which a model tends
to scope to the folder it is working in. The edit result carries them instead.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gaia.logger import get_logger

log = get_logger(__name__)

#: Most locations listed per name; beyond it the count is still given.
MAX_SITES = 12
#: A method name defined in more places than this is too common to be a lead.
MAX_OTHER_DEFINITIONS = 8
GIT_TIMEOUT_S = 10


def _functions(source: str) -> Optional[Dict[str, Tuple[str, str, bool]]]:
    """``qualname -> (source segment, argument list, is_method)``; None if unparsable."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    found: Dict[str, Tuple[str, str, bool]] = {}

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.", True)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                segment = ast.get_source_segment(source, child) or ""
                found[name] = (segment, ast.unparse(child.args), in_class)
                visit(child, f"{name}.", False)

    visit(tree, "", False)
    return found


def _project_root(path: Path) -> Optional[Path]:
    path = path.resolve()
    for parent in [path.parent, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _git_grep(root: Path, pattern: str) -> List[str]:
    """``file:line: text`` for every ``*.py`` line matching *pattern*.

    Plain POSIX extended regex: macOS git grep has no ``\\b`` or ``\\s``.
    """
    try:
        done = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "grep",
                "--untracked",
                "-n",
                "-E",
                "-e",
                pattern,
                "--",
                "*.py",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("edit impact: git grep in %s failed: %s", root, exc)
        return []
    # 1 is git grep's "no match"; anything else is a failure worth a log line.
    if done.returncode not in (0, 1):
        log.warning("edit impact: git grep in %s: %s", root, done.stderr.strip())
    return [line for line in done.stdout.splitlines() if line.strip()]


def _sites(lines: List[str]) -> List[str]:
    return [" ".join(line.split())[:160] for line in lines[:MAX_SITES]]


def edit_impact(path: Path, before: str, after: str) -> Optional[Dict[str, object]]:
    """Callers of changed signatures, and other definitions of changed methods.

    ``None`` when there is nothing to report, the file is not Python, either
    version does not parse, or the file is not inside a git checkout.
    """
    if path.suffix != ".py":
        return None
    old, new = _functions(before), _functions(after)
    if old is None or new is None:
        return None
    root = _project_root(path)
    if root is None:
        return None
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None

    report: Dict[str, object] = {}
    callers = []
    for qualname, (_, args, _) in new.items():
        if qualname not in old or old[qualname][1] == args:
            continue
        name = qualname.rsplit(".", 1)[-1]
        if name.startswith("__"):
            continue
        uses = [
            line
            for line in _git_grep(
                root, rf"(^|[^[:alnum:]_]){re.escape(name)}([^[:alnum:]_]|$)"
            )
            if not re.search(rf"\bdef\s+{re.escape(name)}\b", line)
        ]
        if uses:
            callers.append(
                {
                    "function": qualname,
                    "signature": f"({old[qualname][1]}) -> ({args})",
                    "uses": len(uses),
                    "sites": _sites(uses),
                }
            )
    if callers:
        report["signature_changed"] = callers

    siblings = []
    for qualname, (segment, _, is_method) in new.items():
        if not is_method or old.get(qualname, (None,))[0] == segment:
            continue
        name = qualname.rsplit(".", 1)[-1]
        if name.startswith("__"):
            continue
        others = [
            line
            for line in _git_grep(
                root, rf"def[[:space:]]+{re.escape(name)}[[:space:]]*\("
            )
            if not line.startswith(f"{rel}:")
        ]
        if 0 < len(others) <= MAX_OTHER_DEFINITIONS:
            siblings.append({"method": qualname, "also_defined_in": _sites(others)})
    if siblings:
        report["other_definitions"] = siblings

    if not report:
        return None
    report["note"] = (
        "Other code in this project uses or defines what this edit changed. "
        "Check each site still works, or say why it needs no change."
    )
    return report
