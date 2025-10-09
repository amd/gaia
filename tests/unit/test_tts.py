# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from gaia.audio.kokoro_tts import KokoroTTS
from gaia.logger import get_logger


class TestKokoroTTS(unittest.TestCase):
    def setUp(self):
        """Initialize the test environment."""
        self.log = get_logger(__name__)
        try:
            self.tts = KokoroTTS()
        except Exception as e:
            self.skipTest(f"Failed to initialize KokoroTTS: {str(e)}")

        # Test data directory setup
        self.test_dir = (
            Path(os.environ.get("LOCALAPPDATA", "")) / "GAIA" / "data" / "audio"
        )
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.test_output = self.test_dir / "test_output.wav"

    def test_initialization(self):
        """Test that TTS initializes with default voice."""
        self.assertIsNotNone(self.tts)
        self.assertEqual(self.tts.voice_name, "af_bella")
        self.assertIsNotNone(self.tts.pipeline)

    def test_voice_selection(self):
        """Test voice selection functionality."""
        test_voice = "af_nicole"
        self.tts.set_voice(test_voice)
        self.assertEqual(self.tts.voice_name, test_voice)

        # Test invalid voice name
        with self.assertRaises(ValueError):
            self.tts.set_voice("nonexistent_voice")

    def test_available_voices(self):
        """Test listing available voices."""
        voices = self.tts.list_available_voices()
        self.assertIsInstance(voices, dict)
        self.assertGreater(len(voices), 0)

        # Check voice metadata structure
        for voice_id, metadata in voices.items():
            self.assertIn("name", metadata)
            self.assertIn("quality", metadata)
            self.assertIn("duration", metadata)

    def test_preprocessing_method(self):
        """Test the preprocessing test method."""
        test_text = """
        Let's test some preprocessing.
        • First bullet point
        • Second bullet point
        1. Numbered item
        2. Another numbered item
        **Some bold text**
        """
        result = self.tts.test_preprocessing(test_text)
        print(result)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)

    def test_streaming_playback_method(self):
        """Test the streaming playback test method."""
        test_text = "This is a test of streaming playback."
        try:
            self.tts.test_streaming_playback(test_text)
        except Exception as e:
            self.fail(f"Streaming playback test failed with error: {str(e)}")

    def test_generate_audio_file_method(self):
        """Test the audio file generation test method."""
        test_text = "This is a test of audio file generation."
        try:
            self.tts.test_generate_audio_file(test_text, str(self.test_output))
            self.assertTrue(self.test_output.exists())

            # Verify the audio file
            audio_data, sample_rate = sf.read(str(self.test_output))
            self.assertEqual(sample_rate, 24000)
            self.assertTrue(len(audio_data) > 0)
            self.assertTrue(np.any(audio_data != 0))
        except Exception as e:
            self.fail(f"Audio file generation test failed with error: {str(e)}")

    def tearDown(self):
        """Clean up test resources."""
        if hasattr(self, "test_output") and self.test_output.exists():
            try:
                self.test_output.unlink()
            except Exception as e:
                self.log.warning(f"Failed to delete test output file: {e}")


if __name__ == "__main__":
    unittest.main()
