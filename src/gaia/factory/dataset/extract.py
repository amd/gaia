# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Turn a raw Claude Code transcript into decision points.

``harvest.reader`` normalizes a session into ordered ``Step`` objects and is the
right tool for counting.  It deliberately drops three things a step-level eval
dataset needs, all of which live only in the raw JSONL:

* **The reasoning preamble.** Claude Code writes one JSONL record per *content
  block*, so a message's ``text`` and its ``tool_use`` land in different records.
  Grouping by ``message.id`` is the only way to see them together — a per-record
  scan finds zero co-occurrence and concludes, wrongly, that no reasoning exists.
* **The observation payload.** ``toolUseResult`` carries the file content a
  ``Read`` returned, a ``Bash`` command's stdout/stderr, and an ``Edit``'s
  structured patch.  That is the state the agent actually saw.
* **Episode structure.** Which human turn a step descends from, and how deep into
  that turn's dependent chain it sits.

This module adds those without re-implementing what ``reader`` already gets right:
tool families, full-argument identity hashing, and the ``isMeta`` filter.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from gaia.factory.harvest.analyze import classify_error
from gaia.factory.harvest.reader import _digest_args, _hash_args, tool_family

#: Sequential shell separators.  Pipes are excluded on purpose: ``grep x | head``
#: spawns two processes but is one dependent dataflow producing one answer, and
#: counting it as two actions flatters both the width and depth axes.
_SEGMENT_SEPARATORS = (";", "&&", "||", "\n")

#: Segment leaders that exist to rebuild state the runtime discarded, rather than
#: to do the work.  46% of raw segments in the corpus are these.
_SCAFFOLDING = frozenset({"cd", "echo", "pwd", "export", "set", "source", "."})

#: ``VAR=value`` opening a segment — a shell assignment, not a binary.
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")

#: A token a shell could actually execute: a bare command name, or a path to one.
#: Anything with a quote, paren or bracket in it is a fragment of a script body.
_BINARY_TOKEN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$|^[./~][A-Za-z0-9_./+-]*$")

#: ``<<EOF``, ``<<'EOF'``, ``<<"EOF"`` — everything after is script body until the
#: terminator reappears on its own line.
_HEREDOC = re.compile(
    r"<<-?\s*(?:'([A-Za-z_][A-Za-z0-9_]*)'|\"([A-Za-z_][A-Za-z0-9_]*)\"|([A-Za-z_][A-Za-z0-9_]*))"
)

#: Shell control keywords and heredoc/inline-script fragments. Splitting
#: ``for r in ...; do X; done`` yields segments led by ``for``, ``do`` and
#: ``done``, none of which is a binary. The source analysis notes the same ~7%
#: residual noise from inline script bodies; here it also made a legitimate
#: command look like it invoked an unknown program.
_CONTROL = frozenset(
    {
        "do",
        "done",
        "for",
        "while",
        "until",
        "if",
        "then",
        "else",
        "elif",
        "fi",
        "case",
        "esac",
        "in",
        "function",
        "select",
        "EOF",
        "PYEOF",
        "EOL",
        "{",
        "}",
        "(",
        ")",
        "[",
        "]",
        "[[",
        "]]",
        "!",
        "time",
        "coproc",
    }
)


@dataclass
class ToolCall:
    """One tool invocation inside a decision point."""

    tool: str
    family: str
    arguments: Dict[str, Any]
    arg_hash: str
    arg_digest: str
    shell_segments: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class Observation:
    """What came back from one tool call."""

    ok: Optional[bool] = None
    error_class: str = ""
    error_text: str = ""
    chars: int = 0
    text: str = ""
    file_path: str = ""
    file_content: str = ""
    original_file: str = ""
    structured_patch: Optional[List[Any]] = None
    # Paths a search returned. This is a main way the agent learns what exists,
    # so omitting it makes its known-path vocabulary look far smaller than it was.
    filenames: List[str] = field(default_factory=list)


@dataclass
class DecisionPoint:
    """One assistant inference call that dispatched at least one tool.

    The unit of the dataset.  A decision point is what a harness must reproduce:
    given everything known so far, emit the next action(s).
    """

    session_id: str
    message_id: str
    scope: str  # "main" | "subagent"
    project: str
    step_index: int
    episode_index: int
    depth_index: int
    cwd: str = ""
    git_branch: str = ""
    timestamp: str = ""
    reasoning_text: str = ""
    had_thinking_block: bool = False
    calls: List[ToolCall] = field(default_factory=list)
    observations: List[Observation] = field(default_factory=list)

    @property
    def width(self) -> int:
        return len(self.calls)

    @property
    def any_error(self) -> bool:
        return any(o.ok is False for o in self.observations)

    @property
    def reference_quality(self) -> str:
        """``errored`` flips grading polarity — see PLAN.md §6.3."""
        if any(o.ok is False for o in self.observations):
            return "errored"
        if all(o.ok is None for o in self.observations):
            return "unresolved"
        return "succeeded"


@dataclass
class TranscriptScan:
    """Everything one transcript file yields."""

    session_id: str
    project: str
    scope: str
    cwd: str = ""
    git_branch: str = ""
    started_at: str = ""
    first_prompt: str = ""
    episode_prompts: List[str] = field(default_factory=list)
    decisions: List[DecisionPoint] = field(default_factory=list)
    skipped_lines: int = 0


def split_shell_segments(command: str) -> List[Dict[str, str]]:
    """Split a shell command on its *sequential* separators only.

    §6c of the corpus analysis: ``Bash`` is 54.6% of tool calls and carries a
    median of 2 substantive segments, so a raw tool-call count understates
    executed actions ~2.5x.  The decision, though, is still one decision — the
    model composed the whole pipeline in a single forward pass — so segments are
    recorded as an attribute of the action rather than as separate records.

    Segments inside a heredoc or a quoted ``python -c`` body are marked
    ``script_body``.  15.0% of shell commands in this corpus carry an inline
    script, and splitting one on ``;``/newline as though it were shell yields
    "binaries" like ``open(p`` and ``encoding="utf-8``.  The source analysis
    records the same ~7% residual noise; left in, it also made well-formed
    commands look like they invoked programs that do not exist.
    """
    if not command:
        return []
    parts: List[str] = [command]
    for sep in _SEGMENT_SEPARATORS:
        nxt: List[str] = []
        for part in parts:
            nxt.extend(part.split(sep))
        parts = nxt

    segments: List[Dict[str, str]] = []
    heredoc_terminator: Optional[str] = None
    open_quote = False

    for raw in parts:
        text = raw.strip()
        if not text:
            continue

        in_body = heredoc_terminator is not None or open_quote
        if heredoc_terminator is not None and text.strip() == heredoc_terminator:
            heredoc_terminator = None
            segments.append(
                {"leader": text[:80], "kind": "script_body", "text": text[:600]}
            )
            continue

        leader = text.split()[0].lstrip("(").lstrip("{")
        assignment = _ASSIGNMENT.match(leader)
        if in_body:
            kind = "script_body"
        elif assignment:
            # A leading VAR=value is an environment assignment, not a binary.
            # Keeping the whole token put the assigned value — routinely an
            # absolute path — into the binary vocabulary, unscrubbed.
            leader = assignment.group(1)
            kind = "scaffolding"
        elif leader.startswith("#"):
            kind = "control"
        elif leader in _CONTROL:
            kind = "control"
        elif leader in _SCAFFOLDING:
            kind = "scaffolding"
        elif not _BINARY_TOKEN.match(leader):
            # Not a token any shell could execute — a stray fragment of a script
            # body the quote tracker did not catch.
            kind = "script_body"
        else:
            kind = "substantive"

        if heredoc_terminator is None:
            match = _HEREDOC.search(text)
            if match:
                heredoc_terminator = match.group(1) or match.group(2) or match.group(3)
        open_quote = _has_unbalanced_quote(text) != open_quote

        segments.append({"leader": leader[:80], "kind": kind, "text": text[:600]})
    return segments


def _has_unbalanced_quote(text: str) -> bool:
    """True when a segment leaves a quote open, so the next one is script body."""
    single = double = 0
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "'" and double % 2 == 0:
            single += 1
        elif char == '"' and single % 2 == 0:
            double += 1
    return (single % 2 == 1) or (double % 2 == 1)


def _blocks(content: Any, want: str) -> Iterator[Dict[str, Any]]:
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == want:
            yield block


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in _blocks(content, "text"))


def _observation_from(block: Dict[str, Any], tool_result: Any) -> Observation:
    """Build an Observation from a ``tool_result`` block plus ``toolUseResult``.

    The two carry different things: the block has the error flag and the text the
    model saw; ``toolUseResult`` has the structured payload (file content,
    stdout/stderr, patch hunks) that makes a record replayable.
    """
    content = block.get("content")
    if isinstance(content, str):
        text, chars = content, len(content)
    else:
        parts = [str(b.get("text", "")) for b in _blocks(content, "text")]
        text = "\n".join(parts)
        chars = sum(len(p) for p in parts)

    flat = " ".join(text.split())
    ok = not block.get("is_error")
    if "[Request interrupted" in flat:
        ok = False
    obs = Observation(ok=ok, chars=chars, text=text)
    if not ok:
        obs.error_class = classify_error(flat[:2000])
        obs.error_text = flat[:300]

    if isinstance(tool_result, dict):
        stdout = tool_result.get("stdout")
        stderr = tool_result.get("stderr")
        if isinstance(stdout, str) and stdout and not obs.text:
            obs.text = stdout
            obs.chars = len(stdout)
        if isinstance(stderr, str) and stderr and not obs.text:
            obs.text = stderr
            obs.chars = len(stderr)
        file_block = tool_result.get("file")
        if isinstance(file_block, dict):
            obs.file_path = str(file_block.get("filePath") or "")
            fc = file_block.get("content")
            if isinstance(fc, str):
                obs.file_content = fc
        if not obs.file_path:
            obs.file_path = str(tool_result.get("filePath") or "")
        original = tool_result.get("originalFile")
        # Claude Code nulls originalFile above roughly 10 KB, so its absence is
        # a size effect, not an error — callers must treat it as unavailable
        # rather than as "the file was empty".
        if isinstance(original, str) and original:
            obs.original_file = original
        patch = tool_result.get("structuredPatch")
        if isinstance(patch, list) and patch:
            obs.structured_patch = patch
        if not obs.text and isinstance(tool_result.get("content"), str):
            obs.text = tool_result["content"]
            obs.chars = len(obs.text)
        names = tool_result.get("filenames")
        if isinstance(names, list):
            obs.filenames = [str(n) for n in names if isinstance(n, str)][:400]
    return obs


def scan_transcript(path: Path, scope: str = "main") -> Optional[TranscriptScan]:
    """Parse one transcript into ordered decision points.

    Returns ``None`` when the file has no tool-dispatching assistant message —
    a pure question-and-answer session carries no procedure to evaluate.
    """
    scan = TranscriptScan(session_id=path.stem, project=path.parent.name, scope=scope)

    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                scan.skipped_lines += 1
                continue
            if isinstance(rec, dict):
                records.append(rec)
            else:
                scan.skipped_lines += 1

    # Pass 1 — results, keyed by tool_use id.  A result always arrives after its
    # call, so this cannot be folded into the ordered walk below.
    results: Dict[str, Observation] = {}
    for rec in records:
        if rec.get("type") != "user":
            continue
        message = rec.get("message")
        if not isinstance(message, dict):
            continue
        for block in _blocks(message.get("content"), "tool_result"):
            use_id = block.get("tool_use_id", "")
            if use_id:
                results[use_id] = _observation_from(block, rec.get("toolUseResult"))

    # Pass 2 — ordered walk building episodes and decision points.
    grouped: Dict[str, DecisionPoint] = {}
    episode_index = -1
    depth = 0
    step_index = 0

    for rec in records:
        if rec.get("cwd") and not scan.cwd:
            scan.cwd = rec["cwd"]
        if rec.get("gitBranch") and not scan.git_branch:
            scan.git_branch = rec["gitBranch"]
        if rec.get("timestamp") and not scan.started_at:
            scan.started_at = rec["timestamp"]

        message = rec.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        rtype = rec.get("type")

        if rtype == "user":
            if any(True for _ in _blocks(content, "tool_result")):
                continue
            # Harness-injected turns are not human intent; isMeta is the
            # authoritative marker and a startswith("<") test misses most of them.
            if rec.get("isMeta"):
                continue
            text = _text_of(content).strip()
            if not text or text.startswith("<") or "[Request interrupted" in text:
                continue
            episode_index += 1
            depth = 0
            scan.episode_prompts.append(text)
            if not scan.first_prompt:
                scan.first_prompt = text
            continue

        if rtype != "assistant" or not isinstance(content, list):
            continue

        key = message.get("id") or rec.get("uuid") or ""
        point = grouped.get(key)
        if point is None:
            point = DecisionPoint(
                session_id=scan.session_id,
                message_id=key,
                scope=scope,
                project=scan.project,
                # step_index and depth_index are deliberately left at -1 and
                # assigned when the point is appended below. Capturing them here
                # takes the index the counter happens to hold when a message's
                # *text* block arrives, and a message whose tool_use lands after
                # another message's records then carries a stale index — two
                # decision points end up sharing one step_index.
                step_index=-1,
                episode_index=max(episode_index, 0),
                depth_index=-1,
                cwd=rec.get("cwd", "") or scan.cwd,
                git_branch=rec.get("gitBranch", "") or scan.git_branch,
                timestamp=rec.get("timestamp", ""),
            )
            grouped[key] = point

        for block in _blocks(content, "text"):
            point.reasoning_text += block.get("text", "")
        for block in _blocks(content, "thinking"):
            point.had_thinking_block = True
            # Extended thinking is encrypted corpus-wide: every block carries an
            # empty string and a signature.  Kept only as a flag.
        for block in _blocks(content, "tool_use"):
            args = block.get("input", {})
            if not isinstance(args, dict):
                args = {"_raw": args}
            call = ToolCall(
                tool=block.get("name", "unknown"),
                family=tool_family(block.get("name", "unknown")),
                arguments=args,
                arg_hash=_hash_args(args),
                arg_digest=_digest_args(args),
            )
            if call.tool == "Bash" and isinstance(args.get("command"), str):
                call.shell_segments = split_shell_segments(args["command"])
            point.calls.append(call)
            point.observations.append(results.get(block.get("id", ""), Observation()))
            if len(point.calls) == 1:
                point.step_index = step_index
                point.depth_index = depth
                point.episode_index = max(episode_index, 0)
                scan.decisions.append(point)
                step_index += 1
                depth += 1

    if not scan.decisions:
        return None
    return scan


def iter_transcripts(
    session_ids: List[str], projects_root: Path
) -> Iterator[Tuple[str, Path, str]]:
    """Yield ``(session_id, path, scope)`` for wanted sessions and their subagents.

    A delegated run lives in ``<project>/<session-uuid>/subagents/`` and a plain
    ``*/*.jsonl`` glob misses it entirely — they carry 42.9% of all tool work in
    this corpus, so missing them would silently halve the dataset.
    """
    wanted = set(session_ids)
    for path in sorted(projects_root.glob("*/*.jsonl")):
        if path.stem not in wanted:
            continue
        yield path.stem, path, "main"
        sub_dir = path.parent / path.stem / "subagents"
        if sub_dir.is_dir():
            for sub_path in sorted(sub_dir.glob("*.jsonl")):
                yield path.stem, sub_path, "subagent"
