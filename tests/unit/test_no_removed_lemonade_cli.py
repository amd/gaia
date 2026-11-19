# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""No runtime string may tell a user to run the removed ``lemonade-server`` CLI.

Lemonade 10.7/10.8 removed the ``lemonade-server`` CLI (``serve``, ``pull``,
``stop`` and the rest), so a hard-coded copy sends users to a command their
machine does not have (#4216). Comments and docstrings may still name it.
Start hints come from ``describe_start_hint()`` and pull hints
from ``describe_client_hint()``, which only name the legacy CLI when a legacy
install was actually resolved. The TUI resolves its own through the preflight
launcher.
"""

import ast
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = ("src/gaia", "hub", "tui")
_SCAN_EXTENSIONS = {
    ".py",
    ".go",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".cjs",
    ".html",
    ".md",
}
_SKIP_DIRS = {"node_modules", "dist", "build", ".turbo", "tests", "test", "__tests__"}
# The removed CLI's subcommands, as prose (`lemonade-server serve`) or as an
# argv list (["lemonade-server", "stop"]).
_REMOVED_CLI = re.compile(
    r"lemonade-server[\"'`]?\s*,?\s*[\"'`]?(serve|pull|stop|list|status|run|delete)\b"
)

# Files that may name the legacy CLI on purpose.
_ALLOWED = {
    # Builds the legacy argv only when a legacy install was resolved.
    "src/gaia/llm/lemonade_launcher.py",
    # Detects the stale hint from an older agent package and replaces it.
    "src/gaia/ui/_chat_helpers.py",
}


def _is_test_file(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("test_")
        or name.endswith("_test.go")
        or ".test." in name
        or ".spec." in name
    )


def _is_comment(line: str, suffix: str) -> bool:
    # In markdown `#` and `*` open a heading or a bullet, not a comment — a
    # bullet naming the removed CLI is exactly what the guard is looking for.
    markers = ("//", "/*") if suffix in {".md", ".html"} else ("#", "//", "*", "/*")
    return line.lstrip().startswith(markers)


def _docstring_lines(text: str) -> set:
    """Line numbers inside Python docstrings, which explain history, not instruct."""
    lines = set()
    for node in ast.walk(ast.parse(text)):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            lines.update(range(first.lineno, first.end_lineno + 1))
    return lines


def _runtime_files():
    for root in _SCAN_ROOTS:
        for path in sorted((_REPO / root).rglob("*")):
            if not path.is_file() or path.suffix not in _SCAN_EXTENSIONS:
                continue
            if any(part in _SKIP_DIRS for part in path.relative_to(_REPO).parts):
                continue
            # Changelogs record history, including what older releases said.
            if _is_test_file(path) or path.name == "CHANGELOG.md":
                continue
            yield path


def test_no_runtime_string_names_the_removed_lemonade_cli():
    offenders = []
    for path in _runtime_files():
        rel = path.relative_to(_REPO).as_posix()
        if rel in _ALLOWED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        skip = _docstring_lines(text) if path.suffix == ".py" else set()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if lineno in skip or _is_comment(line, path.suffix):
                continue
            if _REMOVED_CLI.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "These name a `lemonade-server` command, which current Lemonade installs "
        "don't have. Use describe_start_hint() / describe_client_hint() "
        "from gaia.llm.lemonade_launcher (or the TUI preflight resolver):\n  "
        + "\n  ".join(offenders)
    )
