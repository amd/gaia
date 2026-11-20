# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit coverage for Lemonade's binary audio-generation endpoint."""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


BODY = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 64


def _generate(mixin, *args, **kwargs):
    with (
        patch("gaia.llm.lemonade_client.requests.post", return_value=_response()),
        patch("gaia.agents.tools.audio_generation_tools.before_change"),
    ):
        return mixin._generate_lemonade_audio(*args, **kwargs)


class _Validator:
    def __init__(self, allowed=True, reason=""):
        self.allowed, self.reason, self.calls = allowed, reason, []

    def is_path_allowed(self, path, prompt_user=True):
        return self.allowed

    def access_denied_message(self, path):
        return self.reason

    def validate_write(self, path, content_size=0):
        self.calls.append((path, content_size))
        return self.allowed, self.reason


def _project_mixin(tmp_path):
    mixin = _mixin(tmp_path / "cache")
    mixin.audio_gen_output_dir.mkdir()
    mixin.path_validator = _Validator()
    project = tmp_path / "project"
    project.mkdir()
    return mixin, project


class TestSaveTo:
    def test_a_copy_lands_in_the_folder_and_the_chat_keeps_its_own(self, tmp_path):
        mixin, project = _project_mixin(tmp_path)
        result = _generate(
            mixin, "music", "lofi", "ACE-Step-Music", save_to=str(project)
        )
        assert result["status"] == "success", result
        kept = Path(result["audio_path"])
        saved = Path(result["saved_to"])
        assert kept.parent == mixin.audio_gen_output_dir
        assert saved.parent == project.resolve() and saved.name == kept.name
        assert saved.read_bytes() == kept.read_bytes() == BODY
        assert mixin.path_validator.calls == [(str(saved), 0)]

    def test_saves_to_an_explicit_wav_name(self, tmp_path):
        mixin, project = _project_mixin(tmp_path)
        target = project / "theme.WAV"
        result = _generate(
            mixin, "music", "lofi", "ACE-Step-Music", save_to=str(target)
        )
        assert result["status"] == "success", result
        assert Path(result["saved_to"]) == target.resolve()
        assert target.read_bytes() == BODY

    def test_no_save_to_writes_only_gaias_copy(self, tmp_path):
        mixin, project = _project_mixin(tmp_path)
        result = _generate(mixin, "sfx", "door", "ThinkSound-SFX")
        assert result["status"] == "success", result
        assert "saved_to" not in result
        assert list(project.iterdir()) == []

    def test_policy_refusal_sends_no_request(self, tmp_path):
        mixin = _mixin(tmp_path)
        mixin.path_validator = _Validator(False, "Access denied: C:/Windows")
        with patch("gaia.llm.lemonade_client.requests.post") as post:
            result = mixin._generate_lemonade_audio(
                "sfx", "boom", "ThinkSound-SFX", save_to=str(tmp_path / "x.wav")
            )
        assert result == {"status": "error", "error": "Access denied: C:/Windows"}
        post.assert_not_called()

    def test_no_write_policy_refuses_instead_of_writing_unchecked(self, tmp_path):
        mixin = _mixin(tmp_path)
        with patch("gaia.llm.lemonade_client.requests.post") as post:
            result = mixin._generate_lemonade_audio(
                "sfx", "boom", "ThinkSound-SFX", save_to=str(tmp_path / "x.wav")
            )
        assert result["status"] == "error"
        assert "write policy" in result["error"]
        post.assert_not_called()

    @pytest.mark.parametrize(
        "save_to, needle",
        [
            ("relative/out.wav", "absolute"),
            ("", "Invalid save_to"),
            ("   ", "Invalid save_to"),
            ("bad\x00name.wav", "Invalid save_to"),
            # Not a .wav name, so read as a folder that does not exist.
            ("{tmp}/song.mp3", "does not exist"),
            ("{tmp}/run.exe", "does not exist"),
            ("{tmp}/missing/dir/song.wav", "does not exist"),
            ("{tmp}/existing.wav", "already exists"),
        ],
    )
    def test_bad_targets_are_refused_before_any_request(
        self, tmp_path, save_to, needle
    ):
        (tmp_path / "existing.wav").write_bytes(BODY)
        mixin = _mixin(tmp_path)
        mixin.path_validator = _Validator()
        with patch("gaia.llm.lemonade_client.requests.post") as post:
            result = mixin._generate_lemonade_audio(
                "music",
                "lofi",
                "ACE-Step-Music",
                save_to=save_to.replace("{tmp}", str(tmp_path)),
            )
        assert result["status"] == "error"
        assert needle in result["error"]
        post.assert_not_called()
        assert (tmp_path / "existing.wav").read_bytes() == BODY

    def test_invalid_audio_never_lands_in_the_project(self, tmp_path):
        mixin, project = _project_mixin(tmp_path)
        with (
            patch(
                "gaia.llm.lemonade_client.requests.post",
                return_value=_response(b'{"error": "out of memory"}' + b" " * 40),
            ),
            patch("gaia.agents.tools.audio_generation_tools.before_change"),
        ):
            result = mixin._generate_lemonade_audio(
                "music", "lofi", "ACE-Step-Music", save_to=str(project)
            )
        assert result["status"] == "error"
        assert list(project.iterdir()) == []
        assert list(mixin.audio_gen_output_dir.iterdir()) == []


class TestSaveToWithTheRealWritePolicy:
    """The policy decides before anything about the path is revealed."""

    def _mixin(self, tmp_path):
        from gaia.security import PathValidator

        mixin, project = _project_mixin(tmp_path)
        mixin.path_validator = PathValidator(
            allowed_paths=[str(project)], interactive_check=lambda: False
        )
        return mixin, project

    def test_inside_the_scope_saves(self, tmp_path):
        mixin, project = self._mixin(tmp_path)
        result = _generate(
            mixin, "sfx", "door", "ThinkSound-SFX", save_to=str(project / "door.wav")
        )
        assert result["status"] == "success", result
        assert (project / "door.wav").read_bytes() == BODY

    def test_outside_the_scope_is_refused_without_revealing_the_file(self, tmp_path):
        mixin, _ = self._mixin(tmp_path)
        outside = tmp_path / "secret"
        outside.mkdir()
        (outside / "taken.wav").write_bytes(b"keep")
        with patch("gaia.llm.lemonade_client.requests.post") as post:
            result = mixin._generate_lemonade_audio(
                "sfx", "door", "ThinkSound-SFX", save_to=str(outside / "taken.wav")
            )
        assert result["status"] == "error"
        assert "already exists" not in result["error"]
        post.assert_not_called()
        assert (outside / "taken.wav").read_bytes() == b"keep"


def test_a_file_that_appears_during_generation_is_kept(tmp_path):
    mixin, project = _project_mixin(tmp_path)
    target = project / "theme.wav"
    real = LemonadeClient.generate_audio

    def slow_generation(client, payload, output_path, **kwargs):
        target.write_bytes(b"the user's own file")
        return real(client, payload, output_path, **kwargs)

    mixin.audio_gen_client.generate_audio.side_effect = lambda *a, **k: slow_generation(
        mixin.audio_gen_client, *a, **k
    )
    result = _generate(mixin, "music", "x", "ACE-Step-Music", save_to=str(target))
    assert target.read_bytes() == b"the user's own file"
    (original,) = mixin.audio_gen_output_dir.iterdir()
    _assert_kept_without_its_copy(result, original)
    assert "appeared while the audio was generating" in result["copy_not_saved"]


def test_a_failed_copy_leaves_no_partial_file(tmp_path):
    mixin, project = _project_mixin(tmp_path)

    def disk_full(src, dst, length=0):
        dst.write(b"RIFF")
        raise OSError(28, "No space left on device")

    with patch(
        "gaia.agents.tools.audio_generation_tools.shutil.copyfileobj", disk_full
    ):
        result = _generate(
            mixin, "music", "x", "ACE-Step-Music", save_to=str(project / "a.wav")
        )
    assert list(project.iterdir()) == []
    (original,) = mixin.audio_gen_output_dir.iterdir()
    _assert_kept_without_its_copy(result, original)
    assert "No space left" in result["copy_not_saved"]
    assert str(project / "a.wav") in result["copy_not_saved"]


def test_a_folder_removed_during_generation_still_returns_the_track(tmp_path):
    mixin, project = _project_mixin(tmp_path)
    folder = project / "tracks"
    folder.mkdir()
    real = LemonadeClient.generate_audio

    def slow_generation(client, payload, output_path, **kwargs):
        folder.rmdir()
        return real(client, payload, output_path, **kwargs)

    mixin.audio_gen_client.generate_audio.side_effect = lambda *a, **k: slow_generation(
        mixin.audio_gen_client, *a, **k
    )
    result = _generate(mixin, "music", "x", "ACE-Step-Music", save_to=str(folder))
    (original,) = mixin.audio_gen_output_dir.iterdir()
    _assert_kept_without_its_copy(result, original)
    assert str(folder) in result["copy_not_saved"]


def _assert_kept_without_its_copy(result, original):
    """The track plays and lists from GAIA's folder; only the copy is missing."""
    assert result["status"] == "success"
    assert Path(result["audio_path"]) == original.resolve()
    assert original.read_bytes() == BODY
    assert "saved_to" not in result
    assert str(original) in result["copy_not_saved"]
