# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Build the step-level agentic evaluation dataset.

One record is one *decision point*: an assistant inference call that dispatched at
least one tool.  That is what a harness must reproduce — given everything known so
far, emit the next action — and it is the only unit that keeps width (did the
harness fan out?) and the reasoning preamble attached to the action they belong to.

Two passes over the corpus.  The first indexes every decision point cheaply so
quotas can be computed against real availability; the second materializes only the
selected records, with their bounded state and content-addressed blobs.  Splitting
it avoids holding several gigabytes of file content in memory to sample 4,000 rows
out of 32,000.

Everything written goes to the cache directory.  Nothing derived from a transcript
belongs in a repository.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from gaia.factory.dataset import annotate as annotate_mod
from gaia.factory.dataset import audit as audit_mod
from gaia.factory.dataset import axes as axes_mod
from gaia.factory.dataset import partition as part
from gaia.factory.dataset import verify as verify_mod
from gaia.factory.dataset.extract import (
    DecisionPoint,
    TranscriptScan,
    iter_transcripts,
    scan_transcript,
)
from gaia.factory.dataset.scrub import Scrubber, ScrubStats, is_pasted_third_party
from gaia.factory.dataset.verifiers import STATIC_CHECKS, run_static_checks
from gaia.factory.harvest.reader import _hash_args

#: Inline observation text past this goes to a content-addressed blob.  File
#: contents are median ~6 KB and p90 ~32 KB, so leaving them inline would make the
#: JSONL mostly file bodies and defeat streaming.
INLINE_LIMIT = 4000

#: How many prior decision points to materialize in full.  Enough to decide the
#: next action; the exact prefix stays reachable through ``state.transcript_ref``.
RECENT_STEPS = 12

#: Quota shape, applied per (use_case, partition). Compresses the head so
#: doc_authoring's 4,933 decision points do not swamp eval_quality's 164, and
#: floors the tail so the rare use-cases stay evaluable.
QUOTA_FRACTION = 0.12
QUOTA_FLOOR = 40
QUOTA_CEILING = 220

#: Minimum records per capability axis inside a quota, so error_recovery does not
#: sit at its 3.4% natural base rate.
AXIS_FLOOR = 8

#: How much of the agent's accumulated vocabulary to carry on a record. Both caps
#: were far too tight at first: 30% of records hit a 60-binary limit, so binaries
#: the agent had demonstrably used looked unseen to the argument verifier.
KNOWN_PATHS_CAP = 800
OBSERVED_BINARIES_CAP = 250

#: How many distinct transcripts must use a binary before it counts as installed
#: on this machine rather than as a one-off token from an inline script body.
ENV_BINARY_MIN_TRANSCRIPTS = 3

#: A path-shaped token inside a shell command. The agent learns most of its path
#: vocabulary from commands and search hits, not from Read arguments — collecting
#: only the latter left the median record knowing three paths.
#:
#: Both separators must be in the class. Written as ``[\/]`` the backslash is an
#: escape for ``/`` rather than a member, so the class matched forward slashes
#: only and every Windows path in a shell command was silently skipped — on a
#: corpus that is entirely Windows.
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_.<>-]*[\\/][A-Za-z0-9_.<>/\\-]{2,}")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _rank(record_id: str) -> str:
    """Deterministic sampling order, independent of filesystem iteration order."""
    return hashlib.sha256(("rank:" + record_id).encode("utf-8")).hexdigest()


def load_snapshot(
    snapshot: Path,
) -> Tuple[List[str], Dict[str, str], Dict[str, List[str]]]:
    """Session ids from the frozen traces, plus primary and secondary labels."""
    traces = snapshot / "traces.jsonl"
    labels_path = snapshot / "labels.txt"
    if not traces.is_file():
        raise FileNotFoundError(
            f"No traces.jsonl under {snapshot}.\n"
            "Run the extractor first:  python -m gaia.factory.harvest.scan\n"
            "It writes traces.jsonl, intents.jsonl and stats.json to "
            "~/.gaia/cache/factory/. Pass --snapshot only to build from a frozen "
            "copy of those files."
        )
    if not labels_path.is_file():
        raise FileNotFoundError(
            f"No labels.txt under {snapshot}.\n"
            "Use-case labels are required and are assigned by Claude Code, not by "
            "a script — see .claude/skills/building-eval-dataset/SKILL.md, step 2. "
            "It batches intents.jsonl and writes "
            f"{snapshot / 'labels.txt'} as '<8-char-session-prefix> <primary> "
            "<secondary,secondary>'."
        )
    session_ids: List[str] = []
    with traces.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                session_ids.append(json.loads(line)["session_id"])
    primary: Dict[str, str] = {}
    secondary: Dict[str, List[str]] = {}
    with labels_path.open(encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 2:
                primary[parts[0]] = parts[1]
                secondary[parts[0]] = parts[2].split(",") if len(parts) > 2 else []
    return session_ids, primary, secondary


def _use_case(session_id: str, primary: Dict[str, str]) -> str:
    return primary.get(session_id[:8], "unlabelled")


def tools_available_for(
    transcript_tools: Set[str],
    core_tools: Set[str],
    mcp_tools_by_server: Dict[str, Set[str]],
) -> List[str]:
    """The tool set offered at a decision point, without leaking the answer.

    Naively this is "every tool this transcript used", but that is the union over
    the transcript's *whole life* — including the tool the record is asking the
    harness to choose.  Measured on the first build it was severe: 30% of records
    listed three tools or fewer and **4.6% listed exactly the tools used**, which
    hands the answer over.  Short subagent transcripts were the worst case.

    So the set is rebuilt from what is true rather than from what was used:

    * **Core tools are harness-constant.**  Every non-MCP tool observed anywhere
      in the corpus was available in every session — one Claude Code build spans
      the whole period — so listing all of them leaks nothing.
    * **MCP tools come by server.**  An MCP server is connected for a session's
      entire life, so knowing ``mcp__claudia__*`` is reachable at step 1 is a
      fact about the session, not foresight.  Naming every tool that server
      exposes corpus-wide keeps the individual choice hidden.
    """
    available = set(core_tools)
    servers = {
        t.split("__")[1]
        for t in transcript_tools
        if t.startswith("mcp__") and len(t.split("__")) > 2
    }
    for server in servers:
        available |= mcp_tools_by_server.get(server, set())
    return sorted(available)


class BlobStore:
    """Content-addressed store for payloads too big to inline.

    Deduplicates for free, which matters: ~39% of reads in this corpus re-read a
    file already read in the same session.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.written: Set[str] = set()
        self.bytes_written = 0

    def put(self, text: str) -> str:
        digest = _sha(text)
        if digest not in self.written:
            path = self.root / f"{digest}.txt"
            data = text.encode("utf-8", "replace")
            path.write_bytes(data)
            self.written.add(digest)
            self.bytes_written += len(data)
        return digest

    def read(self, digest: str) -> Optional[str]:
        path = self.root / f"{digest}.txt"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")


def _light_record(
    point: DecisionPoint,
    scan: TranscriptScan,
    use_case: str,
    partition_side: str,
) -> Dict[str, Any]:
    """The minimum needed to tag axes and apply quotas, without any payload."""
    return {
        "record_id": _sha(point.session_id + "|" + point.message_id)[:16],
        "session_id": point.session_id,
        "message_id": point.message_id,
        "scope": point.scope,
        "transcript_stem": scan.session_id,
        "step_index": point.step_index,
        "episode_index": point.episode_index,
        "depth_index": point.depth_index,
        "use_case": use_case,
        "partition": partition_side,
        "work_item": part.work_item(scan.project, scan.git_branch, point.session_id),
        "action": {
            "width": point.width,
            "calls": [
                {
                    "tool": c.tool,
                    "family": c.family,
                    "arg_hash": c.arg_hash,
                    "shell_segments": c.shell_segments,
                    "arguments": c.arguments,
                }
                for c in point.calls
            ],
        },
        "observation": [{"chars": o.chars} for o in point.observations],
        "outcome": {"reference_quality": point.reference_quality},
    }


def index_pass(
    session_ids: List[str],
    projects_root: Path,
    primary: Dict[str, str],
    index_path: Path,
) -> Dict[str, Any]:
    """Pass 1 — walk every wanted transcript and index its decision points."""
    tool_keys: Dict[str, Counter] = defaultdict(Counter)
    tool_calls: Counter = Counter()
    tool_types: Dict[str, Dict[str, Counter]] = defaultdict(
        lambda: defaultdict(Counter)
    )
    tools_by_transcript: Dict[str, Set[str]] = defaultdict(set)
    core_tools: Set[str] = set()
    mcp_tools_by_server: Dict[str, Set[str]] = defaultdict(set)
    binary_transcripts: Dict[str, Set[str]] = defaultdict(set)
    stats = {
        "sessions_requested": len(session_ids),
        "sessions_on_disk": 0,
        "sessions_with_decision_points": 0,
        "transcripts_scanned": 0,
        "decision_points": 0,
        "with_preamble": 0,
        "with_thinking_block": 0,
        "with_recoverable_thinking": 0,
        "skipped_lines": 0,
    }
    on_disk: Set[str] = set()
    seen_sessions: Set[str] = set()

    with index_path.open("w", encoding="utf-8") as out:
        for session_id, path, scope in iter_transcripts(session_ids, projects_root):
            on_disk.add(session_id)
            scan = scan_transcript(path, scope=scope)
            if scan is None:
                continue
            seen_sessions.add(session_id)
            stats["transcripts_scanned"] += 1
            stats["skipped_lines"] += scan.skipped_lines
            use_case = _use_case(session_id, primary)
            side = part.assign(session_id)
            episode_last: Dict[int, int] = {}
            for point in scan.decisions:
                episode_last[point.episode_index] = point.step_index
            for point in scan.decisions:
                # Subagent decision points inherit the parent session's id so a
                # delegated run can never land on the other side of the split.
                point.session_id = session_id
                light = _light_record(point, scan, use_case, side)
                light["transcript_path"] = str(path)
                light["is_episode_final"] = (
                    point.step_index == episode_last[point.episode_index]
                )
                light["has_preamble"] = bool(point.reasoning_text.strip())
                light["had_thinking_block"] = point.had_thinking_block
                out.write(json.dumps(light, ensure_ascii=False) + "\n")
                stats["decision_points"] += 1
                if light["has_preamble"]:
                    stats["with_preamble"] += 1
                if point.had_thinking_block:
                    stats["with_thinking_block"] += 1
                for call in point.calls:
                    tool_calls[call.tool] += 1
                    tools_by_transcript[scan.session_id].add(call.tool)
                    parts = call.tool.split("__")
                    if call.tool.startswith("mcp__") and len(parts) > 2:
                        mcp_tools_by_server[parts[1]].add(call.tool)
                    else:
                        core_tools.add(call.tool)
                    for seg in call.shell_segments:
                        if seg["kind"] == "substantive":
                            binary_transcripts[seg["leader"]].add(scan.session_id)
                    for key, value in call.arguments.items():
                        tool_keys[call.tool][key] += 1
                        tool_types[call.tool][key][type(value).__name__] += 1

    # Two different absences, deliberately not merged: Claude Code prunes old
    # transcripts, and a session that made no tool call carries no procedure to
    # evaluate. Reporting them as one number would blame pruning for both.
    stats["sessions_on_disk"] = len(on_disk)
    stats["sessions_pruned_from_disk"] = len(session_ids) - len(on_disk)
    stats["sessions_with_decision_points"] = len(seen_sessions)
    stats["sessions_on_disk_without_tool_calls"] = len(on_disk) - len(seen_sessions)
    catalog = {
        "basis": "induced_from_corpus_usage",
        "why": (
            "Transcripts record tool names and arguments, never the schema block "
            "sent to the API. The harness version field cannot substitute: all 399 "
            "transcripts spanning six weeks stamp one version, which is not "
            "credible as a per-session build. These schemas are induced from "
            "observed usage and must not be read as captured schemas."
        ),
        "tools": {
            tool: {
                "calls": tool_calls[tool],
                "arguments": {
                    key: {
                        "seen": n,
                        "fraction_of_calls": round(n / tool_calls[tool], 3),
                        "types": dict(tool_types[tool][key]),
                    }
                    for key, n in tool_keys[tool].most_common()
                },
            }
            for tool in sorted(tool_calls)
        },
    }
    return {
        "stats": stats,
        "catalog": catalog,
        "tools_by_transcript": tools_by_transcript,
        "core_tools": core_tools,
        "mcp_tools_by_server": dict(mcp_tools_by_server),
        # A binary seen across several unrelated transcripts demonstrably exists
        # on this machine, so its first use in one session is not an invention.
        # Same reasoning as tools_available: environment facts are not foresight.
        "environment_binaries": {
            name
            for name, seen in binary_transcripts.items()
            if len(seen) >= ENV_BINARY_MIN_TRANSCRIPTS
        },
    }


def _quota(available: int) -> int:
    return min(
        available,
        max(QUOTA_FLOOR, min(QUOTA_CEILING, round(QUOTA_FRACTION * available))),
    )


def select(index_path: Path) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Pass 1.5 — stratified selection: use-case quota, then axis floors.

    Returns the chosen ``record_id`` values mapped to the axis and difficulty
    tags computed here.  Those tags depend on a record's *predecessor* and on
    failures accumulated across its episode, so they can only be derived during
    an ordered walk — recomputing them in pass 2, which visits transcripts in a
    different grouping, would silently produce different tags.
    """
    by_group: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    prev_by_transcript: Dict[str, Optional[Dict[str, Any]]] = {}
    failures_in_episode: Dict[Tuple[str, int], int] = defaultdict(int)

    with index_path.open(encoding="utf-8") as fh:
        for line in fh:
            light = json.loads(line)
            stem = light["transcript_path"]
            previous = prev_by_transcript.get(stem)
            light["capability_axes"] = axes_mod.axes_for(
                light, previous, light["is_episode_final"]
            )
            ep_key = (stem, light["episode_index"])
            light["difficulty"] = axes_mod.difficulty_for(
                light, failures_in_episode[ep_key]
            )
            if light["outcome"]["reference_quality"] == "errored":
                failures_in_episode[ep_key] += 1
            prev_by_transcript[stem] = light
            by_group[(light["use_case"], light["partition"])].append(
                {
                    "record_id": light["record_id"],
                    "axes": light["capability_axes"],
                    "difficulty": light["difficulty"],
                    "quality": light["outcome"]["reference_quality"],
                }
            )

    tags: Dict[str, Dict[str, Any]] = {}
    plan: List[Dict[str, Any]] = []
    for (use_case, side), items in sorted(by_group.items()):
        items.sort(key=lambda it: _rank(it["record_id"]))
        quota = _quota(len(items))
        chosen: Set[str] = set()
        # Axis floors first, so scarce capabilities are represented before the
        # bulk fill consumes the quota with whatever is most common.
        for axis in axes_mod.ALL_AXES:
            have = sum(
                1 for it in items if it["record_id"] in chosen and axis in it["axes"]
            )
            for it in items:
                if have >= AXIS_FLOOR or len(chosen) >= quota:
                    break
                if it["record_id"] not in chosen and axis in it["axes"]:
                    chosen.add(it["record_id"])
                    have += 1
        for it in items:
            if len(chosen) >= quota:
                break
            chosen.add(it["record_id"])
        for it in items:
            if it["record_id"] in chosen:
                tags[it["record_id"]] = {
                    "capability_axes": it["axes"],
                    "difficulty": it["difficulty"],
                }
        plan.append(
            {
                "use_case": use_case,
                "partition": side,
                "available": len(items),
                "quota": quota,
                "selected": len(chosen),
                "short_of_floor": len(chosen) < QUOTA_FLOOR,
            }
        )
    return tags, {
        "quota_rule": {
            "fraction": QUOTA_FRACTION,
            "floor": QUOTA_FLOOR,
            "ceiling": QUOTA_CEILING,
            "axis_floor": AXIS_FLOOR,
        },
        "groups": plan,
    }


class StateAccumulator:
    """What the agent knew, rebuilt incrementally as a transcript is walked."""

    def __init__(self) -> None:
        self.known_paths: List[str] = []
        self._paths_seen: Set[str] = set()
        self.binaries: Counter = Counter()
        self.files_in_context: Dict[str, Dict[str, Any]] = {}
        self.recent: List[Dict[str, Any]] = []
        self.episode_actions: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        self.failures: Counter = Counter()

    def note_path(self, value: Any) -> None:
        if isinstance(value, str) and value and ("/" in value or "\\" in value):
            if value not in self._paths_seen and len(self._paths_seen) < 8000:
                self._paths_seen.add(value)
                self.known_paths.append(value)

    def note_paths_in_text(self, text: str, limit: int = 60) -> None:
        """Harvest path-shaped tokens from a command or a search result."""
        if not text:
            return
        for match in _PATH_TOKEN.findall(text[:20000])[:limit]:
            self.note_path(match.strip("\"'`,;:()[]{}"))

    def _entry(self, key: str, text: str, blobs: BlobStore, step: int) -> None:
        self.files_in_context.pop(key, None)
        self.files_in_context[key] = {
            "blob": blobs.put(text),
            "chars": len(text),
            "seen_at_step": step,
        }

    def _apply_edit(self, call, obs, blobs: BlobStore, scrubber: Scrubber) -> None:
        """Replay a successful edit onto the held copy of the file.

        Without this the held copy stays as whatever the last Read returned, so
        every later check against that file fails once the agent has edited it.
        94% of old_string failures were exactly this staleness, not a bad
        argument.
        """
        path = call.arguments.get("file_path") or obs.file_path
        old = call.arguments.get("old_string")
        new = call.arguments.get("new_string")
        if not (
            isinstance(path, str) and isinstance(old, str) and isinstance(new, str)
        ):
            return
        key = scrubber.text(path)
        entry = self.files_in_context.get(key)
        if not entry:
            return
        current = blobs.read(entry["blob"])
        scrubbed_old = scrubber.text(old)
        if current is None or scrubbed_old not in current:
            return
        count = -1 if call.arguments.get("replace_all") else 1
        updated = current.replace(scrubbed_old, scrubber.text(new), count)
        self._entry(key, updated, blobs, entry["seen_at_step"])

    def _apply_write(self, call, obs, blobs: BlobStore, scrubber: Scrubber) -> None:
        path = call.arguments.get("file_path") or obs.file_path
        content = call.arguments.get("content")
        if isinstance(path, str) and isinstance(content, str):
            self._entry(scrubber.text(path), scrubber.text(content), blobs, -1)

    def absorb(
        self, point: DecisionPoint, blobs: BlobStore, scrubber: Scrubber
    ) -> None:
        for call, obs in zip(point.calls, point.observations):
            for key in ("file_path", "path", "notebook_path"):
                self.note_path(call.arguments.get(key))
            if isinstance(call.arguments.get("command"), str):
                self.note_paths_in_text(call.arguments["command"])
            for name in obs.filenames:
                self.note_path(name)
            if obs.ok and obs.text and call.family in ("search", "shell"):
                self.note_paths_in_text(obs.text)
            for seg in call.shell_segments:
                self.binaries[seg["leader"]] += 1
            if obs.ok and call.tool in ("Edit", "MultiEdit"):
                self._apply_edit(call, obs, blobs, scrubber)
            if obs.ok and call.tool == "Write":
                self._apply_write(call, obs, blobs, scrubber)
            if obs.file_path and obs.file_content:
                self.note_path(obs.file_path)
                # Re-reading a file must refresh its position: ~39% of reads
                # in this corpus are re-reads, and updating in place left the
                # recency slice keeping stale entries over just-read ones.
                self._entry(
                    scrubber.text(obs.file_path),
                    scrubber.text(obs.file_content),
                    blobs,
                    point.step_index,
                )
            if obs.ok is False and obs.error_class:
                self.failures[obs.error_class] += 1
            self.episode_actions[point.episode_index].append(
                {
                    "tool": call.tool,
                    "arg_digest": scrubber.text(call.arg_digest[:200]),
                    "ok": obs.ok,
                }
            )
        self.recent.append(
            {
                "step_index": point.step_index,
                "reasoning": scrubber.text(point.reasoning_text.strip()[:600]),
                "calls": [
                    {"tool": c.tool, "arguments": scrubber.value(c.arguments)}
                    for c in point.calls
                ],
                "outcomes": [
                    {
                        "ok": o.ok,
                        "error_class": o.error_class,
                        "chars": o.chars,
                        "head": scrubber.text(" ".join(o.text.split())[:600]),
                    }
                    for o in point.observations
                ],
            }
        )
        if len(self.recent) > RECENT_STEPS:
            self.recent.pop(0)


def _materialize_observation(
    obs: Any, blobs: BlobStore, scrubber: Scrubber, stats: ScrubStats
) -> Dict[str, Any]:
    text = scrubber.text(obs.text or "", stats)
    entry: Dict[str, Any] = {
        "ok": obs.ok,
        "error_class": obs.error_class,
        "error_text": scrubber.text(obs.error_text, stats),
        "chars": obs.chars,
        "truncated": len(text) > INLINE_LIMIT,
        "text": text[:INLINE_LIMIT],
        "blob_ref": blobs.put(text) if len(text) > INLINE_LIMIT else None,
    }
    if obs.file_path:
        entry["file_path"] = scrubber.text(obs.file_path, stats)
    if obs.file_content:
        entry["file_content_ref"] = blobs.put(scrubber.text(obs.file_content, stats))
        entry["file_content_chars"] = len(obs.file_content)
    if obs.original_file:
        entry["original_file_ref"] = blobs.put(scrubber.text(obs.original_file, stats))
    else:
        # Absence is a size effect, not an empty file: Claude Code nulls
        # originalFile above roughly 10 KB.
        entry["original_file_ref"] = None
        entry["original_file_basis"] = "unavailable_above_harness_size_cap"
    if obs.structured_patch:
        entry["structured_patch"] = scrubber.value(obs.structured_patch, stats)
    return entry


def build_record(
    point: DecisionPoint,
    scan: TranscriptScan,
    light: Dict[str, Any],
    state: StateAccumulator,
    blobs: BlobStore,
    scrubber: Scrubber,
    tools_available: List[str],
    environment_binaries: Set[str],
    use_case_secondary: List[str],
    turn_annotations: List[Any],
    episode_outcomes: Dict[int, Any],
    previous_point: Any,
    stats: ScrubStats,
) -> Dict[str, Any]:
    """Assemble one full, scrubbed record."""
    _scrubbed_args = [scrubber.value(c.arguments, stats) for c in point.calls]
    reasoning = point.reasoning_text.strip()
    if reasoning:
        status = "visible_preamble"
    elif point.had_thinking_block:
        status = "redacted_thinking_only"
    else:
        status = "none"

    record = {
        "record_id": light["record_id"],
        "schema_version": 2,
        "session_id": point.session_id,
        "message_id": point.message_id,
        "scope": point.scope,
        # session_id is the *parent* session throughout, so a delegated run can
        # never land on the other side of the partition from its parent.  That
        # makes it useless for locating the file, so the transcript stem ships
        # alongside it — a subagent lives in <session>/subagents/<stem>.jsonl.
        "transcript_id": scan.session_id,
        "parent_session_id": point.session_id if point.scope == "subagent" else None,
        "step_index": point.step_index,
        # Keyed on the transcript, not the session: a parent and its subagents
        # share one session_id, so keying on that gave 20 independent agent runs
        # the same episode_id and merged them into one fake episode.
        "episode_id": f"{scan.session_id}#{point.episode_index}",
        "depth_index": point.depth_index,
        "work_item": scrubber.text(light["work_item"], stats),
        "partition": light["partition"],
        "use_case": light["use_case"],
        "use_case_secondary": use_case_secondary,
        "capability_axes": light["capability_axes"],
        "difficulty": light["difficulty"],
        "goal": scrubber.free_text(scan.first_prompt, stats),
        "episode_instruction": scrubber.free_text(
            (
                scan.episode_prompts[point.episode_index]
                if point.episode_index < len(scan.episode_prompts)
                else scan.first_prompt
            ),
            stats,
        ),
        "tools_available": tools_available,
        "tools_available_basis": "induced_from_corpus_usage",
        "reasoning": {
            "visible_text": scrubber.text(reasoning, stats),
            "thinking": None,
            "thinking_basis": "unavailable_encrypted_by_harness",
            "status": status,
            # The model's own reasoning is encrypted corpus-wide. This is a
            # reconstruction of the decision *context* from observable state, and
            # says so in its own basis field.
            "inferred": annotate_mod.infer_reasoning(
                point,
                previous_point,
                scrubber.free_text(scan.first_prompt, stats),
                point.depth_index,
                stated_preamble=scrubber.text(reasoning, stats),
            ),
        },
        "state": {
            "transcript_ref": {
                "session_id": point.session_id,
                "transcript_id": scan.session_id,
                "scope": point.scope,
                "message_id": point.message_id,
                "step_index": point.step_index,
            },
            "cwd": scrubber.text(point.cwd, stats),
            "git_branch": scrubber.text(point.git_branch, stats),
            "recent_steps": state.recent[-RECENT_STEPS:],
            "episode_actions": state.episode_actions[point.episode_index][-400:],
            "known_paths": [
                scrubber.text(p, stats) for p in state.known_paths[-KNOWN_PATHS_CAP:]
            ],
            "observed_binaries": [
                scrubber.text(b, stats)
                for b, _ in state.binaries.most_common(OBSERVED_BINARIES_CAP)
            ],
            # Present on the machine, evidenced corpus-wide. Distinct from
            # observed_binaries, which is only what *this* run has reached for.
            "environment_binaries": sorted(environment_binaries),
            "files_in_context": dict(list(state.files_in_context.items())[-40:]),
            "prior_failures": {
                "count": sum(state.failures.values()),
                "classes": dict(state.failures),
            },
        },
        "action": {
            "width": point.width,
            "calls": [
                {
                    "tool": c.tool,
                    "family": c.family,
                    "arguments": _scrubbed_args[i],
                    # Hashed over the *scrubbed* arguments, so a consumer can
                    # recompute identity from what they were given. Hashing the
                    # originals left 58% of records carrying a hash that did not
                    # describe their own contents, and made two records shipping
                    # identical arguments look distinct.
                    "arg_hash": _hash_args(_scrubbed_args[i]),
                    "arg_digest": scrubber.text(c.arg_digest[:400], stats),
                    "shell_segments": [
                        {
                            **seg,
                            "leader": scrubber.text(seg["leader"], stats),
                            "text": scrubber.text(seg["text"], stats),
                        }
                        for seg in c.shell_segments
                    ],
                }
                for i, c in enumerate(point.calls)
            ],
        },
        "observation": [
            _materialize_observation(o, blobs, scrubber, stats)
            for o in point.observations
        ],
        "outcome": {
            "any_error": point.any_error,
            "error_classes": [
                o.error_class for o in point.observations if o.error_class
            ],
            "reference_quality": point.reference_quality,
        },
        "repo": {
            "project": scrubber.text(scan.project, stats),
            "branch": scrubber.text(scan.git_branch, stats),
            "commit": None,
            "commit_basis": "unavailable",
            "commit_verified": False,
        },
        # Grading metadata, deliberately OUTSIDE `state`: it describes how the
        # episode turned out, which a candidate harness must never see. Same
        # status as `action` and `observation`.
        "episode_turn_class": (
            turn_annotations[point.episode_index].to_dict()
            if point.episode_index < len(turn_annotations)
            else None
        ),
        "episode_outcome": (
            episode_outcomes[point.episode_index].to_dict()
            if point.episode_index in episode_outcomes
            else None
        ),
        "grading_polarity": (
            "avoid_reference"
            if point.reference_quality == "errored"
            else "match_reference"
        ),
    }
    # Calibration: run the static checks over the reference's own action. Where
    # the reference fails, the check cannot discriminate between harnesses on
    # this record and the consumer drops it rather than scoring noise.
    record["reference_checks"] = run_static_checks(
        record["action"]["calls"], record, blobs.read
    )
    return record


def materialize_pass(
    session_ids: List[str],
    projects_root: Path,
    secondary: Dict[str, List[str]],
    index_path: Path,
    tags: Dict[str, Dict[str, Any]],
    tools_by_transcript: Dict[str, Set[str]],
    core_tools: Set[str],
    mcp_tools_by_server: Dict[str, Set[str]],
    environment_binaries: Set[str],
    blobs: BlobStore,
    scrubber: Scrubber,
) -> Tuple[List[Dict[str, Any]], ScrubStats, Dict[str, int]]:
    """Pass 2 — rebuild the selected decision points with full state."""
    selected = set(tags)
    light_by_id: Dict[str, Dict[str, Any]] = {}
    with index_path.open(encoding="utf-8") as fh:
        for line in fh:
            light = json.loads(line)
            if light["record_id"] in selected:
                light.update(tags[light["record_id"]])
                light_by_id[light["record_id"]] = light

    records: List[Dict[str, Any]] = []
    stats = ScrubStats()
    counters = {"dropped_pasted": 0, "materialized": 0, "missing_light": 0}

    for session_id, path, scope in iter_transcripts(session_ids, projects_root):
        scan = scan_transcript(path, scope=scope)
        if scan is None:
            continue
        # Every human turn, not just the opening one. The canonical case in this
        # corpus opens with "summarize the transcript I'm about to paste" and
        # pastes it in the *second* turn — checking only the first prompt finds
        # nothing and ships the transcript.
        turn_annotations = annotate_mod.annotate_turns(scan.episode_prompts)
        by_episode: Dict[int, List[Any]] = defaultdict(list)
        for decision in scan.decisions:
            by_episode[decision.episode_index].append(decision)
        episode_outcomes = {
            idx: annotate_mod.infer_episode_outcome(
                points,
                turn_annotations[idx + 1] if idx + 1 < len(turn_annotations) else None,
            )
            for idx, points in by_episode.items()
        }
        paste_reason = next(
            (
                reason
                for reason in (
                    is_pasted_third_party(p)
                    for p in [scan.first_prompt, *scan.episode_prompts]
                )
                if reason
            ),
            None,
        )
        state = StateAccumulator()
        previous_point = None
        available = tools_available_for(
            tools_by_transcript.get(scan.session_id, set()),
            core_tools,
            mcp_tools_by_server,
        )
        for point in scan.decisions:
            point.session_id = session_id
            record_id = _sha(point.session_id + "|" + point.message_id)[:16]
            if record_id in selected:
                light = light_by_id.get(record_id)
                if light is None:
                    counters["missing_light"] += 1
                elif paste_reason:
                    # A regex cannot anonymise arbitrary proper nouns, so records
                    # built on pasted third-party prose are dropped, not cleaned.
                    counters["dropped_pasted"] += 1
                    stats.bump("dropped_pasted_third_party")
                else:
                    records.append(
                        build_record(
                            point,
                            scan,
                            light,
                            state,
                            blobs,
                            scrubber,
                            available,
                            environment_binaries,
                            secondary.get(session_id[:8], []),
                            turn_annotations,
                            episode_outcomes,
                            previous_point,
                            stats,
                        )
                    )
                    counters["materialized"] += 1
            state.absorb(point, blobs, scrubber)
            previous_point = point
    return records, stats, counters


def write_dataset(
    out_dir: Path, records: List[Dict[str, Any]], flagged: Set[str]
) -> Dict[str, int]:
    counts = {part.ORACLE: 0, part.POOL: 0}
    handles = {}
    for side in (part.ORACLE, part.POOL):
        (out_dir / side).mkdir(parents=True, exist_ok=True)
        handles[side] = (out_dir / side / "records.jsonl").open("w", encoding="utf-8")
    try:
        for rec in records:
            rec["near_dup_in_other_partition"] = rec["record_id"] in flagged
            rec["record_sha256"] = _sha(
                json.dumps(rec, sort_keys=True, ensure_ascii=False)
            )
            handles[rec["partition"]].write(json.dumps(rec, ensure_ascii=False) + "\n")
            counts[rec["partition"]] += 1
    finally:
        for fh in handles.values():
            fh.close()
    (out_dir / part.ORACLE / "README.md").write_text(
        "# Held-out oracle — do not tune against this\n\n"
        "Records here are selected by "
        "`int(sha256(session_id)[:8], 16) % 100 < 30`, before anything was "
        "generated, so a harness tuned on `pool/` has not seen these sessions.\n\n"
        "Two honest limits:\n\n"
        "- This is an **auto-mined** oracle, not a human-curated one. Treat it as a "
        "regression detector, never as a release gate.\n"
        "- 58% of sessions share a repository branch with a session in `pool/`. "
        "Specific actions barely leak (0.9% overlap), but codebase familiarity "
        "does. See `../contamination.json`.\n",
        encoding="utf-8",
    )
    return counts


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_use_case: Dict[str, Counter] = defaultdict(Counter)
    by_axis: Counter = Counter()
    by_difficulty: Counter = Counter()
    reasoning: Counter = Counter()
    polarity: Counter = Counter()
    obs_recoverable = 0
    obs_total = 0
    file_content = 0
    original_file = 0
    edit_calls = 0
    for rec in records:
        by_use_case[rec["use_case"]][rec["partition"]] += 1
        for axis in rec["capability_axes"]:
            by_axis[axis] += 1
        by_difficulty[rec["difficulty"]] += 1
        reasoning[rec["reasoning"]["status"]] += 1
        polarity[rec["grading_polarity"]] += 1
        for call, obs in zip(rec["action"]["calls"], rec["observation"]):
            obs_total += 1
            if obs.get("text") or obs.get("blob_ref") or obs.get("file_content_ref"):
                obs_recoverable += 1
            if obs.get("file_content_ref"):
                file_content += 1
            if call["tool"] in ("Edit", "MultiEdit"):
                edit_calls += 1
                if obs.get("original_file_ref"):
                    original_file += 1
    baseline = {name: {"applicable": 0, "passed": 0} for name in STATIC_CHECKS}
    for rec in records:
        for name, res in rec.get("reference_checks", {}).items():
            if res.get("applicable"):
                baseline[name]["applicable"] += 1
                baseline[name]["passed"] += 1 if res.get("passed") else 0
    for name, b in baseline.items():
        b["pct_reference_passes"] = round(
            100.0 * b["passed"] / max(b["applicable"], 1), 1
        )
    return {
        "records": len(records),
        "reference_check_baseline": baseline,
        "by_use_case": {k: dict(v) for k, v in sorted(by_use_case.items())},
        "by_capability_axis": dict(by_axis.most_common()),
        "by_difficulty": dict(by_difficulty),
        "reasoning_status": dict(reasoning),
        "grading_polarity": dict(polarity),
        "observations": {
            "total_tool_calls": obs_total,
            "with_recoverable_observation": obs_recoverable,
            "pct_recoverable": round(100.0 * obs_recoverable / max(obs_total, 1), 1),
            "with_file_content": file_content,
            "edit_calls": edit_calls,
            "edit_calls_with_original_file": original_file,
            "pct_edits_with_original_file": round(
                100.0 * original_file / max(edit_calls, 1), 1
            ),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=Path.home() / ".gaia" / "cache" / "factory",
        help=(
            "Directory holding traces.jsonl and labels.txt. Defaults to where "
            "harvest.scan writes. Point it at a frozen copy for reproducibility."
        ),
    )
    parser.add_argument(
        "--projects",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="Raw Claude Code transcript root.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home() / ".gaia" / "cache" / "factory" / "dataset",
        help="Output directory. Must be outside every repository working tree.",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("USERNAME") or os.environ.get("USER") or "",
        help="Account name owning the corpus; required for path anonymisation.",
    )
    parser.add_argument(
        "--extra-name",
        action="append",
        default=[],
        help=(
            "An additional literal to redact (repeatable). Use for colleagues named "
            "in prompts: a regex cannot infer which capitalised words are people, so "
            "known names must be supplied rather than guessed."
        ),
    )
    args = parser.parse_args(argv)

    if (args.out / ".git").exists() or any(
        (p / ".git").exists() for p in args.out.parents
    ):
        raise SystemExit(
            f"Refusing to write dataset into a git working tree: {args.out}. "
            "Nothing derived from a session corpus belongs in a repository. "
            "Point --out at a path under ~/.gaia/cache/."
        )

    scrubber = Scrubber(username=args.username, extra_names=args.extra_name)
    args.out.mkdir(parents=True, exist_ok=True)
    work = args.out / ".work"
    work.mkdir(exist_ok=True)
    index_path = work / "index.jsonl"

    session_ids, primary, secondary = load_snapshot(args.snapshot)
    print(f"[1/5] indexing {len(session_ids)} sessions from {args.snapshot} …")
    indexed = index_pass(session_ids, args.projects, primary, index_path)
    print(
        f"      {indexed['stats']['decision_points']} decision points from "
        f"{indexed['stats']['sessions_with_decision_points']} sessions "
        f"({indexed['stats']['sessions_pruned_from_disk']} pruned from disk, "
        f"{indexed['stats']['sessions_on_disk_without_tool_calls']} with no tool calls)"
    )

    print("[2/5] selecting …")
    tags, sampling = select(index_path)
    print(f"      selected {len(tags)} records")

    print("[3/5] materializing + scrubbing …")
    blobs = BlobStore(args.out / "blobs")
    records, scrub_stats, counters = materialize_pass(
        session_ids,
        args.projects,
        secondary,
        index_path,
        tags,
        indexed["tools_by_transcript"],
        indexed["core_tools"],
        indexed["mcp_tools_by_server"],
        indexed["environment_binaries"],
        blobs,
        scrubber,
    )
    print(
        f"      {counters['materialized']} materialized, "
        f"{counters['dropped_pasted']} dropped as pasted third-party content"
    )

    print("[4/5] measuring contamination …")
    contamination, flagged = part.measure_contamination(records)
    audit = part.build_audit(session_ids)

    print("[5/5] writing …")
    counts = write_dataset(args.out, records, flagged)
    (args.out / "partition_audit.json").write_text(
        json.dumps(audit.to_dict(), indent=2), encoding="utf-8"
    )
    (args.out / "contamination.json").write_text(
        json.dumps(contamination, indent=2), encoding="utf-8"
    )
    (args.out / "tool_catalog.json").write_text(
        json.dumps(indexed["catalog"], indent=2), encoding="utf-8"
    )
    (args.out / "scrub_report.json").write_text(
        json.dumps(
            {
                "rules_fired": dict(sorted(scrub_stats.counts.items())),
                "records_dropped_pasted_third_party": counters["dropped_pasted"],
                "note": (
                    "Rule counts are replacements made, not distinct secrets. "
                    "A regex cannot remove arbitrary proper nouns; records built "
                    "on pasted third-party prose are dropped rather than cleaned."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest = {
        "build": {
            "snapshot": str(args.snapshot),
            "projects_root": str(args.projects),
            "schema_version": 1,
        },
        "extraction": indexed["stats"],
        "sampling": sampling,
        "partition_counts": counts,
        "summary": summarize(records),
        "blobs": {"objects": len(blobs.written), "bytes": blobs.bytes_written},
        "honesty": [
            "No ground truth for task success: no transcript records whether the "
            "human's goal was met. No verifier here claims otherwise.",
            "The reference action is what one strong harness did, not what was "
            "optimal. Divergence is divergence, never accuracy.",
            "Extended thinking is encrypted corpus-wide, so reasoning-quality "
            "grading is not supported and no LLM judge ships.",
            "Tool schemas are induced from usage; the harness version field is "
            "constant across the corpus and cannot date them.",
            "Friction signals upstream of this dataset are regex proxies.",
            "Cost figures in the source analysis are API-equivalent, not spend.",
            "Session duration is unusable past the median.",
        ],
    }
    (args.out / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    shutil.rmtree(work, ignore_errors=True)
    # A dataset that breaks its own invariants is worse than none: it scores a
    # harness against corrupted state and reports a number anyway.
    audit_mod.assert_clean(args.out)
    verify_mod.assert_no_leaks(args.out, args.username)
    print(json.dumps(manifest["summary"], indent=2)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
