# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool-level contract coverage for the flagship speech tool."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gaia_agent")

from gaia.audio.lemonade_tts import LemonadeTTSError
from gaia.security import PathValidator
from tests.unit.test_profilespec_characterization import chat_agent_build_context


@pytest.fixture
def tts_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with chat_agent_build_context("full") as agent:
        agent.path_validator = PathValidator(allowed_paths=[str(allowed)])
        agent._register_tools()
        yield agent._tools_registry["text_to_speech"]["function"], allowed


def test_tts_tool_rejects_invalid_input_before_client(tts_tool):
    tool, allowed = tts_tool
    target = allowed / "speech.wav"

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        assert "speed" in tool("hello", str(target), speed=2.5)["error"]
        assert "empty" in tool("   ", str(target))["error"]
        assert ".wav" in tool("hello", str(allowed / "speech.mp3"))["error"]
        client.assert_not_called()


def test_tts_tool_returns_missing_model_as_structured_error(tts_tool):
    tool, allowed = tts_tool
    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        client.return_value.check_model_available.side_effect = LemonadeTTSError(
            "TTS model is not downloaded. Run `gaia download --agent talk`, then retry."
        )
        result = tool("hello", str(allowed / "speech.wav"))

    assert result["status"] == "error"
    assert "gaia download --agent talk" in result["error"]


def test_tts_tool_uses_configured_model_and_reports_real_chunks(tts_tool):
    tool, allowed = tts_tool
    target = allowed / "speech.wav"
    target.write_bytes(b"old")

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        instance = client.return_value

        def synthesize(_text, destination, **_kwargs):
            Path(destination).write_bytes(b"wav")
            return 1.25, 2

        instance.synthesize_to_wav_long.side_effect = synthesize
        result = tool("First sentence. Second sentence.", str(target))

    assert result["status"] == "success"
    assert result["chunks"] == 2
    assert result["model"] == "kokoro-v1"
    assert result["output_path"] == str(target)
