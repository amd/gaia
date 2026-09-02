# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for device-aware direct prompt model selection."""

from unittest.mock import MagicMock, patch

import pytest


def test_prompt_client_params_forward_device_selector():
    """The prompt dispatch preserves --device for GaiaCliClient."""
    from gaia.cli import _gaia_cli_client_params

    params = _gaia_cli_client_params({"message": "hi", "device": "npu"})

    assert params["device"] == "npu"


def test_prompt_client_uses_npu_model_for_device():
    """The NPU prompt path selects the FLM model from the registry."""
    from gaia.cli import GaiaCliClient

    with patch("gaia.cli.create_client", return_value=MagicMock()) as create_client:
        client = GaiaCliClient(device="npu")

    assert client.model == "gemma4-it-e2b-FLM"
    create_client.assert_called_once_with("lemonade", model="gemma4-it-e2b-FLM")


def test_prompt_client_explicit_model_wins_over_device():
    """An explicit model remains authoritative when both flags are supplied."""
    from gaia.cli import GaiaCliClient

    with patch("gaia.cli.create_client", return_value=MagicMock()) as create_client:
        client = GaiaCliClient(model="custom-model", device="npu")

    assert client.model == "custom-model"
    create_client.assert_called_once_with("lemonade", model="custom-model")


def test_prompt_client_without_device_keeps_default_model():
    """Existing direct prompt callers retain the default GPU model."""
    from gaia.cli import GaiaCliClient
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    with patch("gaia.cli.create_client", return_value=MagicMock()) as create_client:
        client = GaiaCliClient()

    assert client.model == DEFAULT_MODEL_NAME
    create_client.assert_called_once_with("lemonade", model=DEFAULT_MODEL_NAME)


def test_prompt_initialization_uses_explicit_device():
    """Prompt bring-up uses the same device as its selected model."""
    from gaia.cli import initialize_lemonade_for_agent

    with (
        patch(
            "gaia.cli._get_lemonade_config",
            return_value=("localhost", 13305, None),
        ),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ) as ensure_ready,
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value="http://localhost:13305/api/v1",
        ),
    ):
        assert initialize_lemonade_for_agent("minimal", device="npu")[0] is True

    assert ensure_ready.call_args.kwargs["device"] == "npu"


@pytest.mark.allow_network  # asyncio's Windows proactor uses a local socketpair.
def test_prompt_uses_configured_device_when_flag_is_absent(monkeypatch):
    """A prompt without --device follows the persisted device profile."""
    from gaia import cli

    captured = {}

    class FakeClient:
        def __init__(self, device=None, **kwargs):
            captured["device"] = device
            captured.update(kwargs)

        async def prompt(self, _message):
            yield "ok"

    monkeypatch.setattr(cli, "GaiaCliClient", FakeClient)
    monkeypatch.setattr(cli, "_configured_device", lambda: "npu")
    monkeypatch.setattr(
        cli,
        "initialize_lemonade_for_agent",
        lambda **_kwargs: (True, "http://localhost:13305/api/v1"),
    )

    import asyncio

    result = asyncio.run(cli.async_main("prompt", message="hi"))

    assert result == {"response": "ok"}
    assert captured["device"] == "npu"
