import whisper
import queue
import time
import os

from gaia.logger import get_logger
from gaia.audio.audio_recorder import AudioRecorder
import torch


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
        self.log = self.__class__.log  # Use the class-level logger for instances

        # Initialize Whisper model with optimized settings
        self.log.info(f"Loading Whisper model: {model_size}")
        self.model = whisper.load_model(model_size)

        # Add compute type optimization if GPU available
        self.using_cuda = enable_cuda and torch.cuda.is_available()
        if self.using_cuda:
            self.model.to(torch.device("cuda"))
            torch.set_float32_matmul_precision("high")
            self.log.info("GPU acceleration enabled")

        # Rest of initialization remains the same
        self.audio_recorder = AudioRecorder()
        self.transcription_queue = transcription_queue
        self.record_thread = None
        self.process_thread = None

    def _process_audio(self):
        """Internal method to process audio and perform transcription."""
        self.log.info("Starting audio processing...")

        while self.is_recording:
            try:
                try:
                    # Reduced timeout for faster response
                    audio = self.audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                if len(audio) > 0:
                    try:
                        # Optimized transcription settings
                        result = self.model.transcribe(
                            audio,
                            language="en",
                            temperature=0.0,
                            no_speech_threshold=0.4,
                            condition_on_previous_text=True,
                            beam_size=3,
                            best_of=3,
                            fp16=self.using_cuda,  # Use FP16 if GPU available
                        )

                        transcribed_text = result["text"].strip()
                        if transcribed_text:
                            # Split the text into words and stream them
                            words = transcribed_text.split()
                            current_text = ""
                            for word in words:
                                current_text += word + " "
                                time.sleep(0.05)  # Reduced from 0.1 for faster output

                            # Send complete transcription to queue
                            self.log.debug(
                                f"Complete transcription: {transcribed_text}"
                            )
                            if self.transcription_queue:
                                self.transcription_queue.put(transcribed_text)

                    except Exception as e:
                        self.log.error(f"Error during transcription: {e}")

            except Exception as e:
                self.log.error(f"Error in audio processing: {e}")
                if not self.is_recording:
                    break

        self.log.info("Audio processing stopped")

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
