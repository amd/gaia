# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a chat says when the model server stops answering.

A cloud chat on an installed GAIA answered "Couldn't reach the local LLM ...
Lemonade Server is not installed. Run `gaia init` ...": a terminal command, a
false "not installed", and a cloud model called local.
"""

import pytest

from gaia.llm import lemonade_launcher
from gaia.llm.providers.lemonade import (
    LemonadeNetworkError,
    LemonadeServerUnreachableError,
)
from gaia.llm.surface_hints import APP_OTHER_SERVER_DOWN_HINT, APP_SERVER_DOWN_HINT

from .test_spending_failed_turns import (  # noqa: F401  (fixture)
    FLASH,
    _stream_turn,
    _StreamAgent,
    db,
)

_NOT_INSTALLED = (
    "Lemonade Server is not installed. Run `gaia init` to install it, or set "
    "LEMONADE_SERVER_PATH to an existing install."
)


@pytest.fixture
def no_install_found(monkeypatch):
    """The host has no Lemonade install to describe, as on the tester's PC."""
    monkeypatch.setattr(
        lemonade_launcher,
        "describe_start_hint",
        lambda *a, **k: lemonade_launcher.StartHint(instruction=_NOT_INSTALLED),
    )


@pytest.mark.parametrize("ours", [True, False])
def test_an_app_is_never_given_a_terminal_command(monkeypatch, no_install_found, ours):
    monkeypatch.setattr(lemonade_launcher, "gaia_runs_lemonade", lambda *a: ours)

    advice = lemonade_launcher.server_down_advice()

    assert advice == (APP_SERVER_DOWN_HINT if ours else APP_OTHER_SERVER_DOWN_HINT)
    assert "`" not in advice and "gaia init" not in advice
    assert "not installed" not in advice


@pytest.mark.parametrize(
    "failure", [LemonadeNetworkError(), LemonadeServerUnreachableError()]
)
def test_the_sidecar_says_the_server_is_not_answering(
    monkeypatch, no_install_found, failure
):
    stdio = pytest.importorskip("gaia_agent.stdio")
    monkeypatch.setattr(lemonade_launcher, "gaia_runs_lemonade", lambda *a: False)

    message = stdio.model_server_message(failure)

    assert "model server isn't answering" in message
    assert "not installed" not in message
    assert "`" not in message and "gaia init" not in message
    assert "local LLM" not in message
    assert "send" in message.lower() and "message again" in message


class _RelayedFailure(_StreamAgent):
    """A turn the relay ended with the sidecar's terminal error."""

    def process_query(self, message):
        self.calls += 1
        self.console._emit(
            {
                "type": "agent_error",
                "content": LemonadeNetworkError.user_message,
                "fatal": True,
            }
        )
        return {"result": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model, local", [(FLASH, False), ("Gemma-4-E4B-it-GGUF", True)]
)
async def test_a_cloud_chat_is_not_told_its_model_is_local(
    db, model, local
):  # noqa: F811
    events = await _stream_turn(db, model, _RelayedFailure(model, fail=False))

    [error] = [e for e in events if e.get("type") == "agent_error"]
    assert ("local LLM" in error["content"]) is local
    assert ("GAIA's AI engine" in error["content"]) is not local
