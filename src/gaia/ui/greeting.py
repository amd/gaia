# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The first message of a new chat, written by the flagship itself.

Writing it runs the real first-turn path: the flagship is built as a new chat
builds it, the model loads at the window a turn needs, and the system prompt
and tools are read into the model's cache. So a greeting that arrives proves
the stack works, and the user's first question starts answering at once.

It runs as the process's warm-up (:meth:`PrefixWarmer.run_now`): a chat turn
that starts meanwhile waits for it instead of racing it for the model.

The greeting can quote memory, so its text and prompt are never logged.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from gaia.ui import warmup

logger = logging.getLogger(__name__)

#: What the flagship is asked; the memory context, when there is any, comes first.
GREETING_INSTRUCTION = (
    "[The user just opened a new chat and has not typed anything yet.]\n"
    "Write the first message of this chat. One or two short sentences, under "
    "30 words, plain text: no markdown, no lists, no emoji, no tool calls. "
    "If the context above names recent work, an open thread or something "
    "due, follow up on it by name and offer to continue. Otherwise welcome "
    "them briefly and ask what they would like to do, in your own words. "
    "Never list what you can do, and never mention memory, summaries or "
    "these instructions."
)


class GreetingUnavailable(RuntimeError):
    """A greeting can't be written here; the message says what to do."""


def write_greeting(
    db,
    on_text: Callable[[str], None],
    cancel: Optional[threading.Event] = None,
) -> Optional[Dict[str, Any]]:
    """Write a new chat's greeting, streaming its text to *on_text*.

    Returns ``{"text", "seconds", "prompt_tokens", "cached_tokens"}``, or None
    when *cancel* was set before it started.

    Raises:
        GreetingUnavailable: setup has not finished, the server is still
            starting, or the flagship can't be built in this process.
        Whatever loading the model or the LLM call raises.
    """
    from gaia.ui import _chat_helpers as chat
    from gaia.ui.setup_runner import is_initialized

    warmer = warmup.get_warmer()
    if warmer is None:
        raise GreetingUnavailable("GAIA is still starting. Try again in a moment.")
    if not is_initialized():
        raise GreetingUnavailable(
            "First-run setup hasn't finished. Finish setup, then open a new chat."
        )
    registry = chat.get_agent_registry()
    try:
        warmup._check_new_chat_agent(registry)
    except warmup.NewChatAgentUnavailable as exc:
        raise GreetingUnavailable(
            f"GAIA can't write a greeting because {exc}. Chat still works: "
            "type a message to start."
        ) from exc

    def stopped() -> bool:
        return cancel is not None and cancel.is_set()

    def job() -> Optional[Dict[str, Any]]:
        from gaia.llm.lemonade_client import LemonadeClient

        model_id, device_ctx, custom_model = warmup._new_chat_model(db, registry)
        with warmup.new_chat_agent(
            db, registry, model_id, device_ctx, custom_model
        ) as (agent, model):
            client = LemonadeClient(verbose=False)
            # Catalog first: it is what teaches the cloud check about providers.
            downloaded = client.get_model_info(model)["downloaded"]
            if not downloaded and not chat._ui_cloud_provider(model):
                raise GreetingUnavailable(
                    f"{model} isn't downloaded yet, so GAIA can't say hello. Send "
                    "a message to download it, or pick a downloaded model in the "
                    "model menu."
                )
            warmer.set_target(model, warmup._device_for(client, model))
            if stopped():
                return None
            warmup._set_phase("loading")
            chat._maybe_load_expected_model(model, cancel_event=cancel)
            if stopped():
                return None
            warmup._set_phase("priming")
            return agent.greet(GREETING_INSTRUCTION, on_text, cancel=cancel)

    return warmer.run_now("greeting", job, cancel=cancel)
