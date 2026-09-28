# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Playback must finish or fail explicitly before capture can resume."""

import queue
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.audio.kokoro_tts import KokoroTTS


def _tts_and_text():
    tts = KokoroTTS.__new__(KokoroTTS)
    tts.preprocess_text = lambda text: text

    def generate(_text, stream_callback):
        stream_callback(np.ones(24, dtype=np.float32))

    tts.generate_speech = generate
    text_queue = queue.Queue()
    text_queue.put("hello.")
    text_queue.put("__END__")
    return tts, text_queue


def test_streaming_waits_for_playback_and_closes_stream():
    tts, text_queue = _tts_and_text()
    stream = MagicMock()
    wrote = threading.Event()
    release = threading.Event()
    status = []
    errors = []

    def write(_audio):
        wrote.set()
        assert release.wait(2)

    stream.write.side_effect = write

    def run():
        try:
            tts.generate_speech_streaming(text_queue, status_callback=status.append)
        except Exception as error:
            errors.append(error)

    with patch("gaia.audio.kokoro_tts.sd") as sd:
        sd.OutputStream.return_value = stream
        runner = threading.Thread(target=run)
        runner.start()
        try:
            assert wrote.wait(2)
            assert runner.is_alive()
            assert status == [True]
            stream.close.assert_not_called()
        finally:
            release.set()
            runner.join(2)
    assert not runner.is_alive()
    assert not errors
    assert status == [True, False]
    stream.close.assert_called_once()


def test_stalled_device_times_out_instead_of_hanging():
    tts, text_queue = _tts_and_text()
    tts.PLAYBACK_TIMEOUT_SLACK = 0.05
    release = threading.Event()
    closed = threading.Event()
    stream = MagicMock()
    stream.write.side_effect = lambda _audio: release.wait(2)
    stream.close.side_effect = closed.set
    try:
        with patch("gaia.audio.kokoro_tts.sd") as sd:
            sd.OutputStream.return_value = stream
            with pytest.raises(TimeoutError, match="microphone remains paused"):
                tts.generate_speech_streaming(text_queue)
    finally:
        release.set()
        assert closed.wait(2)


@pytest.mark.parametrize("stage", ["write", "stop", "close", "synthesis"])
def test_streaming_reports_output_and_synthesis_errors(stage):
    tts, text_queue = _tts_and_text()
    stream = MagicMock()
    if stage == "synthesis":
        tts.generate_speech = MagicMock(side_effect=OSError("synthesis failed"))
    else:
        getattr(stream, stage).side_effect = OSError(f"{stage} failed")
    with patch("gaia.audio.kokoro_tts.sd") as sd:
        sd.OutputStream.return_value = stream
        with pytest.raises((RuntimeError, OSError), match=f"{stage} failed"):
            tts.generate_speech_streaming(text_queue)
    stream.close.assert_called_once()
