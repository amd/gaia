# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for device-aware direct prompt model selection."""

from unittest.mock import MagicMock, patch


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
