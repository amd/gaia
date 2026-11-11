# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool-level contract coverage for the flagship speech tool."""

from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent")

import gaia.config
from gaia.audio.lemonade_tts import LemonadeTTSError
from gaia.security import PathValidator
from gaia.ui.generated_media import media_card, media_dir
from tests.unit.test_profilespec_characterization import chat_agent_build_context


@pytest.fixture
def tts_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(gaia.config, "GAIA_CONFIG_FILE", tmp_path / "config.json")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with chat_agent_build_context("full") as agent:
        agent.path_validator = PathValidator(allowed_paths=[str(allowed)])
        agent._register_tools()
        yield agent._tools_registry["text_to_speech"]["function"], allowed


def _speaks(client, seconds=1.25):
    """Make the mocked client write each recording, as the real one does."""

    def synthesize(pieces, destination, **_kwargs):
        Path(destination).write_bytes(" | ".join(pieces).encode())
        return seconds

    client.return_value.synthesize_file.side_effect = synthesize
    return client.return_value.synthesize_file


def _card(result):
    return media_card({"type": "tool_result", "success": True, "data": result})


def test_tts_tool_rejects_invalid_input_before_client(tts_tool):
    tool, allowed = tts_tool
    target = allowed / "speech.wav"

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        assert "speed" in tool("hello", str(target), speed=2.5)["error"]
        assert "empty" in tool("   ", str(target))["error"]
        assert ".wav or .mp3" in tool("hello", str(allowed / "speech.ogg"))["error"]
        assert "Unknown Kokoro voice" in tool("hello", voice="zz_nobody")["error"]
        client.assert_not_called()
    assert not media_dir("audio").exists() or not list(media_dir("audio").iterdir())


def test_tts_tool_returns_missing_model_as_structured_error(tts_tool):
    tool, allowed = tts_tool
    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        client.return_value.check_model_available.side_effect = LemonadeTTSError(
            "TTS model is not downloaded. Run `gaia download --agent talk`, then retry."
        )
        result = tool("hello", str(allowed / "speech.wav"))

    assert result["status"] == "error"
    assert "gaia download --agent talk" in result["error"]
    assert not (allowed / "speech.wav").exists()


def test_tts_tool_uses_configured_model_and_reports_real_chunks(tts_tool):
    tool, allowed = tts_tool
    target = allowed / "speech.wav"
    target.write_bytes(b"old")
    text = "First sentence. " * 150

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        synthesize = _speaks(client)
        result = tool(text, str(target))

    pieces = synthesize.call_args.args[0]
    assert len(pieces) == 2 and all(len(piece) <= 1800 for piece in pieces)
    assert result["status"] == "success"
    assert result["chunks"] == 2
    assert result["model"] == "kokoro-v1"
    assert result["duration_seconds"] == 1.25
    assert result["output_path"] == str(target)


def test_tts_tool_speaks_in_the_voice_and_speed_chosen_for_reading_aloud(tts_tool):
    from gaia.config import GaiaConfig

    tool, allowed = tts_tool
    GaiaConfig.save_keys({"read_aloud_voice": "bm_george", "read_aloud_speed": 125})

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        synthesize = _speaks(client)
        result = tool("Hello.", str(allowed / "speech.wav"))
        named = tool("Hello.", str(allowed / "named.wav"), voice="af_sky", speed=0.75)

    assert result["voice"] == "bm_george"
    first, second = synthesize.call_args_list
    assert (first.kwargs["voice"], first.kwargs["speed"]) == ("bm_george", 1.25)
    assert named["voice"] == "af_sky"
    assert (second.kwargs["voice"], second.kwargs["speed"]) == ("af_sky", 0.75)


def test_tts_default_output_gets_an_inline_player(tts_tool):
    tool, _allowed = tts_tool

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        _speaks(client)
        result = tool("Read this aloud.")

    assert result["status"] == "success"
    assert result["output_path"] == result["audio_path"]
    card = _card(result)
    assert card is not None
    assert card["render"] == "audio"
    assert card["data"]["src"].startswith("/api/media/audio/speech_")


def test_tts_saved_to_a_named_folder_still_gets_a_player(tts_tool):
    """The user's copy is outside GAIA's audio folder, so the player needs GAIA's own."""
    tool, allowed = tts_tool
    target = allowed / "notes" / "speech.mp3"

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        synthesize = _speaks(client)
        result = tool("Read this aloud.", str(target))

    assert synthesize.call_args.kwargs["audio_format"] == "mp3"
    assert result["format"] == "mp3"
    assert result["output_path"] == result["file_path"] == str(target)
    kept = Path(result["audio_path"])
    assert kept.parent == media_dir("audio") and kept.suffix == ".mp3"
    assert target.read_bytes() == kept.read_bytes() == b"Read this aloud."
    card = _card(result)
    assert card is not None and card["render"] == "audio"
    assert card["data"]["path"] == str(kept.resolve())


def test_tts_copy_that_cannot_be_saved_says_where_the_recording_is(
    tts_tool, monkeypatch
):
    import shutil

    tool, allowed = tts_tool

    def denied(_source, _destination):
        raise PermissionError(13, "Permission denied")

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        _speaks(client)
        monkeypatch.setattr(shutil, "copyfile", denied)
        result = tool("Read this aloud.", str(allowed / "speech.wav"))

    assert result["status"] == "error"
    assert Path(result["kept_at"]).read_bytes() == b"Read this aloud."
    assert result["kept_at"] in result["error"]
    assert not (allowed / "speech.wav").exists()
    assert _card(result) is None


def test_tts_refuses_a_destination_outside_the_allowed_folders(tts_tool, tmp_path):
    tool, _allowed = tts_tool
    outside = tmp_path / "elsewhere" / "speech.wav"

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        result = tool("Read this aloud.", str(outside))
        client.assert_not_called()

    assert result["status"] == "error"
    assert not outside.exists()


def test_tts_default_outputs_never_overwrite_each_other(tts_tool):
    tool, _allowed = tts_tool

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        _speaks(client)
        first = tool("First clip.")
        second = tool("Second clip.")

    assert first["audio_path"] != second["audio_path"]
    assert Path(first["audio_path"]).read_bytes() == b"First clip."
    for result in (first, second):
        card = _card(result)
        assert card is not None and card["render"] == "audio"


def test_tts_failed_synthesis_gets_no_player(tts_tool):
    tool, _allowed = tts_tool

    with patch("gaia.audio.lemonade_tts.LemonadeTTSClient") as client:
        client.return_value.synthesize_file.side_effect = LemonadeTTSError(
            "Lemonade returned 500 for /audio/speech"
        )
        result = tool("Read this aloud.")

    assert result["status"] == "error"
    assert "500" in result["error"]
    assert "audio_path" not in result
    assert _card(result) is None
