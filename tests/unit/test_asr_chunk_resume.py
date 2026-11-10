# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A long recording is transcribed in chunks, and a failure keeps finished ones."""

import threading
import time
import wave
from unittest.mock import MagicMock, patch

import pytest

from gaia.audio import lemonade_asr
from gaia.audio.lemonade_asr import LemonadeASRClient
from gaia.tool_cancellation import ToolCancelled, set_tool_cancel_event


def _wav(path, seconds):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(1000)
        handle.writeframes(b"\0\0" * seconds * 1000)
    return path


def _response(text):
    return {
        "segments": [{"start": 1.0, "end": 2.0, "text": text, "avg_logprob": -0.1}],
        "language": "en",
        "duration": 4.0,
    }


@pytest.fixture
def client():
    with (
        patch.object(lemonade_asr, "MAX_CHUNK_SECONDS", 4),
        patch.object(lemonade_asr, "CHUNK_OVERLAP_SECONDS", 1.0),
        patch.object(LemonadeASRClient, "_refuse_unless_local"),
        patch.object(LemonadeASRClient, "_slot_lease"),
    ):
        yield LemonadeASRClient(
            base_url="http://localhost:1/api/v1", model="Whisper-Base"
        )


def test_a_rerun_after_a_failure_only_sends_the_unfinished_chunks(client, tmp_path):
    audio = _wav(tmp_path / "long.wav", 10)
    saved = tmp_path / "chunks"
    sent = []

    def fails_on_second(*_args, **_kwargs):
        sent.append(1)
        if len(sent) == 2:
            raise lemonade_asr.LemonadeASRError("Lemonade went away")
        return _response(f" part {len(sent)}")

    with patch.object(client, "_post_multipart", side_effect=fails_on_second):
        with pytest.raises(lemonade_asr.LemonadeASRError):
            client.transcribe(audio, checkpoint_dir=saved)
    assert [p.name for p in saved.iterdir()] == ["chunk-0000.json"]

    with patch.object(
        client, "_post_multipart", return_value=_response(" later")
    ) as post:
        result = client.transcribe(audio, checkpoint_dir=saved)

    assert post.call_count == 2, "the finished chunk is not sent again"
    assert result.text.startswith("part 1")
    assert result.segments[1].start == pytest.approx(4.0), "offsets survive a resume"


def test_a_damaged_chunk_is_transcribed_again(client, tmp_path):
    audio = _wav(tmp_path / "long.wav", 10)
    saved = tmp_path / "chunks"
    saved.mkdir()
    (saved / "chunk-0000.json").write_text("{cut off", encoding="utf-8")

    with patch.object(
        client, "_post_multipart", return_value=_response(" fresh")
    ) as post:
        client.transcribe(audio, checkpoint_dir=saved)

    assert post.call_count == 3


def test_no_checkpoint_dir_writes_nothing(client, tmp_path):
    audio = _wav(tmp_path / "long.wav", 10)

    with patch.object(client, "_post_multipart", return_value=_response(" text")):
        client.transcribe(audio)

    assert not list(tmp_path.rglob("chunk-*.json"))


def test_stop_does_not_wait_for_a_chunk_in_flight(client, tmp_path):
    """One Whisper chunk runs for minutes; Stop used to wait for all of it."""
    audio = _wav(tmp_path / "long.wav", 10)
    release = threading.Event()
    cancelled = threading.Event()
    set_tool_cancel_event(cancelled)

    def slow_post(*_args, **_kwargs):
        cancelled.set()
        release.wait(30)
        return MagicMock()

    client._session = MagicMock(post=slow_post)
    started = time.monotonic()
    try:
        with pytest.raises(ToolCancelled):
            client.transcribe(audio)
    finally:
        release.set()
        set_tool_cancel_event(None)

    assert time.monotonic() - started < 5
