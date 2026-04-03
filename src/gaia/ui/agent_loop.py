# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""AgentLoop — background autonomous agent execution for GAIA Agent UI.

The AgentLoop runs as an asyncio background task inside the FastAPI server.
It wakes on event triggers (user message, scheduled wake, idle tick) and
runs the ChatAgent against pending approved goals in GoalStore.

State machine:
    PAUSED     → (agent_mode != "manual" AND initialized) → IDLE
    IDLE       → (trigger fires + approved goals exist)   → RUNNING
    RUNNING    → (set_loop_state("idle"))                 → IDLE
    RUNNING    → (set_loop_state("scheduled", N))         → SCHEDULED
    RUNNING    → (set_loop_state("paused"))               → PAUSED
    SCHEDULED  → (timer fires)                            → RUNNING
    PAUSED     → (agent_mode changed to non-manual)       → IDLE

Design notes:
- Event-driven: triggers are enqueued; no unconditional polling.
- Startup gate: no ticks until ~/.gaia/chat/initialized exists.
- Tunnel gate: no background ticks when ngrok tunnel is active.
- Rate limiting: hourly call count + per-tick step budget.
- All private sessions are unconditionally skipped.
"""

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Configuration constants ─────────────────────────────────────────────────

# Seconds between idle ticks (when no goals are pending this is a no-op check)
_TICK_INTERVAL = int(os.environ.get("GAIA_AGENT_TICK_INTERVAL", "60"))

# Maximum autonomous LLM calls per hour
_HOURLY_LIMIT = int(os.environ.get("GAIA_AGENT_HOURLY_LIMIT", "30"))

# Maximum goals created by the agent per day
_DAILY_GOAL_LIMIT = int(os.environ.get("GAIA_AGENT_DAILY_GOAL_LIMIT", "10"))

# Lightweight model for observation/classification (cheap ticks)
_OBSERVE_MODEL = os.environ.get("GAIA_AUTO_OBSERVE_MODEL", "Qwen3-4B-GGUF")

# Timeout (seconds) for a single autonomous tick execution
_TICK_TIMEOUT = int(os.environ.get("GAIA_AGENT_TICK_TIMEOUT", "300"))


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class AgentTrigger:
    """A reason to wake the agent loop."""

    source: str  # "user_message_followup" | "idle_tick" | "scheduled_wake" | "continuation"
    session_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopDirective:
    """Outcome of a single agent tick; tells the loop what to do next."""

    directive: str  # "idle" | "continue" | "scheduled" | "waiting_input" | "paused"
    wake_in_seconds: int = 0
    reason: str = ""


# ── AgentLoop ────────────────────────────────────────────────────────────────


class AgentLoop:
    """Background autonomous agent loop.

    Instantiated once per server process.  ``start()`` is called during
    FastAPI lifespan startup; ``stop()`` during shutdown.
    """

    def __init__(self):
        self._trigger_queue: asyncio.Queue = asyncio.Queue()
        self._loop_task: Optional[asyncio.Task] = None
        self._tick_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._db = None
        self._app_state = None
        # Rate limiting
        self._hour_start: float = time.time()
        self._calls_this_hour: int = 0
        # Track in-flight tick (prevents overlapping runs)
        self._running_lock: Optional[asyncio.Lock] = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self, db, app_state) -> None:
        """Launch the loop.  Safe to call multiple times (idempotent)."""
        if self._loop_task is not None and not self._loop_task.done():
            return  # already running

        self._db = db
        self._app_state = app_state
        self._stop_event = asyncio.Event()
        self._running_lock = asyncio.Lock()
        self._loop_task = asyncio.create_task(self._loop(), name="agent-loop")
        self._tick_task = asyncio.create_task(self._tick_loop(), name="agent-tick")
        logger.info("AgentLoop started (tick_interval=%ds)", _TICK_INTERVAL)

    async def stop(self) -> None:
        """Gracefully stop the loop."""
        if self._stop_event:
            self._stop_event.set()
        for task in (self._loop_task, self._tick_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        logger.info("AgentLoop stopped")

    # ── Public API (called from routers/chat) ────────────────────────────────

    def notify_user_message(self, session_id: str) -> None:
        """Enqueue a followup trigger after the user sends a message.

        The loop will check for approved goals and continue working if any
        exist — without the user needing to send another message.
        """
        try:
            self._trigger_queue.put_nowait(
                AgentTrigger("user_message_followup", session_id)
            )
        except asyncio.QueueFull:
            pass  # queue is unbounded; this should never happen

    # ── Internal: trigger consumer ────────────────────────────────────────────

    async def _loop(self) -> None:
        """Main coroutine: drain trigger queue and process each trigger."""
        while not self._stop_event.is_set():
            try:
                trigger = await asyncio.wait_for(
                    self._trigger_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # Prevent overlapping tick runs
            if self._running_lock.locked():
                logger.debug(
                    "AgentLoop: skipping trigger %s — previous tick still running",
                    trigger.source,
                )
                continue

            try:
                await self._process_trigger(trigger)
            except Exception as exc:
                logger.error("AgentLoop tick error: %s", exc, exc_info=True)

    async def _tick_loop(self) -> None:
        """Periodic idle-tick generator."""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(_TICK_INTERVAL)
            except asyncio.CancelledError:
                break
            if not self._stop_event.is_set():
                try:
                    self._trigger_queue.put_nowait(AgentTrigger("idle_tick", None))
                except Exception:
                    pass

    # ── Internal: trigger processing ─────────────────────────────────────────

    async def _process_trigger(self, trigger: AgentTrigger) -> None:
        """Process one trigger: run the agent, then follow the directive."""
        async with self._running_lock:
            while True:
                directive = await self._run_step(trigger)

                if directive.directive == "continue":
                    trigger = AgentTrigger("continuation", trigger.session_id)
                    # immediately re-run — no sleep
                    continue

                if directive.directive == "scheduled":
                    delay = max(30, directive.wake_in_seconds)
                    asyncio.create_task(self._schedule_wake(delay))

                # idle | scheduled | waiting_input | paused
                break

    async def _schedule_wake(self, delay_seconds: int) -> None:
        """Fire a scheduled_wake trigger after a delay."""
        await asyncio.sleep(delay_seconds)
        if not self._stop_event.is_set():
            self._trigger_queue.put_nowait(AgentTrigger("scheduled_wake", None))

    # ── Internal: single tick ─────────────────────────────────────────────────

    async def _run_step(self, trigger: AgentTrigger) -> LoopDirective:
        """Check gates, find session, run one agent tick.

        Returns a LoopDirective describing what the loop should do next.
        """
        # ── Startup gate ─────────────────────────────────────────────────
        initialized = (Path.home() / ".gaia" / "chat" / "initialized").exists()
        if not initialized:
            logger.debug("AgentLoop: startup gate — not yet initialized")
            return LoopDirective("idle")

        # ── Tunnel gate ──────────────────────────────────────────────────
        tunnel = getattr(self._app_state, "tunnel", None)
        allow_tunnel = os.environ.get("GAIA_AUTONOMOUS_ALLOW_TUNNEL", "").lower() in (
            "1",
            "true",
            "yes",
        )
        if tunnel and tunnel.active and not allow_tunnel:
            logger.debug("AgentLoop: tunnel gate — suspended while tunnel is active")
            return LoopDirective("idle")

        # ── Agent mode gate ───────────────────────────────────────────────
        agent_mode = self._db.get_setting("agent_mode") or "autonomous"
        if agent_mode == "manual":
            return LoopDirective("paused", reason="agent_mode=manual")

        # ── Hourly rate limit ────────────────────────────────────────────
        now = time.time()
        if now - self._hour_start > 3600:
            self._hour_start = now
            self._calls_this_hour = 0
        if self._calls_this_hour >= _HOURLY_LIMIT:
            logger.warning(
                "AgentLoop: hourly rate limit reached (%d calls)", _HOURLY_LIMIT
            )
            return LoopDirective("idle", reason="hourly rate limit")
        self._calls_this_hour += 1

        # ── Session selection ────────────────────────────────────────────
        session_id = trigger.session_id or await self._get_active_session()
        if session_id is None:
            return LoopDirective("idle")

        # Double-check: never run on a private session
        session = self._db.get_session(session_id)
        if session is None or session.get("private"):
            return LoopDirective("idle")

        # ── Goal check ───────────────────────────────────────────────────
        goals = self._get_actionable_goals()
        if not goals:
            if agent_mode == "goal_driven":
                return LoopDirective("idle")
            # autonomous mode: TODO — run observation cycle (Phase 2)
            # For now, also idle when there are no goals
            return LoopDirective("idle")

        # ── Execute tick ─────────────────────────────────────────────────
        directive = await self._execute_tick(session_id, session, goals)
        return directive

    async def _get_active_session(self) -> Optional[str]:
        """Return the most recently updated non-private session, or None."""
        try:
            sessions = self._db.list_sessions(limit=20)
            # Skip private sessions unconditionally (spec §11)
            for s in sessions:
                if not s.get("private"):
                    return s["id"]
        except Exception as exc:
            logger.debug("AgentLoop: session lookup failed: %s", exc)
        return None

    def _get_actionable_goals(self) -> List[Any]:
        """Return approved, queued goals from GoalStore."""
        try:
            from gaia.agents.base.goal_store import GoalStore

            store = GoalStore()
            return store.get_actionable_goals(limit=5)
        except Exception as exc:
            logger.debug("AgentLoop: GoalStore lookup failed: %s", exc)
            return []

    # ── Tick execution ────────────────────────────────────────────────────────

    async def _execute_tick(
        self,
        session_id: str,
        session: dict,
        goals: List[Any],
    ) -> LoopDirective:
        """Run one autonomous agent tick in a thread pool executor.

        Builds a synthetic 'tick prompt' from pending goals, runs the
        ChatAgent in background mode, and returns the directive signalled
        by ``set_loop_state()``.
        """
        from gaia.ui.sse_handler import SSEOutputHandler

        sse_handler = SSEOutputHandler(background_mode=True)

        goal_lines = "\n".join(
            f"  [{g.priority.upper()}] {g.title}"
            + (f": {g.description}" if g.description else "")
            for g in goals[:3]
        )
        tick_prompt = (
            f"[Autonomous tick — {len(goals)} pending goal(s)]\n\n"
            f"Approved goals to work on:\n{goal_lines}\n\n"
            "Work on the highest-priority goal. Use your available tools to make "
            "progress. When you are done or blocked, call set_loop_state() to "
            "signal your status so the loop knows what to do next."
        )

        # Build a minimal ChatRequest-like object
        from gaia.ui.models import ChatRequest

        request = ChatRequest(
            session_id=session_id,
            message=tick_prompt,
            stream=False,
        )

        result_holder: Dict[str, Any] = {"error": None}
        db = self._db

        def _run_agent() -> None:
            try:
                import gaia.ui._chat_helpers as _helpers

                # Reuse cached agent if available; build fresh if not.
                # We bypass the full _stream_chat_response pipeline to avoid
                # yielding SSE events (nothing is consuming them in background mode).
                # The SSEOutputHandler still captures events for the activity log.
                _helpers._stream_chat_response  # ensure module is loaded

                # Run the heavier sync work inline (we're in a thread)
                from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig

                model_id = session.get("model")
                custom_model = db.get_setting("custom_model")
                if custom_model:
                    model_id = custom_model

                cached_agent = _helpers._get_cached_agent(session_id, model_id)
                if cached_agent is not None:
                    agent = cached_agent
                    agent.console = sse_handler
                    agent._register_tools()
                else:
                    rag_paths, lib_paths = _helpers._resolve_rag_paths(
                        db, session.get("document_ids", [])
                    )
                    allowed = _helpers._compute_allowed_paths(rag_paths + lib_paths)
                    config = ChatAgentConfig(
                        model_id=model_id,
                        max_steps=int(os.environ.get("GAIA_AGENT_MAX_STEPS", "20")),
                        streaming=False,
                        silent_mode=True,
                        debug=False,
                        allowed_paths=allowed,
                        ui_session_id=session_id,
                    )
                    agent = ChatAgent(config)
                    _helpers._register_agent_memory_ops(agent)
                    agent.console = sse_handler

                # Inject conversation history (capped for autonomous ticks)
                messages = db.get_messages(session_id, limit=10)
                history_pairs = _helpers._build_history_pairs(messages)
                agent.conversation_history = []
                for u, a in history_pairs[-3:]:  # 3-pair rolling window for ticks
                    agent.conversation_history.append({"role": "user", "content": u[:1000]})
                    agent.conversation_history.append(
                        {"role": "assistant", "content": a[:1000]}
                    )

                # Set incognito flag (respect private/memory settings)
                if hasattr(agent, "_incognito"):
                    memory_off = db.get_setting("memory_enabled", "true") == "false"
                    agent._incognito = memory_off

                agent.process_query(tick_prompt)

            except Exception as exc:
                logger.error("AgentLoop tick execution failed: %s", exc, exc_info=True)
                result_holder["error"] = str(exc)
            finally:
                sse_handler.signal_done()

        # Run synchronous agent in a thread pool so we don't block the event loop
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _run_agent),
                timeout=_TICK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("AgentLoop: tick timed out after %ds", _TICK_TIMEOUT)
            sse_handler.cancelled.set()

        # Save the autonomous tick as a message in the session DB
        # (stored as role="autonomous" — hidden from the UI by default)
        try:
            self._db.add_message(
                session_id=session_id,
                role="autonomous",
                content=tick_prompt,
            )
        except Exception:
            pass  # Non-fatal — activity logging is best-effort

        # Read directive from SSE handler (set by set_loop_state tool)
        if sse_handler.loop_state_directive:
            d = sse_handler.loop_state_directive
            return LoopDirective(
                directive=d.get("directive", "idle"),
                wake_in_seconds=d.get("wake_in_seconds", 0),
                reason=d.get("reason", ""),
            )

        # If agent didn't call set_loop_state, check if more work remains
        remaining = self._get_actionable_goals()
        return LoopDirective("continue" if remaining else "idle")


# ── Module-level singleton ────────────────────────────────────────────────────

#: Singleton instance shared between server lifespan, chat router, and user-input endpoint.
agent_loop: AgentLoop = AgentLoop()
