# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An answer's footer says where the turn ran: "on your GPU", "via Fireworks AI"."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gaia.ui._chat_helpers import _turn_place

BASE = "http://localhost:8000/api/v1"


def _client(loaded=None, status=200):
    resp = MagicMock(status_code=status)
    resp.json.return_value = {"all_models_loaded": loaded or []}
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    return client


@pytest.fixture(autouse=True)
def _no_eval_provider(monkeypatch):
    monkeypatch.delenv("GAIA_EVAL_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("GAIA_EVAL_CLAUDE_MODEL", raising=False)


@pytest.mark.parametrize(
    "device,expected",
    [
        ("gpu", "on your GPU"),
        ("amd_npu", "on your NPU"),
        ("cpu", "on your CPU"),
        ("gpu npu", "on your NPU and GPU"),
        ("none", "on this PC"),
    ],
)
async def test_local_turn_names_the_device_lemonade_reports(device, expected):
    client = _client(
        [
            {"model_name": "embed-model", "device": "cpu"},
            {"model_name": "Gemma-4-E4B-it-GGUF", "device": device},
        ]
    )
    place = await _turn_place("Gemma-4-E4B-it-GGUF", client, BASE, {})
    assert place == expected
    assert client.get.await_args.args[0] == f"{BASE}/health"


async def test_user_prefixed_model_matches_lemonades_bare_name():
    client = _client([{"model_name": "My-Model", "device": "gpu"}])
    assert await _turn_place("user.My-Model", client, BASE, {}) == "on your GPU"


async def test_local_model_not_loaded_says_this_pc():
    assert await _turn_place("Gemma-4-E4B-it-GGUF", _client(), BASE, {}) == (
        "on this PC"
    )


async def test_cloud_turn_names_the_provider_and_skips_lemonade():
    client = _client()
    place = await _turn_place(
        "fireworks.accounts/fireworks/models/glm-5", client, BASE, {}
    )
    assert place == "via Fireworks AI"
    client.get.assert_not_awaited()
