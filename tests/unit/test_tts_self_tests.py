# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia test --test-type tts-*` must fail when speech synthesis fails.

The self-tests used to log the error and return, so the command exited 0 and a
broken TTS setup looked like a pass.
"""

import sys
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia import cli
from gaia.audio.kokoro_tts import KokoroTTS

STATS = {"processing_time": 0.1, "audio_duration": 0.1, "realtime_ratio": 1.0}


def _fail(*_args, **_kwargs):
    raise RuntimeError("synthesis failed")


def _tts(generate_speech=_fail, preprocess_text=lambda text: text):
    tts = KokoroTTS.__new__(KokoroTTS)
    tts.preprocess_text = preprocess_text
    tts.generate_speech = generate_speech
    return tts


def _fail_after_first_chunk():
    """Count chunks normally, then fail mid-stream after one chunk is queued."""
    calls = []

    def generate(_text, stream_callback=None):
        calls.append(_text)
        stream_callback(np.ones(24, dtype=np.float32))
        if len(calls) > 1:
            raise RuntimeError("synthesis failed mid-stream")
        return None, STATS

    return generate


def test_preprocessing_failure_raises():
    tts = _tts(preprocess_text=_fail)

    with pytest.raises(RuntimeError, match="synthesis failed"):
        tts.test_preprocessing("hello")


def test_audio_file_failure_raises(tmp_path):
    output = tmp_path / "out.wav"

    with pytest.raises(RuntimeError, match="synthesis failed"):
        _tts().test_generate_audio_file("hello", str(output))
    assert not output.exists()


def test_streaming_failure_mid_stream_raises_and_cleans_up():
    tts = _tts(generate_speech=_fail_after_first_chunk())
    stream = MagicMock()
    errors = []

    def run():
        try:
            tts.test_streaming_playback("hello world")
        except Exception as error:
            errors.append(error)

    with patch("gaia.audio.kokoro_tts.sd") as sd:
        sd.OutputStream.return_value = stream
        runner = threading.Thread(target=run)
        runner.start()
        runner.join(5)
    assert not runner.is_alive(), "streaming test hung after a mid-stream failure"

    playback = [t for t in threading.enumerate() if "stream_audio" in t.name]
    for thread in playback:
        thread.join(2)
    assert not [t for t in playback if t.is_alive()], "playback thread never exited"
    assert len(errors) == 1 and "mid-stream" in str(errors[0])
    stream.stop.assert_called_once()
    stream.close.assert_called_once()


def test_streaming_success_closes_stream_once():
    def generate(_text, stream_callback=None):
        stream_callback(np.ones(24, dtype=np.float32))
        return None, STATS

    stream = MagicMock()
    with patch("gaia.audio.kokoro_tts.sd") as sd:
        sd.OutputStream.return_value = stream
        _tts(generate_speech=generate).test_streaming_playback("hello world")

    assert stream.write.call_count == 1
    stream.stop.assert_called_once()
    stream.close.assert_called_once()


def _run_main(monkeypatch, *argv):
    """Run ``gaia <argv>`` through ``main()``; return its exit status."""
    monkeypatch.setattr(sys, "argv", ["gaia", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        return 0 if exc.code is None else exc.code
    return 0


@pytest.mark.parametrize(
    "test_type", ["tts-preprocessing", "tts-streaming", "tts-audio-file"]
)
def test_cli_exits_non_zero_when_synthesis_fails(
    test_type, monkeypatch, capsys, tmp_path
):
    tts = _tts(preprocess_text=_fail)
    monkeypatch.setattr("gaia.audio.kokoro_tts.KokoroTTS", lambda say=None: tts)
    monkeypatch.setattr("gaia.audio.kokoro_tts.sd", MagicMock())

    code = _run_main(
        monkeypatch,
        "test",
        "--test-type",
        test_type,
        "--output-audio-file",
        str(tmp_path / "out.wav"),
    )

    assert code == 1
    assert f"❌ Error: {test_type} test failed: synthesis failed" in (
        capsys.readouterr().out
    )


def test_cli_audio_file_success_exits_zero(monkeypatch, tmp_path):
    output = tmp_path / "out.wav"
    tts = _tts(generate_speech=lambda _text: (np.zeros(24, dtype=np.float32), STATS))
    monkeypatch.setattr("gaia.audio.kokoro_tts.KokoroTTS", lambda say=None: tts)

    code = _run_main(
        monkeypatch,
        "test",
        "--test-type",
        "tts-audio-file",
        "--output-audio-file",
        str(output),
    )

    assert code == 0
    assert output.exists()
