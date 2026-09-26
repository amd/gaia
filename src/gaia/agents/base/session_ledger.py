# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Session findings ledger: what exploration already found, shared by a parent
agent and every worker it delegates to.

Each worker starts from an empty conversation, so without this every one of
them re-reads the same files. The ledger records, deterministically and with
no model in the loop, what every tool result of the session established: files
read (with the outline the chunk index gives them), searches and their hits,
checks and their summary line, files changed, and each worker's report. The
full text of every read is archived in the session's
:class:`~gaia.agents.base.artifacts.ArtifactStore` under the ledger's own
index, so ``read_tool_output(artifact, entry=n)`` fetches exactly one outlined
part instead of re-reading the file.

The digest (:meth:`SessionLedger.render`) carries no handles or timestamps:
the same findings always render to the same bytes, so children spawned from
the same ledger state share a byte-identical prompt prefix. Handles and entry
numbers come from the ``session_findings`` tool.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from gaia.agents.base.artifacts import store_for
from gaia.agents.base.checks import (
    CHECK_RESULT_KEY,
    runner_summary,
    summary_reports_failure,
)
from gaia.agents.base.chunk_index import OUTPUT_TOOLS, chunk_text, sniff_kind
from gaia.agents.base.verification import is_mutating_tool

LEDGER_TOOL = "session_findings"
DIGEST_HEADER = "What is already known (session findings)"
#: The digest a child brief carries.
DIGEST_CHARS = 6000
#: The tool's own cap; its result is never condensed below it.
TOOL_CHARS = 8000
#: Text shorter than this is recorded by path and size only.
OUTLINE_MIN_CHARS = 2000
FINDING_CHARS = 600
LABEL_CHARS = 60
OUTLINE_LINE_ENTRIES = 40

KINDS = ("read", "search", "check", "change", "finding")
_SECTION_TITLES = {
    "read": "Files read",
    "search": "Searches",
    "check": "Checks run",
    "change": "Files changed",
    "finding": "Worker findings",
}

#: Results these tools return are the ledger's own output, or a worker's.
_UNRECORDED_TOOLS = frozenset({"read_tool_output", LEDGER_TOOL, "delegate_task"})
_READ_TOOLS = frozenset({"read_file"})
_SEARCH_TOOLS = frozenset({"search_file_content"})
_PAGERS = frozenset({"cat", "head", "tail", "sed"})
_GREPS = frozenset({"grep", "egrep", "fgrep", "rg", "ag"})
#: grep/rg options that take a value, so it is never mistaken for the pattern.
_GREP_VALUE_OPTS = frozenset(
    {
        "-A",
        "-B",
        "-C",
        "-m",
        "-e",
        "-f",
        "-g",
        "-t",
        "-T",
        "--include",
        "--exclude",
        "--exclude-dir",
        "--glob",
        "--type",
        "--max-count",
        "--regexp",
        "--context",
    }
)
_PATH_ARGS = ("file_path", "path", "filepath", "filename", "file", "target_path")
_EXTENSION_KINDS = {
    ".py": "python",
    ".md": "markdown",
    ".mdx": "markdown",
    ".markdown": "markdown",
    ".json": "json",
}

_CD_PREFIX_RE = re.compile(r"^\s*cd\s+\S+\s*&&\s*")
_SEGMENT_SPLIT_RE = re.compile(r"\s*(?:\|\||&&|\||;)\s*")
_SED_RANGE_RE = re.compile(r"^(\d+)(?:,(?:\d+|\$))?p$")
_GREP_LINE_RE = re.compile(r"^([^:\s][^:]*):\d+[:-]")
#: The chunker's own line prefix; the ledger recomputes ranges for a paged read.
_LINE_PREFIX_RE = re.compile(r"^L\d+(?:-\d+)? ")


@dataclass
class Entry:
    """One finding; ``(kind, key)`` is its identity for dedup."""

    kind: str
    key: str
    step: int
    actor: str
    size: int = 0
    artifact: Optional[str] = None
    #: ``{n, label, lines: [first, last], offset, length}`` per outlined part.
    outline: List[Dict[str, Any]] = field(default_factory=list)
    #: Search hits as ``(file, count)`` in first-seen order.
    hits: List[Tuple[str, int]] = field(default_factory=list)
    label: str = ""
    summary: str = ""
    passed: Optional[bool] = None
    #: A partial read (a page or a paged shell command) carries no outline.
    partial: bool = False

    def to_dict(self) -> Dict[str, Any]:
        record: Dict[str, Any] = {
            "kind": self.kind,
            "step": self.step,
            "found_by": self.actor,
        }
        if self.kind == "read":
            record.update(
                {
                    "path": self.key,
                    "chars": self.size,
                    "partial": self.partial,
                    "artifact": self.artifact,
                    "entries": [
                        {"n": e["n"], "label": e["label"], "lines": e["lines"]}
                        for e in self.outline
                    ],
                }
            )
        elif self.kind == "search":
            record.update(
                {
                    "pattern": self.key,
                    "via": self.label,
                    "files": [{"file": f, "matches": n} for f, n in self.hits],
                    "total_matches": self.size,
                }
            )
        elif self.kind == "check":
            record.update(
                {
                    "command": self.key,
                    "check": self.label,
                    "passed": self.passed,
                    "summary": self.summary,
                }
            )
        elif self.kind == "change":
            record["path"] = self.key
        else:
            record.update(
                {"goal": self.key, "worker": self.label, "result": self.summary}
            )
        return record

    def matches(self, needle: str) -> bool:
        haystack = [self.key, self.label, self.summary]
        haystack.extend(e["label"] for e in self.outline)
        haystack.extend(f for f, _ in self.hits)
        return any(needle in text.lower() for text in haystack if text)


class SessionLedger:
    """Findings of one session, shared by the parent and all its workers."""

    def __init__(self, owner):
        # The owner's store is looked up per use: the agent replaces its store
        # late in construction, and workers are pointed at the parent's.
        self._owner = owner
        self._entries: Dict[Tuple[str, str], Entry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> List[Entry]:
        return list(self._entries.values())

    def _store(self):
        return store_for(self._owner)

    def _put(self, entry: Entry) -> Entry:
        # A later finding replaces the earlier one in place, so the digest
        # keeps first-seen order and stays deterministic.
        self._entries[(entry.kind, entry.key)] = entry
        return entry

    # ── recording ───────────────────────────────────────────────────────────

    def record(
        self,
        tool_name: str,
        tool_args: Optional[Dict[str, Any]],
        result: Any,
        step: int,
        actor: str,
    ) -> Optional[Entry]:
        """Record what one tool result established, or ``None`` when nothing."""
        if tool_name in _UNRECORDED_TOOLS or not isinstance(result, dict):
            return None
        if result.get("status") not in (None, "success"):
            return None
        args = tool_args if isinstance(tool_args, dict) else {}
        if tool_name in _READ_TOOLS:
            return self._record_read_tool(args, result, step, actor)
        if tool_name in _SEARCH_TOOLS:
            return self._record_search_tool(result, step, actor)
        if is_mutating_tool(tool_name):
            return self._record_change(tool_name, args, result, step, actor)
        check = self._record_check(tool_name, args, result, step, actor)
        if check is not None:
            return check
        if tool_name == "run_shell_command":
            return self._record_shell(args, result, step, actor)
        return None

    def add_finding(
        self, kind: str, goal: str, text: str, step: int, actor: str
    ) -> Entry:
        """A worker's report: its brief's goal and its whole result text."""
        return self._put(
            Entry(
                kind="finding",
                key=" ".join(goal.split()),
                step=step,
                actor=actor,
                label=kind,
                summary=text,
            )
        )

    def _record_read_tool(
        self, args: Dict[str, Any], result: Dict[str, Any], step: int, actor: str
    ) -> Optional[Entry]:
        path = result.get("file_path") or args.get("file_path")
        content = result.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            return None
        if result.get("file_type") == "binary":
            return None
        partial = "offset" in result or "next_offset" in result
        return self._record_read(
            path, content, 1 if not partial else None, step, actor, partial
        )

    def _record_read(
        self,
        path: str,
        text: str,
        first_line: Optional[int],
        step: int,
        actor: str,
        partial: bool,
    ) -> Entry:
        outline = (
            _outline(path, text, first_line)
            if first_line is not None and len(text) >= OUTLINE_MIN_CHARS
            else []
        )
        spans = outline or [
            {
                "n": 1,
                "label": os.path.basename(path),
                "lines": (
                    [first_line, first_line + max(0, len(text.splitlines()) - 1)]
                    if first_line is not None
                    else None
                ),
                "offset": 0,
                "length": len(text),
            }
        ]
        store = self._store()
        handle = store.put(text)
        store.set_index(handle, spans)
        return self._put(
            Entry(
                kind="read",
                key=path,
                step=step,
                actor=actor,
                size=len(text),
                artifact=handle,
                outline=spans,
                partial=partial,
            )
        )

    def _record_search_tool(
        self, result: Dict[str, Any], step: int, actor: str
    ) -> Optional[Entry]:
        pattern = result.get("pattern")
        matches = result.get("matches")
        if not isinstance(pattern, str) or not isinstance(matches, list):
            return None
        hits = _count_hits(
            str(m.get("file")) for m in matches if isinstance(m, dict) and "file" in m
        )
        total = result.get("total_matches")
        return self._put(
            Entry(
                kind="search",
                key=pattern,
                step=step,
                actor=actor,
                size=int(total) if isinstance(total, int) else len(matches),
                hits=hits,
                label="search_file_content",
            )
        )

    def _record_change(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: Dict[str, Any],
        step: int,
        actor: str,
    ) -> Optional[Entry]:
        path = result.get("file_path")
        if not isinstance(path, str):
            path = next(
                (args[k] for k in _PATH_ARGS if isinstance(args.get(k), str)), None
            )
        if not path:
            return None
        return self._put(
            Entry(kind="change", key=path, step=step, actor=actor, label=tool_name)
        )

    def _record_check(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: Dict[str, Any],
        step: int,
        actor: str,
    ) -> Optional[Entry]:
        check = result.get(CHECK_RESULT_KEY)
        command = _CD_PREFIX_RE.sub(
            "", str(args.get("command") or args.get("code") or "")
        )
        if isinstance(check, dict) and check.get("label"):
            return self._put(
                Entry(
                    kind="check",
                    key=" ".join(command.split()) or str(check.get("target") or ""),
                    step=step,
                    actor=actor,
                    label=str(check["label"]),
                    summary=str(check.get("summary") or ""),
                    passed=bool(check.get("passed")),
                )
            )
        if tool_name not in OUTPUT_TOOLS:
            return None
        output = "\n".join(
            s
            for s in (result.get("stdout"), result.get("stderr"))
            if isinstance(s, str)
        )
        found = runner_summary(output)
        if found is None:
            return None
        label, summary = found
        code = result.get("return_code")
        return self._put(
            Entry(
                kind="check",
                key=" ".join(command.split()) or tool_name,
                step=step,
                actor=actor,
                label=label,
                summary=summary,
                passed=code == 0 and not summary_reports_failure(summary),
            )
        )

    def _record_shell(
        self, args: Dict[str, Any], result: Dict[str, Any], step: int, actor: str
    ) -> Optional[Entry]:
        command = args.get("command")
        stdout = result.get("stdout")
        if not isinstance(command, str) or not isinstance(stdout, str):
            return None
        if result.get("return_code") not in (0, 1):
            return None
        for argv in _segments(command):
            name = os.path.basename(argv[0])
            if name in _GREPS:
                pattern = _grep_pattern(argv)
                if pattern is None:
                    return None
                hits = _grep_hits(stdout, argv)
                return self._put(
                    Entry(
                        kind="search",
                        key=pattern,
                        step=step,
                        actor=actor,
                        size=sum(n for _, n in hits),
                        hits=hits,
                        label=name,
                    )
                )
            if name in _PAGERS and result.get("return_code") == 0 and stdout:
                paged = _paged_path(argv)
                if paged is None:
                    return None
                path, first_line = paged
                return self._record_read(
                    path, stdout, first_line, step, actor, partial=first_line != 1
                )
        return None

    # ── exposure ────────────────────────────────────────────────────────────

    def render(self, max_chars: int = DIGEST_CHARS) -> str:
        """The digest: plain text, no handles, identical for identical findings."""
        sections = []
        for kind in KINDS:
            lines = [_digest_line(e) for e in self._entries.values() if e.kind == kind]
            if lines:
                sections.append((_SECTION_TITLES[kind], lines))
        if not sections:
            return "(nothing recorded yet)"
        rendered = _fit_sections(sections, max_chars)
        if len(rendered) > max_chars:
            rendered = rendered[: max_chars - 1] + "…"
        return rendered

    def brief_block(self) -> str:
        return f"{DIGEST_HEADER}\n{self.render()}"

    def find(self, query: str) -> List[Entry]:
        needle = " ".join(query.split()).lower()
        if not needle:
            return self.entries
        return [e for e in self._entries.values() if e.matches(needle)]

    def tool_result(self, query: str = "") -> Dict[str, Any]:
        """What ``session_findings`` returns, kept under :data:`TOOL_CHARS`."""
        if not isinstance(query, str):
            raise ValueError(f"query must be a string, got {type(query).__name__}")
        if not query.strip():
            return {
                "status": "success",
                "findings": self.render(TOOL_CHARS - 200),
                "fetch": (
                    "session_findings(query=<path, pattern or symbol>) returns the "
                    "artifact and entry numbers; read_tool_output(artifact, "
                    "entry=n) fetches that exact part"
                ),
            }
        matched = self.find(query)
        result: Dict[str, Any] = {
            "status": "success",
            "query": query,
            "total": len(matched),
            "matches": [],
            "fetch": "read_tool_output(artifact, entry=n) returns one outlined part verbatim",
        }
        if not matched:
            result["note"] = (
                "nothing recorded matches; whatever you read or search now is "
                "recorded for the next worker"
            )
            return result
        for entry in matched:
            result["matches"].append(entry.to_dict())
            if len(_serialize(result)) > TOOL_CHARS:
                result["matches"].pop()
                break
        omitted = len(matched) - len(result["matches"])
        if omitted:
            result["omitted"] = omitted
            result["note"] = "narrow the query to see the omitted entries"
        return result


def ledger_for(owner) -> SessionLedger:
    """The session's ledger; a worker is handed its parent's before it runs."""
    ledger = getattr(owner, "_session_ledger", None)
    if ledger is None:
        ledger = SessionLedger(owner)
        owner._session_ledger = ledger  # pylint: disable=protected-access
    return ledger


# ── helpers ──────────────────────────────────────────────────────────────────


def _serialize(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _clip(text: str, limit: int = LABEL_CHARS) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def _outline(path: str, text: str, first_line: int) -> List[Dict[str, Any]]:
    kind = _EXTENSION_KINDS.get(os.path.splitext(path)[1].lower()) or sniff_kind(text)
    entries = []
    for n, chunk in enumerate(chunk_text(text, kind), 1):
        start = first_line + text.count("\n", 0, chunk.offset)
        end = first_line + text.count("\n", 0, max(chunk.offset, chunk.end - 1))
        entries.append(
            {
                "n": n,
                "label": _clip(_LINE_PREFIX_RE.sub("", chunk.short or chunk.label)),
                "lines": [start, end],
                "offset": chunk.offset,
                "length": chunk.length,
            }
        )
    return entries


def _count_hits(files) -> List[Tuple[str, int]]:
    counts: Dict[str, int] = {}
    for name in files:
        counts[name] = counts.get(name, 0) + 1
    return list(counts.items())


def _segments(command: str) -> List[List[str]]:
    """argv of each pipeline stage, in order; a command shlex rejects is none."""
    stripped = _CD_PREFIX_RE.sub("", command)
    segments = []
    for piece in _SEGMENT_SPLIT_RE.split(stripped):
        try:
            argv = shlex.split(piece)
        except ValueError:
            return []
        if argv:
            segments.append(argv)
    return segments


def _grep_pattern(argv: List[str]) -> Optional[str]:
    rest = argv[1:]
    i = 0
    positional: List[str] = []
    while i < len(rest):
        arg = rest[i]
        if arg in ("-e", "--regexp") and i + 1 < len(rest):
            return rest[i + 1]
        if arg.startswith("--") and "=" in arg:
            i += 1
            continue
        if arg in _GREP_VALUE_OPTS:
            i += 2
            continue
        if arg.startswith("-") and len(arg) > 1:
            i += 1
            continue
        positional.append(arg)
        i += 1
    return positional[0] if positional else None


def _grep_hits(stdout: str, argv: List[str]) -> List[Tuple[str, int]]:
    files_only = any(a in ("-l", "--files-with-matches") for a in argv[1:])
    if files_only:
        return [(line.strip(), 1) for line in stdout.splitlines() if line.strip()]
    hits = []
    for line in stdout.splitlines():
        match = _GREP_LINE_RE.match(line)
        if match:
            hits.append(match.group(1))
    return _count_hits(hits)


def _paged_path(argv: List[str]) -> Optional[Tuple[str, Optional[int]]]:
    """``(path, first line)`` a cat/head/tail/sed invocation shows, if one file."""
    name = os.path.basename(argv[0])
    rest = argv[1:]
    first_line: Optional[int] = 1
    if name == "sed":
        scripts = [a for a in rest if not a.startswith("-")]
        if len(scripts) != 2:
            return None
        script, path = scripts
        match = _SED_RANGE_RE.match(script.replace(" ", ""))
        if match is None:
            return None
        first_line = int(match.group(1))
        return path, first_line
    files: List[str] = []
    skip = False
    for arg in rest:
        if skip:
            skip = False
            continue
        if arg in ("-n", "-c", "--lines", "--bytes"):
            skip = True
            continue
        if arg.startswith("-"):
            continue
        files.append(arg)
    if len(files) != 1:
        return None
    if name == "tail":
        first_line = None
    return files[0], first_line


def _digest_line(entry: Entry) -> str:
    who = f"{entry.actor}, step {entry.step}"
    if entry.kind == "read":
        head = f"- {entry.key} ({entry.size} chars; {who})"
        if entry.partial:
            return f"{head}: partial"
        parts = [
            f"{e['n']} {e['label']} L{e['lines'][0]}-{e['lines'][1]}"
            for e in entry.outline[:OUTLINE_LINE_ENTRIES]
        ]
        more = len(entry.outline) - OUTLINE_LINE_ENTRIES
        if more > 0:
            parts.append(f"… +{more} more")
        return f"{head}: " + " | ".join(parts)
    if entry.kind == "search":
        shown = ", ".join(f"{f} x{n}" for f, n in entry.hits[:12])
        more = len(entry.hits) - 12
        if more > 0:
            shown += f", … +{more} files"
        hits = f"{entry.size} hits in {len(entry.hits)} files"
        return f"- {entry.key!r} ({entry.label}; {hits}; {who}): {shown or 'none'}"
    if entry.kind == "check":
        verdict = "passed" if entry.passed else "FAILED"
        summary = f" — {_clip(entry.summary, 160)}" if entry.summary else ""
        return f"- {entry.key} ({entry.label}; {who}): {verdict}{summary}"
    if entry.kind == "change":
        return f"- {entry.key} ({entry.label}; {who})"
    text = " ".join(entry.summary.split())
    if len(text) > FINDING_CHARS:
        text = text[: FINDING_CHARS - 1] + "…"
    return f"- [{entry.label}] {entry.key} ({who}): {text}"


def _fit_sections(sections: List[Tuple[str, List[str]]], max_chars: int) -> str:
    """Sections under the cap: each keeps whole lines up to an equal share."""
    full = "\n".join(f"{title}:\n" + "\n".join(lines) for title, lines in sections)
    if len(full) <= max_chars:
        return full
    share = max(200, max_chars // len(sections) - 20)
    rendered = []
    for title, lines in sections:
        kept: List[str] = []
        used = len(title) + 2
        for line in lines:
            if used + len(line) + 1 > share:
                break
            kept.append(line)
            used += len(line) + 1
        if not kept:
            kept.append(lines[0][: share - 1] + "…")
        dropped = len(lines) - len(kept)
        if dropped > 0:
            kept.append(f"… +{dropped} more ({LEDGER_TOOL}(query) lists them)")
        rendered.append(f"{title}:\n" + "\n".join(kept))
    return "\n".join(rendered)
