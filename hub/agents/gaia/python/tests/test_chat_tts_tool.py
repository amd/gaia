# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool-level contract coverage for the flagship speech tool."""

from pathlib import Path
from unittest.mock import patch

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


def test_tts_tool_speaks_in_the_voice_and_speed_chosen_for_reading_aloud(
    tts_tool, tmp_path, monkeypatch
):
    from gaia import config as config_mod
    from gaia.config import GaiaConfig

    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
    tool, allowed = tts_tool
    GaiaConfig.save_keys({"read_aloud_voice": "bm_george", "read_aloud_speed": 125})

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        instance = client.return_value

        def synthesize(_text, destination, **_kwargs):
            Path(destination).write_bytes(b"wav")
            return 1.0, 1

        instance.synthesize_to_wav_long.side_effect = synthesize
        result = tool("Hello.", str(allowed / "speech.wav"))
        named = tool("Hello.", str(allowed / "named.wav"), voice="af_sky", speed=0.75)

    assert result["voice"] == "bm_george"
    first, second = instance.synthesize_to_wav_long.call_args_list
    assert (first.kwargs["voice"], first.kwargs["speed"]) == ("bm_george", 1.25)
    assert named["voice"] == "af_sky"
    assert (second.kwargs["voice"], second.kwargs["speed"]) == ("af_sky", 0.75)


def test_tts_default_output_gets_an_inline_player(tts_tool, tmp_path, monkeypatch):
    import gaia.config
    from gaia.ui.generated_media import media_card

    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_DIR", tmp_path / "config")
    tool, _allowed = tts_tool

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:

        def synthesize(_text, destination, **_kwargs):
            Path(destination).write_bytes(b"wav")
            return 0.5, 1

        client.return_value.synthesize_to_wav_long.side_effect = synthesize
        result = tool("Read this aloud.")

    assert result["status"] == "success"
    card = media_card({"type": "tool_result", "success": True, "data": result})
    assert card is not None
    assert card["render"] == "audio"
    assert card["data"]["src"].startswith("/api/media/audio/speech_")


def test_tts_default_outputs_never_overwrite_each_other(
    tts_tool, tmp_path, monkeypatch
):
    import gaia.config
    from gaia.ui.generated_media import media_card

    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_DIR", tmp_path / "config")
    tool, _allowed = tts_tool

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:

        def synthesize(text, destination, **_kwargs):
            Path(destination).write_bytes(text.encode())
            return 0.5, 1

        client.return_value.synthesize_to_wav_long.side_effect = synthesize
        first = tool("First clip.")
        second = tool("Second clip.")

    assert first["audio_path"] != second["audio_path"]
    assert Path(first["audio_path"]).read_bytes() == b"First clip."
    for result in (first, second):
        card = media_card({"type": "tool_result", "success": True, "data": result})
        assert card is not None and card["render"] == "audio"


def test_tts_failed_synthesis_gets_no_player(tts_tool, tmp_path, monkeypatch):
    import gaia.config
    from gaia.ui.generated_media import media_card

    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_DIR", tmp_path / "config")
    tool, _allowed = tts_tool

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        client.return_value.synthesize_to_wav_long.side_effect = LemonadeTTSError(
            "Lemonade returned 500 for /audio/speech"
        )
        result = tool("Read this aloud.")

    assert result["status"] == "error"
    assert "500" in result["error"]
    assert "audio_path" not in result
    assert media_card({"type": "tool_result", "success": True, "data": result}) is None
