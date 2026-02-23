# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for AudioRecorder with mocked sounddevice."""

import queue
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture
def mock_sd():
    """Mock sounddevice module."""
    with patch("gaia.audio.audio_recorder.sd") as mock:
        # Mock query_devices for default device
        mock.query_devices.return_value = {
            "name": "Test Microphone",
            "index": 0,
            "max_input_channels": 2,
            "default_samplerate": 16000.0,
        }

        # Mock InputStream
        mock_stream = MagicMock()
        # Return (frames, overflowed) where frames is (CHUNK, channels) shape
        chunk_size = 2048
        silence = np.zeros((chunk_size, 1), dtype=np.float32)
        mock_stream.read.return_value = (silence, False)
        mock.InputStream.return_value = mock_stream

        yield mock


@pytest.fixture
def recorder(mock_sd):
    """Create an AudioRecorder with mocked sounddevice."""
    from gaia.audio.audio_recorder import AudioRecorder

    return AudioRecorder(device_index=0)


class TestAudioRecorderInit:
    def test_default_init(self, recorder):
        assert recorder.RATE == 16000
        assert recorder.CHANNELS == 1
        assert recorder.DTYPE == "float32"
        assert recorder.CHUNK == 2048
        assert recorder.device_index == 0
        assert not recorder.is_recording

    def test_custom_device_index(self, mock_sd):
        from gaia.audio.audio_recorder import AudioRecorder

        rec = AudioRecorder(device_index=3)
        assert rec.device_index == 3

    def test_default_device_index(self, mock_sd):
        from gaia.audio.audio_recorder import AudioRecorder

        mock_sd.query_devices.return_value = {
            "name": "Default Mic",
            "index": 2,
            "max_input_channels": 1,
            "default_samplerate": 16000.0,
        }
        rec = AudioRecorder()
        assert rec.device_index == 2

    def test_missing_sounddevice_raises(self):
        with patch("gaia.audio.audio_recorder.sd", None):
            from importlib import reload

            import gaia.audio.audio_recorder as mod

            # Re-assign sd to None to simulate missing import
            original_sd = mod.sd
            mod.sd = None
            try:
                with pytest.raises(ImportError, match="sounddevice"):
                    mod.AudioRecorder()
            finally:
                mod.sd = original_sd


class TestListDevices:
    def test_list_audio_devices(self, recorder, mock_sd):
        mock_sd.query_devices.return_value = [
            {"name": "Mic 1", "index": 0, "max_input_channels": 2},
            {"name": "Speaker", "index": 1, "max_input_channels": 0},
            {"name": "Mic 2", "index": 2, "max_input_channels": 1},
        ]
        devices = recorder.list_audio_devices()
        assert len(devices) == 2
        assert devices[0]["name"] == "Mic 1"
        assert devices[1]["name"] == "Mic 2"


class TestGetDeviceName:
    def test_get_device_name(self, recorder, mock_sd):
        mock_sd.query_devices.return_value = {
            "name": "Test Microphone",
            "index": 0,
        }
        assert recorder.get_device_name() == "Test Microphone"

    def test_get_device_name_error(self, recorder, mock_sd):
        mock_sd.query_devices.side_effect = Exception("Device error")
        name = recorder.get_device_name()
        assert "Error" in name


class TestRecording:
    def test_start_stop_recording(self, recorder, mock_sd):
        """Test that start/stop recording works without errors."""
        # Make the stream read return silence quickly
        mock_stream = mock_sd.InputStream.return_value
        silence = np.zeros((2048, 1), dtype=np.float32)
        mock_stream.read.return_value = (silence, False)

        recorder.start_recording()
        assert recorder.is_recording
        time.sleep(0.3)
        recorder.stop_recording()
        assert not recorder.is_recording

    def test_double_start_warning(self, recorder, mock_sd):
        """Test that starting recording twice logs a warning."""
        mock_stream = mock_sd.InputStream.return_value
        silence = np.zeros((2048, 1), dtype=np.float32)
        mock_stream.read.return_value = (silence, False)

        recorder.start_recording()
        recorder.start_recording()  # Should warn, not error
        time.sleep(0.2)
        recorder.stop_recording()

    def test_speech_detection_queues_audio(self, recorder, mock_sd):
        """Test that speech above threshold gets queued."""
        mock_stream = mock_sd.InputStream.return_value

        # Create audio data that exceeds the silence threshold
        chunk_size = 2048
        loud_audio = np.full((chunk_size, 1), 0.1, dtype=np.float32)
        silence = np.zeros((chunk_size, 1), dtype=np.float32)

        # Return loud audio for enough chunks, then silence to trigger queue
        call_count = [0]

        def read_side_effect(n):
            call_count[0] += 1
            if call_count[0] <= 15:  # Enough for speech + min length
                return (loud_audio, False)
            return (silence, False)

        mock_stream.read.side_effect = read_side_effect

        # Only start the record thread (not the process thread which drains the queue)
        recorder.is_recording = True
        recorder.record_thread = threading.Thread(target=recorder._record_audio)
        recorder.record_thread.start()

        # Wait for speech detection and silence timeout
        time.sleep(2.0)
        recorder.is_recording = False
        recorder.record_thread.join(timeout=2.0)

        # Should have detected speech and queued it
        assert not recorder.audio_queue.empty()

    def test_pause_resume(self, recorder, mock_sd):
        """Test pause and resume functionality."""
        mock_stream = mock_sd.InputStream.return_value
        silence = np.zeros((2048, 1), dtype=np.float32)
        mock_stream.read.return_value = (silence, False)

        recorder.start_recording()
        time.sleep(0.2)

        recorder.pause_recording()
        assert recorder.is_paused

        recorder.resume_recording()
        assert not recorder.is_paused

        recorder.stop_recording()
