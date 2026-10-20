# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Memory-bench scenarios: one person's chats over weeks, and what must hold after.

A scenario is a YAML file in ``scenarios/``. Each session is one chat on one
date; each turn is what the user said, what the assistant answered, the tool
calls it made, and any explicit ``remember`` / ``forget`` calls. Probes say what
the agent must (and must not) be shown, or have stored, at a point in time.

``extract`` and ``consolidation`` are only for the deterministic ``pr`` suite:
they stand in for the model, and are written as a *plausible* model would
answer, including the mistakes a real one makes (labelling document content as
the user's, repeating an old value). The ``full`` suite ignores them and asks
the real model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import yaml

SCENARIO_DIR = Path(__file__).parent / "scenarios"

#: What a probe checks, so the report can say which kind of memory regressed.
PROBE_KINDS = frozenset(
    {
        "current_fact",  # a fact the user stated and still holds
        "changed_fact",  # the newest value of a fact that changed
        "preference",  # how the user wants things done
        "relationship",  # people in the user's life and facts about them
        "reminder",  # dated commitments
        "lesson",  # a mistake not to repeat
        "forgotten",  # something the user asked GAIA to forget
        "injection",  # document, web or tool content posing as the user
        "attribution",  # someone else's fact, or a hypothetical, not the user's
        "credential",  # secrets that must never be stored
        "scale",  # an older fact among many
    }
)

#: A ``seen`` / ``mentions`` entry: one string, or any-of alternatives.
Expectation = Union[str, List[str]]


class ScenarioError(ValueError):
    """A scenario file is malformed; the message names the file and field."""


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: Dict[str, Any]
    result: Any


@dataclass(frozen=True)
class Remember:
    fact: str
    category: str = "fact"
    due_at: str = ""
    sensitive: bool = False


@dataclass(frozen=True)
class Update:
    """The agent's ``update_memory`` call on the memory *match* finds."""

    match: str
    content: str = ""
    due_at: str = ""


@dataclass(frozen=True)
class Turn:
    user: str
    assistant: str
    tools: List[ToolCall] = field(default_factory=list)
    remember: List[Remember] = field(default_factory=list)
    #: Patterns: the agent recalls and forgets every memory matching each one.
    forget: List[str] = field(default_factory=list)
    update: List[Update] = field(default_factory=list)
    #: ``pr`` suite only: the extraction model's proposed operations.
    extract: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Session:
    id: str
    date: datetime
    turns: List[Turn]


@dataclass(frozen=True)
class Probe:
    id: str
    at: str  # a session id, or "end" (after a final upkeep pass)
    kind: str
    query: str = ""
    seen: List[Expectation] = field(default_factory=list)
    unseen: List[str] = field(default_factory=list)
    mentions: List[Expectation] = field(default_factory=list)
    avoids: List[str] = field(default_factory=list)
    stored: List[str] = field(default_factory=list)
    not_stored: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    persona: str
    workspace: str
    sessions: List[Session]
    end: datetime
    probes: List[Probe]
    #: ``pr`` suite only: what consolidation proposes, keyed by session id.
    consolidation: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    path: Optional[Path] = None


def _require(data: Dict[str, Any], key: str, where: str) -> Any:
    if key not in data or data[key] in (None, ""):
        raise ScenarioError(f"{where}: missing required field '{key}'")
    return data[key]


def _only(data: Dict[str, Any], allowed: Sequence[str], where: str) -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ScenarioError(
            f"{where}: unknown field(s) {unknown}; allowed: {sorted(allowed)}"
        )


def _date(value: Any, where: str) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ScenarioError(f"{where}: '{value}' is not an ISO date-time") from exc


def _patterns(values: Any, where: str) -> List[str]:
    out = list(values or [])
    for pattern in out:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ScenarioError(f"{where}: bad regex {pattern!r}: {exc}") from exc
    return out


def _turn(data: Dict[str, Any], where: str) -> Turn:
    _only(
        data,
        ("user", "assistant", "tools", "remember", "forget", "update", "extract"),
        where,
    )
    tools = []
    for i, call in enumerate(data.get("tools") or []):
        _only(call, ("name", "args", "result"), f"{where}.tools[{i}]")
        tools.append(
            ToolCall(
                name=_require(call, "name", f"{where}.tools[{i}]"),
                args=dict(call.get("args") or {}),
                result=_require(call, "result", f"{where}.tools[{i}]"),
            )
        )
    remember = []
    for i, item in enumerate(data.get("remember") or []):
        _only(
            item, ("fact", "category", "due_at", "sensitive"), f"{where}.remember[{i}]"
        )
        remember.append(
            Remember(
                fact=_require(item, "fact", f"{where}.remember[{i}]"),
                category=item.get("category", "fact"),
                due_at=str(item.get("due_at") or ""),
                sensitive=bool(item.get("sensitive", False)),
            )
        )
    updates = []
    for i, item in enumerate(data.get("update") or []):
        uw = f"{where}.update[{i}]"
        _only(item, ("match", "content", "due_at"), uw)
        match = _patterns([_require(item, "match", uw)], uw)[0]
        if not (item.get("content") or item.get("due_at")):
            raise ScenarioError(f"{uw}: an update must change content or due_at")
        updates.append(
            Update(
                match=match,
                content=str(item.get("content") or ""),
                due_at=str(item.get("due_at") or ""),
            )
        )
    return Turn(
        user=_require(data, "user", where),
        assistant=_require(data, "assistant", where),
        tools=tools,
        remember=remember,
        forget=_patterns(data.get("forget"), f"{where}.forget"),
        update=updates,
        extract=list(data.get("extract") or []),
    )


def _probe(data: Dict[str, Any], where: str, session_ids: Sequence[str]) -> Probe:
    _only(
        data,
        (
            "id",
            "at",
            "kind",
            "query",
            "seen",
            "unseen",
            "mentions",
            "avoids",
            "stored",
            "not_stored",
        ),
        where,
    )
    at = str(_require(data, "at", where))
    if at != "end" and at not in session_ids:
        raise ScenarioError(f"{where}: at={at!r} is neither 'end' nor a session id")
    kind = _require(data, "kind", where)
    if kind not in PROBE_KINDS:
        raise ScenarioError(f"{where}: kind {kind!r} not in {sorted(PROBE_KINDS)}")
    probe = Probe(
        id=_require(data, "id", where),
        at=at,
        kind=kind,
        query=str(data.get("query") or ""),
        seen=list(data.get("seen") or []),
        unseen=list(data.get("unseen") or []),
        mentions=list(data.get("mentions") or []),
        avoids=list(data.get("avoids") or []),
        stored=_patterns(data.get("stored"), f"{where}.stored"),
        not_stored=_patterns(data.get("not_stored"), f"{where}.not_stored"),
    )
    if (
        probe.seen or probe.unseen or probe.mentions or probe.avoids
    ) and not probe.query:
        raise ScenarioError(f"{where}: seen/unseen/mentions/avoids need a query")
    if not any(
        (
            probe.seen,
            probe.unseen,
            probe.mentions,
            probe.avoids,
            probe.stored,
            probe.not_stored,
        )
    ):
        raise ScenarioError(f"{where}: probe checks nothing")
    return probe


def load_scenario(path: Path) -> Scenario:
    where = path.name
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{where}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ScenarioError(f"{where}: top level must be a mapping")
    _only(
        data,
        (
            "id",
            "title",
            "persona",
            "workspace",
            "sessions",
            "end",
            "probes",
            "consolidation",
        ),
        where,
    )
    sessions = []
    for i, s in enumerate(_require(data, "sessions", where)):
        sw = f"{where}.sessions[{i}]"
        _only(s, ("id", "date", "turns"), sw)
        sessions.append(
            Session(
                id=str(_require(s, "id", sw)),
                date=_date(_require(s, "date", sw), sw),
                turns=[
                    _turn(t, f"{sw}.turns[{j}]")
                    for j, t in enumerate(_require(s, "turns", sw))
                ],
            )
        )
    ids = [s.id for s in sessions]
    if len(set(ids)) != len(ids):
        raise ScenarioError(f"{where}: duplicate session ids")
    for earlier, later in zip(sessions, sessions[1:]):
        if later.date <= earlier.date:
            raise ScenarioError(
                f"{where}: session {later.id} ({later.date}) is not after "
                f"{earlier.id} ({earlier.date}); sessions must be in date order"
            )
    end = _date(_require(data, "end", where), where)
    if end <= sessions[-1].date:
        raise ScenarioError(f"{where}: end must be after the last session")
    consolidation = dict(data.get("consolidation") or {})
    for sid in consolidation:
        if sid not in ids:
            raise ScenarioError(f"{where}: consolidation names unknown session {sid!r}")
    probes = [
        _probe(p, f"{where}.probes[{i}]", ids)
        for i, p in enumerate(_require(data, "probes", where))
    ]
    pids = [p.id for p in probes]
    if len(set(pids)) != len(pids):
        raise ScenarioError(f"{where}: duplicate probe ids")
    return Scenario(
        id=str(_require(data, "id", where)),
        title=str(_require(data, "title", where)),
        persona=str(_require(data, "persona", where)),
        workspace=str(data.get("workspace") or f"/home/user/{data['id']}"),
        sessions=sessions,
        end=end,
        probes=probes,
        consolidation=consolidation,
        path=path,
    )


def load_scenarios(only: Optional[Sequence[str]] = None) -> List[Scenario]:
    """Every scenario in ``scenarios/``, or just the ids in *only*."""
    paths = sorted(SCENARIO_DIR.glob("*.yaml"))
    if not paths:
        raise ScenarioError(f"no scenarios found in {SCENARIO_DIR}")
    scenarios = [load_scenario(p) for p in paths]
    if only:
        known = {s.id for s in scenarios}
        missing = sorted(set(only) - known)
        if missing:
            raise ScenarioError(
                f"unknown scenario id(s) {missing}; known: {sorted(known)}"
            )
        scenarios = [s for s in scenarios if s.id in set(only)]
    return scenarios
