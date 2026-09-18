# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read Claude Code session transcripts into normalized traces.

Claude Code writes one JSONL file per session under
``~/.claude/projects/<slugified-cwd>/<session-uuid>.jsonl``, and one per
delegated run under ``<session-uuid>/subagents/agent-*.jsonl``.  Each line is a
record; the ones that matter are ``user`` (human turns and tool results),
``assistant`` (model turns carrying ``tool_use`` blocks and token usage), and
``ai-title`` (an auto-generated summary).

The reader is deterministic and runs no LLM.  It reports what the transcript
says; judging how well a session went is ``analyze``'s job.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# Claude Code's own tool names, grouped by what they do.
TOOL_FAMILIES: Dict[str, str] = {
    "Read": "read",
    "NotebookRead": "read",
    "Write": "write",
    "Edit": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Bash": "shell",
    "BashOutput": "shell",
    "KillShell": "shell",
    "Grep": "search",
    "Glob": "search",
    "LS": "search",
    "WebSearch": "web",
    "WebFetch": "web",
    "Task": "delegate",
    "Agent": "delegate",
    "TodoWrite": "plan",
    "ExitPlanMode": "plan",
    "AskUserQuestion": "plan",
    "SlashCommand": "meta",
    "Skill": "meta",
}

# Argument keys worth showing a human, in preference order. Used only for
# display — never for identity, which hashes the whole argument object.
_DIGEST_KEYS = ("command", "pattern", "file_path", "path", "query", "url")


def tool_family(name: str) -> str:
    """Bucket a tool name; MCP tools are ``mcp``, unknown ones ``other``."""
    if name in TOOL_FAMILIES:
        return TOOL_FAMILIES[name]
    if name.startswith("mcp__"):
        return "mcp"
    return "other"


@dataclass
class Usage:
    """Token accounting for one session."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens


@dataclass
class Step:
    """One tool invocation and how it turned out.

    ``ok`` is ``None`` when no ``tool_result`` ever arrived — an interrupted or
    truncated session.  Unknown is not success, so success rates must exclude
    it rather than count it either way.
    """

    tool: str
    family: str
    ok: Optional[bool] = None
    # Stable identity over the FULL argument object. Two calls are the same
    # call only if this matches; a digest of one field silently merges
    # different edits to one file into a fake repeat.
    arg_hash: str = ""
    arg_digest: str = ""
    result_chars: int = 0
    error: str = ""


@dataclass
class Trace:
    """A normalized session: what was asked, and what the model did about it."""

    session_id: str
    project: str
    kind: str = "session"  # or "subagent"
    cwd: str = ""
    git_branch: str = ""
    title: str = ""
    goal: str = ""
    prompts: List[str] = field(default_factory=list)
    steps: List[Step] = field(default_factory=list)
    started_at: str = ""
    last_at: str = ""
    assistant_turns: int = 0
    max_result_chars: int = 0
    models: List[str] = field(default_factory=list)
    interrupts: int = 0
    usage: Usage = field(default_factory=Usage)
    # Usage split by the model that produced it. A session can switch models
    # mid-run, so pricing the whole session at one model is wrong by up to a
    # 5x rate difference.
    usage_by_model: Dict[str, Usage] = field(default_factory=dict)
    # Records the reader could not use, surfaced so a caller can tell a clean
    # parse from one that quietly dropped part of a file.
    skipped_lines: int = 0
    subagents: List["Trace"] = field(default_factory=list)

    @property
    def attempt_count(self) -> int:
        """Tool calls whose outcome is known."""
        return sum(1 for s in self.steps if s.ok is not None)

    @property
    def total_calls(self) -> int:
        return len(self.steps)

    @property
    def success_count(self) -> int:
        return sum(1 for s in self.steps if s.ok is True)

    @property
    def unresolved_count(self) -> int:
        return sum(1 for s in self.steps if s.ok is None)

    @property
    def tool_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for s in self.steps:
            counts[s.tool] = counts.get(s.tool, 0) + 1
        return counts

    @property
    def family_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for s in self.steps:
            counts[s.family] = counts.get(s.family, 0) + 1
        return counts

    def walk(self) -> Iterator["Trace"]:
        """This trace and every subagent beneath it."""
        yield self
        for sub in self.subagents:
            yield from sub.walk()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["attempt_count"] = self.attempt_count
        d["total_calls"] = self.total_calls
        d["success_count"] = self.success_count
        d["unresolved_count"] = self.unresolved_count
        d["tool_counts"] = self.tool_counts
        d["family_counts"] = self.family_counts
        d["usage"]["total"] = self.usage.total
        # Subagent steps are kept in full: they are 43% of all tool work, and
        # summarising them made every downstream table silently main-only.
        d["subagents"] = [
            {
                "session_id": s.session_id,
                "kind": s.kind,
                "goal": " ".join(s.goal.split())[:300],
                "steps": s.attempt_count,
                "total_calls": s.total_calls,
                "success_count": s.success_count,
                "attempt_count": s.attempt_count,
                "assistant_turns": s.assistant_turns,
                "families": s.family_counts,
                "tool_counts": s.tool_counts,
                "usage": {**asdict(s.usage), "total": s.usage.total},
                "usage_by_model": {m: asdict(u) for m, u in s.usage_by_model.items()},
                "interrupts": s.interrupts,
                "steps_detail": [asdict(st) for st in s.steps],
            }
            for s in self.subagents
        ]
        return d


def _text_of(content: Any) -> str:
    """Flatten a message ``content`` field to plain text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _result_info(content: Any) -> tuple[int, str]:
    """Return ``(char_count, leading_text)`` for a ``tool_result`` payload."""
    if isinstance(content, str):
        return len(content), content[:400]
    if isinstance(content, list):
        total = 0
        head = ""
        for block in content:
            if isinstance(block, dict):
                text = str(block.get("text", ""))
                total += len(text)
                if not head:
                    head = text[:400]
        return total, head
    return 0, ""


def _hash_args(args: Any) -> str:
    try:
        blob = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        blob = repr(args)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


def _digest_args(args: Any) -> str:
    """The argument a human would recognise, kept long enough to parse.

    Newlines are preserved: flattening a multi-line script onto one line made
    every flag on lines 2..N look like a flag of line 1's binary. The 4000-char
    cap is generous because truncation was dropping ~31% of shell binary
    invocations, and non-uniformly — it distorted the ranking, not just scale.
    """
    if not isinstance(args, dict):
        return ""
    for key in _DIGEST_KEYS:
        if key in args:
            return str(args[key])[:4000]
    return ""


def read_session(path: Path, kind: str = "session") -> Optional[Trace]:
    """Parse one ``.jsonl`` transcript into a ``Trace``.

    Returns ``None`` for a transcript with no assistant activity — aborted or
    metadata-only sessions carry no procedure to learn from.
    """
    trace = Trace(session_id=path.stem, project=path.parent.name, kind=kind)
    pending: Dict[str, int] = {}  # tool_use id -> index into trace.steps
    saw_assistant = False
    # One API response is written as several JSONL records — one per content
    # block — and every one repeats the SAME usage object. Summing per record
    # double-counts tokens ~2x. Keep one usage per message id, taking the max
    # of each field because a mid-stream record can carry a partial count.
    usage_by_msg: Dict[str, tuple[Usage, str]] = {}

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                trace.skipped_lines += 1
                continue
            if not isinstance(rec, dict):
                trace.skipped_lines += 1
                continue

            rtype = rec.get("type")

            if rtype == "ai-title":
                trace.title = rec.get("aiTitle", "") or trace.title
                continue

            ts = rec.get("timestamp", "")
            if ts:
                if not trace.started_at:
                    trace.started_at = ts
                trace.last_at = ts
            if not trace.cwd:
                trace.cwd = rec.get("cwd", "") or ""
            if not trace.git_branch:
                trace.git_branch = rec.get("gitBranch", "") or ""

            message = rec.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")

            if rtype == "user":
                results = [
                    b
                    for b in (content if isinstance(content, list) else [])
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                ]
                if results:
                    for block in results:
                        idx = pending.pop(block.get("tool_use_id", ""), None)
                        chars, head = _result_info(block.get("content"))
                        flat = " ".join(head.split())
                        ok = not block.get("is_error")
                        # An interrupt lands inside the result, not as a turn.
                        if "[Request interrupted" in flat:
                            trace.interrupts += 1
                            ok = False
                        if idx is not None:
                            trace.steps[idx].ok = ok
                            trace.steps[idx].result_chars = chars
                            if not ok:
                                trace.steps[idx].error = flat[:300]
                        trace.max_result_chars = max(trace.max_result_chars, chars)
                    continue
                # Harness-injected turns are not human intent. isMeta is the
                # authoritative marker; the "<" test only catches some of them.
                if rec.get("isMeta"):
                    continue
                text = _text_of(content).strip()
                if "[Request interrupted" in text:
                    trace.interrupts += 1
                    continue
                if text and not text.startswith("<"):
                    trace.prompts.append(text)
                continue

            if rtype == "assistant":
                saw_assistant = True
                model = message.get("model")
                if model and model not in trace.models:
                    trace.models.append(model)
                u = message.get("usage")
                if isinstance(u, dict):
                    turn = Usage(
                        input_tokens=int(u.get("input_tokens") or 0),
                        output_tokens=int(u.get("output_tokens") or 0),
                        cache_read_tokens=int(u.get("cache_read_input_tokens") or 0),
                        cache_write_tokens=int(
                            u.get("cache_creation_input_tokens") or 0
                        ),
                    )
                    msg_id = message.get("id") or rec.get("uuid") or ""
                    prev = usage_by_msg.get(msg_id)
                    if prev is None:
                        usage_by_msg[msg_id] = (turn, model or "unknown")
                    else:
                        kept, kept_model = prev
                        usage_by_msg[msg_id] = (
                            Usage(
                                input_tokens=max(kept.input_tokens, turn.input_tokens),
                                output_tokens=max(
                                    kept.output_tokens, turn.output_tokens
                                ),
                                cache_read_tokens=max(
                                    kept.cache_read_tokens, turn.cache_read_tokens
                                ),
                                cache_write_tokens=max(
                                    kept.cache_write_tokens, turn.cache_write_tokens
                                ),
                            ),
                            kept_model,
                        )
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use":
                        continue
                    args = block.get("input", {})
                    trace.steps.append(
                        Step(
                            tool=block.get("name", "unknown"),
                            family=tool_family(block.get("name", "unknown")),
                            arg_hash=_hash_args(args),
                            arg_digest=_digest_args(args),
                        )
                    )
                    use_id = block.get("id")
                    if use_id:
                        pending[use_id] = len(trace.steps) - 1

    if not saw_assistant:
        return None
    # One model call == one message id, not one JSONL record.
    trace.assistant_turns = len(usage_by_msg)
    for turn, model in usage_by_msg.values():
        trace.usage.add(turn)
        trace.usage_by_model.setdefault(model, Usage()).add(turn)
    if trace.prompts:
        trace.goal = trace.prompts[0]
    return trace


def iter_traces(root: Optional[Path] = None) -> Iterator[Trace]:
    """Yield a ``Trace`` per top-level session, subagent transcripts attached.

    A delegated (``Task``/``Agent``) run gets its own transcript under
    ``<project>/<session-uuid>/subagents/``.  Those are sidechains of the
    parent, not sessions in their own right, so they are attached rather than
    yielded separately.
    """
    root = root or (Path.home() / ".claude" / "projects")
    for path in sorted(root.glob("*/*.jsonl")):
        trace = read_session(path)
        if trace is None:
            continue
        sub_dir = path.parent / path.stem / "subagents"
        if sub_dir.is_dir():
            for sub_path in sorted(sub_dir.glob("*.jsonl")):
                sub = read_session(sub_path, kind="subagent")
                if sub is not None:
                    trace.subagents.append(sub)
        yield trace
