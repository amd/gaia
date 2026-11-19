# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Replay a scenario through the real memory pipeline and score its probes.

Each session is a fresh agent on one shared memory database, as each new chat
is in the Agent UI: ``init_memory``, then the first message's upkeep
(reconcile, consolidate, prune), then every turn through
``MemoryMixin.process_query`` — tool calls, the turn's tool record, extraction,
lessons. Only the agent loop is stood in for: the turns' answers and tool calls
come from the scenario, because what is measured is memory, not the agent.

The ``pr`` suite also stands in for the model (``ScriptedChat``) and the
embedder (``HashEmbedder``), so it is deterministic and offline. The ``full``
suite uses the real ones.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import tempfile
import zlib
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import content_stems
from gaia.eval.memory.clock import simulated_time
from gaia.eval.memory.scenario import Probe, Scenario, ScenarioError, Turn

#: Memory's own loggers; a strict replay fails on any warning they log.
_MEMORY_LOGGERS = ("gaia.agents.base.memory", "gaia.agents.base.memory_store")

#: How long one turn's extraction may take before the replay gives up on it.
EXTRACTION_WAIT_S = 300.0

#: Gap between turns of one session on the simulated clock.
TURN_GAP = timedelta(minutes=3)


# ---------------------------------------------------------------------------
# Stand-ins for the model and the embedder (``pr`` suite)
# ---------------------------------------------------------------------------


class HashEmbedder:
    """A deterministic embedder: word stems hashed into a fixed-size vector.

    Similar wording gives similar vectors, which is all the ``pr`` suite needs
    to exercise indexing and recall; it is no measure of semantic recall.
    """

    MODEL = "memory-bench-hash"
    DIM = 384

    def embed(
        self, texts: Sequence[str], model: Optional[str] = None
    ) -> List[List[float]]:
        del model  # one vector space, whatever model the caller names
        out = []
        for text in texts:
            vec = np.zeros(self.DIM, dtype=np.float32)
            for stem in content_stems(text):
                h = zlib.crc32(stem.encode("utf-8"))
                vec[h % self.DIM] += 1.0 if (h >> 16) & 1 else -1.0
            if not vec.any():
                vec[0] = 1.0
            out.append(vec.tolist())
        return out


@dataclass
class _Reply:
    text: str
    finish_reason: str = "stop"


class ScriptedChat:
    """Answers memory's side requests from the scenario, the way a model would.

    Extraction gets the turn's ``extract`` ops, consolidation the session's
    ``consolidation`` entry, reconciliation "neutral". Any other request means
    the pipeline asked the model something the bench does not model, and fails.
    """

    effective_model = "memory-bench-scripted"

    def __init__(self, scenario: Scenario, resolve: Callable[[str], str]):
        self._resolve = resolve
        self._extract: Dict[str, List[Dict[str, Any]]] = {}
        for session in scenario.sessions:
            for turn in session.turns:
                key = " ".join(turn.user.split())
                if key in self._extract:
                    raise ScenarioError(
                        f"{scenario.id}: two turns say {turn.user[:60]!r}; user "
                        "turns must be unique so extraction can be matched"
                    )
                self._extract[key] = turn.extract
        self._consolidation = {
            " ".join(s.turns[0].user[:200].split()): scenario.consolidation.get(s.id)
            for s in scenario.sessions
        }

    def send_messages(self, messages, system_prompt=None, cancel=None, **kwargs):
        # A scripted answer is instant, so there is nothing to cancel or tune.
        del system_prompt, cancel, kwargs
        prompt = messages[-1]["content"]
        if prompt.startswith("You are a memory manager"):
            return _Reply(json.dumps(self._extraction_ops(prompt)))
        if prompt.startswith("Summarize this conversation session"):
            return _Reply(json.dumps(self._consolidation_for(prompt)))
        if prompt.startswith("Given these two memory items"):
            return _Reply(json.dumps({"relationship": "neutral", "action": "none"}))
        raise RuntimeError(
            "memory bench: the pipeline made a model request the pr suite does "
            f"not script: {prompt[:120]!r}"
        )

    def _extraction_ops(self, prompt: str) -> List[Dict[str, Any]]:
        user = prompt.split("\nUser: ", 1)[1].split("\nAssistant: ", 1)[0]
        key = " ".join(user.split())
        if key not in self._extract:
            raise RuntimeError(f"memory bench: no scripted turn for {user[:80]!r}")
        ops = []
        for op in self._extract[key]:
            op = dict(op)
            for ref in ("update_of", "delete_of"):
                if ref in op:
                    op["knowledge_id"] = self._resolve(op.pop(ref))
            op.setdefault("grounded", "user")
            ops.append(op)
        return ops

    def _consolidation_for(self, prompt: str) -> Dict[str, Any]:
        flat = " ".join(prompt.split())
        for first_user, entry in self._consolidation.items():
            if f"user: {first_user[:120]}" in flat:
                return entry or {"summary": "", "knowledge": []}
        raise RuntimeError(
            "memory bench: consolidation asked about a session the scenario "
            f"does not contain: {flat[:200]!r}"
        )


# ---------------------------------------------------------------------------
# The agent stand-in
# ---------------------------------------------------------------------------


class _AgentStandIn:
    """Plays the agent loop's part: run the turn's tool calls, then the hook."""

    _bench_turn: Turn
    _bench_calls: List[Any]
    _memory_store: Any  # set by MemoryMixin.init_memory

    def _after_process_query(self, user_input: str, assistant_response: str) -> None:
        # MemoryMixin, ahead of this class in BenchHost's MRO, provides the hook.
        raise NotImplementedError

    @staticmethod
    def _bench_tool(name: str) -> Callable[..., Dict[str, Any]]:
        from gaia.agents.base.tools import _TOOL_REGISTRY

        return _TOOL_REGISTRY[name]["function"]

    def process_query(self, user_input, **_kwargs):
        turn = self._bench_turn
        self._bench_calls = list(turn.tools)
        for call in turn.tools:
            self._execute_tool(call.name, call.args)
        for item in turn.remember:
            result = self._bench_tool("remember")(
                fact=item.fact,
                category=item.category,
                due_at=item.due_at,
                sensitive="true" if item.sensitive else "false",
            )
            # "skipped" is a refusal the scenario may be testing (a credential).
            if result.get("status") == "error":
                raise RuntimeError(f"memory bench: remember failed: {result}")
        for item in turn.update:
            rows = active_rows(self._memory_store, item.match)
            if len(rows) != 1:
                raise RuntimeError(
                    f"memory bench: update {item.match!r} matches {len(rows)} "
                    "memories; it must match exactly one"
                )
            result = self._bench_tool("update_memory")(
                knowledge_id=rows[0]["id"], content=item.content, due_at=item.due_at
            )
            if result.get("status") != "updated":
                raise RuntimeError(f"memory bench: update_memory failed: {result}")
        for pattern in turn.forget:
            for row in active_rows(self._memory_store, pattern):
                result = self._bench_tool("forget")(row["id"])
                if result.get("status") != "removed":
                    raise RuntimeError(f"memory bench: forget failed: {result}")
        self._after_process_query(user_input, turn.assistant)
        return {"result": turn.assistant}

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        call = self._bench_calls.pop(0)
        if (call.name, call.args) != (tool_name, tool_args):
            raise RuntimeError(
                f"memory bench: expected tool call {call.name}({call.args}), "
                f"got {tool_name}({tool_args})"
            )
        return call.result


class BenchHost(MemoryMixin, _AgentStandIn):
    """One chat's agent, on the bench's database."""

    def __init__(
        self,
        db_path: Path,
        chat: Any,
        embedder: Any,
        workspace: str,
        session_id: Optional[str] = None,
    ):
        self.chat = chat
        self._bench_embedder = embedder
        self._faiss_index = None
        self._faiss_id_map = []
        self.init_memory(
            db_path=db_path,
            context="global",
            embedding_model=getattr(embedder, "MODEL", None),
        )
        if self._memory_store is None:
            raise RuntimeError(
                "memory bench: memory did not start "
                f"({self._memory_unavailable_reason}): {self._memory_unavailable_detail}"
            )
        if session_id is not None:
            self._memory_session_id = session_id
        # Lessons are keyed by the workspace; a scenario names its own.
        self._lesson_context_cache = f"workspace:{workspace}"
        self.register_memory_tools()

    def _get_embedder(self) -> Any:
        if self._bench_embedder is not None:
            return self._bench_embedder
        return super()._get_embedder()

    def init_system_context(self, force: bool = False) -> Dict[str, int]:
        # This PC's hardware is not part of a persona's history.
        return {"stored": 0}

    def start_skill_synthesis(self, *, force: bool = False):
        # Procedures are benchmarked separately (gaia eval tasks).
        return None

    def close(self) -> None:
        if not self.wait_for_memory_extraction(timeout=EXTRACTION_WAIT_S):
            raise RuntimeError(
                f"memory bench: extraction still running after {EXTRACTION_WAIT_S}s"
            )
        self._memory_store.close()


def active_rows(
    store_or_path: Any, pattern: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Memories not superseded, optionally only those *pattern* matches."""
    sql = (
        "SELECT id, category, content, source, domain FROM knowledge "
        "WHERE superseded_by IS NULL"
    )
    if isinstance(store_or_path, (str, Path)):
        conn = sqlite3.connect(str(store_or_path))
        try:
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
    else:
        with store_or_path._locked():
            rows = store_or_path._conn.execute(sql).fetchall()
    out = [
        {
            "id": r[0],
            "category": r[1],
            "content": r[2],
            "source": r[3],
            "session_summary": r[3] == "consolidation"
            and (r[4] or "").startswith("session:"),
        }
        for r in rows
    ]
    if pattern is not None:
        rx = re.compile(pattern, re.IGNORECASE)
        out = [r for r in out if rx.search(r["content"])]
    return out


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    scenario: str
    probe: str
    kind: str
    check: str  # seen | unseen | mentions | avoids | stored | not_stored
    target: str
    passed: bool
    detail: str = ""

    @property
    def key(self) -> str:
        return f"{self.scenario}/{self.probe}/{self.check}:{self.target}"


def _contains(text: str, expectation: Any) -> bool:
    """Whether *text* has any of the options as whole words (case-insensitive),
    so "Dan" does not match "guidance"."""
    options = expectation if isinstance(expectation, list) else [expectation]
    return any(
        re.search(rf"(?<!\w){re.escape(str(o))}(?!\w)", text, re.IGNORECASE)
        for o in options
    )


def _label(expectation: Any) -> str:
    return (
        " | ".join(expectation) if isinstance(expectation, list) else str(expectation)
    )


def agent_view(host: BenchHost, query: str) -> str:
    """What the agent is shown for *query* in a new chat: memory's system-prompt
    section plus the per-turn context prepended to the message."""
    stable = host.get_memory_system_prompt()
    host._original_user_input = query
    host._memory_turn_query = query
    dynamic = host.get_memory_dynamic_context()
    return f"{stable}\n\n{dynamic}"


@dataclass
class ReplayResult:
    scenario: str
    checks: List[CheckResult] = field(default_factory=list)
    views: Dict[str, str] = field(default_factory=dict)
    answers: Dict[str, str] = field(default_factory=dict)
    memories_at_end: int = 0
    warnings: List[str] = field(default_factory=list)


class _WarningLog(logging.Handler):
    """Collects WARNING and above from memory's loggers during a replay."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage()[:300])


class Replay:
    """Replays one scenario on a fresh database and scores its probes."""

    def __init__(
        self,
        scenario: Scenario,
        workdir: Path,
        *,
        chat_factory: Callable[[Callable[[str], str]], Any],
        embedder: Any,
        answer: Optional[Callable[[str, str], str]] = None,
        strict: bool = False,
    ):
        self.scenario = scenario
        self.db = workdir / f"{scenario.id}.db"
        self.workdir = workdir
        self._embedder = embedder
        self._answer = answer
        # Production catches and logs a failed extraction or consolidation, so
        # a scripted-model slip would otherwise store nothing and let every
        # "must not be stored" check pass without testing anything.
        self._strict = strict
        self.chat = chat_factory(self._resolve)

    def _resolve(self, pattern: str) -> str:
        """A scripted op's ``update_of``/``delete_of``: the matching memory's id.

        Session summaries are not candidates; they repeat the facts they cover.
        """
        rows = [r for r in active_rows(self.db, pattern) if not r["session_summary"]]
        if not rows:
            raise RuntimeError(
                f"memory bench ({self.scenario.id}): scripted op refers to "
                f"{pattern!r}, but no current memory matches it"
            )
        if len(rows) > 1:
            raise RuntimeError(
                f"memory bench ({self.scenario.id}): scripted op refers to "
                f"{pattern!r}, which matches {len(rows)} current memories; "
                "it must match exactly one"
            )
        return rows[0]["id"]

    def _host(self, session_id: Optional[str] = None) -> BenchHost:
        return BenchHost(
            self.db, self.chat, self._embedder, self.scenario.workspace, session_id
        )

    def run(self) -> ReplayResult:
        warnings = _WarningLog()
        loggers = [logging.getLogger(name) for name in _MEMORY_LOGGERS]
        for lg in loggers:
            lg.addHandler(warnings)
        try:
            result = self._run()
        finally:
            for lg in loggers:
                lg.removeHandler(warnings)
        result.warnings = warnings.messages
        if self._strict and warnings.messages:
            raise RuntimeError(
                f"memory bench ({self.scenario.id}): memory logged "
                f"{len(warnings.messages)} warning(s) during a strict replay, so "
                "some step failed and the checks cannot be trusted: "
                + " | ".join(warnings.messages[:5])
            )
        return result

    def _run(self) -> ReplayResult:
        result = ReplayResult(scenario=self.scenario.id)
        with simulated_time() as clock:
            clock.start(self.scenario.sessions[0].date)
            for session in self.scenario.sessions:
                clock.set(session.date)
                host = self._host(f"{self.scenario.id}:{session.id}")
                try:
                    for turn in session.turns:
                        host._bench_turn = turn
                        host.process_query(turn.user)
                        if not host.wait_for_memory_extraction(
                            timeout=EXTRACTION_WAIT_S
                        ):
                            raise RuntimeError(
                                f"memory bench ({self.scenario.id}/{session.id}): "
                                "extraction did not finish"
                            )
                        clock.advance(TURN_GAP)
                finally:
                    host.close()
                self._probe(session.id, result)
            clock.set(self.scenario.end)
            self._upkeep()
            self._probe("end", result)
        result.memories_at_end = len(active_rows(self.db))
        return result

    def _upkeep(self) -> None:
        """What opening the next chat does, run until consolidation is caught up."""
        host = self._host()
        try:
            host._run_memory_post_init()
            while host.consolidate_old_sessions(max_sessions=20)["windows"]:
                pass
        finally:
            host.close()

    def _probe(self, at: str, result: ReplayResult) -> None:
        probes = [p for p in self.scenario.probes if p.at == at]
        if not probes:
            return
        # A probe is the next chat, opened on a copy: its upkeep and the
        # reminders it raises must not change the timeline after this point.
        copy = self.workdir / f"{self.scenario.id}.{at}.probe.db"
        _snapshot(self.db, copy)
        host = BenchHost(copy, self.chat, self._embedder, self.scenario.workspace)
        try:
            host._run_memory_post_init()
            # A session summary is dated history ("in March, Maya lived in
            # Calgary"); whether it reaches the agent is what view checks test.
            rows = [r for r in active_rows(copy) if not r["session_summary"]]
            for probe in probes:
                self._score(probe, host, rows, result)
        finally:
            host.close()

    def _score(
        self,
        probe: Probe,
        host: BenchHost,
        rows: List[Dict[str, Any]],
        result: ReplayResult,
    ) -> None:
        def add(check: str, target: str, passed: bool, detail: str = "") -> None:
            result.checks.append(
                CheckResult(
                    scenario=self.scenario.id,
                    probe=probe.id,
                    kind=probe.kind,
                    check=check,
                    target=target,
                    passed=passed,
                    detail=detail,
                )
            )

        if probe.query:
            view = agent_view(host, probe.query)
            result.views[probe.id] = view
            for exp in probe.seen:
                add("seen", _label(exp), _contains(view, exp))
            for text in probe.unseen:
                add("unseen", text, not _contains(view, text))
            if self._answer is not None and (probe.mentions or probe.avoids):
                answer = self._answer(view, probe.query)
                result.answers[probe.id] = answer
                for exp in probe.mentions:
                    add("mentions", _label(exp), _contains(answer, exp), answer[:300])
                for text in probe.avoids:
                    add("avoids", text, not _contains(answer, text), answer[:300])
        for pattern in probe.stored:
            rx = re.compile(pattern, re.IGNORECASE)
            hits = [r["content"] for r in rows if rx.search(r["content"])]
            add("stored", pattern, bool(hits))
        for pattern in probe.not_stored:
            rx = re.compile(pattern, re.IGNORECASE)
            hits = [r["content"] for r in rows if rx.search(r["content"])]
            add("not_stored", pattern, not hits, "; ".join(hits)[:300])


def _snapshot(src: Path, dst: Path) -> None:
    """Copy a live WAL database consistently."""
    if dst.exists():
        dst.unlink()
    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dst))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def new_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="gaia-memory-bench-"))
