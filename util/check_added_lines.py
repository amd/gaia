#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Catch three bug patterns on lines a PR adds, never on existing code.

The tree already holds hundreds of matches, so a whole-repo check could not fail.
Each pattern shipped a real bug through review:

- a subprocess or HTTP call with no timeout (#5383 hung)
- a broad ``except`` that neither raises, logs nor reads the error (#5745)
- ``code=$?`` on its own line in a script running under ``set -e`` (#4942): the
  failing command stops the script first, so the line never runs

Add ``lint-ok: <reason>`` in a comment on the line to accept one on purpose.

Run ``python util/check_added_lines.py --base origin/main``.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ACCEPTED = "lint-ok:"
TIMED_CALLS = {
    "subprocess": {"run", "call", "check_call", "check_output"},
    "requests": {"get", "post", "put", "patch", "delete", "head", "request"},
    "httpx": {"get", "post", "put", "patch", "delete", "head", "request"},
}
LOG_CALLS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
SHELL_SUFFIXES = (".sh", ".yml", ".yaml")
EXIT_CAPTURE = re.compile(r"^\s*(?:export\s+)?\w+=\$\?\s*(?:#.*)?$")
ERREXIT_OFF = re.compile(r"\bset \+e\b|\bset \+o errexit\b")
ERREXIT_LOOKBACK = 10
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    message: str


def added_lines(diff: str) -> dict[str, set[int]]:
    """Map each file in a ``--unified=0`` diff to the line numbers it added."""
    added: dict[str, set[int]] = {}
    path = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[len("+++ b/") :]
        elif line.startswith("+++ "):
            path = ""
        elif path and (hunk := HUNK.match(line)):
            start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
            added.setdefault(path, set()).update(range(start, start + count))
    return added


def _catches_broadly(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    return caught is None or (
        isinstance(caught, ast.Name) and caught.id in ("Exception", "BaseException")
    )


def _handles_error(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Name) and node.id == handler.name:
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_CALLS
        ):
            return True
    return False


def check_python(path: str, source: str, lines: set[int]) -> list[Finding]:
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise SystemExit(f"{path}:{e.lineno}: cannot parse: {e.msg}") from e
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno in lines:
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.attr in TIMED_CALLS.get(func.value.id, ())
                and not any(kw.arg in ("timeout", None) for kw in node.keywords)
            ):
                findings.append(
                    Finding(
                        path,
                        node.lineno,
                        f"{func.value.id}.{func.attr}() has no timeout, so it can hang",
                    )
                )
        if (
            isinstance(node, ast.ExceptHandler)
            and node.lineno in lines
            and _catches_broadly(node)
            and not _handles_error(node)
        ):
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    "broad except drops the error: raise it, log it or use it",
                )
            )
    return findings


def check_shell(path: str, source: str, lines: set[int]) -> list[Finding]:
    text = source.splitlines()
    findings = []
    for number in sorted(lines):
        if number > len(text) or not EXIT_CAPTURE.match(text[number - 1]):
            continue
        before = text[max(0, number - 1 - ERREXIT_LOOKBACK) : number - 1]
        if not any(ERREXIT_OFF.search(line) for line in before):
            findings.append(
                Finding(
                    path,
                    number,
                    "`set -e` exits before this captures $?: use `cmd || code=$?`",
                )
            )
    return findings


def check(added: dict[str, set[int]], root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path, lines in added.items():
        full = root / path
        if not full.is_file() or path.startswith("tests/"):
            continue
        if path.endswith(".py"):
            checker = check_python
        elif path.endswith(SHELL_SUFFIXES):
            checker = check_shell
        else:
            continue
        source = full.read_text(encoding="utf-8")
        text = source.splitlines()
        findings += [
            f for f in checker(path, source, lines) if ACCEPTED not in text[f.line - 1]
        ]
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)
    result = subprocess.run(
        ["git", "diff", "--unified=0", "--no-color", f"{args.base}...HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=REPO_ROOT,
        timeout=120,
    )
    if result.returncode != 0:
        print(
            f"git diff against {args.base} failed: {result.stderr.strip()}\n"
            "Fetch the base branch first, or pass --base.",
            file=sys.stderr,
        )
        return 2
    findings = check(added_lines(result.stdout), REPO_ROOT)
    for f in findings:
        print(f"{f.path}:{f.line}: {f.message}")
    if findings:
        print(
            f"\n{len(findings)} problem(s) on added lines. {ACCEPTED} <reason> accepts one."
        )
        return 1
    print("OK: no problems on added lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
