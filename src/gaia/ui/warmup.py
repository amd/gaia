# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Prime Lemonade with a new chat's exact prompt prefix before anyone asks.

A new chat's first turn sends ~20K tokens of system prompt and tool schemas.
Read cold, that is most of a half-minute wait for "hello". Every new chat
renders the same prefix, so building the flagship the way a new chat does and
running its :meth:`~gaia.agents.base.agent.Agent.warm_up` leaves that prefix in
Lemonade's cache and the first real question only reads its own words.

One warm-up runs at a time, and never while a chat turn is running: both would
share Lemonade's single slot. A turn that starts mid-warm-up waits for it
(:func:`wait_for_warm_up`) instead of reading the prefix a second time.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

#: A re-request inside this window after a successful warm-up is a no-op.
DEBOUNCE_SECONDS = 30.0

#: The flagship: what a new chat runs when no agent is picked.
NEW_CHAT_AGENT = "gaia"

IDLE, WARMING, READY, FAILED, SKIPPED = "idle", "warming", "ready", "failed", "skipped"


class PrefixWarmer:
    """Single-flight, debounced runner for one warm-up job."""

    def __init__(self, job: Callable[[], Optional[Dict[str, Any]]]):
        self._job = job
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._done.set()
        self._state = IDLE
        self._started: Optional[float] = None
        self._finished: Optional[float] = None
        self._result: Dict[str, Any] = {}
        self._error: Optional[str] = None
        self._busy: Callable[[], bool] = lambda: False

    def set_busy_check(self, busy: Callable[[], bool]) -> None:
        """Install the "is a chat turn running?" probe."""
        self._busy = busy

    def request(self, reason: str) -> bool:
        """Start a warm-up in the background. Returns whether one was started."""
        with self._lock:
            if self._state == WARMING:
                return False
            recent = (
                self._state == READY
                and self._finished is not None
                and time.monotonic() - self._finished < DEBOUNCE_SECONDS
            )
            if recent:
                return False
            if self._busy():
                logger.info("Warm-up (%s) not started: a chat turn is running", reason)
                return False
            self._state = WARMING
            self._started = time.monotonic()
            self._finished = None
            self._result = {}
            self._error = None
            self._done.clear()
        threading.Thread(
            target=self._run, args=(reason,), name="gaia-prefix-warmup", daemon=True
        ).start()
        return True

    def _run(self, reason: str) -> None:
        try:
            result = self._job()
        except Exception as exc:  # noqa: BLE001 - surfaced in status and the log
            logger.warning(
                "Warm-up (%s) failed: %s. Chat still works, but the first answer "
                "in a new chat will take longer; check that Lemonade is running.",
                reason,
                exc,
                exc_info=True,
            )
            self._finish(FAILED, {}, str(exc))
            return
        if result is None:
            self._finish(SKIPPED, {}, None)
            return
        logger.info(
            "Warm-up (%s) done in %ss: %s of %s prompt tokens were already cached",
            reason,
            result.get("seconds"),
            result.get("cached_tokens", "?"),
            result.get("prompt_tokens", "?"),
        )
        self._finish(READY, result, None)

    def _finish(self, state: str, result: Dict[str, Any], error: Optional[str]):
        with self._lock:
            self._state = state
            self._result = result
            self._error = error
            self._finished = time.monotonic()
            self._done.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until no warm-up is running. Returns False on timeout."""
        return self._done.wait(timeout)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            elapsed = None
            if self._started is not None:
                end = self._finished if self._finished is not None else time.monotonic()
                elapsed = round(end - self._started, 1)
            return {
                "state": self._state,
                "elapsed_seconds": elapsed,
                "prompt_tokens": self._result.get("prompt_tokens"),
                "cached_tokens": self._result.get("cached_tokens"),
                "error": self._error,
            }


def _warm_new_chat_prefix(db) -> Optional[Dict[str, Any]]:
    """Build the flagship exactly as a new chat's first turn does, and warm it.

    Returns None when there is nothing to warm locally: a cloud model, a model
    not downloaded yet (loading it would start a download nobody asked for),
    or no flagship registered.
    """
    from gaia.llm.lemonade_client import LemonadeClient, cloud_model_provider
    from gaia.ui import _chat_helpers as chat
    from gaia.ui.database import resolved_default_model

    registry = chat.get_agent_registry()
    if registry is None or registry.get(NEW_CHAT_AGENT) is None:
        logger.info("Warm-up skipped: the %s agent is not registered", NEW_CHAT_AGENT)
        return None
    # What ChatDatabase.create_session stores for a chat the user just opened.
    session = {
        "model": resolved_default_model(),
        "agent_type": NEW_CHAT_AGENT,
        "device": "gpu",
    }
    custom_model = db.get_setting("custom_model")
    model_id, device_ctx = chat.resolve_session_model(
        session, NEW_CHAT_AGENT, custom_model, registry
    )
    if model_id and cloud_model_provider(model_id):
        logger.info("Warm-up skipped: %s runs in the cloud", model_id)
        return None
    if (
        model_id
        and not LemonadeClient(verbose=False).get_model_info(model_id)["downloaded"]
    ):
        logger.info("Warm-up skipped: %s is not downloaded yet", model_id)
        return None

    agent = registry.create_agent(
        NEW_CHAT_AGENT,
        **chat._build_create_kwargs(
            custom_model=custom_model,
            model_id=model_id,
            streaming=True,
            device=session["device"],
            min_context_size=device_ctx,
        ),
        **chat._session_agent_kwargs(
            rag_file_paths=[],
            library_paths=[],
            allowed=chat._compute_allowed_paths([]),
            session_id="warm-up",
            dynamic_tools=db.get_setting("dynamic_tools", "false") == "true",
            memory_incognito=chat._memory_off(session, db),
        ),
    )
    try:
        # The turn's own pre-flight: load at the ctx a turn expects, or the
        # first turn reloads the model and the primed cache goes with it.
        chat._maybe_load_expected_model(chat._effective_model(agent, model_id))
        return agent.warm_up()
    finally:
        close = getattr(agent, "close", None)
        if callable(close):
            close()


_warmer: Optional[PrefixWarmer] = None


def configure(db) -> PrefixWarmer:
    """Create the process's warmer for *db*. Called once at server start."""
    global _warmer
    from gaia.ui import _chat_helpers as chat

    _warmer = PrefixWarmer(lambda: _warm_new_chat_prefix(db))
    _warmer.set_busy_check(lambda: bool(chat._active_sse_handlers))
    return _warmer


def request_warm_up(reason: str) -> bool:
    """Ask for a warm-up; a no-op before :func:`configure` or while one runs."""
    return _warmer.request(reason) if _warmer is not None else False


def wait_for_warm_up(timeout: float = 300.0) -> None:
    """Let a running warm-up finish so the turn reuses what it cached."""
    if _warmer is None or _warmer.wait(0):
        return
    logger.info("Chat turn waiting for the running warm-up to finish")
    if not _warmer.wait(timeout):
        logger.warning(
            "Warm-up still running after %ss; sending the turn anyway", timeout
        )


def warm_up_status() -> Dict[str, Any]:
    """The warm-up state the UI shows, or idle before :func:`configure`."""
    if _warmer is None:
        return {"state": IDLE, "elapsed_seconds": None}
    return _warmer.snapshot()
