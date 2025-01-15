import unittest
import os
from whisper_recorder import WhisperRecorder


class TestWhisperRecorder(unittest.TestCase):
    def setUp(self):
        self.recorder = WhisperRecorder(model_size="base")

    def test_list_devices(self):
        """Test that we can list audio devices."""
        devices = self.recorder.list_audio_devices()
        self.assertIsInstance(devices, list)

    def test_short_recording(self):
        """Test a short recording session."""
        try:
            self.recorder.start_recording(duration=5)  # Record for 5 seconds
            # No assertion needed - just checking it doesn't crash
        except Exception as e:
            self.fail(f"Recording failed with error: {str(e)}")

    def test_file_transcription(self):
        """Test transcription of an existing file."""
        # Replace with path to a test audio file
        test_file = "./data/audio/test.mp3"
        if os.path.exists(test_file):
            result = self.recorder.transcribe_file(test_file)
            self.assertIsInstance(result, str)
            self.assertTrue(len(result) > 0)


if __name__ == "__main__":
    unittest.main()
