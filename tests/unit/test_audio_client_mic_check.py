# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for AudioClient._check_mic_levels() and no-speech warning logic."""

import queue
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.audio.audio_client import AudioClient


@pytest.fixture
def audio_client():
    """AudioClient with a mocked LLM client and a fake WhisperAsr."""
    with patch("gaia.audio.audio_client.create_client"):
        client = AudioClient(enable_tts=False)

    mock_asr = MagicMock()
    mock_asr.RATE = 16000
    mock_asr.CHUNK = 2048
    mock_asr.device_index = 0
    client.whisper_asr = mock_asr
    return client


def _make_sd_mock(samples: np.ndarray):
    """Return a mock sounddevice module where InputStream.read() yields the given samples.

    sounddevice stream.read() returns (frames, overflowed) where frames has
    shape (blocksize, channels).
    """
    mock_sd = MagicMock()
    mock_stream = MagicMock()

    # stream.read() returns (ndarray shape (CHUNK, 1), overflowed_bool)
    frames = samples.reshape(-1, 1)
    mock_stream.read.return_value = (frames, False)
    mock_sd.InputStream.return_value = mock_stream

    return mock_sd


def test_check_mic_levels_warns_on_silence(audio_client, capsys):
    """_check_mic_levels prints a warning when all audio samples are zero."""
    silent_samples = np.zeros(2048, dtype=np.float32)
    mock_sd = _make_sd_mock(silent_samples)

    with patch.dict(sys.modules, {"sounddevice": mock_sd}):
        audio_client._check_mic_levels()

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "No audio detected" in captured.out


def test_check_mic_levels_passes_on_audio(audio_client, capsys):
    """_check_mic_levels does not warn when audio is present."""
    noisy_samples = np.ones(2048, dtype=np.float32) * 0.1
    mock_sd = _make_sd_mock(noisy_samples)

    with patch.dict(sys.modules, {"sounddevice": mock_sd}):
        audio_client._check_mic_levels()

    captured = capsys.readouterr()
    assert "WARNING" not in captured.out


def test_check_mic_levels_handles_exception_gracefully(audio_client):
    """_check_mic_levels does not raise when sounddevice raises an exception."""
    mock_sd = MagicMock()
    mock_sd.InputStream.side_effect = OSError("No audio device")

    with patch.dict(sys.modules, {"sounddevice": mock_sd}):
        # Should not raise
        audio_client._check_mic_levels()


def test_check_mic_levels_skipped_without_whisper_asr():
    """_check_mic_levels returns immediately when whisper_asr is None."""
    with patch("gaia.audio.audio_client.create_client"):
        client = AudioClient(enable_tts=False)
    client.whisper_asr = None

    mock_sd = MagicMock()
    with patch.dict(sys.modules, {"sounddevice": mock_sd}):
        client._check_mic_levels()

    mock_sd.InputStream.assert_not_called()


def test_no_speech_warning_after_10_seconds(audio_client, capsys):
    """_process_audio_wrapper prints a no-speech warning after 10s with no transcriptions."""

    # Make the queue raise Empty on first get (which also stops is_recording)
    def fake_queue_get(timeout=0.1):
        audio_client.whisper_asr.is_recording = False
        raise queue.Empty

    audio_client.transcription_queue = MagicMock()
    audio_client.transcription_queue.get.side_effect = fake_queue_get
    audio_client.transcription_queue.qsize.return_value = 0

    # time.time() calls in _process_audio_wrapper:
    #   1. last_transcription_time = time.time()  -> base_time
    #   2. session_start_time = time.time()        -> base_time
    #   3. warning check: time.time() - session_start_time  -> base_time + 15
    base_time = 1000.0
    with patch("gaia.audio.audio_client.time") as mock_time:
        mock_time.time.side_effect = [base_time, base_time, base_time + 15]

        dummy_callback = MagicMock()
        audio_client._process_audio_wrapper(dummy_callback)

    captured = capsys.readouterr()
    assert "No speech detected" in captured.out
