import pyaudio
import wave
import numpy as np
import whisper
import threading
import queue
import time
import os
from gaia.logger import get_logger
import torch


class WhisperRecorder:
    log = get_logger(__name__)

    def __init__(
        self,
        model_size="small",
        device_index=1,
        transcription_queue=None,
        enable_cuda=False,
    ):
        self.log = self.__class__.log  # Use the class-level logger for instances

        # Audio parameters - optimized for better quality
        self.CHUNK = 1024 * 2  # Reduced for lower latency while maintaining quality
        self.FORMAT = pyaudio.paFloat32
        self.CHANNELS = 1
        self.RATE = 16000
        self.device_index = device_index

        # Voice detection parameters - fine-tuned
        self.SILENCE_THRESHOLD = 0.003  # More sensitive
        self.MIN_AUDIO_LENGTH = self.RATE * 0.25  # Reduced to 0.25 seconds
        self.is_speaking = False

        # Initialize Whisper model with optimized settings
        self.log.info(f"Loading Whisper model: {model_size}")
        self.model = whisper.load_model(model_size)

        # Add compute type optimization if GPU available
        if enable_cuda and torch.cuda.is_available():
            self.model.to(torch.device("cuda"))
            torch.set_float32_matmul_precision("high")
            self.log.info("GPU acceleration enabled")

        # Rest of initialization remains the same
        self.is_recording = False
        self.audio_queue = queue.Queue()
        self.transcription_queue = transcription_queue
        self.record_thread = None
        self.process_thread = None

    def list_audio_devices(self):
        """List all available audio input devices."""
        p = pyaudio.PyAudio()
        info = []
        self.log.info("Available Audio Devices:")
        for i in range(p.get_device_count()):
            dev_info = p.get_device_info_by_index(i)
            if dev_info.get("maxInputChannels") > 0:
                self.log.info(f"Index {i}: {dev_info.get('name')}")
                info.append(dev_info)
        p.terminate()
        return info

    def _is_speech(self, audio_chunk):
        """Detect if audio chunk contains speech based on amplitude."""
        return np.abs(audio_chunk).mean() > self.SILENCE_THRESHOLD

    def _record_audio(self):
        """Internal method to record audio."""
        p = pyaudio.PyAudio()

        try:
            device_info = p.get_device_info_by_index(self.device_index)
            self.log.info(f"Using audio device: {device_info['name']}")

            stream = p.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self.CHUNK,
            )

            self.log.info("Recording started...")

            # For detecting continuous speech
            speech_buffer = np.array([], dtype=np.float32)
            silence_counter = 0
            SILENCE_LIMIT = (
                10  # Number of silent chunks before considering speech ended
            )

            while self.is_recording:
                try:
                    data = np.frombuffer(
                        stream.read(self.CHUNK, exception_on_overflow=False),
                        dtype=np.float32,
                    )
                    data = np.clip(data, -1, 1)

                    if self._is_speech(data):
                        silence_counter = 0
                        speech_buffer = np.concatenate((speech_buffer, data))
                        if (
                            not self.is_speaking
                            and len(speech_buffer) > self.MIN_AUDIO_LENGTH
                        ):
                            self.is_speaking = True
                    else:
                        silence_counter += 1
                        if self.is_speaking:
                            speech_buffer = np.concatenate((speech_buffer, data))

                        # If we've had enough silence and were speaking
                        if silence_counter >= SILENCE_LIMIT and self.is_speaking:
                            if len(speech_buffer) > self.MIN_AUDIO_LENGTH:
                                self.audio_queue.put(speech_buffer)
                            speech_buffer = np.array([], dtype=np.float32)
                            self.is_speaking = False
                            silence_counter = 0

                except Exception as e:
                    self.log.error(f"Error reading from stream: {e}")
                    break

        except Exception as e:
            self.log.error(f"Error with device {self.device_index}: {e}")
            raise
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception as e:
                self.log.error(f"Error closing audio stream: {e}")
            p.terminate()

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
                            # initial_prompt="Transcribe the following speech accurately: ",
                            beam_size=3,  # Added for better accuracy
                            best_of=3,  # Added for better accuracy
                            fp16=torch.cuda.is_available(),  # Use FP16 if GPU available
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

    def start_recording(self, duration=None):
        """Start recording and transcription."""
        self.log.info("Initializing recording...")

        # Make sure we're not already recording
        if self.is_recording:
            self.log.warning("Recording is already in progress")
            return

        # Set recording flag before starting threads
        self.is_recording = True

        # Start record thread
        self.log.info("Starting record thread...")
        self.record_thread = threading.Thread(target=self._record_audio)
        self.record_thread.start()

        # Wait a short moment to ensure recording has started
        time.sleep(0.1)

        # Start process thread
        self.log.info("Starting process thread...")
        self.process_thread = threading.Thread(target=self._process_audio)
        self.process_thread.start()

        # Wait another moment to ensure processing has started
        time.sleep(0.1)

        if duration:
            time.sleep(duration)
            self.stop_recording()

    def stop_recording(self):
        """Stop recording and transcription."""
        self.log.info("Stopping recording...")
        self.is_recording = False
        if self.record_thread:
            self.log.info("Waiting for record thread to finish...")
            self.record_thread.join()
        if self.process_thread:
            self.log.info("Waiting for process thread to finish...")
            self.process_thread.join()
        self.log.info("Recording stopped")

    def transcribe_file(self, file_path):
        """Transcribe an existing audio file."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        result = self.model.transcribe(file_path)
        return result["text"]

    def get_device_name(self):
        """Get the name of the current audio device"""
        p = pyaudio.PyAudio()
        try:
            device_info = p.get_device_info_by_index(self.device_index)
            return device_info.get("name", f"Device {self.device_index}")
        except Exception as e:
            self.log.error(f"Error getting device name: {str(e)}")
            return f"Device {self.device_index} (Error: {str(e)})"
        finally:
            p.terminate()


if __name__ == "__main__":
    # Create recorder instance
    recorder = WhisperRecorder(model_size="small")

    print("=== Whisper Recorder Demo ===")
    print("\n1. Listing available audio devices...")
    recorder.list_audio_devices()

    print("\n2. Starting 30-second recording session...")
    print("(Speak into your microphone)")
    recorder.start_recording(duration=30)
    print("\nRecording session completed!")

    print("\n3. Demonstrating file transcription...")
    try:
        # Attempt to transcribe a test file if it exists
        test_file = "./data/audio/test.m4a"
        text = recorder.transcribe_file(test_file)
        print(f"Test file transcription: {text}")
    except FileNotFoundError:
        print(f"Note: To test file transcription, place an audio file at {test_file}")

    print("\nDemo completed!")
