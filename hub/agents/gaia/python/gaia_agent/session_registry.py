# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Session-scoped agent retention for the flagship sidecar (#2829, schema 2.12).

Schema 2.12 is the contract version that let ``/query`` resolve a conversation's
agent *by ``session_id``* instead of building a throwaway one per call. Building
per call is not merely wasteful — it silently discards everything the agent
learned during the turn. A skill activated by ``load_skill`` lives on
``Agent.loaded_skills``, so a throwaway agent means the very next turn reports
no skills loaded while the model, having just said "it's loaded", keeps
promising otherwise.

Mirrors ``gaia_agent_email.agent_routes._SessionRegistry`` deliberately: same
idle-TTL + LRU bounds, same claim-the-lock eviction. The duplication is a known
cost — extracting one shared registry into core touches the email agent's
tested paths and belongs in its own change.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Human label used in the cap-exhausted error below.
AGENT_LABEL = "GAIA"

#: Distinguishes "id was in _evicted_ids" from "id mapped to None" — dict.pop's
#: default can't do that since the dict's values are always None already.
_SENTINEL = object()


def build_session_agent(**config_kwargs: Any):
    """Construct a live ``GaiaAgent`` for a new session.

    Imported lazily so this module stays dependency-light until a session is
    actually created. Tests monkeypatch this attribute to inject a fake agent.
    """
    from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

    return GaiaAgent(config=GaiaAgentConfig(silent_mode=True, **config_kwargs))


class SessionCapacityError(RuntimeError):
    """Every session slot is busy and none is idle enough to evict.

    A temporary, actionable condition — the HTTP layer maps it to 503 so a
    client can distinguish "try again shortly" from a bug-shaped 500.
    """


class _AgentSession:
    """A retained agent instance plus its per-session state.

    One turn runs at a time per session: ``run_lock`` serialises ``/query`` so a
    second turn cannot corrupt the cached agent's conversation state.
    """

    def __init__(self, session_id: str, agent: Any) -> None:
        self.session_id = session_id
        self.agent = agent
        self.run_lock = threading.Lock()
        #: True exactly once, on the session built to replace one this
        #: registry involuntarily evicted (LRU cap or idle timeout) — never
        #: for a session_id used for the first time. The caller (server.py)
        #: surfaces this as a loud warning instead of silently handing back
        #: a skill-less agent under the same session_id: that silence is the
        #: exact failure mode this module exists to prevent (see module
        #: docstring), and an LRU-cap eviction can happen to a conversation
        #: that is still very much in use, just crowded out by others.
        self.reclaimed_after_eviction = False

    def is_running(self) -> bool:
        return self.run_lock.locked()


#: Idle-only, generous by design: a session_id roots an agent for the life of a
#: conversation, not one call — the reaper bounds that, it never times out a
#: conversation still in use.
_DEFAULT_IDLE_TTL_SECONDS = 4 * 60 * 60  # 4 hours

#: Each retained GaiaAgent holds RAG/index handles and loaded-skill state —
#: generous, but not unbounded, for a single-tenant sidecar.
_DEFAULT_MAX_SESSIONS = 100


def _close_agent(agent: Any) -> None:
    """Best-effort teardown of a retained agent's handles on eviction."""
    close = getattr(agent, "close_db", None)
    if callable(close):
        try:
            close()
        except Exception as exc:  # pragma: no cover - teardown must not raise
            logger.warning("agent session close_db failed: %s", exc)


class _SessionRegistry:
    """Process-local map of ``session_id`` → :class:`_AgentSession`.

    In-process and single-tenant by design (the sidecar hosts one user's agent).
    Agents are built lazily on first use and torn down on eviction, idle
    timeout, or the LRU cap — never while a session's ``run_lock`` is held.
    """

    def __init__(
        self,
        *,
        idle_ttl_seconds: float = _DEFAULT_IDLE_TTL_SECONDS,
        max_sessions: int = _DEFAULT_MAX_SESSIONS,
    ) -> None:
        self._sessions: Dict[str, _AgentSession] = {}
        self._last_used: Dict[str, float] = {}
        #: session_ids involuntarily evicted whose NEXT get_or_create must
        #: come back flagged `reclaimed_after_eviction` instead of silently
        #: looking identical to a first-time session_id. Consumed (popped) the
        #: first time that id is reused, so a later, genuinely-fresh reuse of
        #: the same id is not flagged twice.
        #:
        #: Bounded, but generously — NOT to max_sessions. A tombstone must
        #: outlive the eviction that created it long enough for the evicted
        #: id's own next get_or_create to see it, and at max_sessions=1 that
        #: next call is itself the very eviction that would immediately
        #: retire the previous tombstone to make room, erasing it before it
        #: was ever consumed.
        self._evicted_ids: Dict[str, None] = {}
        self._max_evicted_ids = max(4 * max_sessions, 32)
        self._lock = threading.Lock()
        self._idle_ttl_seconds = idle_ttl_seconds
        self._max_sessions = max_sessions

    def get(self, session_id: str) -> Optional[_AgentSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def _note_evicted_locked(self, session_id: str) -> None:
        """Record an involuntary eviction. Caller holds ``self._lock``."""
        self._evicted_ids.pop(session_id, None)  # re-insert at the end (MRU)
        self._evicted_ids[session_id] = None
        while len(self._evicted_ids) > self._max_evicted_ids:
            self._evicted_ids.pop(next(iter(self._evicted_ids)))

    def get_or_create(self, session_id: str, **config_kwargs: Any) -> _AgentSession:
        self.reap()
        evicted: Optional[_AgentSession] = None
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                self._last_used[session_id] = time.monotonic()
                return existing
            if len(self._sessions) >= self._max_sessions:
                evicted = self._claim_lru_locked()
                if evicted is None:
                    raise SessionCapacityError(
                        f"cannot start a new {AGENT_LABEL} session: "
                        f"{self._max_sessions} sessions are already active and "
                        "none are idle enough to evict. Close an idle "
                        "terminal/window, or wait for one to finish its current "
                        "turn, and retry."
                    )
        if evicted is not None:
            _close_agent(evicted.agent)
        # Build outside the lock — construction is slow and must not block other
        # sessions. A racing creator for the SAME id is resolved below by
        # discarding the loser's agent.
        agent = build_session_agent(**config_kwargs)
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                _close_agent(agent)
                self._last_used[session_id] = time.monotonic()
                return existing
            # Consume the eviction tombstone HERE, on the branch that installs
            # the session — popped any earlier, a racing creator for the same
            # id could win the install with the flag already consumed, and the
            # "your loaded skills were reset" warning would reach no one.
            reclaimed = self._evicted_ids.pop(session_id, _SENTINEL) is not _SENTINEL
            session = _AgentSession(session_id, agent)
            session.reclaimed_after_eviction = reclaimed
            self._sessions[session_id] = session
            self._last_used[session_id] = time.monotonic()
            return session

    def _claim_lru_locked(self) -> Optional[_AgentSession]:
        """Pop the least-recently-used session whose ``run_lock`` this call
        successfully CLAIMS (acquires and never releases).

        Caller holds ``self._lock``. Checking ``.locked()`` and popping
        separately is a TOCTOU race: a session can look unlocked at the instant
        this scans it and have its lock taken a moment later by the turn about
        to run on it. Acquiring here closes that window — either this call wins
        (the session was genuinely idle and is now permanently claimed), or it
        was already taken by a real turn and is skipped. Returns ``None`` when
        every session is mid-turn: nothing is safe to evict, so the caller must
        refuse rather than silently exceed the cap.
        """
        by_age = sorted(self._sessions, key=lambda sid: self._last_used.get(sid, 0.0))
        for sid in by_age:
            if self._sessions[sid].run_lock.acquire(blocking=False):
                session = self._sessions.pop(sid)
                self._last_used.pop(sid, None)
                self._note_evicted_locked(sid)
                return session
        return None

    def reap(self) -> List[str]:
        """Evict idle-expired sessions, CLAIMING each ``run_lock`` rather than
        merely checking it — same TOCTOU as ``_claim_lru_locked``.

        Teardown runs OUTSIDE the lock: it can block on I/O and must not stall
        every other session's ``get_or_create``.
        """
        now = time.monotonic()
        evicted: List[_AgentSession] = []
        with self._lock:
            expired_ids = [
                sid
                for sid, last in self._last_used.items()
                if now - last > self._idle_ttl_seconds
            ]
            for sid in expired_ids:
                session = self._sessions[sid]
                if not session.run_lock.acquire(blocking=False):
                    continue  # a real turn is starting/running — never evict
                self._sessions.pop(sid)
                self._last_used.pop(sid, None)
                self._note_evicted_locked(sid)
                evicted.append(session)
        for session in evicted:
            _close_agent(session.agent)
        return [s.session_id for s in evicted]

    def delete(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
            self._last_used.pop(session_id, None)
        if session is None:
            return False
        _close_agent(session.agent)
        return True

    def clear(self) -> None:
        """Drop every session. Test/shutdown helper."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._last_used.clear()
        for session in sessions:
            _close_agent(session.agent)


#: One process == the whole registry. The sidecar never runs uvicorn with
#: ``workers>1``; that assumption breaks if it ever does.
registry = _SessionRegistry()


__all__ = [
    "build_session_agent",
    "registry",
    "_AgentSession",
    "_SessionRegistry",
]
