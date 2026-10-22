# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The first message of a new chat, written by the flagship itself.

Writing it runs the real first-turn path: the flagship is built as a new chat
builds it, the model loads at the window a turn needs, and the system prompt
and tools are read into the model's cache. So a greeting that arrives proves
the stack works, and the user's first question starts answering at once.

A flagship built in this process greets here; one that runs as its daemon
sidecar greets through ``POST /v1/gaia/greeting`` (contract >= 2.19). Either
way it runs as the process's warm-up (:meth:`PrefixWarmer.run_now`): a chat
turn that starts meanwhile waits for it instead of racing it for the model.

The greeting can quote memory, so its text and prompt are never logged.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from gaia.ui import warmup

logger = logging.getLogger(__name__)


class GreetingUnavailable(RuntimeError):
    """A greeting can't be written here; the message says what to do."""


def write_greeting(
    db,
    on_text: Callable[[str], None],
    cancel: Optional[threading.Event] = None,
) -> Optional[Dict[str, Any]]:
    """Write a new chat's greeting, streaming its text to *on_text*.

    Returns ``{"text", ...}``, or None when *cancel* was set before it finished.

    Raises:
        GreetingUnavailable: setup has not finished, the server is still
            starting, the model isn't downloaded, or the flagship can't greet
            (not installed, or a sidecar too old for the greeting route).
        Whatever loading the model, the LLM call or the sidecar raises.
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
    if registry is not None and chat._should_relay_to_sidecar(
        warmup.NEW_CHAT_AGENT, registry
    ):
        return warmer.run_now(
            "greeting", lambda: _greet_in_sidecar(db, on_text, cancel), cancel=cancel
        )
    try:
        warmup._check_new_chat_agent(registry)
    except warmup.NewChatAgentUnavailable as exc:
        raise GreetingUnavailable(
            f"GAIA can't write a greeting because {exc}. Run `gaia init` to "
            "install the GAIA agent."
        ) from exc
    return warmer.run_now(
        "greeting", lambda: _greet_here(db, registry, on_text, cancel), cancel=cancel
    )


def _greet_here(db, registry, on_text, cancel) -> Optional[Dict[str, Any]]:
    """Build the flagship in this process and have it greet."""
    from gaia.llm.lemonade_client import LemonadeClient
    from gaia.ui import _chat_helpers as chat

    def stopped() -> bool:
        return cancel is not None and cancel.is_set()

    model_id, device_ctx, custom_model = warmup._new_chat_model(db, registry)
    with warmup.new_chat_agent(db, registry, model_id, device_ctx, custom_model) as (
        agent,
        model,
    ):
        client = LemonadeClient(verbose=False)
        # Catalog first: it is what teaches the cloud check about providers.
        downloaded = client.get_model_info(model)["downloaded"]
        if not downloaded and not chat._ui_cloud_provider(model):
            raise GreetingUnavailable(
                f"{model} isn't downloaded yet, so GAIA can't say hello. Send "
                "a message to download it, or pick a downloaded model in the "
                "model menu."
            )
        warmup.get_warmer().set_target(model, warmup._device_for(client, model))
        if stopped():
            return None
        warmup._set_phase("loading")
        chat._maybe_load_expected_model(model, cancel_event=cancel)
        if stopped():
            return None
        warmup._set_phase("priming")
        return agent.greet(on_text, cancel=cancel)


def _greet_in_sidecar(db, on_text, cancel) -> Optional[Dict[str, Any]]:
    """Have the flagship's daemon sidecar greet, relaying its stream."""
    from gaia.ui import _chat_helpers as chat
    from gaia.ui.email_sidecar import daemon_client
    from gaia.ui.email_sidecar.errors import SidecarError
    from gaia.ui.email_sidecar.profiles import GAIA_PROFILE, greeting_supported

    warmup._set_phase("building")
    try:
        handle = daemon_client.acquire_handle(GAIA_PROFILE.agent_id)
    except SidecarError as exc:
        raise GreetingUnavailable(
            f"The GAIA agent could not be started to say hello: {exc}"
        ) from exc
    if not greeting_supported(GAIA_PROFILE, handle.api_version):
        raise GreetingUnavailable(GAIA_PROFILE.greeting_upgrade_message)
    body = {"memory": chat._memory_mode({}, db)}
    stop_watching = threading.Event()

    def close_on_cancel(resp) -> None:
        # A read blocked on a quiet sidecar only ends when the response closes.
        def watch() -> None:
            while not stop_watching.wait(0.2):
                if cancel.is_set():
                    resp.close()
                    return

        threading.Thread(target=watch, name="gaia-greeting-cancel", daemon=True).start()

    stream = handle.proxy().greeting_stream(
        body, on_response=close_on_cancel if cancel is not None else None
    )
    try:
        for event in stream:
            if cancel is not None and cancel.is_set():
                return None
            kind = event.get("type")
            if kind == "status":
                warmup._set_phase(event.get("phase") or "building")
            elif kind == "chunk":
                on_text(event.get("content", ""))
            elif kind == "done":
                return {k: v for k, v in event.items() if k != "type"}
            elif kind == "error":
                raise RuntimeError(event.get("message") or "the sidecar gave no reason")
    except Exception:
        if cancel is not None and cancel.is_set():
            return None
        raise
    finally:
        stop_watching.set()
    if cancel is not None and cancel.is_set():
        return None
    raise RuntimeError("The GAIA agent's greeting stream ended without an answer.")
