# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import asyncio
import queue
import threading
import time

from gaia.logger import get_logger
from gaia.llm.llm_client import LLMClient


class AudioClient:
    """Handles all audio-related functionality including TTS, ASR, and voice chat."""

    def __init__(
        self,
        whisper_model_size="base",
        audio_device_index=None,  # Use default input device
        silence_threshold=0.5,
        enable_tts=True,
        logging_level="INFO",
        use_claude=False,
        use_chatgpt=False,
        system_prompt=None,
    ):
        self.log = get_logger(__name__)
        self.log.setLevel(getattr(__import__("logging"), logging_level))

        # Audio configuration
        self.whisper_model_size = whisper_model_size
        self.audio_device_index = audio_device_index
        self.silence_threshold = silence_threshold
        self.enable_tts = enable_tts

        # Audio state
        self.is_speaking = False
        self.tts_thread = None
        self.whisper_asr = None
        self.transcription_queue = queue.Queue()
        self.tts = None

        # Initialize LLM client (base_url handled automatically)
        self.llm_client = LLMClient(
            use_claude=use_claude,
            use_openai=use_chatgpt,  # LLMClient uses use_openai, not use_chatgpt
            system_prompt=system_prompt,
        )

        self.log.info("Audio client initialized.")

    async def start_voice_chat(self, message_processor_callback):
        """Start a voice-based chat session."""
        try:
            self.log.debug("Initializing voice chat...")
            print(
                "Starting voice chat.\n"
                "Say 'stop' to quit application "
                "or 'restart' to clear the chat history.\n"
                "Press Enter key to stop during audio playback."
            )

            # Initialize TTS before starting voice chat
            self.initialize_tts()

            from gaia.audio.whisper_asr import WhisperAsr

            # Create WhisperAsr with custom thresholds
            # Your audio shows energy levels of 0.02-0.03 when speaking
            self.whisper_asr = WhisperAsr(
                model_size=self.whisper_model_size,
                device_index=self.audio_device_index,
                transcription_queue=self.transcription_queue,
                silence_threshold=0.01,  # Set higher to ensure detection (your levels are 0.01-0.2+)
                min_audio_length=16000 * 1.0,  # 1 second minimum at 16kHz
            )

            # Log the thresholds being used (reduce verbosity)
            self.log.debug(
                f"Audio settings: SILENCE_THRESHOLD={self.whisper_asr.SILENCE_THRESHOLD}, "
                f"MIN_LENGTH={self.whisper_asr.MIN_AUDIO_LENGTH/self.whisper_asr.RATE:.1f}s"
            )

            device_name = self.whisper_asr.get_device_name()
            self.log.debug(f"Using audio device: {device_name}")

            # Start recording
            self.log.debug("Starting audio recording...")
            self.whisper_asr.start_recording()

            # Start the processing thread after recording is initialized
            self.log.debug("Starting audio processing thread...")
            process_thread = threading.Thread(
                target=self._process_audio_wrapper, args=(message_processor_callback,)
            )
            process_thread.daemon = True
            process_thread.start()

            # Keep the main thread alive while processing
            self.log.debug("Listening for voice input...")
            try:
                while True:
                    if not process_thread.is_alive():
                        self.log.debug("Process thread stopped unexpectedly")
                        break
                    if not self.whisper_asr or not self.whisper_asr.is_recording:
                        self.log.warning("Recording stopped unexpectedly")
                        break
                    await asyncio.sleep(0.1)

            except KeyboardInterrupt:
                self.log.info("Received keyboard interrupt")
                print("\nStopping voice chat...")
            except Exception as e:
                self.log.error(f"Error in main processing loop: {str(e)}")
                raise
            finally:
                if self.whisper_asr:
                    self.log.debug("Stopping recording...")
                    self.whisper_asr.stop_recording()
                    self.log.debug("Waiting for process thread to finish...")
                    process_thread.join(timeout=2.0)

        except ImportError:
            self.log.error(
                "WhisperAsr not found. Please install voice support with: pip install .[talk]"
            )
            raise
        except Exception as e:
            self.log.error(f"Failed to initialize voice chat: {str(e)}")
            raise
        finally:
            if self.whisper_asr:
                self.whisper_asr.stop_recording()
                self.log.info("Voice recording stopped")

    async def process_voice_input(self, text, get_stats_callback=None):
        """Process transcribed voice input and get AI response"""

        # Initialize TTS streaming
        text_queue = None
        tts_finished = threading.Event()  # Add event to track TTS completion
        interrupt_event = threading.Event()  # Add event for keyboard interrupts

        try:
            # Check if we're currently generating and halt if needed
            if self.llm_client.is_generating():
                self.log.debug("Generation in progress, halting...")
                if self.llm_client.halt_generation():
                    print("\nGeneration interrupted.")
                    await asyncio.sleep(0.5)

            # Pause audio recording before sending query
            if self.whisper_asr:
                self.whisper_asr.pause_recording()
                self.log.debug("Recording paused before generation")

            self.log.debug(f"Sending message to LLM: {text[:50]}...")
            print("\nGaia: ", end="", flush=True)

            # Keyboard listener thread for both generation and playback
            def keyboard_listener():
                input()  # Wait for any input

                # Use LLMClient to halt generation
                if self.llm_client.halt_generation():
                    print("\nGeneration interrupted.")
                else:
                    print("\nInterrupt requested.")

                interrupt_event.set()
                if text_queue:
                    text_queue.put("__HALT__")  # Signal TTS to stop immediately

            # Start keyboard listener thread
            keyboard_thread = threading.Thread(target=keyboard_listener)
            keyboard_thread.daemon = True
            keyboard_thread.start()

            if self.enable_tts:
                text_queue = queue.Queue(maxsize=100)

                # Define status callback to update speaking state
                def tts_status_callback(is_speaking):
                    self.is_speaking = is_speaking
                    if not is_speaking:  # When TTS finishes speaking
                        tts_finished.set()
                        if self.whisper_asr:
                            self.whisper_asr.resume_recording()
                    else:  # When TTS starts speaking
                        if self.whisper_asr:
                            self.whisper_asr.pause_recording()
                    self.log.debug(f"TTS speaking state: {is_speaking}")

                self.tts_thread = threading.Thread(
                    target=self.tts.generate_speech_streaming,
                    args=(text_queue,),
                    kwargs={
                        "status_callback": tts_status_callback,
                        "interrupt_event": interrupt_event,
                    },
                    daemon=True,
                )
                self.tts_thread.start()

            # Use LLMClient streaming instead of WebSocket
            accumulated_response = ""
            initial_buffer = ""  # Buffer for the start of response
            initial_buffer_sent = False

            try:
                # Start LLM generation with streaming
                response_stream = self.llm_client.generate(text, stream=True)

                # Process streaming response
                for chunk in response_stream:
                    if interrupt_event.is_set():
                        self.log.debug("Keyboard interrupt detected, stopping...")
                        if text_queue:
                            text_queue.put("__END__")
                        break

                    if self.transcription_queue.qsize() > 0:
                        self.log.debug(
                            "New input detected during generation, stopping..."
                        )
                        if text_queue:
                            text_queue.put("__END__")
                        # Use LLMClient to halt generation
                        if self.llm_client.halt_generation():
                            self.log.debug("Generation interrupted for new input.")
                        return

                    if chunk:
                        print(chunk, end="", flush=True)
                        if text_queue:
                            if not initial_buffer_sent:
                                initial_buffer += chunk
                                # Send if we've reached 20 chars or if we get a clear end marker
                                if len(initial_buffer) >= 20 or chunk.endswith(
                                    ("\n", ". ", "! ", "? ")
                                ):
                                    text_queue.put(initial_buffer)
                                    initial_buffer_sent = True
                            else:
                                text_queue.put(chunk)
                        accumulated_response += chunk

                # Send any remaining buffered content
                if text_queue:
                    if not initial_buffer_sent and initial_buffer:
                        # Small delay for very short responses
                        if len(initial_buffer) <= 20:
                            await asyncio.sleep(0.1)
                        text_queue.put(initial_buffer)
                    text_queue.put("__END__")

            except Exception as e:
                if text_queue:
                    text_queue.put("__END__")
                raise e
            finally:
                if self.tts_thread and self.tts_thread.is_alive():
                    self.tts_thread.join(timeout=1.0)  # Add timeout to thread join
                keyboard_thread.join(timeout=1.0)  # Add timeout to keyboard thread join

            print("\n")
            # Get performance stats from LLMClient
            if get_stats_callback:
                # First try the provided callback for backward compatibility
                stats = get_stats_callback()
            else:
                # Use LLMClient stats
                stats = self.llm_client.get_performance_stats()

            if stats:
                from pprint import pprint

                formatted_stats = {
                    k: round(v, 1) if isinstance(v, float) else v
                    for k, v in stats.items()
                }
                pprint(formatted_stats)

        except Exception as e:
            if text_queue:
                text_queue.put("__END__")
            raise e
        finally:
            if self.tts_thread and self.tts_thread.is_alive():
                # Wait for TTS to finish before resuming recording
                tts_finished.wait(timeout=2.0)  # Add reasonable timeout
                self.tts_thread.join(timeout=1.0)

            # Only resume recording after TTS is completely finished
            if self.whisper_asr:
                self.whisper_asr.resume_recording()

    def initialize_tts(self):
        """Initialize TTS if enabled."""
        if self.enable_tts:
            try:
                from gaia.audio.kokoro_tts import KokoroTTS

                self.tts = KokoroTTS()
                self.log.debug("TTS initialized successfully")
            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize TTS:\n{e}\nInstall talk dependencies with: pip install .[talk]\nYou can also use --no-tts option to disable TTS"
                )

    async def speak_text(self, text: str) -> None:
        """Speak text using initialized TTS, if available."""
        if not self.enable_tts:
            return
        if not getattr(self, "tts", None):
            self.log.debug("TTS is not initialized; skipping speak_text")
            return
        # Reuse the streaming path used in process_voice_input
        text_queue = queue.Queue(maxsize=100)
        interrupt_event = threading.Event()
        tts_thread = threading.Thread(
            target=self.tts.generate_speech_streaming,
            args=(text_queue,),
            kwargs={"interrupt_event": interrupt_event},
            daemon=True,
        )
        tts_thread.start()
        # Send the whole text and end
        text_queue.put(text)
        text_queue.put("__END__")
        tts_thread.join(timeout=5.0)

    def _process_audio_wrapper(self, message_processor_callback):
        """Wrapper method to process audio and handle transcriptions"""
        try:
            accumulated_text = []
            current_display = ""
            last_transcription_time = time.time()
            spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            dots_animation = ["   ", ".  ", ".. ", "..."]
            spinner_idx = 0
            dots_idx = 0
            animation_counter = 0
            self.is_speaking = False  # Initialize speaking state

            while self.whisper_asr and self.whisper_asr.is_recording:
                try:
                    text = self.transcription_queue.get(timeout=0.1)

                    current_time = time.time()
                    time_since_last = current_time - last_transcription_time
                    cleaned_text = text.lower().strip().rstrip(".!?")

                    # Handle special commands
                    if cleaned_text in ["stop"]:
                        print("\nStopping voice chat...")
                        self.whisper_asr.stop_recording()
                        break

                    # Update animations
                    spinner_idx = (spinner_idx + 1) % len(spinner_chars)
                    animation_counter += 1
                    if animation_counter % 4 == 0:  # Update dots every fourth cycle
                        dots_idx = (dots_idx + 1) % len(dots_animation)
                    spinner = spinner_chars[spinner_idx]
                    dots = dots_animation[dots_idx]

                    # Normal text processing - only if it's not a system message
                    if text != current_display:
                        # Clear the current line and display updated text with spinner
                        print(f"\r\033[K{spinner} {text}", end="", flush=True)
                        current_display = text

                        # Only add new text if it's significantly different
                        if not any(text in existing for existing in accumulated_text):
                            accumulated_text = [text]  # Replace instead of append
                            last_transcription_time = current_time

                    # Process accumulated text after silence threshold
                    if time_since_last > self.silence_threshold:
                        if accumulated_text:
                            complete_text = accumulated_text[
                                -1
                            ]  # Use only the last transcription
                            print()  # Add a newline before agent response
                            asyncio.run(message_processor_callback(complete_text))
                            accumulated_text = []
                            current_display = ""

                except queue.Empty:
                    # Update animations
                    spinner_idx = (spinner_idx + 1) % len(spinner_chars)
                    animation_counter += 1
                    if animation_counter % 4 == 0:
                        dots_idx = (dots_idx + 1) % len(dots_animation)
                    spinner = spinner_chars[spinner_idx]
                    dots = dots_animation[dots_idx]

                    if current_display:
                        print(
                            f"\r\033[K{spinner} {current_display}", end="", flush=True
                        )
                    else:
                        # Access the class-level speaking state
                        status = (
                            "Speaking"
                            if getattr(self, "is_speaking", False)
                            else "Listening"
                        )
                        print(f"\r\033[K{spinner} {status}{dots}", end="", flush=True)

                    if (
                        accumulated_text
                        and (time.time() - last_transcription_time)
                        > self.silence_threshold
                    ):
                        complete_text = accumulated_text[-1]
                        print()  # Add a newline before agent response
                        asyncio.run(message_processor_callback(complete_text))
                        accumulated_text = []
                        current_display = ""

        except Exception as e:
            self.log.error(f"Error in process_audio_wrapper: {str(e)}")
        finally:
            if self.whisper_asr:
                self.whisper_asr.stop_recording()
            if self.tts_thread and self.tts_thread.is_alive():
                self.tts_thread.join(timeout=1.0)  # Add timeout to thread join

    async def halt_generation(self):
        """Send a request to halt the current generation."""
        if self.llm_client.halt_generation():
            self.log.debug("Successfully halted generation via LLMClient")
            print("\nGeneration interrupted.")
        else:
            self.log.debug("Halt requested - generation will stop on next iteration")
            print("\nInterrupt requested.")
