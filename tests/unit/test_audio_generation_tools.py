# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit coverage for Lemonade's binary audio-generation endpoint."""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from gaia.agents.tools.audio_generation_tools import AudioGenerationToolsMixin
from gaia.llm.lemonade_client import LemonadeClient


def _mixin(tmp_path):
    host = AudioGenerationToolsMixin()
    host.audio_gen_output_dir = tmp_path
    host.audio_gen_client = MagicMock()
    host.audio_gen_client.base_url = "http://localhost:13305/api/v1"
    host.audio_gen_client._auth_headers.return_value = {
        "Authorization": "Bearer secret"
    }
    host.audio_gen_client.generate_audio.side_effect = (
        lambda *args, **kwargs: LemonadeClient.generate_audio(
            host.audio_gen_client, *args, **kwargs
        )
    )
    host.audio_gen_client.list_models.return_value = {
        "data": [
            {
                "id": "ACE-Step-Music",
                "recipe": "acestep",
                "labels": ["audio-generation"],
            },
            {
                "id": "ThinkSound-SFX",
                "recipe": "thinksound",
                "labels": ["audio-generation"],
            },
            {"id": "Text-LLM", "recipe": "llamacpp", "labels": ["chat"]},
        ]
    }
    return host


def _response(body=b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 64, status=200):
    response = MagicMock()
    response.status_code = status
    response.headers = {"Content-Type": "audio/wav"}
    response.iter_content.return_value = [body[:30], body[30:]]
    response.__enter__.return_value = response
    return response


def test_music_writes_real_wav_and_passes_lyrics(tmp_path):
    mixin = _mixin(tmp_path)
    with (
        patch(
            "gaia.llm.lemonade_client.requests.post",
            return_value=_response(),
        ) as post,
        patch("gaia.agents.tools.audio_generation_tools.before_change"),
    ):
        result = mixin._generate_lemonade_audio(
            "music",
            "cinematic synth ambience",
            "ACE-Step-Music",
            lyrics="[verse]\nHello",
            vocal_language="en",
            duration=20,
            seed=42,
        )
    assert result["status"] == "success"
    output = Path(result["audio_path"])
    assert output.is_file() and output.read_bytes()[8:12] == b"WAVE"
    assert post.call_args.kwargs["json"]["lyrics"] == "[verse]\nHello"
    assert post.call_args.kwargs["json"]["vocal_language"] == "en"
    assert post.call_args.kwargs["stream"] is True
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer secret"


def test_sfx_forwards_backend_cfg_only_when_given(tmp_path):
    mixin = _mixin(tmp_path)
    with (
        patch(
            "gaia.llm.lemonade_client.requests.post",
            return_value=_response(),
        ) as post,
        patch("gaia.agents.tools.audio_generation_tools.before_change"),
    ):
        result = mixin._generate_lemonade_audio(
            "sfx",
            "wooden door creaks",
            "ThinkSound-SFX",
            cfg=2.0,
        )
    assert result["status"] == "success"
    assert post.call_args.kwargs["json"]["cfg"] == 2.0
    assert "lyrics" not in post.call_args.kwargs["json"]


def test_not_downloaded_rejected_without_post(tmp_path):
    mixin = _mixin(tmp_path)
    with patch("gaia.llm.lemonade_client.requests.post") as post:
        result = mixin._generate_lemonade_audio("music", "ambient", "Unknown-Music")
    assert result["status"] == "error"
    post.assert_not_called()


def test_invalid_wav_rejected_and_temp_cleaned(tmp_path):
    mixin = _mixin(tmp_path)
    with patch(
        "gaia.llm.lemonade_client.requests.post",
        return_value=_response(b'{"error":"failed"}'),
    ):
        result = mixin._generate_lemonade_audio("sfx", "door slam", "ThinkSound-SFX")
    assert result["status"] == "error"
    assert list(tmp_path.iterdir()) == []


def test_auth_error_is_not_leaked(tmp_path):
    mixin = _mixin(tmp_path)
    response = _response(status=401)
    response.text = "Authorization: Bearer secret"
    with patch("gaia.llm.lemonade_client.requests.post", return_value=response):
        result = mixin._generate_lemonade_audio("music", "ambient", "ACE-Step-Music")
    assert result["status"] == "error"
    assert "secret" not in result["error"]


def test_wrong_kind_rejected(tmp_path):
    mixin = _mixin(tmp_path)
    assert (
        mixin._generate_lemonade_audio("music", "ambient", "ThinkSound-SFX")["status"]
        == "error"
    )
    assert (
        mixin._generate_lemonade_audio("sfx", "boom", "ACE-Step-Music")["status"]
        == "error"
    )


def test_model_lease_covers_the_entire_wav_stream(tmp_path):
    mixin = _mixin(tmp_path)
    client = mixin.audio_gen_client
    events = []

    @contextmanager
    def lease():
        events.append("acquired")
        try:
            yield
        finally:
            events.append("released")

    client._model_slot_lease.side_effect = lambda model, request_timeout: lease()
    response = _response()

    def chunks(chunk_size):
        assert chunk_size == 64 * 1024
        assert events == ["acquired", "request"]
        events.append("first chunk")
        yield b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 32
        assert events == ["acquired", "request", "first chunk"]
        events.append("last chunk")
        yield b"\x00" * 24

    response.iter_content.side_effect = chunks

    def post(*args, **kwargs):
        assert events == ["acquired"]
        assert kwargs["stream"] is True
        events.append("request")
        return response

    with (
        patch("gaia.llm.lemonade_client.requests.post", side_effect=post),
        patch("gaia.agents.tools.audio_generation_tools.before_change"),
    ):
        result = mixin._generate_lemonade_audio("sfx", "wooden door", "ThinkSound-SFX")

    assert result["status"] == "success", result
    assert events == ["acquired", "request", "first chunk", "last chunk", "released"]
    client._model_slot_lease.assert_called_once_with(
        "ThinkSound-SFX", request_timeout=1800
    )
    client.load_model.assert_not_called()


def test_lease_released_and_temp_removed_on_http_error(tmp_path):
    mixin = _mixin(tmp_path)
    client = mixin.audio_gen_client
    events = []

    @contextmanager
    def lease():
        events.append("acquired")
        try:
            yield
        finally:
            events.append("released")

    client._model_slot_lease.side_effect = lambda model, request_timeout: lease()
    response = _response(status=503)
    response.text = "Backend temporarily unavailable"
    with patch("gaia.llm.lemonade_client.requests.post", return_value=response):
        result = mixin._generate_lemonade_audio("music", "ambient", "ACE-Step-Music")
    assert result["status"] == "error"
    assert "503" in result["error"]
    assert events == ["acquired", "released"]
    assert list(tmp_path.iterdir()) == []
