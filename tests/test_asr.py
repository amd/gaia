import os
import sys
import unittest

from gaia.logger import get_logger
from gaia.audio.whisper_asr import WhisperAsr


class TestWhisperAsr(unittest.TestCase):
    def setUp(self):
        self.log = get_logger(__name__)
        self.asr = WhisperAsr(model_size="base")

    def test_list_devices(self):
        """Test that we can list audio devices."""
        devices = self.asr.list_audio_devices()
        self.assertIsInstance(devices, list)
        # Log devices for debugging
        self.log.info(f"Found audio devices: {devices}")

    def test_short_recording(self):
        """Test a short recording session."""
        devices = self.asr.list_audio_devices()
        if not devices:
            self.skipTest("No audio devices available - skipping recording test")

        try:
            self.asr.start_recording(duration=5)  # Record for 5 seconds
        except OSError as e:
            if "Invalid device info" in str(e):
                self.skipTest("No valid audio input device available")
            else:
                raise
        except Exception as e:
            self.fail(f"Recording failed with error: {str(e)}")

    def test_file_transcription(self):
        """Test transcription of an existing file."""
        # Replace with path to a test audio file
        test_file = "data/audio/test.m4a"
        result = self.asr.transcribe_file(test_file)
        self.log.info(f"Transcription Result: {result}")
        self.assertTrue(result.strip() == "This is a test.")

    def tearDown(self):
        """Clean up resources after tests."""
        if hasattr(self, "asr"):
            # Ensure model resources are properly closed
            if hasattr(self.asr, "model"):
                del self.asr.model
        super().tearDown()


if __name__ == "__main__":
    unittest.main()
