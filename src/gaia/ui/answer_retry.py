# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Answer a chat's latest question again on another local model, to compare.

The retry does not run the turn's tools again — a second run could repeat
their side effects. It replays what the turn already gathered (its tool calls
and results, as text) and asks the other model for the final answer, so both
models answer from the same evidence.
"""

import json
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, Optional

from gaia.agents.base.history import select_history, text_tool_history
from gaia.agents.base.turn_metrics import count_tokens
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Room left for the retried answer, thinking included.
RETRY_MAX_TOKENS = 8192

RETRY_SYSTEM_PROMPT = (
    "You are GAIA, a helpful assistant running locally on the user's PC. "
    "Tool calls and their results from earlier in this conversation appear as "
    "text; you cannot run tools now. Using that evidence, answer the user's "
    "last request directly and completely."
)


class RetryError(Exception):
    """A retry that cannot run; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class PendingRetry:
    """A retried answer waiting for the user to keep it or the original."""

    message_id: int
    model: str
    content: str
    turn: list[dict[str, Any]]
    recorded: bool
    stats: dict[str, Any]


_pending_lock = threading.Lock()
_pending: dict[str, PendingRetry] = {}


def store_pending(session_id: str, retry: PendingRetry) -> None:
    with _pending_lock:
        _pending[session_id] = retry


def forget(session_id: str) -> None:
    """Drop a deleted session's open comparison."""
    with _pending_lock:
        _pending.pop(session_id, None)


def take_pending(session_id: str, message_id: int) -> Optional[PendingRetry]:
    """Remove and return the session's pending retry of *message_id*."""
    with _pending_lock:
        retry = _pending.get(session_id)
        if retry is None or retry.message_id != message_id:
            return None
        return _pending.pop(session_id)


def _turn_without_answer(turn: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turn up to, not including, its final answer."""
    end = len(turn)
    while (
        end > 0
        and turn[end - 1].get("role") == "assistant"
        and not turn[end - 1].get("tool_calls")
    ):
        end -= 1
    return [dict(m) for m in turn[:end]]


def split_latest_turn(
    context_messages: list[dict[str, Any]], record: dict[str, Any]
) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]], bool]:
    """Earlier turns, and the latest turn without its answer.

    *record* is the latest message (an assistant answer). The third value is
    whether that turn was recorded as model messages (else it was text only).
    """
    from gaia.agents.base.history import transcript_turns

    turns = transcript_turns(context_messages)
    if not turns:
        raise RetryError(409, "This chat has no answered question to retry yet.")
    recorded = record.get("model_messages") is not None
    latest = record["model_messages"] if recorded else turns[-1]
    question = _turn_without_answer(latest)
    if not any(m.get("role") == "user" for m in question):
        raise RetryError(
            409,
            "The latest answer has no question recorded with it, so there is "
            "nothing to ask the other model. Ask the question again instead.",
        )
    return turns[:-1], question, recorded


def build_retry_messages(
    earlier: list[list[dict[str, Any]]], question: list[dict[str, Any]], ctx: int
) -> list[dict[str, Any]]:
    """The messages to send the other model, fitted to its window."""
    rendered = text_tool_history([question])[0]
    overhead = count_tokens(
        json.dumps([RETRY_SYSTEM_PROMPT, rendered], ensure_ascii=False)
    )
    room = ctx - overhead - RETRY_MAX_TOKENS - 2048
    if room < 0:
        raise RetryError(
            413,
            f"The question and what it gathered (about {overhead:,} tokens) do "
            f"not fit this model's {ctx:,}-token window. Pick a model with a "
            "larger window.",
        )
    history = select_history(text_tool_history(earlier), min(ctx // 2, room))
    return [{"role": "system", "content": RETRY_SYSTEM_PROMPT}, *history, *rendered]


def _is_loaded(client: Any, model: str) -> bool:
    from gaia.llm.lemonade_client import LemonadeClientError

    try:
        loaded = client.health_check(timeout=5.0).get("all_models_loaded", [])
    except LemonadeClientError as exc:
        raise RetryError(
            503,
            f"Could not reach the model server at {client.base_url} to load "
            f"{model}: {exc}. Check Settings → Model.",
        ) from exc
    return any((m.get("model_name") or "").lower() == model.lower() for m in loaded)


def answer_with(model: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Load *model* the way a chat turn would, then ask it. Blocking."""
    import requests

    from gaia.daemon.broker_client import BrokerUnavailableError, model_lease
    from gaia.llm.lemonade_client import (
        InsufficientMemoryError,
        LemonadeClient,
        LemonadeClientError,
    )
    from gaia.llm.lemonade_manager import LemonadeManager
    from gaia.ui._chat_helpers import _maybe_load_expected_model

    client = LemonadeClient(base_url=LemonadeManager.get_base_url(), verbose=False)

    started = time.monotonic()
    # One lease over load and ask, so no other process swaps the model out between them.
    with ExitStack() as lease:
        # The loader's errors carry the remedy (free memory, another app holds
        # the model, the daemon kept the slot); pass them on, not a bare 500.
        try:
            lease.enter_context(model_lease(model, priority="interactive"))
            _maybe_load_expected_model(model)
        except (InsufficientMemoryError, BrokerUnavailableError, RuntimeError) as exc:
            raise RetryError(503, f"{model} could not be loaded: {exc}") from exc
        # Asking a model that isn't loaded would let Lemonade load it at its own,
        # smaller window.
        if not _is_loaded(client, model):
            raise RetryError(
                503,
                f"{model} could not be loaded, often because there is not enough "
                "free memory. Close other apps and try again; gaia.log says why.",
            )
        loaded = time.monotonic()
        try:
            data = client.chat_completions(
                model,
                messages,
                max_completion_tokens=RETRY_MAX_TOKENS,
                stream=False,
                timeout=900,
                auto_download=False,
            )
        except (LemonadeClientError, requests.RequestException) as exc:
            raise RetryError(
                503,
                f"{model} did not answer at {client.base_url}: {exc}. "
                "Check Settings → Model.",
            ) from exc
        finished = time.monotonic()
        try:
            # Read under the lease, so the numbers are this request's, as on a chat turn.
            server = client.get_stats()
        except LemonadeClientError as exc:
            logger.warning(
                "Lemonade /stats unavailable after retrying with %s; the comparison "
                "shows no speed for it: %s",
                model,
                exc,
            )
            server = {}
    content = (
        (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    ).strip()
    if not content:
        raise RetryError(
            503,
            f"{model} returned an empty answer. It may have spent its whole "
            f"{RETRY_MAX_TOKENS:,}-token reply on thinking; try another model.",
        )
    usage = data.get("usage") or {}
    stats: dict[str, Any] = {
        "model": model,
        "retried": True,
        "seconds": round(finished - started, 1),
        "load_seconds": round(loaded - started, 1),
        # Zero reads as "not reported", as on a chat turn's stats.
        "input_tokens": usage.get("prompt_tokens") or 0,
        "output_tokens": usage.get("completion_tokens") or 0,
        "tokens_per_second": round(server.get("tokens_per_second", 0), 1),
        "time_to_first_token": round(server.get("time_to_first_token", 0), 3),
    }
    return {"content": content, "stats": stats}
