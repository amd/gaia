# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Standard library imports first
import queue
import threading
import time

# Third-party imports next
import numpy as np
import pyaudio

from gaia.logger import get_logger


class AudioRecorder:
    log = get_logger(__name__)

    def __init__(
        self,
        device_index=None,
    ):
        self.log = self.__class__.log  # Use the class-level logger for instances

        # Add thread attributes
        self.record_thread = None
        self.process_thread = None

        # Audio parameters - optimized for better quality
        self.CHUNK = 1024 * 2  # Reduced for lower latency while maintaining quality
        self.FORMAT = pyaudio.paFloat32
        self.CHANNELS = 1
        self.RATE = 16000
        self.device_index = (
            self._get_default_input_device() if device_index is None else device_index
        )
        self.is_recording = False
        self.audio_queue = queue.Queue()
        self.stream = None  # Add stream as class attribute

        # Voice detection parameters
        self.SILENCE_THRESHOLD = 0.003
        self.MIN_AUDIO_LENGTH = self.RATE * 0.25
        self.is_speaking = False

        # New attributes for pause functionality
        self.is_paused = False
        self.pause_lock = threading.Lock()

    def _get_default_input_device(self):
        """Get the default input device index."""
        pa = pyaudio.PyAudio()
        try:
            default_device = pa.get_default_input_device_info()
            return default_device["index"]
        except Exception as e:
            self.log.error(f"Error getting default input device: {e}")
            # Fall back to device 0 if no default found
            return 0
        finally:
            pa.terminate()

    def _is_speech(self, audio_chunk):
        """Detect if audio chunk contains speech based on amplitude."""
        return np.abs(audio_chunk).mean() > self.SILENCE_THRESHOLD

    def _record_audio(self):
        """Internal method to record audio."""
        pa = pyaudio.PyAudio()

        try:
            device_info = pa.get_device_info_by_index(self.device_index)
            self.log.debug(f"Using audio device: {device_info['name']}")

            self.stream = pa.open(  # Store stream as class attribute
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self.CHUNK,
            )

            self.log.debug("Recording started...")

            # For detecting continuous speech
            speech_buffer = np.array([], dtype=np.float32)
            silence_counter = 0
            SILENCE_LIMIT = (
                10  # Number of silent chunks before considering speech ended
            )

            chunk_count = 0
            self.log.debug(f"Silence threshold: {self.SILENCE_THRESHOLD}")

            while self.is_recording:
                try:
                    # Skip recording if paused
                    with self.pause_lock:
                        if self.is_paused:
                            time.sleep(0.1)
                            continue

                    data = np.frombuffer(
                        self.stream.read(self.CHUNK, exception_on_overflow=False),
                        dtype=np.float32,
                    )
                    data = np.clip(data, -1, 1)

                    chunk_count += 1
                    energy = np.abs(data).mean()

                    # Log every 10th chunk to avoid spam
                    if chunk_count % 10 == 0:
                        self.log.debug(
                            f"Chunk {chunk_count}: energy={energy:.6f}, is_speech={self._is_speech(data)}, buffer_size={len(speech_buffer)}"
                        )

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
                                self.log.debug(
                                    f"Adding speech to queue: {len(speech_buffer)} samples ({len(speech_buffer)/self.RATE:.2f}s)"
                                )
                                self.audio_queue.put(speech_buffer)
                            else:
                                self.log.debug(
                                    f"Speech too short: {len(speech_buffer)} samples < {self.MIN_AUDIO_LENGTH}"
                                )
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
                if self.stream is not None:
                    self.stream.stop_stream()
                    self.stream.close()
                    self.stream = None
            except Exception as e:
                self.log.error(f"Error closing audio stream: {e}")
            pa.terminate()

    def _process_audio(self):
        """Process recorded audio chunks from the queue."""
        while self.is_recording:
            try:
                # Process any audio in the queue
                if not self.audio_queue.empty():
                    _ = self.audio_queue.get_nowait()
                else:
                    time.sleep(0.1)  # Prevent busy-waiting
            except queue.Empty:
                continue
            except Exception as e:
                self.log.error(f"Error processing audio: {e}")
                break

    def list_audio_devices(self):
        """List all available audio input devices."""
        pa = pyaudio.PyAudio()
        info = []
        self.log.info("Available Audio Devices:")
        for i in range(pa.get_device_count()):
            dev_info = pa.get_device_info_by_index(i)
            if dev_info.get("maxInputChannels") > 0:
                self.log.info(f"Index {i}: {dev_info.get('name')}")
                info.append(dev_info)
        pa.terminate()
        return info

    def get_device_name(self):
        """Get the name of the current audio device"""
        pa = pyaudio.PyAudio()
        try:
            device_info = pa.get_device_info_by_index(self.device_index)
            return device_info.get("name", f"Device {self.device_index}")
        except Exception as e:
            self.log.error(f"Error getting device name: {str(e)}")
            return f"Device {self.device_index} (Error: {str(e)})"
        finally:
            pa.terminate()

    def start_recording(self, duration=None):
        """Start recording and transcription."""
        self.log.debug("Initializing recording...")

        # Make sure we're not already recording
        if self.is_recording:
            self.log.warning("Recording is already in progress")
            return

        # Set recording flag before starting threads
        self.is_recording = True

        # Start record thread
        self.log.debug("Starting record thread...")
        self.record_thread = threading.Thread(target=self._record_audio)
        self.record_thread.start()

        # Wait a short moment to ensure recording has started
        time.sleep(0.1)

        # Start process thread
        self.log.debug("Starting process thread...")
        self.process_thread = threading.Thread(target=self._process_audio)
        self.process_thread.start()

        # Wait another moment to ensure processing has started
        time.sleep(0.1)

        if duration:
            time.sleep(duration)
            self.stop_recording()

    def stop_recording(self):
        """Stop recording and transcription."""
        self.log.debug("Stopping recording...")
        self.is_recording = False
        if self.record_thread:
            self.log.debug("Waiting for record thread to finish...")
            self.record_thread.join()
        if self.process_thread:
            self.log.debug("Waiting for process thread to finish...")
            self.process_thread.join()
        self.log.debug("Recording stopped")

    def pause_recording(self):
        """Pause the recording without stopping threads."""
        with self.pause_lock:
            self.is_paused = True
            self.log.debug("Recording paused")

    def resume_recording(self):
        """Resume the recording."""
        with self.pause_lock:
            self.is_paused = False
            self.log.debug("Recording resumed")


if __name__ == "__main__":
    ar = AudioRecorder()

    print("Listing available audio devices...")
    ar.list_audio_devices()

    print("Starting 30-second recording session...")
    ar.start_recording(duration=30)
    print("Recording session completed!")
