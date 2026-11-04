# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia init` verifies a speech model with a speech request, never a chat one.

The talk profile declares Kokoro, so `--profile all` collects it too. Sent a
chat request, Kokoro fails, and a clean install would report it broken.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.installer.init_command import (
    InitCommand,
    _load_model_once,
    is_tts_model_id,
)
from gaia.llm.lemonade_client import LemonadeClient

KOKORO = "kokoro-v1"


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch, tmp_path):
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / ".gaia"))


def _client():
    client = MagicMock()
    client.base_url = "http://127.0.0.1:13305/api/v1"
    client.health_check.return_value = {"status": "ok"}
    client.check_model_available.return_value = True
    client.get_required_models.side_effect = lambda agent: (
        LemonadeClient.get_required_models(client, agent)
    )
    client.list_models.return_value = {
        "data": [{"id": "Gemma-4-E4B-it-GGUF", "recipe_options": {"ctx_size": 65536}}]
    }
    # Setup shows a vision model a red image, so the fake reads it.
    client.chat_completions.side_effect = lambda **kw: {
        "choices": [
            {
                "message": {
                    "content": (
                        "red"
                        if isinstance(kw["messages"][-1]["content"], list)
                        else "ok"
                    )
                }
            }
        ]
    }
    client.embeddings.return_value = {"data": [{"embedding": [0.1] * 8}]}
    return client


def _speech_ok():
    return patch(
        "gaia.audio.lemonade_tts.LemonadeTTSClient.synthesize",
        autospec=True,
        return_value=(np.zeros(240, dtype=np.float32), 24000),
    )


def test_kokoro_is_the_only_registered_speech_model():
    assert is_tts_model_id(KOKORO)
    assert is_tts_model_id("Kokoro-V1")
    assert not is_tts_model_id("Gemma-4-E4B-it-GGUF")
    assert not is_tts_model_id("user.embeddinggemma-300m-GGUF")


def test_all_profile_verification_never_sends_kokoro_a_chat_request():
    client = _client()
    assert KOKORO in client.get_required_models("all")
    cmd = InitCommand(profile="all", yes=True)
    cmd.console = MagicMock()

    with (
        _speech_ok() as synthesize,
        patch("gaia.llm.lemonade_client.LemonadeClient", return_value=client),
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ),
        patch(
            "gaia.llm.lemonade_client.fit_ctx_to_free_memory",
            side_effect=lambda _m, ctx, _url: ctx,
        ),
    ):
        assert cmd._verify_setup() is True

    chatted = {c.kwargs.get("model") for c in client.chat_completions.call_args_list}
    loaded = {c.args[0] for c in client.load_model.call_args_list}
    assert KOKORO not in chatted
    assert KOKORO not in loaded
    spoken = [c.args[0].model for c in synthesize.call_args_list]
    assert spoken == [KOKORO]


def test_a_speech_model_that_will_not_speak_fails_verification():
    from gaia.audio.lemonade_tts import LemonadeTTSError

    cmd = InitCommand(profile="all", yes=True)
    cmd.console = MagicMock()
    client = _client()
    with patch(
        "gaia.audio.lemonade_tts.LemonadeTTSClient.synthesize",
        side_effect=LemonadeTTSError("Lemonade returned 500 for speech"),
    ):
        ok, error = cmd._test_model_inference(client, KOKORO)

    assert ok is False
    assert "500" in error
    client.chat_completions.assert_not_called()


def test_empty_audio_fails_verification():
    cmd = InitCommand(profile="all", yes=True)
    cmd.console = MagicMock()
    with patch(
        "gaia.audio.lemonade_tts.LemonadeTTSClient.synthesize",
        return_value=(np.zeros(0, dtype=np.float32), 24000),
    ):
        ok, error = cmd._test_model_inference(_client(), KOKORO)

    assert ok is False
    assert "no audio" in error


def test_load_check_labels_kokoro_speech_and_speaks_it():
    client = _client()
    client.get_model_info.return_value = {"size_gb": 0.34}
    with _speech_ok() as synthesize:
        result = _load_model_once(client, KOKORO)

    assert (result.role, result.loaded) == ("speech", True)
    synthesize.assert_called_once()
    client._ensure_model_loaded.assert_not_called()
