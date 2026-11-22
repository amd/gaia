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

Idle-TTL + LRU bounds with claim-the-lock eviction. It reserves a slot while
an agent builds so the cap is a real bound, and tears agents down through
``close()``.
"""

from __future__ import annotations

import threading
import time
from queue import Empty, SimpleQueue
from typing import Any, Dict, List, Optional, Set, Tuple

from gaia.logger import get_logger

logger = get_logger(__name__)

#: Human label used in the cap-exhausted error below.
AGENT_LABEL = "GAIA"

#: Inference backends, spelled as ``/query``'s ``provider`` field spells them.
PROVIDER_LOCAL = "lemonade"
PROVIDER_CLAUDE = "claude"

#: Distinguishes "id was in _evicted_ids" from "id mapped to None" — dict.pop's
#: default can't do that since the dict's values are always None already.
_SENTINEL = object()

#: Held while an agent is built. A build fills the process-wide tool registry
#: and then snapshots it, so two at once can leave one agent running the other's
#: tools — e.g. the start-up warm-up racing the first session's build.
AGENT_BUILD_LOCK = threading.Lock()


def build_session_agent(**config_kwargs: Any):
    """Construct a live ``GaiaAgent`` for a new session.

    Imported lazily so this module stays dependency-light until a session is
    actually created. Tests monkeypatch this attribute to inject a fake agent.
    """
    from gaiapy.agent import GaiaAgent, GaiaAgentConfig

    with AGENT_BUILD_LOCK:
        return GaiaAgent(config=GaiaAgentConfig(silent_mode=True, **config_kwargs))


def _permission_state():
    """Build the per-session permission state.

    Imported lazily, like :func:`build_session_agent`, so this module stays
    dependency-light until a session actually exists.

    ``/sessions/{id}/bypass`` is full access, the same as over stdio: gated
    tools run unasked and the shell runs any command. The caller-auth token
    decides who may turn it on.
    """
    from gaiapy.stdio import PermissionState

    return PermissionState()


class SessionCapacityError(RuntimeError):
    """Every session slot is busy and none is idle enough to evict.

    A temporary, actionable condition — the HTTP layer maps it to 503 so a
    client can distinguish "try again shortly" from a bug-shaped 500.
    """


def _skill_reload_reason(exc: Exception) -> str:
    # Do not put an exception's text on the chat wire. It can contain local
    # paths or arbitrary output from loading the skill.
    from gaia.skills.errors import (
        SkillNotFoundError,
        SkillPermissionError,
        SkillUnavailableError,
    )

    if isinstance(exc, SkillNotFoundError):
        return "its files could not be found"
    if isinstance(exc, SkillUnavailableError):
        return "the current model does not offer the tools it needs"
    if isinstance(exc, (SkillPermissionError, PermissionError)):
        return "the current settings do not permit it to load"
    return "it failed to load with the current settings"


def pop_skill_reload_warning(agent: Any) -> Optional[Dict[str, str]]:
    """Take one notice for the wire, once a turn can actually send it."""
    warnings = getattr(agent, "_skill_reload_warnings", None)
    if warnings is None:
        return None
    try:
        message = warnings.get_nowait()
    except Empty:
        return None
    return {"type": "status", "level": "warning", "message": message}


def _carry_session_state(old: Any, new: Any, session_id: str) -> None:
    """Give a rebuilt agent what its chat set on the old one.

    A retained session keeps these until the caller changes them, so a
    rebuild that dropped them would quietly put an offline chat back online,
    or a private one back into memory: the conversation, loaded skills, the
    internet switch, the memory mode, the sources it has seen, granted,
    read-only and withheld paths, which documents are attached, the working
    folder, and which skills were auto-loaded or unloaded, so a rebuild never
    auto-loads one the user dropped.
    """
    new.conversation_history = list(getattr(old, "conversation_history", None) or [])
    # What the chat has seen, so a skill's fact check still knows its sources.
    new._seen_sources = list(getattr(old, "_seen_sources", None) or [])
    if hasattr(old, "internet_off"):
        new.internet_off = old.internet_off
    if getattr(old, "_incognito", False) and hasattr(new, "_incognito"):
        new._incognito = True
        new._incognito_reason = getattr(old, "_incognito_reason", None)
    old_validator = getattr(old, "path_validator", None)
    new_validator = getattr(new, "path_validator", None)
    if old_validator is not None and new_validator is not None:
        for path in sorted(old_validator.allowed_paths - new_validator.allowed_paths):
            new_validator.add_allowed_path(str(path))
        for path in sorted(
            old_validator.read_only_paths - new_validator.read_only_paths
        ):
            new_validator.add_read_only_path(str(path))
        # Last, so a document the user took off stays refused over any grant.
        for path in sorted(old_validator.withheld_paths):
            new_validator.withhold_path(
                str(path), document=old_validator.is_withheld_document(path)
            )
    # Which documents are attached, so one removed next turn loses its grant.
    for records in ("_attached_documents", "_withheld_documents"):
        if hasattr(old, records):
            setattr(new, records, set(getattr(old, records)))
    old_dir = getattr(getattr(old, "config", None), "working_directory", None)
    if old_dir is not None and getattr(new, "config", None) is not None:
        new.config.working_directory = old_dir
    failures = []
    for name in list(getattr(old, "loaded_skills", None) or {}):
        try:
            new.load_skill(name)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning(
                "session %s: skill %r was not loaded again after its agent was "
                "rebuilt for changed settings (%s); the user can load it again",
                session_id,
                name,
                exc,
            )
            failures.append(
                f"Skill '{name}' was not restored after settings changed: "
                f"{_skill_reload_reason(exc)}. Reload it if needed."
            )
    # After the loads above, so they cannot overwrite what the session recorded.
    for records in ("_autoloaded_skills", "_unloaded_skills"):
        if getattr(old, records, None) is not None:
            setattr(new, records, set(getattr(old, records)))
    if getattr(old, "_autoload_granted", None) is not None:
        new._autoload_granted = {
            name: list(programs) for name, programs in old._autoload_granted.items()
        }
    # Carry pending notices through another rebuild, including when the request
    # that rebuilt the agent was cancelled before its stream could start. The
    # HTTP worker can release the session while its stream is still draining;
    # keeping the same queue makes taking a notice atomic across both turns.
    warnings = getattr(old, "_skill_reload_warnings", None)
    if warnings is not None or failures:
        if warnings is None:
            warnings = SimpleQueue()
        for message in failures:
            warnings.put(message)
        new._skill_reload_warnings = warnings


class _AgentSession:
    """A retained agent instance plus its per-session state.

    One turn runs at a time per session: ``run_lock`` serialises ``/query`` so a
    second turn cannot corrupt the cached agent's conversation state.
    """

    def __init__(
        self,
        session_id: str,
        agent: Any,
        model_id: Any = None,
        provider: str = PROVIDER_LOCAL,
    ) -> None:
        self.session_id = session_id
        self.agent = agent
        #: The ``model_id`` this session's agent was BUILT with (``None`` = the
        #: agent's own default). Only construction reads a model, so a later turn
        #: asking for a different one cannot be honoured — the server compares
        #: against this and refuses rather than running the old model silently.
        self.model_id = model_id
        #: The backend the agent is on now — ``PROVIDER_LOCAL`` or
        #: ``PROVIDER_CLAUDE`` — so a turn naming another one is a switch.
        self.provider = provider
        self.run_lock = threading.Lock()
        #: The cancel flag of the turn holding ``run_lock``, so a new turn can
        #: tell a stopped turn that is still winding down from a live one.
        self.active_cancel: Optional[threading.Event] = None
        #: True exactly once, on the session built to replace one this
        #: registry involuntarily evicted (LRU cap or idle timeout) — never
        #: for a session_id used for the first time. The caller (server.py)
        #: surfaces this as a loud warning instead of silently handing back
        #: a skill-less agent under the same session_id: that silence is the
        #: exact failure mode this module exists to prevent (see module
        #: docstring), and an LRU-cap eviction can happen to a conversation
        #: that is still very much in use, just crowded out by others.
        self.reclaimed_after_eviction = False
        #: True once, for the turn that finds this session newly built —
        #: eviction-reclaim, a sidecar restart that has lost all memory of
        #: this id, or true first use (deliberately included, not just the
        #: two recognisable-as-a-loss cases: the caller drops stale
        #: pre-this-turn state either way, and on first use there IS none,
        #: so the drop is a harmless no-op there). Unlike
        #: ``reclaimed_after_eviction`` (eviction only), the caller surfaces
        #: this on every fresh build so state the client assumed outlived
        #: the old agent, such as paths approved at a prompt, is dropped on
        #: a restart too.
        self.freshly_built = True
        #: Permission state that outlives any single turn: whether bypass is on,
        #: and which calls the user has granted "always". A fresh
        #: ``SSEOutputHandler`` is built per turn, so without this both are lost
        #: at every turn boundary — which re-prompts for a call the user already
        #: approved, the same defect as never having asked.
        #:
        #: Reuses the stdio transport's ``PermissionState`` rather than a second
        #: copy: the two transports must not disagree about what "always" means
        #: or when bypass takes effect.
        self.permissions = _permission_state()

    def switch_model(self, target: str) -> str:
        """Move this session's retained agent onto *target*, keeping the chat.

        Delegates to ``gaiapy.stdio.switch_model`` — the same in-place client
        swap the stdio transport's ``/model`` performs, and the reason a switch
        keeps ``conversation_history`` and ``loaded_skills`` instead of
        destroying them the way a rebuilt agent would. Two implementations of
        that would be two things to get wrong.

        Must be called with ``run_lock`` held: swapping the client under a turn
        that is mid-inference would leave that turn talking to two backends.

        Returns the friendly display name. Raises ``RuntimeError`` with an
        actionable message on failure, having left the agent untouched.
        """
        from gaiapy.stdio import is_claude_model
        from gaiapy.stdio import switch_model as _switch

        display = _switch(self.agent, target)
        self.model_id = target
        self.provider = PROVIDER_CLAUDE if is_claude_model(target) else PROVIDER_LOCAL
        return display

    def follow_settings(self, **config_kwargs: Any) -> bool:
        """Rebuild the agent when settings it was built from changed since.

        Its model server and MCP tools are fixed as it is built, so a server
        or connector changed in Settings needs a new agent. Keeps this
        session's model and what :func:`_carry_session_state` lists; the old
        agent's pending memory work finishes and its MCP servers stop before
        it closes. Must be called with ``run_lock`` held. Returns True when it
        rebuilt.
        """
        changed = getattr(self.agent, "built_settings_changed", None)
        if not callable(changed) or changed() is not True:
            return False
        kwargs = {
            k: v
            for k, v in config_kwargs.items()
            if k not in ("model_id", "claude_model", "use_claude")
        }
        if self.provider == PROVIDER_CLAUDE:
            kwargs["use_claude"] = True
            if self.model_id:
                kwargs["claude_model"] = self.model_id
        elif self.model_id:
            kwargs["model_id"] = self.model_id
        old = self.agent
        new = build_session_agent(**kwargs)
        _carry_session_state(old, new, self.session_id)
        self.agent = new
        close_agent(old)
        return True

    def is_running(self) -> bool:
        return self.run_lock.locked()


#: Idle-only, generous by design: a session_id roots an agent for the life of a
#: conversation, not one call — the reaper bounds that, it never times out a
#: conversation still in use.
_DEFAULT_IDLE_TTL_SECONDS = 4 * 60 * 60  # 4 hours

#: Each retained GaiaAgent holds RAG/index handles and loaded-skill state —
#: generous, but not unbounded, for a single-tenant sidecar.
_DEFAULT_MAX_SESSIONS = 100


def close_agent(agent: Any) -> None:
    """Release one agent's handles — RAG/index, scratchpad DB, HTTP session.

    Waits, briefly and boundedly, for the agent's background memory extraction
    first: for a one-shot agent this is the only chance the last turn's facts
    get to reach disk. Callers are off the turn's critical path by then — the
    sidecar closes only after ``signal_done``.

    ``close()`` is the flagship's teardown; ``close_db()`` is the email agent's,
    accepted so a caller can hand either here. An agent exposing neither is a
    LOUD error, not a quiet skip: this helper reached that state once already by
    probing only ``close_db``, which ``GaiaAgent`` does not define, and every
    eviction leaked the whole agent while looking like it had cleaned up.
    """
    # Background memory extraction still holds this agent's store and embedder.
    from gaia.agents.base.memory import drain_memory_extraction

    drain_memory_extraction(agent)

    close = getattr(agent, "close", None)
    if not callable(close):
        close = getattr(agent, "close_db", None)
    if not callable(close):
        logger.error(
            "%s exposes neither close() nor close_db(), so its RAG index, "
            "scratchpad DB and HTTP session leak until the process exits. "
            "Give the agent class a close() method.",
            type(agent).__name__,
        )
        return
    try:
        close()
    except Exception as exc:  # pragma: no cover - teardown must not raise
        logger.warning("%s teardown failed: %s", type(agent).__name__, exc)


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
        #: session_ids whose agent is being BUILT right now. Construction runs
        #: outside the lock, so without reserving the slot here two creators for
        #: two new ids both pass the capacity check at len == max-1 and both
        #: install, putting the registry at max+1. Counted against the cap and
        #: dropped by whichever creator added it, on every exit path.
        self._pending: Set[str] = set()
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
        session, _ = self._get_or_create(session_id, False, config_kwargs)
        return session

    def checkout(
        self, session_id: str, **config_kwargs: Any
    ) -> Tuple[_AgentSession, bool]:
        """``get_or_create`` plus a try at the session's ``run_lock``.

        Returns ``(session, took_turn)``. The lock is tried under the registry
        lock, which eviction also holds, so nothing can evict the session
        between finding it and taking its turn — a window that turned a live
        conversation into a spurious "already running a turn".
        """
        return self._get_or_create(session_id, True, config_kwargs)

    def _get_or_create(
        self, session_id: str, take_turn: bool, config_kwargs: Dict[str, Any]
    ) -> Tuple[_AgentSession, bool]:
        self.reap()
        evicted: Optional[_AgentSession] = None
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                self._last_used[session_id] = time.monotonic()
                return existing, take_turn and existing.run_lock.acquire(blocking=False)
            # Racers for the SAME id share one reservation (a set), so the pair
            # costs one slot — which is what they will end up occupying.
            if len(self._sessions) + len(self._pending) >= self._max_sessions:
                evicted = self._claim_lru_locked()
                if evicted is None:
                    raise SessionCapacityError(
                        f"cannot start a new {AGENT_LABEL} session: "
                        f"{self._max_sessions} sessions are already active and "
                        "none are idle enough to evict. Close an idle "
                        "terminal/window, or wait for one to finish its current "
                        "turn, and retry."
                    )
            self._pending.add(session_id)
        if evicted is not None:
            close_agent(evicted.agent)
        # Build outside the lock — construction is slow and must not block other
        # sessions. A racing creator for the SAME id is resolved below by
        # discarding the loser's agent.
        try:
            agent = build_session_agent(**config_kwargs)
        except BaseException:
            with self._lock:
                self._pending.discard(session_id)
            raise
        # The agent to tear down if this creator lost the race. Closed AFTER
        # the critical section: close_agent drains background extraction, which
        # is bounded in seconds, and no other session should queue behind a
        # throwaway agent's teardown. One lock block still decides the winner.
        lost_the_race = None
        with self._lock:
            self._pending.discard(session_id)
            existing = self._sessions.get(session_id)
            if existing is not None:
                lost_the_race = agent
                session = existing
                self._last_used[session_id] = time.monotonic()
            else:
                # Consume the eviction tombstone HERE, on the branch that
                # installs the session — popped any earlier, a racing creator
                # for the same id could win the install with the flag already
                # consumed, and the "your loaded skills were reset" warning
                # would reach no one.
                reclaimed = (
                    self._evicted_ids.pop(session_id, _SENTINEL) is not _SENTINEL
                )
                session = _AgentSession(
                    session_id,
                    agent,
                    # Either spelling, because the two backends name their model
                    # in different kwargs: local builds pass ``model_id``,
                    # Claude passes ``claude_model``. Recording only the first
                    # left a Claude session reporting no model at all, so the
                    # caller's very next turn saw a "change" and tried to switch
                    # a session that had just been built with exactly what it
                    # asked for.
                    model_id=config_kwargs.get("model_id")
                    or config_kwargs.get("claude_model"),
                    provider=(
                        PROVIDER_CLAUDE
                        if config_kwargs.get("use_claude")
                        else PROVIDER_LOCAL
                    ),
                )
                session.reclaimed_after_eviction = reclaimed
                self._sessions[session_id] = session
                self._last_used[session_id] = time.monotonic()
            took_turn = take_turn and session.run_lock.acquire(blocking=False)
        if lost_the_race is not None:
            close_agent(lost_the_race)
        return session, took_turn

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
            close_agent(session.agent)
        return [s.session_id for s in evicted]

    def delete(self, session_id: str) -> bool:
        """Delete an idle session; return False if absent or running a turn.

        Callers must not interpret False as proof the session does not exist.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or not session.run_lock.acquire(blocking=False):
                return False
            self._sessions.pop(session_id)
            self._last_used.pop(session_id, None)
        close_agent(session.agent)
        return True

    def clear(self) -> None:
        """Drop every session. Test/shutdown helper."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._last_used.clear()
        for session in sessions:
            close_agent(session.agent)


#: One process == the whole registry. The sidecar never runs uvicorn with
#: ``workers>1``; that assumption breaks if it ever does.
registry = _SessionRegistry()


__all__ = [
    "build_session_agent",
    "close_agent",
    "registry",
    "_AgentSession",
    "_SessionRegistry",
]
