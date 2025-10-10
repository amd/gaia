# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Standard library imports
import os
import queue
import threading
import time

# Third-party imports
import numpy as np
import pyaudio
import torch
import whisper

from gaia.audio.audio_recorder import AudioRecorder

# First-party imports
from gaia.logger import get_logger


class WhisperAsr(AudioRecorder):
    log = get_logger(__name__)

    def __init__(
        self,
        model_size="small",
        device_index=None,  # Use default input device
        transcription_queue=None,
        enable_cuda=False,
        silence_threshold=None,  # Custom silence threshold
        min_audio_length=None,  # Custom minimum audio length
    ):
        super().__init__(device_index)

        # Override thresholds if provided
        if silence_threshold is not None:
            self.SILENCE_THRESHOLD = silence_threshold
        if min_audio_length is not None:
            self.MIN_AUDIO_LENGTH = min_audio_length
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
        self.transcription_queue = transcription_queue

    def _record_audio_streaming(self):
        """Record audio for streaming mode - puts chunks directly into queue."""
        pa = pyaudio.PyAudio()

        try:
            # Log device info
            if self.device_index is not None:
                device_info = pa.get_device_info_by_index(self.device_index)
            else:
                device_info = pa.get_default_input_device_info()
                self.device_index = device_info["index"]

            self.log.debug(
                f"Using audio device [{self.device_index}]: {device_info['name']}"
            )

            self.stream = pa.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self.CHUNK,
            )

            self.log.debug("Streaming recording started...")
            audio_buffer = np.array([], dtype=np.float32)
            chunks_processed = 0

            # Use 3-second chunks for better context (Whisper works better with longer segments)
            chunk_duration = 3.0  # seconds
            overlap_duration = 0.5  # seconds of overlap to avoid cutting words

            chunk_size = int(self.RATE * chunk_duration)
            overlap_size = int(self.RATE * overlap_duration)

            # Simple VAD - only send chunks with sufficient audio energy
            min_energy_threshold = 0.001  # Minimum energy to consider as speech

            while self.is_recording:
                try:
                    data = np.frombuffer(
                        self.stream.read(self.CHUNK, exception_on_overflow=False),
                        dtype=np.float32,
                    )
                    audio_buffer = np.concatenate((audio_buffer, data))

                    # Process when we have enough audio (3 seconds)
                    if len(audio_buffer) >= chunk_size:
                        chunk = audio_buffer[:chunk_size].copy()

                        # Only process if chunk has sufficient audio energy (not silence)
                        energy = np.abs(chunk).mean()
                        chunks_processed += 1

                        if energy > min_energy_threshold:
                            self.audio_queue.put(chunk)
                            self.log.debug(
                                f"Chunk {chunks_processed}: Added to queue (energy: {energy:.6f})"
                            )
                        else:
                            self.log.debug(
                                f"Chunk {chunks_processed}: Skipped - too quiet (energy: {energy:.6f})"
                            )

                        # Keep overlap to maintain context between chunks
                        audio_buffer = audio_buffer[chunk_size - overlap_size :]

                except Exception as e:
                    self.log.error(f"Error reading from stream: {e}")
                    break

            # Process any remaining audio
            if len(audio_buffer) > self.RATE * 0.5:  # At least 0.5 seconds
                self.audio_queue.put(audio_buffer.copy())

        finally:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
            pa.terminate()

    def start_recording_streaming(self):
        """Start recording in streaming mode."""
        self.is_recording = True
        self.record_thread = threading.Thread(target=self._record_audio_streaming)
        self.record_thread.start()
        time.sleep(0.1)
        self.process_thread = threading.Thread(target=self._process_audio)
        self.process_thread.start()
        time.sleep(0.1)

    def _process_audio(self):
        """Internal method to process audio with batching and optimizations."""
        self.log.debug("Starting optimized audio processing...")
        processed_count = 0

        while self.is_recording:
            try:
                current_time = time.time()

                # Collect audio segments into buffer
                while len(self.audio_buffer) < self.batch_size:
                    try:
                        audio = self.audio_queue.get_nowait()
                        if len(audio) > 0:
                            self.audio_buffer.append(audio)
                            self.log.debug(
                                f"Added audio to buffer (size: {len(self.audio_buffer)}/{self.batch_size})"
                            )
                    except queue.Empty:
                        break

                # Process batch if enough time has passed or buffer is full
                if len(self.audio_buffer) >= self.batch_size or (
                    len(self.audio_buffer) > 0
                    and current_time - self.last_process_time >= self.process_interval
                ):

                    try:
                        processed_count += 1
                        self.log.debug(
                            f"Processing batch {processed_count} with {len(self.audio_buffer)} segments..."
                        )

                        with torch.inference_mode():
                            # Process batch of audio segments with better quality settings
                            results = [
                                self.model.transcribe(
                                    audio,
                                    language="en",
                                    temperature=0.0,  # Deterministic, no randomness
                                    no_speech_threshold=0.6,  # Higher threshold to filter noise
                                    condition_on_previous_text=False,  # Don't use previous text as it can cause hallucinations
                                    beam_size=5,  # Larger beam for better quality
                                    best_of=5,  # More attempts for better quality
                                    fp16=self.using_cuda,
                                    suppress_blank=True,  # Suppress blank outputs
                                    suppress_tokens=[-1],  # Suppress special tokens
                                    without_timestamps=False,  # Keep timestamps for context
                                )
                                for audio in self.audio_buffer
                            ]

                            # Send transcriptions to queue
                            for i, result in enumerate(results):
                                transcribed_text = result["text"].strip()
                                if transcribed_text and self.transcription_queue:
                                    self.transcription_queue.put(transcribed_text)
                                    self.log.debug(
                                        f"Transcribed segment {i+1}: {transcribed_text}"
                                    )
                                else:
                                    self.log.debug(f"Segment {i+1}: No text or empty")

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
    import argparse

    parser = argparse.ArgumentParser(description="Whisper ASR Demo")
    parser.add_argument(
        "--mode",
        choices=["file", "mic", "both"],
        default="file",
        help="Test mode: file, mic, or both",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=5,
        help="Recording duration in seconds for mic mode",
    )
    parser.add_argument(
        "--model",
        default="base",
        help="Whisper model size (tiny, base, small, medium, large)",
    )
    parser.add_argument(
        "--cuda", action="store_true", help="Enable CUDA acceleration if available"
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream transcriptions in real-time as they arrive",
    )
    args = parser.parse_args()

    print("=== Whisper ASR Demo ===")
    print(f"Model: {args.model}, CUDA: {args.cuda}")

    # Test file transcription
    if args.mode in ["file", "both"]:
        print("\n--- File Transcription Test ---")
        asr = WhisperAsr(model_size=args.model, enable_cuda=args.cuda)
        try:
            test_file = "./data/audio/test.m4a"
            start_time = time.time()
            text = asr.transcribe_file(test_file)
            elapsed = time.time() - start_time
            print(f"Transcription: {text}")
            print(f"Time taken: {elapsed:.2f} seconds")
        except FileNotFoundError:
            print(f"No audio file found at {test_file}")

    # Test microphone transcription
    if args.mode in ["mic", "both"]:
        print("\n--- Microphone Transcription Test ---")
        print(f"Recording for {args.duration} seconds...")
        print(f"Mode: {'Streaming' if args.stream else 'Batch'}")

        # Create a queue to collect transcriptions
        transcription_queue = queue.Queue()
        asr = WhisperAsr(
            model_size=args.model,
            transcription_queue=transcription_queue,
            enable_cuda=args.cuda,
        )

        start_time = time.time()
        transcriptions = []

        if args.stream:
            # Streaming mode - show text as it arrives
            print("Starting recording threads...")
            asr.start_recording_streaming()  # Use streaming-specific method

            print("\n[STREAMING] Transcriptions as they arrive:")
            print("-" * 50)

            # Give recording a moment to start properly
            time.sleep(0.5)

            print(f"Recording status: {asr.is_recording}")
            print(f"Listening for {args.duration} seconds...")

            end_time = start_time + args.duration
            checks = 0

            try:
                while time.time() < end_time:
                    checks += 1
                    # Check for new transcriptions
                    while not transcription_queue.empty():
                        try:
                            text = transcription_queue.get_nowait()
                            if text:
                                transcriptions.append(text)
                                # Stream the text immediately with timestamp
                                time_offset = time.time() - start_time
                                print(f"[{time_offset:5.1f}s] {text}")
                        except queue.Empty:
                            break

                    # Debug: Show we're still checking
                    if checks % 20 == 0:  # Every second (20 * 0.05)
                        print(
                            f"  ... still listening (audio_queue size: ~{asr.audio_queue.qsize()})"
                        )

                    # Small sleep to prevent CPU spinning
                    time.sleep(0.05)

            finally:
                # Stop recording
                asr.stop_recording()

                # Collect any remaining transcriptions
                time.sleep(0.5)  # Give a moment for final processing
                while not transcription_queue.empty():
                    try:
                        text = transcription_queue.get_nowait()
                        if text:
                            transcriptions.append(text)
                            time_offset = time.time() - start_time
                            print(f"[{time_offset:5.1f}s] {text}")
                    except queue.Empty:
                        break

            print("-" * 50)

        else:
            # Batch mode - collect all text then display
            asr.start_recording(duration=args.duration)  # Blocking

            # Collect all transcriptions after recording
            while not transcription_queue.empty():
                try:
                    text = transcription_queue.get_nowait()
                    if text:
                        transcriptions.append(text)
                except queue.Empty:
                    break

        elapsed = time.time() - start_time

        # Display results
        print("\nResults:")
        if transcriptions:
            print(f"  Transcription segments: {len(transcriptions)}")
            if not args.stream:  # Show individual segments in batch mode
                for i, text in enumerate(transcriptions, 1):
                    print(f"    {i}. {text}")
            print(f"  Full transcript: {' '.join(transcriptions)}")
        else:
            print("  No transcriptions received (possibly no speech detected)")

        print(f"  Total time: {elapsed:.2f} seconds")
        print(f"  Processing efficiency: {args.duration/elapsed:.2f}x realtime")

    print("\nDemo completed!")
