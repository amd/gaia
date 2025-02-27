# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Standard library imports
import os
import queue
import time

# Third-party imports
import torch
import whisper

# First-party imports
from gaia.logger import get_logger
from gaia.audio.audio_recorder import AudioRecorder


class WhisperAsr(AudioRecorder):
    log = get_logger(__name__)

    def __init__(
        self,
        model_size="small",
        device_index=1,
        transcription_queue=None,
        enable_cuda=False,
    ):
        super().__init__(device_index)
        self.log = self.__class__.log

        # Initialize Whisper model with optimized settings
        self.log.debug(f"Loading Whisper model: {model_size}")
        self.model = whisper.load_model(model_size)

        # Add compute type optimization if GPU available
        self.using_cuda = enable_cuda and torch.cuda.is_available()
        if self.using_cuda:
            self.model.to(torch.device("cuda"))
            torch.set_float32_matmul_precision("high")
            # Enable torch compile for better performance
            if hasattr(torch, "compile"):
                self.model = torch.compile(self.model)
            self.log.debug("GPU acceleration enabled with optimizations")

        # Add batch processing capability
        self.batch_size = 3  # Process multiple audio segments at once
        self.audio_buffer = []
        self.last_process_time = time.time()
        self.process_interval = 0.5  # Process every 0.5 seconds

        # Rest of initialization
        self.audio_recorder = AudioRecorder()
        self.transcription_queue = transcription_queue
        self.record_thread = None
        self.process_thread = None

    def _process_audio(self):
        """Internal method to process audio with batching and optimizations."""
        self.log.debug("Starting optimized audio processing...")

        while self.is_recording:
            try:
                current_time = time.time()

                # Collect audio segments into buffer
                while len(self.audio_buffer) < self.batch_size:
                    try:
                        audio = self.audio_queue.get_nowait()
                        if len(audio) > 0:
                            self.audio_buffer.append(audio)
                    except queue.Empty:
                        break

                # Process batch if enough time has passed or buffer is full
                if len(self.audio_buffer) >= self.batch_size or (
                    len(self.audio_buffer) > 0
                    and current_time - self.last_process_time >= self.process_interval
                ):

                    try:
                        with torch.inference_mode():
                            # Process batch of audio segments
                            results = [
                                self.model.transcribe(
                                    audio,
                                    language="en",
                                    temperature=0.0,
                                    no_speech_threshold=0.3,
                                    condition_on_previous_text=True,
                                    beam_size=3,
                                    best_of=3,
                                    fp16=self.using_cuda,
                                )
                                for audio in self.audio_buffer
                            ]

                            # Send transcriptions to queue
                            for result in results:
                                transcribed_text = result["text"].strip()
                                if transcribed_text and self.transcription_queue:
                                    self.transcription_queue.put(transcribed_text)
                                    self.log.debug(f"Transcribed: {transcribed_text}")

                        self.audio_buffer = []
                        self.last_process_time = current_time

                    except Exception as e:
                        self.log.error(f"Batch transcription error: {e}")
                        self.audio_buffer = []  # Clear buffer on error

                else:
                    # Small sleep to prevent CPU spinning
                    time.sleep(0.01)

            except Exception as e:
                self.log.error(f"Error in audio processing: {e}")
                if not self.is_recording:
                    break

        self.log.debug("Audio processing stopped")

    def transcribe_file(self, file_path):
        """Transcribe an existing audio file."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        result = self.model.transcribe(file_path)
        return result["text"]


if __name__ == "__main__":
    print("=== Whisper ASR Demo ===")
    asr = WhisperAsr(model_size="small")
    try:
        # Attempt to transcribe a test file if it exists
        test_file = "./data/audio/test.m4a"
        text = asr.transcribe_file(test_file)
        print(f"Test file transcription: {text}")
    except FileNotFoundError:
        print(f"No audio file found at {test_file}")

    print("\nDemo completed!")
