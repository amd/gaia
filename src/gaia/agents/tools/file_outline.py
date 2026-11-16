# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A line-numbered outline of a text file too large for one tool result.

A whole-file read of a large file used to come back condensed into small
archived parts the model paged through one call at a time. The outline lists
the file's definitions and headings with their line numbers instead, so the
natural next call is a ranged ``read_file(start_line=..., end_line=...)``.
"""

import ast
import re
from typing import List, Tuple

#: Declarations in common languages, matched at a line start. Python is
#: outlined from its syntax tree; the ``def`` form covers a file that won't parse.
_DECLARATION = re.compile(
    r"^[ \t]*(?:"
    r"(?:async[ \t]+)?def[ \t]+\w+|"
    r"(?:export[ \t]+)?(?:default[ \t]+)?(?:abstract[ \t]+)?(?:async[ \t]+)?"
    r"(?:function\*?|class|interface|enum|type|namespace)[ \t]+[\w$]+"
    r"|(?:export[ \t]+)?const[ \t]+[\w$]+[ \t]*=[ \t]*(?:async[ \t]+)?(?:\(|function)"
    r"|func[ \t]+(?:\([^)]*\)[ \t]*)?\w+"
    r"|(?:pub(?:\([\w ]+\))?[ \t]+)?(?:fn|struct|enum|trait|impl|mod)\b[^;{]*"
    r"|(?:public|private|protected|internal)[ \t][^=;]*\b(?:class|interface|enum|record|struct)[ \t]+\w+"
    r")"
)
_DIFF_FILE = re.compile(r"^diff --git ")
_HEADING = re.compile(r"^#{1,6}[ \t]+\S")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_ENTRY_CHARS = 100


def _python(text: str) -> List[Tuple[int, int, str]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    entries: List[Tuple[int, int, str]] = []

    def visit(nodes, depth: int) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                entries.append((node.lineno, depth, f"class {node.name}"))
                visit(node.body, depth + 1)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = (
                    "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                )
                entries.append((node.lineno, depth, f"{prefix} {node.name}"))

    visit(tree.body, 0)
    return entries


def _markdown(lines: List[str]) -> List[Tuple[int, int, str]]:
    entries: List[Tuple[int, int, str]] = []
    fenced = False
    for number, line in enumerate(lines, 1):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and _HEADING.match(line):
            level = len(line) - len(line.lstrip("#"))
            entries.append((number, level - 1, line.strip()))
    return entries


def _by_pattern(lines: List[str], pattern) -> List[Tuple[int, int, str]]:
    return [
        (number, 0, line.strip())
        for number, line in enumerate(lines, 1)
        if pattern.match(line)
    ]


def outline_entries(text: str, file_type: str) -> List[Tuple[int, int, str]]:
    """``(line, depth, label)`` for each definition or heading, in file order."""
    lines = text.splitlines()
    if file_type == "python":
        entries = _python(text)
        if entries:
            return entries
    if file_type in ("markdown", "md", "mdx"):
        return _markdown(lines)
    if any(_DIFF_FILE.match(line) for line in lines[:50]) or file_type in (
        "diff",
        "patch",
    ):
        return _by_pattern(lines, _DIFF_FILE)
    return _by_pattern(lines, _DECLARATION)


def render_outline(entries: List[Tuple[int, int, str]], budget: int) -> str:
    """The entries as ``line: label`` rows, within *budget* characters.

    Deeper entries are dropped first. If the rest still does not fit, every
    n-th entry is kept, so the outline still spans the whole file, and its
    last line says how many it shows.
    """

    def rows(kept):
        return [
            f"{line:>6}: {'  ' * depth}{label[:_ENTRY_CHARS]}"
            for line, depth, label in kept
        ]

    kept = list(entries)
    depth = max((d for _, d, _ in kept), default=0)
    while depth > 0 and len("\n".join(rows(kept))) > budget:
        kept = [entry for entry in kept if entry[1] < depth]
        depth -= 1
    rendered = rows(kept)
    if len("\n".join(rendered)) <= budget:
        return "\n".join(rendered)
    step = 2
    while True:
        sampled = rendered[::step]
        note = (
            f"... 1 in {step} of {len(rendered)} entries shown; "
            "search_file_content finds a name"
        )
        text = "\n".join(sampled + [note])
        if len(text) <= budget or len(sampled) <= 1:
            return text
        step += 1
