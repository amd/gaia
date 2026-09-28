# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Index a large tool result by its own structure, so the model sees what it is missing.

A result over the budget reaches the conversation as ``shown`` (the parts kept
verbatim) plus ``index`` (one ``{label, offset, length}`` per part left out).
The whole text is archived; ``read_tool_output(artifact, offset, length)``
returns exactly one indexed part.

Every chunker tiles its text: chunks are in document order, each
``text[offset:offset + length]`` is that chunk, and together they are the text.
Boundaries follow the document's structure -- a ``def``, a heading, a JSON
record, a file's matches, a test section, a paragraph -- and fall at line
starts. The one exception is a JSON document on a single line, which splits
between values because it has no inner lines to split at.
"""

from __future__ import annotations

import ast
import bisect
import json
import os
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from gaia.logger import get_logger

log = get_logger(__name__)

#: A chunk this size or smaller is fetched whole by one ``read_tool_output``
#: call (limit 8000), with room left for the index to name it precisely.
MAX_CHUNK_CHARS = 4000
#: A section or paragraph shorter than this absorbs the one after it.
MIN_CHUNK_CHARS = 200
LABEL_CHARS = 90
SHORT_LABEL_CHARS = 48
JSON_GROUP = 20
#: Share of the room the index may take before adjacent entries are merged.
INDEX_SHARE = 0.6
#: More chunks than this are merged pairwise before selection; an index that
#: fits the budget holds far fewer entries anyway.
MAX_CANDIDATES = 256

#: Tools whose large text field is command output.
OUTPUT_TOOLS = frozenset(
    {"run_shell_command", "run_python", "execute_python_file", "run_tests"}
)
#: Carried by every condensed result, so the model learns the read from the result.
FETCH_HINT = "read_tool_output(artifact, entry=n) returns one indexed part verbatim"
#: Arguments whose value can name a part of the result (a symbol, a pattern).
NAMING_ARGS = ("pattern", "query", "function_name", "name", "symbol", "section")

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")
_SECTION_RE = re.compile(
    r"^(?:={3,}|_{3,} |-{5,}|FAILED\b|ERROR\b|FAIL:|"
    r"Traceback \(most recent call last\)|error(?:\[\w+\])?:|Error:)"
)
#: A section that reports a failure; pytest titles each failing test "___ name ___".
_FAILURE_RE = re.compile(r"FAIL|ERROR|Error|error|Traceback|^_{3,} ")
#: A chunk boundary: ``(start, label, lead, refs)`` plus an optional terse name.
Bound = Tuple[Any, ...]


class NotStructured(ValueError):
    """The text does not have the structure its chunker splits along."""


@dataclass(frozen=True)
class Chunk:
    """One structural part of a text: ``text[offset:offset + length]``."""

    offset: int
    length: int
    label: str
    kind: str
    lead: bool = False
    #: Structural addresses this chunk covers (JSON keys / item ranges).
    refs: Tuple[tuple, ...] = field(default=())
    #: Terse name used when the index has to shrink.
    short: str = ""
    #: How many original chunks this one merges (see :func:`fit_index`).
    parts: int = 1
    #: Terse names of the first and last original chunk it covers.
    ends: Tuple[str, str] = ("", "")

    @property
    def end(self) -> int:
        return self.offset + self.length


def label_for_unstructured(chunk: str) -> str:
    """One-line label for a chunk with no structure of its own.

    The seam for a model-written label: today it is the chunk's first
    non-blank line, which keeps indexing deterministic and model-free.
    """
    for line in chunk.splitlines():
        if line.strip():
            return _clip(line)
    return "(blank)"


def _clip(text: str, limit: int = LABEL_CHARS) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def _line_starts(text: str) -> List[int]:
    starts = [0]
    for match in re.finditer("\n", text):
        if match.end() < len(text):
            starts.append(match.end())
    return starts


def _lines(text: str) -> List[Tuple[int, str]]:
    """(offset, line-without-newline) for every line."""
    out = []
    for start in _line_starts(text):
        end = text.find("\n", start)
        out.append((start, text[start : end if end >= 0 else len(text)]))
    return out


def _tile(
    text: str,
    bounds: Sequence[Bound],
    kind: str,
    with_lines: bool = False,
) -> List[Chunk]:
    """Chunks from ``(start, label, lead, refs[, short])`` boundaries; the first must be 0."""
    bounds = sorted(
        {b[0]: b for b in bounds if 0 <= b[0] < len(text)}.values(), key=lambda b: b[0]
    )
    if not bounds or bounds[0][0] != 0:
        raise ValueError("chunk boundaries must start at offset 0")
    starts = _line_starts(text) if with_lines else []
    chunks = []
    for i, (start, label, lead, refs, *name) in enumerate(bounds):
        end = bounds[i + 1][0] if i + 1 < len(bounds) else len(text)
        short = _clip(name[0] if name else label, SHORT_LABEL_CHARS)
        if with_lines:
            first = bisect.bisect_right(starts, start)
            last = bisect.bisect_right(starts, max(start, end - 1))
            prefix = f"L{first}-{last} " if last > first else f"L{first} "
            label, short = prefix + label, _clip(prefix + short, SHORT_LABEL_CHARS)
        chunks.append(Chunk(start, end - start, _clip(label), kind, lead, refs, short))
    return chunks


def split_oversized(text: str, chunks: List[Chunk]) -> List[Chunk]:
    """Break any chunk over ``MAX_CHUNK_CHARS`` into line groups, never mid-line."""
    out: List[Chunk] = []
    for chunk in chunks:
        if chunk.length <= MAX_CHUNK_CHARS:
            out.append(chunk)
            continue
        body = text[chunk.offset : chunk.end]
        parts, start = [], 0
        for line_start in _line_starts(body)[1:]:
            if _next_line_end(body, line_start) - start > MAX_CHUNK_CHARS:
                parts.append((start, line_start))
                start = line_start
        parts.append((start, len(body)))
        if len(parts) == 1:
            out.append(chunk)
            continue
        for k, (a, b) in enumerate(parts, 1):
            suffix = f" (part {k}/{len(parts)})"
            out.append(
                replace(
                    chunk,
                    offset=chunk.offset + a,
                    length=b - a,
                    label=_clip(chunk.label, LABEL_CHARS - len(suffix)) + suffix,
                    short=_clip(chunk.short, SHORT_LABEL_CHARS) + suffix,
                    lead=chunk.lead and k == 1,
                )
            )
    return out


def _next_line_end(text: str, start: int) -> int:
    end = text.find("\n", start)
    return len(text) if end < 0 else end + 1


def _merge_small(chunks: List[Chunk]) -> List[Chunk]:
    """Fold each chunk into one before it that is under ``MIN_CHUNK_CHARS``.

    A lone header line joins the section after it, and a run of one-line
    sections (``FAILED ...`` lines) becomes one chunk; a short chunk after a
    full one keeps its own label.
    """
    out: List[Chunk] = []
    for chunk in chunks:
        if out and out[-1].length < MIN_CHUNK_CHARS:
            prev = out[-1]
            label = (
                _clip(f"{prev.label} › {chunk.label}")
                if chunk.length >= MIN_CHUNK_CHARS
                else prev.label
            )
            out[-1] = replace(
                prev,
                length=prev.length + chunk.length,
                lead=prev.lead or chunk.lead,
                label=label,
                short=_clip(label, SHORT_LABEL_CHARS),
            )
        else:
            out.append(chunk)
    return out


# ---------------------------------------------------------------------------
# Chunkers
# ---------------------------------------------------------------------------


def _def_label(node: ast.AST, qualname: str) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(b) for b in node.bases)
        head = f"class {qualname}({_clip(bases, 40)})" if bases else f"class {qualname}"
    else:
        keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        head = f"{keyword} {qualname}({_clip(ast.unparse(node.args), 50)})"
    doc = ast.get_docstring(node)
    return f"{head} — {doc.strip().splitlines()[0]}" if doc and doc.strip() else head


def chunk_python(text: str) -> List[Chunk]:
    """Module header, then each top-level def/class; a class splits into its methods.

    Raises NotStructured for text that is not valid Python.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        raise NotStructured(f"not valid Python: {exc}") from exc
    starts = _line_starts(text)
    lines = text.split("\n")

    def offset_of(lineno: int) -> int:
        return starts[lineno - 1] if lineno - 1 < len(starts) else len(text)

    def first_line(node: ast.AST, floor: int) -> int:
        line = min(
            [node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]
        )
        # A comment block above a definition -- a section banner too -- belongs to it.
        probe = line - 1
        while probe > floor and (
            not lines[probe - 1].strip() or lines[probe - 1].lstrip().startswith("#")
        ):
            if lines[probe - 1].strip():
                line = probe
            probe -= 1
        return line

    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    bounds: List[Bound] = []
    header: List[ast.stmt] = []
    previous_end = 0
    in_code = False
    for stmt in tree.body:
        if not bounds and not isinstance(stmt, defs):
            header.append(stmt)
            previous_end = stmt.end_lineno or stmt.lineno
            continue
        if not isinstance(stmt, defs):
            if not in_code:
                label = f"module code: {_clip(ast.unparse(stmt), 60)}"
                bounds.append((offset_of(stmt.lineno), label, False, ()))
                in_code = True
            previous_end = stmt.end_lineno or stmt.lineno
            continue
        in_code = False
        start = first_line(stmt, previous_end)
        bounds.append(
            (offset_of(start), _def_label(stmt, stmt.name), False, (), stmt.name)
        )
        if isinstance(stmt, ast.ClassDef):
            method_end = stmt.lineno
            for member in stmt.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qual = f"{stmt.name}.{member.name}"
                    member_start = first_line(member, method_end)
                    bounds.append(
                        (
                            offset_of(member_start),
                            _def_label(member, qual),
                            False,
                            (),
                            qual,
                        )
                    )
                method_end = member.end_lineno or member.lineno
        previous_end = stmt.end_lineno or stmt.lineno
    if header:
        doc = ast.get_docstring(tree)
        imports = sum(isinstance(s, (ast.Import, ast.ImportFrom)) for s in header)
        summary = (
            doc.strip().splitlines()[0] if doc and doc.strip() else "module header"
        )
        label = f"{summary} ({imports} imports)" if imports else summary
        bounds.insert(0, (0, label, False, ()))
    elif bounds:
        bounds[0] = (0,) + bounds[0][1:]
    return _tile(text, bounds, "python", with_lines=True) if len(bounds) > 1 else []


def chunk_markdown(text: str) -> List[Chunk]:
    """One chunk per heading (any depth), outside fenced code."""
    bounds: List[Bound] = []
    fenced = False
    for offset, line in _lines(text):
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        match = None if fenced else _HEADING_RE.match(line)
        if match:
            bounds.append((offset, f"{match.group(1)} {match.group(2)}", False, ()))
    if not bounds:
        return []
    if bounds[0][0] != 0:
        bounds.insert(0, (0, label_for_unstructured(text[: bounds[0][0]]), False, ()))
    return _tile(text, bounds, "markdown", with_lines=True) if len(bounds) > 1 else []


_DECODER = json.JSONDecoder()


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    return i


def _scan_array(text: str, i: int) -> List[Tuple[int, Any]]:
    """(start, value) of each item of the array opening at ``text[i]``."""
    items: List[Tuple[int, Any]] = []
    j = _skip_ws(text, i + 1)
    if text[j] == "]":
        return items
    while True:
        value, end = _DECODER.raw_decode(text, j)
        items.append((j, value))
        j = _skip_ws(text, end)
        if text[j] == ",":
            j = _skip_ws(text, j + 1)
        elif text[j] == "]":
            return items
        else:
            raise NotStructured(f"malformed JSON array at {j}")


def _scan_object(text: str, i: int) -> List[Tuple[str, int, int, Any]]:
    """(key, member start, value start, value) of each member of the object at ``text[i]``."""
    members: List[Tuple[str, int, int, Any]] = []
    j = _skip_ws(text, i + 1)
    if text[j] == "}":
        return members
    while True:
        key, end = _DECODER.raw_decode(text, j)
        colon = _skip_ws(text, end)
        if text[colon] != ":":
            raise NotStructured(f"malformed JSON object at {colon}")
        value_start = _skip_ws(text, colon + 1)
        value, end = _DECODER.raw_decode(text, value_start)
        members.append((key, j, value_start, value))
        j = _skip_ws(text, end)
        if text[j] == ",":
            j = _skip_ws(text, j + 1)
        elif text[j] == "}":
            return members
        else:
            raise NotStructured(f"malformed JSON object at {j}")


def _summary(value: Any) -> str:
    if isinstance(value, dict):
        pairs = [f"{k}={_clip(str(v), 30)}" for k, v in list(value.items())[:2]]
        return ", ".join(pairs) or "{}"
    if isinstance(value, list):
        return f"list of {len(value)}"
    return _clip(value if isinstance(value, str) else json.dumps(value), 50)


def _line_start_if_leading(text: str, pos: int) -> int:
    line_start = text.rfind("\n", 0, pos) + 1
    return line_start if not text[line_start:pos].strip() else pos


def _item_groups(
    text: str, items: List[Tuple[int, Any]], key: Optional[str], first_start: int
) -> List[Bound]:
    bounds = []
    name = f'"{key}"' if key is not None else "items"
    for a in range(0, len(items), JSON_GROUP):
        b = min(a + JSON_GROUP, len(items)) - 1
        start = first_start if a == 0 else _line_start_if_leading(text, items[a][0])
        label = f"{name} {a}–{b}: {_summary(items[a][1])}"
        bounds.append((start, label, False, ((key, a, b),)))
    return bounds


def chunk_json(text: str) -> List[Chunk]:
    """Top-level members, or list items in groups of ``JSON_GROUP``.

    Raises NotStructured for text that is not a JSON object or array.
    """
    i = _skip_ws(text, 0)
    if i >= len(text) or text[i] not in "[{":
        raise NotStructured("not a JSON object or array")
    try:
        end = _DECODER.raw_decode(text, i)[1]
    except json.JSONDecodeError as exc:
        raise NotStructured(f"invalid JSON: {exc}") from exc
    if _skip_ws(text, end) != len(text):
        raise NotStructured("trailing data after JSON document")
    bounds: List[Bound] = []
    if text[i] == "[":
        bounds = _item_groups(text, _scan_array(text, i), None, 0)
    else:
        for n, (key, start, value_start, value) in enumerate(_scan_object(text, i)):
            start = 0 if n == 0 else _line_start_if_leading(text, start)
            if isinstance(value, list) and len(value) > JSON_GROUP:
                items = _scan_array(text, value_start)
                bounds.extend(_item_groups(text, items, key, start))
            else:
                bounds.append(
                    (start, f'"{key}": {_summary(value)}', False, ((key, None, None),))
                )
    return _tile(text, bounds, "json") if len(bounds) > 1 else []


def chunk_output(text: str) -> List[Chunk]:
    """Command output split at test/section markers; failures and the tail lead."""
    bounds: List[Bound] = []
    for offset, line in _lines(text):
        if _SECTION_RE.match(line):
            label = line.strip(" =_-") or line
            bounds.append((offset, _clip(label), bool(_FAILURE_RE.search(line)), ()))
    if not bounds:
        return chunk_paragraphs(text, kind="output", tail_leads=True)
    if bounds[0][0] != 0:
        bounds.insert(0, (0, label_for_unstructured(text[: bounds[0][0]]), False, ()))
    chunks = _merge_small(_tile(text, bounds, "output"))
    chunks = _refine_large_sections(text, chunks)
    if len(chunks) > 1:
        chunks[-1] = replace(chunks[-1], lead=True)
    return chunks if len(chunks) > 1 else []


def _refine_large_sections(text: str, chunks: List[Chunk]) -> List[Chunk]:
    """Split a section too large to fetch whole at its paragraphs first."""
    out: List[Chunk] = []
    for chunk in chunks:
        if chunk.length <= MAX_CHUNK_CHARS:
            out.append(chunk)
            continue
        section = text[chunk.offset : chunk.end]
        inner = chunk_paragraphs(section, kind=chunk.kind) or chunk_lines(
            section, kind=chunk.kind
        )
        if not inner:
            out.append(chunk)
            continue
        for k, part in enumerate(inner):
            out.append(
                replace(
                    part,
                    offset=chunk.offset + part.offset,
                    label=chunk.label if k == 0 else part.label,
                    short=chunk.short if k == 0 else part.short,
                    lead=chunk.lead if k == 0 else part.lead,
                )
            )
    return out


def chunk_paragraphs(
    text: str, kind: str = "text", tail_leads: bool = False
) -> List[Chunk]:
    """Blank-line-separated blocks, labelled by :func:`label_for_unstructured`."""
    bounds: List[Bound] = [(0, "", False, ())]
    blank = False
    for offset, line in _lines(text):
        if not line.strip():
            blank = True
        elif blank:
            bounds.append((offset, "", False, ()))
            blank = False
    chunks = _merge_small(_tile(text, bounds, kind))
    chunks = [
        replace(
            c,
            label=label_for_unstructured(text[c.offset : c.end]),
            short=_clip(
                label_for_unstructured(text[c.offset : c.end]), SHORT_LABEL_CHARS
            ),
        )
        for c in chunks
    ]
    if tail_leads and len(chunks) > 1:
        chunks[-1] = replace(chunks[-1], lead=True)
    return chunks if len(chunks) > 1 else []


_GREP_LINE_RE = re.compile(r"^([^:\s][^:]*):\d+[:-]")


def chunk_lines(text: str, kind: str = "text") -> List[Chunk]:
    """A list of lines with no blank lines between: its items are its lines.

    ``grep -n`` output groups by file; anything else starts an item at each
    least-indented line. Items are gathered until each holds at least
    ``MIN_CHUNK_CHARS``.
    """
    lines = [(o, line) for o, line in _lines(text) if line.strip()]
    if not lines:
        return []
    newline = "\n"
    grep = [_GREP_LINE_RE.match(line) for _, line in lines]
    if sum(1 for m in grep if m) * 5 >= len(lines) * 4:
        bounds, current = [], None
        for (offset, _), match in zip(lines, grep):
            path = match.group(1) if match else current
            if path != current:
                bounds.append((offset, path or "", False, ()))
                current = path
        bounds[0] = (0,) + bounds[0][1:]
        chunks = _merge_small(_tile(text, bounds, kind))
        return [
            replace(
                c,
                label=_clip(f"{c.label}: {text.count(newline, c.offset, c.end)} lines"),
            )
            for c in chunks
        ]
    indent = min(len(line) - len(line.lstrip()) for _, line in lines)
    bounds = [
        (offset, "", False, ())
        for offset, line in lines
        if len(line) - len(line.lstrip()) == indent
    ]
    bounds[0] = (0,) + bounds[0][1:]
    chunks = _merge_small(_tile(text, bounds, kind))
    return [
        replace(
            c,
            label=label_for_unstructured(text[c.offset : c.end]),
            short=_clip(
                label_for_unstructured(text[c.offset : c.end]), SHORT_LABEL_CHARS
            ),
        )
        for c in chunks
    ]


def chunk_diff(text: str) -> List[Chunk]:
    """A unified diff split at each file and each ``@@`` hunk after a file's first."""
    lines = _lines(text)
    bounds: List[Bound] = []
    git_header = first_hunk = False
    for i, (offset, line) in enumerate(lines):
        following = lines[i + 1][1] if i + 1 < len(lines) else ""
        if line.startswith("diff --git "):
            bounds.append((offset, line, False, ()))
            git_header = first_hunk = True
        elif line.startswith("--- ") and following.startswith("+++ "):
            if not git_header:
                bounds.append((offset, following[4:], False, ()))
            git_header, first_hunk = False, True
        elif line.startswith("@@ "):
            if not first_hunk:
                bounds.append((offset, line, False, ()))
            first_hunk = False
    if not bounds:
        return []
    if bounds[0][0] != 0:
        bounds.insert(0, (0, label_for_unstructured(text[: bounds[0][0]]), False, ()))
    return _tile(text, bounds, "diff") if len(bounds) > 1 else []


def render_search(matches: List[Dict[str, Any]]) -> Tuple[str, List[Chunk]]:
    """Grep-style text for search matches, one chunk per file's first match and rest.

    Every field of every match is kept: unknown keys ride along as JSON.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for match in matches:
        groups.setdefault(str(match.get("file") or ""), []).append(match)
    try:
        paths = [p for p in groups if p]
        root = (
            os.path.commonpath(paths)
            if len(paths) > 1
            else os.path.dirname(paths[0]) if paths else ""
        )
    except ValueError:
        root = ""

    def relative(path: str) -> str:
        # Keep the separator the caller's path used: ntpath.relpath answers in
        # backslashes even for a POSIX path, so the index would name a file
        # differently from the body text right above it.
        name = os.path.relpath(path, root)
        return name.replace("\\", "/") if "\\" not in path else name

    def block(match: Dict[str, Any]) -> str:
        out = f"  {match.get('line')}: {match.get('content', '')}\n"
        context = match.get("context") or []
        if isinstance(context, str):
            context = [context]
        out += "".join(f"    | {line}\n" for line in context)
        extra = {
            k: v
            for k, v in match.items()
            if k not in ("file", "line", "content", "context")
        }
        if extra:
            out += f"    {json.dumps(extra, ensure_ascii=False, default=str)}\n"
        return out

    pieces: List[Tuple[str, str, bool]] = []
    for path, found in groups.items():
        if not path:
            name = "(no file)"
        elif root:
            name = relative(path)
        else:
            name = path
        noun = "match" if len(found) == 1 else "matches"
        first = found[0]
        pieces.append(
            (
                f"{path or '(no file)'} ({len(found)} {noun})\n" + block(first),
                f"{name}: {len(found)} {noun}, L{first.get('line')}: "
                f"{first.get('content', '')}",
                True,
            )
        )
        if len(found) > 1:
            more = ", ".join(str(m.get("line")) for m in found[1:6])
            more += ", …" if len(found) > 6 else ""
            pieces.append(
                (
                    "".join(block(m) for m in found[1:]),
                    f"{name}: {len(found) - 1} more (lines {more})",
                    False,
                )
            )
    text = "".join(p[0] for p in pieces)
    bounds, offset = [], 0
    for body, label, lead in pieces:
        bounds.append((offset, label, lead, ()))
        offset += len(body)
    if not text:
        return text, []
    return text, split_oversized(text, _tile(text, bounds, "search"))


CHUNKERS: Dict[str, Callable[[str], List[Chunk]]] = {
    "python": chunk_python,
    "markdown": chunk_markdown,
    "json": chunk_json,
    "output": chunk_output,
    "diff": chunk_diff,
    "text": chunk_paragraphs,
}


def chunk_text(text: str, kind: str) -> List[Chunk]:
    """Chunks of ``text`` by ``kind``'s structure; ``[]`` when it has fewer than two.

    Text without the structure its kind promises (a ``.py`` file with a syntax
    error, invalid JSON, output with no sections) is split at its paragraphs,
    or failing those at its lines.
    """
    try:
        chunks = CHUNKERS[kind](text)
    except (NotStructured, RecursionError) as exc:
        log.debug("chunk index: %s text did not parse (%s)", kind, exc)
        chunks = []
    if len(chunks) < 2 and kind != "text":
        chunks = chunk_paragraphs(text)
    if len(chunks) < 2:
        chunks = chunk_lines(text)
    chunks = split_oversized(text, chunks)
    return chunks if len(chunks) > 1 else []


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def sniff_kind(text: str) -> str:
    """Structure of a text with no file type to go by."""
    if text.lstrip()[:1] in ("{", "[") and _is_json(text):
        return "json"
    if text.startswith(("diff --git ", "--- ")):
        return "diff"
    if re.search(r"^(?:={3,} .* ={3,}|FAILED |Traceback )", text, re.MULTILINE):
        return "output"
    if len(chunk_markdown(text)) > 1:
        return "markdown"
    return "text"


# ---------------------------------------------------------------------------
# Selection: what stays in the conversation, what goes in the index
# ---------------------------------------------------------------------------


def _cost(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def index_entries(chunks: Sequence[Chunk], short: bool = False) -> List[Dict[str, Any]]:
    """Index entries numbered from 1; ``read_tool_output(entry=n)`` reads one."""
    return [
        {
            "n": n,
            "label": (c.short or c.label) if short else c.label,
            "offset": c.offset,
            "length": c.length,
        }
        for n, c in enumerate(chunks, 1)
    ]


def _merge_pair(a: Chunk, b: Chunk) -> Chunk:
    first = a.ends[0] if a.parts > 1 else a.short
    last = b.ends[1] if b.parts > 1 else b.short
    parts = a.parts + b.parts
    label = f"{_clip(first, 40)} … {_clip(last, 30)} ({parts} parts)"
    return Chunk(
        a.offset,
        a.length + b.length,
        label,
        a.kind,
        a.lead or b.lead,
        a.refs + b.refs,
        label,
        parts,
        (first, last),
    )


def fit_index(chunks: List[Chunk], room: int) -> Tuple[List[Chunk], bool]:
    """Coarsen ``chunks`` until their index costs at most ``room`` characters.

    Full labels first, then terse ones, then adjacent entries merged pairwise
    -- only entries that touch, so each still names one contiguous span.
    Returns the chunks and whether terse labels are in force. Raises ValueError
    when nothing is left to merge and the index still does not fit.
    """
    if _cost(index_entries(chunks)) <= room:
        return chunks, False
    while _cost(index_entries(chunks, short=True)) > room:
        merged: List[Chunk] = []
        i = 0
        while i < len(chunks):
            if i + 1 < len(chunks) and chunks[i].end == chunks[i + 1].offset:
                merged.append(_merge_pair(chunks[i], chunks[i + 1]))
                i += 2
            else:
                merged.append(chunks[i])
                i += 1
        if len(merged) == len(chunks):
            raise ValueError(
                f"Tool output budget ({room} chars) is too small for a chunk index."
            )
        chunks = merged
    return chunks, True


def select(
    text: str,
    chunks: List[Chunk],
    room: int,
    named: Sequence[str] = (),
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """``(shown, index)`` for ``text`` within ``room`` serialized characters.

    Shown is a leading chunk that leads (a run's closing summary), then the
    first chunk, then the other leads (a file's first match, a failing section,
    a part the call's arguments name), then the rest in document order -- each
    taken only while it still fits. The index names every chunk not shown,
    merging neighbours once it would take more than ``INDEX_SHARE`` of the room.
    """
    index_cap = int(room * INDEX_SHARE)
    while len(chunks) > MAX_CANDIDATES:
        # The trailing lead sits out the merge: fused into a multi-KB
        # neighbour it stops fitting, and it is the line that says whether the
        # whole run passed.
        head, tail = (chunks[:-1], chunks[-1:]) if chunks[-1].lead else (chunks, [])
        chunks = [
            _merge_pair(head[i], head[i + 1]) if i + 1 < len(head) else head[i]
            for i in range(0, len(head), 2)
        ] + tail
    wanted = [c for c in named if isinstance(c, str) and len(c) >= 3]
    # A long log's closing summary bids before chunk 0, which for command
    # output is the "test session starts" banner.
    last = len(chunks) - 1
    order = [last] if last > 0 and chunks[last].lead else []
    order.append(0)
    order += [
        i
        for i, c in enumerate(chunks)
        if i and i not in order and (c.lead or any(w in c.label for w in wanted))
    ]
    order += [i for i in range(1, len(chunks)) if i not in order]

    def segments(kept: set) -> List[Dict[str, Any]]:
        shown: List[Dict[str, Any]] = []
        for i, c in enumerate(chunks):
            if i not in kept:
                continue
            if shown and shown[-1]["offset"] + len(shown[-1]["text"]) == c.offset:
                shown[-1]["text"] += text[c.offset : c.end]
            else:
                shown.append({"offset": c.offset, "text": text[c.offset : c.end]})
        return shown

    def rest(kept: set) -> List[Chunk]:
        return [c for i, c in enumerate(chunks) if i not in kept]

    entry_cost = [_cost(e) for e in index_entries(chunks)]
    kept_order: List[int] = []
    rest_cost, rest_count = sum(entry_cost), len(chunks)
    for i in order:
        trial = set(kept_order) | {i}
        count = rest_count - 1
        # A JSON list costs its items, a ", " between each, and the brackets.
        index_cost = rest_cost - entry_cost[i] + 2 * max(count - 1, 0) + 2
        if _cost(segments(trial)) + min(index_cost, index_cap) <= room:
            kept_order.append(i)
            rest_cost, rest_count = rest_cost - entry_cost[i], count
    while True:
        kept = set(kept_order)
        shown = segments(kept)
        try:
            entries, short = fit_index(rest(kept), room - _cost(shown))
        except ValueError:
            # Shown parts split the index into runs that cannot merge; give
            # back the last one taken until the index fits.
            if not kept_order:
                raise
            kept_order.pop()
            continue
        return shown, index_entries(entries, short)


def head_and_tail(
    text: str, room: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """``(shown, index)``: the text's start and end, the middle as one entry."""

    def view(keep: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        head, tail = (keep + 1) // 2, keep // 2
        shown = [{"offset": 0, "text": text[:head]}]
        if tail:
            shown.append({"offset": len(text) - tail, "text": text[-tail:]})
        omitted = len(text) - keep
        index = [
            {
                "n": 1,
                "label": f"omitted middle ({omitted} chars)",
                "offset": head,
                "length": omitted,
            }
        ]
        return shown, index

    low, high = 0, len(text) - 1
    while low < high:
        keep = (low + high + 1) // 2
        shown, index = view(keep)
        if _cost(shown) + _cost(index) <= room:
            low = keep
        else:
            high = keep - 1
    return view(low)


def covered(chunk: Chunk, original: Any, shown: Any) -> bool:
    """Whether a JSON chunk's records reach the model intact in ``shown``."""
    for key, a, b in chunk.refs:
        if key is None:
            source, kept = original, shown
        elif isinstance(shown, dict) and isinstance(original, dict):
            source, kept = original.get(key), shown.get(key)
            if a is None:
                if key not in shown or kept != source:
                    return False
                continue
        else:
            return False
        if not isinstance(kept, list) or kept[a : b + 1] != source[a : b + 1]:
            return False
    return bool(chunk.refs)


# ---------------------------------------------------------------------------
# A whole tool result
# ---------------------------------------------------------------------------

#: Other text fields of a condensed result keep at most this share of the target.
FIELD_SHARE = 0.15
#: Below this much room for shown + index, a result is not worth indexing.
MIN_ROOM = 600
_HANDLE_PLACEHOLDER = "output_" + "0" * 32
#: read_file outlines the index already carries, with offsets added.
_SUPERSEDED = {"python": ("symbols",), "markdown": ("headers",)}
_EXTENSION_KINDS = {
    ".py": "python",
    ".md": "markdown",
    ".mdx": "markdown",
    ".markdown": "markdown",
    ".json": "json",
}


@dataclass
class Body:
    """The one text in a result that carries its bulk."""

    field: Optional[str]
    text: str
    kind: str
    handle: Optional[str] = None
    chunks: Optional[List[Chunk]] = None


def _archived_excerpt(value: Any, store) -> Optional[Tuple[str, str]]:
    """``(handle, full text)`` when ``value`` is an excerpt this store archived."""
    if not isinstance(value, str) or not value.startswith("{"):
        return None
    try:
        excerpt = json.loads(value)
    except ValueError:
        return None
    if not isinstance(excerpt, dict) or excerpt.get("continuation") != (
        "read_tool_output"
    ):
        return None
    handle = excerpt.get("artifact")
    if not store.has(handle):
        return None
    return handle, store.text(handle)


def _kind_for(tool_name: str, result: Any, field_name: Optional[str], text: str) -> str:
    if isinstance(result, dict):
        file_type = str(result.get("file_type") or "").lower()
        ext = os.path.splitext(str(result.get("file_path") or ""))[1].lower()
        kind = {"python": "python", "markdown": "markdown"}.get(file_type) or (
            _EXTENSION_KINDS.get(ext)
        )
        if kind == "python" and result.get("is_valid") is False:
            return "text"
        if kind and field_name == "content":
            return kind
    if tool_name in OUTPUT_TOOLS or field_name in ("stdout", "stderr"):
        # A command that prints a diff or a JSON document still prints its structure.
        sniffed = sniff_kind(text)
        return sniffed if sniffed in ("diff", "json") else "output"
    if field_name == "diff":
        return "diff"
    return sniff_kind(text)


def find_body(tool_name: str, result: Any, store) -> Optional[Body]:
    """The text to index, or ``None`` when the result's bulk is records, not text."""
    if isinstance(result, str):
        return Body(None, result, _kind_for(tool_name, result, None, result))
    if not isinstance(result, dict):
        return None
    matches = result.get("matches")
    if (
        isinstance(matches, list)
        and matches
        and all(isinstance(m, dict) and "file" in m for m in matches)
    ):
        text, chunks = render_search(matches)
        return Body("matches", text, "search", chunks=chunks)
    texts = [(k, v) for k, v in result.items() if isinstance(v, str)]
    if not texts:
        return None
    name, value = max(texts, key=lambda kv: len(kv[1]))
    archived = _archived_excerpt(value, store)
    handle, text = archived if archived else (None, value)
    # Text is most of the result; otherwise its bulk is records.
    if sum(len(v) for _, v in texts) * 2 < _cost(result):
        return None
    return Body(name, text, _kind_for(tool_name, result, name, text), handle)


def _bounded_field(value: str, cap: int, store) -> str:
    """Another large text field of the result, as an archived head/tail excerpt."""
    from gaia.agents.base.tool_output import elide_text

    archived = _archived_excerpt(value, store)
    handle, text = archived if archived else (store.put(value), value)
    metadata = {
        "artifact": handle,
        "continuation": "read_tool_output",
        "offset_unit": "characters",
    }
    excerpt = elide_text(text, max(200, cap - _cost(metadata) - 2))
    excerpt.update(metadata)
    return json.dumps(excerpt, ensure_ascii=False)


def condense_result(
    tool_name: str,
    result: Any,
    tool_args: Optional[Dict[str, Any]],
    target: int,
    store,
    serialize: Callable[[Any], str],
) -> Optional[Dict[str, Any]]:
    """``result`` as shown + index within ``target`` characters, or ``None``.

    ``None`` means the result has no text body with at least two structural
    parts; the caller's record-dropping or head/tail path applies instead.
    Every field other than the body is kept verbatim -- a ``check_result``
    included -- except other long text, which becomes an archived excerpt.
    An over-target render raises rather than degrading: a budgeting bug here
    would otherwise reach the model as a silently oversized prompt.
    """
    body = find_body(tool_name, result, store)
    if body is None:
        return None
    chunks = (
        body.chunks if body.chunks is not None else chunk_text(body.text, body.kind)
    )
    if len(chunks) < 2:
        return None
    base: Dict[str, Any] = {}
    if isinstance(result, dict):
        cap = int(target * FIELD_SHARE)
        for key, value in result.items():
            if key == body.field or key in _SUPERSEDED.get(body.kind, ()):
                continue
            if isinstance(value, str) and len(value) > cap:
                value = _bounded_field(value, cap, store)
            base[key] = value
    metadata: Dict[str, Any] = {
        "artifact": body.handle or _HANDLE_PLACEHOLDER,
        "continuation": "read_tool_output",
        "fetch": FETCH_HINT,
        "total_chars": len(body.text),
    }
    if body.field is not None:
        metadata["archived_field"] = body.field
    if body.kind == "search":
        metadata["archived_format"] = "grep"

    def render(
        shown: List[Dict[str, Any]], index: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        omitted = len(body.text) - sum(len(s["text"]) for s in shown)
        return {
            **base,
            "truncated": omitted > 0,
            "original_chars": len(body.text),
            "omitted_chars": omitted,
            "shown": shown,
            "index": index,
            **metadata,
        }

    # +1: "truncated" may render as false, one character longer than true.
    skeleton = len(serialize(render([], []))) + 1
    room = target - skeleton
    if room < MIN_ROOM:
        log.info(
            "chunk index: %s leaves %d chars for its parts (need %d); "
            "using record truncation",
            tool_name,
            room,
            MIN_ROOM,
        )
        return None
    named = [
        v
        for k, v in (tool_args or {}).items()
        if k in NAMING_ARGS and isinstance(v, str)
    ]
    largest = max(c.length for c in chunks)
    if largest > MAX_CHUNK_CHARS and largest * 2 > len(body.text):
        # One line too long to split is most of the text: keep its end in view.
        shown, index = head_and_tail(body.text, room)
    else:
        shown, index = select(body.text, chunks, room, named)
    if body.handle is None:
        metadata["artifact"] = store.put(body.text)
    store.set_index(metadata["artifact"], index)
    condensed = render(shown, index)
    size = len(serialize(condensed))
    if size > target:
        raise ValueError(
            f"chunk index for {tool_name} came to {size} chars, over its {target}-char "
            "target; this is a bug in gaia.agents.base.chunk_index"
        )
    log.debug(
        "chunk index: %s %s body, %d chunks, %d shown segment(s), %d indexed",
        tool_name,
        body.kind,
        len(chunks),
        len(shown),
        len(index),
    )
    return condensed
