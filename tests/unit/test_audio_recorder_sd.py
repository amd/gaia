# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for AudioRecorder with sounddevice (no hardware required)."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture
def mock_sd():
    """Mock sounddevice module."""
    with patch("gaia.audio.audio_recorder.sd") as mock:
        mock.default.device = (3, 5)
        mock.query_devices.return_value = {"name": "Test Microphone", "max_input_channels": 2}
        mock.InputStream = MagicMock()
        yield mock


@pytest.fixture
def recorder(mock_sd):
    """Create an AudioRecorder with mocked sounddevice."""
    from gaia.audio.audio_recorder import AudioRecorder

    return AudioRecorder(device_index=0)


class TestAudioRecorderInit:
    def test_init_dtype(self, recorder):
        """Verify DTYPE is set to float32 (not pyaudio format constant)."""
        assert recorder.DTYPE == "float32"
        assert not hasattr(recorder, "FORMAT")

    def test_init_raises_without_sounddevice(self):
        """Verify ImportError when sounddevice is not available."""
        with patch("gaia.audio.audio_recorder.sd", None):
            from gaia.audio.audio_recorder import AudioRecorder

            with pytest.raises(ImportError, match="sounddevice"):
                AudioRecorder()


class TestDefaultInputDevice:
    def test_get_default_input_device(self, mock_sd):
        """Verify default device index comes from sd.default.device."""
        from gaia.audio.audio_recorder import AudioRecorder

        recorder = AudioRecorder()
        assert recorder.device_index == 3

    def test_get_default_input_device_fallback(self, mock_sd):
        """Verify fallback to 0 when sd.default.device fails."""
        type(mock_sd.default).device = property(
            lambda self: (_ for _ in ()).throw(Exception("no device"))
        )
        from gaia.audio.audio_recorder import AudioRecorder

        recorder = AudioRecorder()
        assert recorder.device_index == 0


class TestListAudioDevices:
    def test_list_audio_devices(self, mock_sd):
        """Verify only input devices are returned with correct keys."""
        mock_sd.query_devices.return_value = [
            {"name": "Microphone", "max_input_channels": 2, "max_output_channels": 0},
            {"name": "Speaker", "max_input_channels": 0, "max_output_channels": 2},
            {"name": "Headset", "max_input_channels": 1, "max_output_channels": 1},
        ]
        from gaia.audio.audio_recorder import AudioRecorder

        recorder = AudioRecorder(device_index=0)
        devices = recorder.list_audio_devices()

        assert len(devices) == 2
        assert devices[0]["name"] == "Microphone"
        assert devices[0]["index"] == 0
        assert devices[0]["max_input_channels"] == 2
        assert devices[1]["name"] == "Headset"
        assert devices[1]["index"] == 2


class TestGetDeviceName:
    def test_get_device_name(self, recorder, mock_sd):
        """Verify device name is returned."""
        mock_sd.query_devices.return_value = {"name": "Test Mic"}
        assert recorder.get_device_name() == "Test Mic"

    def test_get_device_name_error(self, recorder, mock_sd):
        """Verify fallback on error."""
        mock_sd.query_devices.side_effect = Exception("device error")
        name = recorder.get_device_name()
        assert "Device 0" in name
        assert "device error" in name


class TestRecordAudio:
    def test_record_opens_stream(self, recorder, mock_sd):
        """Verify InputStream is created with correct params and lifecycle."""
        mock_stream = MagicMock()
        # Return silent audio frames then stop recording
        silent_frames = np.zeros((recorder.CHUNK, 1), dtype=np.float32)
        call_count = 0

        def fake_read(chunk_size):
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                recorder.is_recording = False
            return silent_frames, False

        mock_stream.read = fake_read
        mock_sd.InputStream.return_value = mock_stream

        recorder.is_recording = True
        recorder._record_audio()

        mock_sd.InputStream.assert_called_once_with(
            samplerate=recorder.RATE,
            channels=recorder.CHANNELS,
            dtype=recorder.DTYPE,
            device=recorder.device_index,
            blocksize=recorder.CHUNK,
        )
        mock_stream.start.assert_called_once()
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()


class TestPauseResume:
    def test_pause_resume(self, recorder):
        """Verify pause/resume flags."""
        assert not recorder.is_paused
        recorder.pause_recording()
        assert recorder.is_paused
        recorder.resume_recording()
        assert not recorder.is_paused
