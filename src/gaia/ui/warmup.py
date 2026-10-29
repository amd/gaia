# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Prime Lemonade with a new chat's exact prompt prefix before anyone asks.

A new chat's first turn sends ~20K tokens of system prompt and tool schemas.
Read cold, that is most of a half-minute wait for "hello". Every new chat
renders the same prefix, so building the flagship the way a new chat does and
running its :meth:`~gaia.agents.base.agent.Agent.warm_up` leaves that prefix in
Lemonade's cache and the first real question only reads its own words.

One warm-up runs at a time, and never starts while a streamed chat turn is
running: both would share Lemonade's single slot (a non-streaming turn is not
tracked, so it can overlap). A turn that starts mid-warm-up waits for it
(:func:`wait_for_warm_up`) instead of reading the prefix a second time, and
says what it is waiting on; Stop ends that wait.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

#: A re-request inside this window after a successful warm-up is a no-op.
DEBOUNCE_SECONDS = 30.0

#: The flagship: what a new chat runs when no agent is picked.
NEW_CHAT_AGENT = "gaia"

IDLE, WARMING, READY, FAILED, SKIPPED = "idle", "warming", "ready", "failed", "skipped"

#: What a turn waiting on the warm-up is told, by the step the warm-up is on.
_PHASE_MESSAGES = {
    "building": "Finishing start-up: preparing the agent",
    "loading": "Finishing start-up: waiting for the model to load "
    "(another app may be using it)",
    "priming": "Finishing start-up: reading GAIA's instructions into the model",
}
_DEFAULT_PHASE_MESSAGE = "Finishing start-up"


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
        self._phase: Optional[str] = None
        self._target: Dict[str, Optional[str]] = {}
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
            self._phase = None
            self._target = {}
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

    def run_now(
        self,
        reason: str,
        job: Callable[[], Dict[str, Any]],
        cancel: Optional[threading.Event] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run *job* in this thread as the warm-up, once any running one ends
        and no chat turn is running (a load now could swap that turn's model).

        Turns wait for it like any warm-up. Returns the job's result, or None
        when *cancel* was set first or the job returned None. Raises whatever
        the job raises.
        """
        while True:
            if cancel is not None and cancel.is_set():
                return None
            with self._lock:
                warming = self._state == WARMING
                if not warming and not self._busy():
                    self._state = WARMING
                    self._started = time.monotonic()
                    self._finished = None
                    self._result = {}
                    self._error = None
                    self._phase = None
                    self._target = {}
                    self._done.clear()
                    break
            if warming:
                self._done.wait(0.2)
            else:
                time.sleep(0.2)
        try:
            result = job()
        except Exception as exc:
            self._finish(FAILED, {}, str(exc))
            raise
        if result is None:
            self._finish(SKIPPED, {}, None)
            return None
        logger.info(
            "Warm-up (%s) done in %ss: %s of %s prompt tokens were already cached",
            reason,
            result.get("seconds"),
            result.get("cached_tokens", "?"),
            result.get("prompt_tokens", "?"),
        )
        # Only the numbers: the snapshot is served to the UI and the log.
        self._finish(
            READY,
            {k: result.get(k) for k in ("seconds", "prompt_tokens", "cached_tokens")},
            None,
        )
        return result

    def waiting_on_a_turn(self) -> bool:
        """True while no warm-up runs but a chat turn holds the model."""
        with self._lock:
            return self._state != WARMING and self._busy()

    def set_target(self, model: str, device: Optional[str]) -> None:
        """Name what the running warm-up loads, so the UI can say so."""
        with self._lock:
            self._target = {"model": model, "device": device}

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

    def set_phase(self, phase: str) -> None:
        """Record the step the running warm-up is on (see ``_PHASE_MESSAGES``)."""
        with self._lock:
            self._phase = phase

    def phase(self) -> Optional[str]:
        with self._lock:
            return self._phase

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
                "model": self._target.get("model"),
                "device": self._target.get("device"),
            }


def preload_skip_reason() -> Optional[str]:
    """Why this PC loads the chat model on the first message, or None.

    On a small machine a startup preload races first-run setup and whatever
    else is open for the same memory; loading on demand leaves the free-memory
    check to the one load that has to happen.
    """
    from gaia.llm.model_fit import (
        EAGER_LOAD_MIN_RAM_GB,
        preloads_chat_model,
        ram_total_gb,
    )

    ram = ram_total_gb()
    if preloads_chat_model(ram):
        return None
    return (
        f"this PC has {ram:.0f} GB of memory (under "
        f"{EAGER_LOAD_MIN_RAM_GB:.0f} GB), so the model loads with the first message"
    )


def _device_for(client, model: str) -> Optional[str]:
    """``npu``, ``gpu`` or ``cpu``: where Lemonade runs *model*; None if unknown.

    A llama.cpp model runs where its backend does: the user's choice, else the
    one Lemonade picks. Any other recipe is left unnamed rather than guessed.
    """
    from gaia.config import GaiaConfigError
    from gaia.llm.lemonade_client import (
        LemonadeClientError,
        _model_ids_match,
        preferred_llamacpp_backend,
        runs_on_npu,
    )
    from gaia.llm.model_server_info import default_llamacpp_backend

    if runs_on_npu(model):
        return "npu"
    try:
        recipe = next(
            (
                m.get("recipe")
                for m in client.list_models(show_all=True).get("data", [])
                if _model_ids_match(m.get("id"), model)
            ),
            None,
        )
        if recipe != "llamacpp":
            return None
        backend = preferred_llamacpp_backend() or default_llamacpp_backend(
            client.get_system_info(timeout=5)
        )
    except (LemonadeClientError, GaiaConfigError) as e:
        logger.warning("Warm-up can't say where %s runs: %s", model, e)
        return None
    if not backend:
        return None
    return "cpu" if backend == "cpu" else "gpu"


class NewChatAgentUnavailable(RuntimeError):
    """The flagship a new chat runs can't be built in this process."""


def _new_chat_model(db, registry):
    """``(model_id, device_ctx, custom_model)`` a new chat's first turn uses."""
    from gaia.ui import _chat_helpers as chat
    from gaia.ui.database import resolved_default_model

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
    return model_id, device_ctx, custom_model


def _check_new_chat_agent(registry) -> None:
    """Raise :class:`NewChatAgentUnavailable` unless the flagship builds here."""
    from gaia.ui import _chat_helpers as chat

    if registry is None or registry.get(NEW_CHAT_AGENT) is None:
        raise NewChatAgentUnavailable(f"the {NEW_CHAT_AGENT} agent is not registered")
    if chat._should_relay_to_sidecar(NEW_CHAT_AGENT, registry):
        # Chat relays to the sidecar, which cannot be built here.
        raise NewChatAgentUnavailable(
            f"the {NEW_CHAT_AGENT} agent runs in its daemon sidecar"
        )


@contextmanager
def new_chat_agent(db, registry, model_id, device_ctx, custom_model):
    """Build the flagship exactly as a new chat's first turn does.

    Yields ``(agent, effective_model)``; the model is the agent's own default
    when *model_id* is None.

    Raises:
        NewChatAgentUnavailable: the agent has no model.
    """
    from gaia.ui import _chat_helpers as chat

    _set_phase("building")
    session = {"device": "gpu"}
    with chat.writing_tool_registry():
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
        # An omitted model_id lets the agent pick its own default.
        effective = chat._effective_model(agent, model_id)
        if not effective:
            raise NewChatAgentUnavailable(f"the {NEW_CHAT_AGENT} agent has no model")
        yield agent, effective
    finally:
        close = getattr(agent, "close", None)
        if callable(close):
            close()


def _warm_new_chat_prefix(
    db, note_target: Optional[Callable[[str, Optional[str]], None]] = None
) -> Optional[Dict[str, Any]]:
    """Build the flagship exactly as a new chat's first turn does, and warm it.

    Returns None when there is nothing to warm locally: a cloud model, a model
    not downloaded yet (loading it would start a download nobody asked for),
    no flagship registered, a flagship that runs in its daemon sidecar, or a
    PC small enough to load on demand.
    """
    from gaia.llm.lemonade_client import LemonadeClient
    from gaia.ui import _chat_helpers as chat

    low_memory = preload_skip_reason()
    if low_memory:
        logger.info("Warm-up skipped: %s", low_memory)
        return None
    if chat._eval_provider_kwargs():
        logger.info("Warm-up skipped: chat turns run on a cloud provider")
        return None
    registry = chat.get_agent_registry()
    try:
        _check_new_chat_agent(registry)
    except NewChatAgentUnavailable as exc:
        # A sidecar warms its own prompt when the daemon starts it.
        logger.info("Warm-up skipped: %s", exc)
        return None
    client = LemonadeClient(verbose=False)

    def skip_reason(model: Optional[str]) -> Optional[str]:
        # The catalog read comes first: it is what teaches the cloud check
        # about providers beyond the built-in ones.
        downloaded = client.get_model_info(model)["downloaded"]
        if chat._ui_cloud_provider(model):
            return "runs in the cloud"
        if not downloaded:
            return "is not downloaded yet"
        return None

    model_id, device_ctx, custom_model = _new_chat_model(db, registry)
    reason = skip_reason(model_id) if model_id else None
    if reason:
        logger.info("Warm-up skipped: %s %s", model_id, reason)
        return None

    try:
        with new_chat_agent(db, registry, model_id, device_ctx, custom_model) as (
            agent,
            effective,
        ):
            if effective != model_id:
                reason = skip_reason(effective)
                if reason:
                    logger.info("Warm-up skipped: %s %s", effective, reason)
                    return None
            if note_target is not None:
                note_target(effective, _device_for(client, effective))
            # The turn's own pre-flight: load at the ctx a turn expects, or the
            # first turn reloads the model and the primed cache goes with it.
            _set_phase("loading")
            chat._maybe_load_expected_model(effective)
            _set_phase("priming")
            return agent.warm_up()
    except NewChatAgentUnavailable as exc:
        logger.info("Warm-up skipped: %s", exc)
        return None


_warmer: Optional[PrefixWarmer] = None


def configure(db) -> PrefixWarmer:
    """Create the process's warmer for *db*. Called once at server start."""
    global _warmer
    from gaia.ui import _chat_helpers as chat

    _warmer = PrefixWarmer(lambda: _warm_new_chat_prefix(db, _warmer.set_target))
    _warmer.set_busy_check(lambda: bool(chat._active_sse_handlers))
    return _warmer


def get_warmer() -> Optional[PrefixWarmer]:
    """The process's warmer, or None before :func:`configure`."""
    return _warmer


def request_warm_up(reason: str) -> bool:
    """Ask for a warm-up; a no-op before :func:`configure`, while one runs, or
    before first-run setup has finished (it would load a model setup didn't pick).
    """
    from gaia.ui.setup_runner import is_initialized

    if _warmer is None:
        return False
    if not is_initialized():
        logger.info(
            "Warm-up (%s) not started: first-run setup has not finished", reason
        )
        return False
    return _warmer.request(reason)


def _set_phase(phase: str) -> None:
    if _warmer is not None:
        _warmer.set_phase(phase)


def wait_for_warm_up(timeout: float = 300.0, *, handler=None) -> None:
    """Let a running warm-up finish so the turn reuses what it cached.

    With an SSE *handler*, the turn shows what the warm-up is doing, and a
    Stop (``handler.cancelled``) ends the wait at once; the caller checks
    ``cancelled`` afterwards.

    Raises:
        TimeoutError: the warm-up is still running after *timeout* seconds;
            the turn does not race it for the model.
    """
    warmer = _warmer
    if warmer is None or warmer.wait(0):
        return
    logger.info("Chat turn waiting for the running warm-up to finish")
    deadline = time.monotonic() + timeout
    shown = None
    while not warmer.wait(0.2):
        if handler is not None:
            if handler.cancelled.is_set():
                logger.info("Chat turn stopped while waiting for the warm-up")
                return
            message = _PHASE_MESSAGES.get(warmer.phase(), _DEFAULT_PHASE_MESSAGE)
            if message != shown:
                handler._emit(
                    {
                        "type": "status",
                        "status": "working",
                        "phase": "warm_up",
                        "message": message,
                    }
                )
                shown = message
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"The start-up warm-up was still {warmer.phase() or 'starting'} "
                f"after {timeout:g} s, so this turn did not start. Check that "
                "Lemonade is running (`gaia init --check`), then send the "
                "message again."
            )


def warm_up_status() -> Dict[str, Any]:
    """The warm-up state the UI shows, or idle before :func:`configure`."""
    if _warmer is None:
        return {"state": IDLE, "elapsed_seconds": None}
    return _warmer.snapshot()
