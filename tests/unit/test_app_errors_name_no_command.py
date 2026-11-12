# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A model failure worded for the Agent UI never tells the user to run a command.

Seen in the desktop app: a mistyped Custom model ID failed the turn with the
raw preload error and "run: gaia lemonade embedded start ... re-run your GAIA
command". The app has a model menu and Settings; a terminal remedy is no next
step there.
"""

import re

import pytest

from gaia.llm.lemonade_client import LemonadeClientError
from gaia.llm.providers.lemonade import (
    LemonadeModelNotFoundError,
    LemonadeUnknownModelError,
    classify_lemonade_exception,
)
from gaia.llm.surface_hints import (
    AGENT_UI_MODEL_DOWNLOAD_HINT,
    AGENT_UI_MODEL_SWITCH_HINT,
    AGENT_UI_PRELOAD_RETRY_HINT,
    CLI_OWN_SERVER_START_HINT,
    TUI_MODEL_SWITCH_HINT,
    surface_wording,
)

COMMAND_RE = re.compile(
    r"\brun: |re-run your|`gaia |gaia lemonade|lemonade(?:\.exe)? pull"
)

UNKNOWN_ID = "no-such-model-xyz-123"


def _preload_failure(inner: str) -> LemonadeClientError:
    """What ``LemonadeManager`` raises when its preload of an idle server fails."""
    return LemonadeClientError(
        f"Failed to preload Lemonade model {UNKNOWN_ID!r} with ctx_size=65536 "
        f"on idle server at http://localhost:64679/api/v1: {inner}\n"
        "To recover manually: stop the running server, then restart it "
        f"({CLI_OWN_SERVER_START_HINT}) and re-run your GAIA command.\n"
        "GAIA's Lemonade log has the full error: C:\\x\\lemond.log."
    )


UNKNOWN_NAME = _preload_failure(
    f"Failed to auto-download '{UNKNOWN_ID}': Download failed: When registering "
    "a new model, the model name must include the `user` namespace, for example "
    f"`user.Phi-4-Mini-GGUF`. Received: {UNKNOWN_ID}"
)


def test_a_name_the_server_does_not_list_is_classified_with_the_model_named():
    typed = classify_lemonade_exception(UNKNOWN_NAME)

    assert isinstance(typed, LemonadeUnknownModelError)
    assert typed.model_id == UNKNOWN_ID
    assert typed.retryable is False
    assert f"`{UNKNOWN_ID}`" in typed.user_message
    assert typed.user_message.endswith(TUI_MODEL_SWITCH_HINT)


def test_the_app_is_sent_to_the_model_menu_and_the_custom_id_setting():
    typed = classify_lemonade_exception(UNKNOWN_NAME)
    shown = surface_wording(typed.user_message, "agent_ui")

    assert AGENT_UI_MODEL_SWITCH_HINT in shown
    assert "Settings > Advanced > Custom model ID" in shown
    assert not COMMAND_RE.search(shown)
    assert "ctx_size" not in shown and "localhost" not in shown


def test_an_unclassified_preload_failure_loses_its_terminal_remedy_in_the_app():
    error = _preload_failure("the backend exited with code 3")
    assert classify_lemonade_exception(error) is None

    shown = surface_wording(str(error), "agent_ui")

    assert AGENT_UI_PRELOAD_RETRY_HINT in shown
    assert not COMMAND_RE.search(shown)
    # The cause and the log stay for Details.
    assert "the backend exited with code 3" in shown
    assert "lemond.log" in shown


def test_a_terminal_keeps_the_restart_instruction():
    assert "gaia lemonade embedded start" in surface_wording(str(UNKNOWN_NAME), "cli")
    assert "gaia lemonade embedded start" in surface_wording(str(UNKNOWN_NAME), "tui")


@pytest.mark.parametrize(
    "pull",
    [
        "Run: C:\\lemonade\\lemonade.exe pull Gemma-4-E4B-it-GGUF",
        "Gemma-4-E4B-it-GGUF is not downloaded, and no Lemonade client CLI was "
        "found to do it — see https://lemonade-server.ai.",
    ],
)
def test_a_model_that_is_not_downloaded_is_fetched_from_settings_in_the_app(
    monkeypatch, pull
):
    from gaia.llm.lemonade_launcher import StartHint

    monkeypatch.setattr(
        "gaia.llm.providers.lemonade.describe_client_hint",
        lambda *a, **k: StartHint(instruction=pull),
    )
    message = LemonadeModelNotFoundError(model_id="Gemma-4-E4B-it-GGUF").user_message
    assert pull.rstrip(".") in message

    shown = surface_wording(message, "agent_ui")

    assert shown.endswith(AGENT_UI_MODEL_DOWNLOAD_HINT)
    assert "`Gemma-4-E4B-it-GGUF`" in shown
    assert not COMMAND_RE.search(shown)
    assert "/setup" not in shown


def test_the_start_hint_for_gaias_own_server_names_no_command_in_the_app():
    shown = surface_wording(
        f"Lemonade is stopped. {CLI_OWN_SERVER_START_HINT}.", "agent_ui"
    )

    assert not COMMAND_RE.search(shown)
    assert (
        shown == "Lemonade is stopped. GAIA starts its model server when it needs it."
    )
