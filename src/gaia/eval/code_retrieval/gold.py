# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a fix touched: files and enclosing functions/classes, read from its diff.

A gold patch says which lines changed, in old-file (base commit) numbering.
The relevant files are the ones it modifies or deletes; a file it creates did
not exist at the base, so nothing could have retrieved it. The relevant
symbols are the innermost ``def``/``class`` around each changed line, found
with :mod:`ast` in the base version of the file. Lines that replace
others are credited through the lines they replace. A pure insertion is
credited to the innermost symbol around the line before it whose ``def`` is
indented less than the inserted code: a statement appended to a method belongs
to the method, a method appended to a class belongs to the class, and a new
top-level function belongs to nothing.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Set, Tuple

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class GoldError(ValueError):
    """A patch could not be read as a unified diff."""


@dataclass
class FileChange:
    """One file in a unified diff, in base-commit terms."""

    path: str
    #: Base lines the patch removed or rewrote.
    changed_lines: Set[int] = field(default_factory=set)
    #: Pure insertions: the base line they follow, and their first non-blank line.
    insertions: List[Tuple[int, Optional[str]]] = field(default_factory=list)
    created: bool = False
    deleted: bool = False


def _strip_prefix(path: str) -> Optional[str]:
    path = path.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def parse_patch(patch: str) -> List[FileChange]:
    """Every file *patch* touches, with its changed base lines."""
    changes: List[FileChange] = []
    current: Optional[FileChange] = None
    old_line = 0
    # Lines left in the current hunk, per side: a hunk ends by count, so a
    # removed line reading "-- x" is never mistaken for a "--- " header.
    old_left = new_left = 0
    # A run of -/+ lines; its + lines are an insertion only if it removed nothing.
    run_removed = False
    run_insertion: Optional[int] = None
    # Not splitlines(): a form feed inside a line is not a line break in a diff.
    lines = patch.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if (old_left or new_left) and current is not None:
            if line.startswith("-"):
                current.changed_lines.add(old_line)
                old_line += 1
                old_left -= 1
                run_removed = True
            elif line.startswith("+"):
                text = line[1:] if line[1:].strip() else None
                if not run_removed:
                    if run_insertion is None:
                        current.insertions.append((old_line - 1, text))
                        run_insertion = len(current.insertions) - 1
                    elif current.insertions[run_insertion][1] is None:
                        current.insertions[run_insertion] = (old_line - 1, text)
                new_left -= 1
            elif line.startswith(" ") or line == "":
                old_line += 1
                old_left -= 1
                new_left -= 1
                run_removed, run_insertion = False, None
            elif not line.startswith("\\"):
                raise GoldError(f"unexpected line inside a hunk: {line!r}")
            # "\ No newline at end of file" is not a line of either side.
        elif (
            line.startswith("--- ")
            and i + 1 < len(lines)
            and lines[i + 1].startswith("+++ ")
        ):
            old = _strip_prefix(line[4:])
            new = _strip_prefix(lines[i + 1][4:])
            path = old if old is not None else new
            if path is None:
                raise GoldError(f"diff header names no file: {line!r}")
            current = FileChange(
                path=path,
                created=old is None,
                deleted=new is None,
            )
            changes.append(current)
            i += 2
            continue
        elif line.startswith("@@"):
            match = _HUNK.match(line)
            if not match or current is None:
                raise GoldError(f"hunk header outside a file diff: {line!r}")
            old_line = int(match.group(1))
            run_removed, run_insertion = False, None
            old_left = int(match.group(2) if match.group(2) is not None else 1)
            new_left = int(match.group(4) if match.group(4) is not None else 1)
            # A zero-length old side ("-12,0") names the line *before* the insertion.
            if old_left == 0:
                old_line += 1
        i += 1
    if patch.strip() and not changes:
        raise GoldError("the patch names no files")
    return changes


@dataclass(frozen=True)
class Symbol:
    """A ``def`` or ``class`` in one file of the base commit."""

    path: str
    name: str
    qualname: str
    #: First line, decorators included.
    start: int
    end: int
    #: The ``def``/``class`` line, which is where a code-index chunk starts.
    def_line: int = 0
    indent: int = 0


def python_symbols(path: str, source: str) -> List[Symbol]:
    """Every function and class in *source*, nested ones included.

    Empty when the file does not parse (the code index then falls back to
    block chunks with no symbols, so there is nothing to match either way).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    found: List[Symbol] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}{child.name}"
                found.append(
                    Symbol(
                        path=path,
                        name=child.name,
                        qualname=qual,
                        start=min(
                            [child.lineno] + [d.lineno for d in child.decorator_list]
                        ),
                        end=getattr(child, "end_lineno", child.lineno),
                        def_line=child.lineno,
                        indent=child.col_offset,
                    )
                )
                walk(child, qual + ".")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def enclosing(symbols: List[Symbol], line: int) -> List[Symbol]:
    """Every symbol whose span holds *line*, innermost first."""
    hits = [s for s in symbols if s.start <= line <= s.end]
    return sorted(hits, key=lambda s: s.end - s.start)


def innermost(symbols: List[Symbol], line: int) -> Optional[Symbol]:
    hits = enclosing(symbols, line)
    return hits[0] if hits else None


def _insertion_owner(
    symbols: List[Symbol], before: int, text: Optional[str]
) -> Optional[Symbol]:
    if before < 1 or text is None:
        return None
    indent = len(text) - len(text.lstrip())
    for sym in enclosing(symbols, before):
        if sym.indent < indent:
            return sym
    return None


@dataclass
class Gold:
    """The retrieval targets for one query."""

    files: List[str]
    #: ``(path, qualified name)``, e.g. ``("m.py", "Session.request")``.
    symbols: List[Tuple[str, str]]
    #: Files the patch creates: absent at the base, so not scored.
    created: List[str]


def gold_from_patch(patch: str, read_base: Callable[[str], Optional[str]]) -> Gold:
    """The files and symbols *patch* changes; *read_base* returns a base file's text."""
    files: List[str] = []
    created: List[str] = []
    symbols: List[Tuple[str, str]] = []
    for change in parse_patch(patch):
        if change.created:
            created.append(change.path)
            continue
        if change.path not in files:
            files.append(change.path)
        if not change.path.endswith((".py", ".pyw")):
            continue
        source = read_base(change.path)
        if source is None:
            raise GoldError(
                f"{change.path} is modified by the patch but absent at the base commit"
            )
        syms = python_symbols(change.path, source)
        hits: List[Symbol] = []
        for ln in sorted(change.changed_lines):
            sym = innermost(syms, ln)
            if sym is not None:
                hits.append(sym)
        for before, text in change.insertions:
            sym = _insertion_owner(syms, before, text)
            if sym is not None:
                hits.append(sym)
        for sym in hits:
            key = (sym.path, sym.qualname)
            if key not in symbols:
                symbols.append(key)
    return Gold(files=files, symbols=symbols, created=created)
