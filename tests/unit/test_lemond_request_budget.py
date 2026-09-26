# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The per-request budget must follow the window — at BOTH ends.

Lemonade 11.9.0 replaced a fixed 120s prefill ceiling with ``global_timeout``,
defaulting to 600s. At the window GAIA pins that is not enough: a long PDF
arrives as one large prefill, and 65536 tokens on a slow machine does not finish
inside ten minutes. The request dies and the answer comes back truncated, which
is the shape of #1030.

Raising it on the server alone changes nothing a user can see. GAIA's own client
abandons the request after its read timeout, so the smaller of the two wins —
which is how a second number that drifts from the window gets reintroduced while
looking fixed. One helper feeds lemond's config and the client's timeout.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.lemonade_client import (
    DEFAULT_REQUEST_TIMEOUT,
    GPU_CTX_SIZE,
    NPU_CTX_SIZE,
    REQUEST_BUDGET_ENV,
    LemonadeClient,
    LemonadeClientError,
    request_budget_seconds,
)
from gaia.llm.lemonade_embedded import _lemond_config


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    """Every test here is about the DERIVED value unless it says otherwise."""
    monkeypatch.delenv(REQUEST_BUDGET_ENV, raising=False)


# -- the derived budget ------------------------------------------------------


@pytest.mark.parametrize("ctx", [NPU_CTX_SIZE, GPU_CTX_SIZE])
def test_the_pinned_windows_get_more_than_the_old_ceiling(ctx):
    """Both device profiles are exactly the case the old defaults were short for."""
    assert request_budget_seconds(ctx) > DEFAULT_REQUEST_TIMEOUT


def test_a_bigger_window_gets_a_bigger_budget():
    assert request_budget_seconds(GPU_CTX_SIZE) > request_budget_seconds(NPU_CTX_SIZE)


def test_it_never_lowers_the_existing_client_ceiling():
    """Deriving the budget must not make a short-context machine worse off."""
    assert request_budget_seconds(4096) == DEFAULT_REQUEST_TIMEOUT


def test_a_wedged_request_still_ends():
    assert request_budget_seconds(10_000_000) == 3600


@pytest.mark.parametrize("ctx", [0, -1])
def test_a_nonsense_window_falls_back_to_the_client_default(ctx):
    assert request_budget_seconds(ctx) == DEFAULT_REQUEST_TIMEOUT


# -- both ends agree ---------------------------------------------------------


def test_the_server_config_carries_the_same_number_the_client_uses():
    """The regression this guards: a 2048s server budget behind a 900s client."""
    assert _lemond_config(GPU_CTX_SIZE)["global_timeout"] == request_budget_seconds(
        GPU_CTX_SIZE
    )


@patch.object(LemonadeClient, "_ensure_model_loaded")
@patch("gaia.llm.lemonade_client.requests.post")
def test_the_client_spends_the_budget_on_a_non_streaming_turn(mock_post, _ensure):
    """Agreeing on the number is worthless if the request does not carry it.

    The provider every agent goes through passes no timeout, so this default is
    the only thing standing between a 2048s server budget and a 900s cut-off.
    """
    mock_post.return_value = MagicMock(status_code=200, **{"json.return_value": {}})

    LemonadeClient(host="localhost", port=13305).chat_completions(
        model="test-model", messages=[{"role": "user", "content": "hi"}]
    )

    assert mock_post.call_args.kwargs["timeout"] == request_budget_seconds()


@patch.object(LemonadeClient, "_ensure_model_loaded")
@patch("gaia.llm.lemonade_client.OpenAI")
def test_the_client_spends_the_budget_on_a_streamed_turn(mock_openai, _ensure):
    """A streamed turn prefills the same document, so it needs the same budget."""
    mock_openai.return_value.chat.completions.create.return_value = iter([])

    list(
        LemonadeClient(host="localhost", port=13305).chat_completions(
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
    )

    assert mock_openai.call_args.kwargs["timeout"] == request_budget_seconds()


def test_the_written_config_keeps_the_private_instance_settings():
    config = _lemond_config(GPU_CTX_SIZE)
    # broadcast=False is what keeps a stray `lemonade` CLI off GAIA's server.
    assert config["broadcast"] is False
    assert config["config_version"] == 2
    assert config["host"] == "localhost"


# -- the override ------------------------------------------------------------


def test_an_explicit_override_wins_at_both_ends(monkeypatch):
    monkeypatch.setenv(REQUEST_BUDGET_ENV, "1500")
    assert request_budget_seconds(GPU_CTX_SIZE) == 1500
    assert _lemond_config(GPU_CTX_SIZE)["global_timeout"] == 1500


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_override_is_ignored(monkeypatch, blank):
    monkeypatch.setenv(REQUEST_BUDGET_ENV, blank)
    assert request_budget_seconds(GPU_CTX_SIZE) > DEFAULT_REQUEST_TIMEOUT


@pytest.mark.parametrize("bad", ["soon", "12.5", "0", "-30"])
def test_an_unusable_override_is_an_error_not_a_silent_fallback(monkeypatch, bad):
    """A timeout quietly other than the one you set is worse than being told."""
    monkeypatch.setenv(REQUEST_BUDGET_ENV, bad)
    with pytest.raises(LemonadeClientError) as excinfo:
        request_budget_seconds(GPU_CTX_SIZE)
    assert REQUEST_BUDGET_ENV in str(excinfo.value)
