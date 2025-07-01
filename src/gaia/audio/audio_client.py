# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import asyncio
import queue
import threading
import time
import aiohttp

from gaia.logger import get_logger


class AudioClient:
    """Handles all audio-related functionality including TTS, ASR, and voice chat."""

    def __init__(
        self,
        whisper_model_size="base",
        audio_device_index=1,
        silence_threshold=0.5,
        enable_tts=True,
        host="127.0.0.1",
        port=8001,
        llm_port=8000,
        agent_name="Chaty",
        logging_level="INFO",
    ):
        self.log = get_logger(__name__)
        self.log.setLevel(getattr(__import__("logging"), logging_level))

        # Audio configuration
        self.whisper_model_size = whisper_model_size
        self.audio_device_index = audio_device_index
        self.silence_threshold = silence_threshold
        self.enable_tts = enable_tts

        # Server configuration
        self.host = host
        self.port = port
        self.llm_port = llm_port
        self.agent_name = agent_name

        # Audio state
        self.is_speaking = False
        self.tts_thread = None
        self.whisper_asr = None
        self.transcription_queue = queue.Queue()
        self.tts = None

        self.log.info("Audio client initialized.")

    async def start_voice_chat(self, message_processor_callback):
        """Start a voice-based chat session."""
        try:
            self.log.debug("Initializing voice chat...")
            print(
                f"Starting voice chat with {self.agent_name}.\n"
                "Say 'stop' to quit application "
                "or 'restart' to clear the chat history.\n"
                "Press Enter key to stop during audio playback."
            )

            from gaia.audio.whisper_asr import WhisperAsr

            self.whisper_asr = WhisperAsr(
                model_size=self.whisper_model_size,
                device_index=self.audio_device_index,
                transcription_queue=self.transcription_queue,
            )

            device_name = self.whisper_asr.get_device_name()
            self.log.debug(f"Using audio device: {device_name}")

            # Start recording first
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
                        self.log.warning("Process thread stopped unexpectedly")
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
                    self.log.info("Stopping recording...")
                    self.whisper_asr.stop_recording()
                    self.log.info("Waiting for process thread to finish...")
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

        async with aiohttp.ClientSession() as session:
            # Initialize TTS streaming
            text_queue = None
            tts_finished = threading.Event()  # Add event to track TTS completion
            interrupt_event = threading.Event()  # Add event for keyboard interrupts

            try:
                # First check if we're currently generating
                async with session.get(
                    f"http://{self.host}:{self.llm_port}/generating"
                ) as response:
                    response_data = await response.json()
                    is_generating = response_data.get("is_generating", False)
                    self.log.debug(f"Generation status check: {is_generating}")

                    if is_generating:
                        # Send halt request
                        async with session.get(
                            f"http://{self.host}:{self.llm_port}/halt"
                        ) as halt_response:
                            if halt_response.status == 200:
                                self.log.debug("Successfully halted current generation")
                                print("\nGeneration interrupted.")
                                await asyncio.sleep(0.5)
                            else:
                                self.log.warning(
                                    f"Failed to halt generation: {halt_response.status}"
                                )

                # Pause audio recording before sending query
                if self.whisper_asr:
                    self.whisper_asr.pause_recording()
                    self.log.debug("Recording paused before generation")

                # Connect to websocket for new message
                ws = await session.ws_connect(f"ws://{self.host}:{self.port}/ws")
                self.log.debug(f"Sending message: {text[:50]}...")

                print(f"\n{self.agent_name}: ", end="", flush=True)
                await ws.send_str(text)

                # Keyboard listener thread for both generation and playback
                def keyboard_listener():
                    input()  # Wait for any input

                    # Send halt request when keyboard interrupt detected
                    async def halt():
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                f"http://{self.host}:{self.llm_port}/halt"
                            ) as response:
                                if response.status == 200:
                                    print("\nGeneration interrupted.")

                    asyncio.run(halt())
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

                accumulated_response = ""
                initial_buffer = ""  # Buffer for the start of response
                initial_buffer_sent = False
                try:
                    while True:
                        # Create a task for receiving the next message
                        receive_task = asyncio.create_task(ws.receive())
                        done, pending = await asyncio.wait([receive_task], timeout=0.1)

                        for task in pending:
                            task.cancel()

                        if interrupt_event.is_set():
                            self.log.debug(
                                "Keyboard interrupt detected, halting generation..."
                            )
                            if text_queue:
                                text_queue.put("__END__")
                            break

                        if self.transcription_queue.qsize() > 0:
                            self.log.debug(
                                "New input detected during generation, halting..."
                            )
                            if text_queue:
                                text_queue.put("__END__")
                            async with session.get(
                                f"http://{self.host}:{self.llm_port}/halt"
                            ) as halt_response:
                                if halt_response.status == 200:
                                    self.log.debug(
                                        "\nGeneration interrupted for new input."
                                    )
                                    return

                        if receive_task in done:
                            msg = await receive_task
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                if msg.data == "":
                                    if text_queue:
                                        # Send any buffered content at the end
                                        if not initial_buffer_sent and initial_buffer:
                                            # Small delay for very short responses
                                            if len(initial_buffer) <= 20:
                                                await asyncio.sleep(0.1)
                                            text_queue.put(initial_buffer)
                                        text_queue.put("__END__")
                                    break

                                print(msg.data, end="", flush=True)
                                if text_queue and msg.data:
                                    if not initial_buffer_sent:
                                        initial_buffer += msg.data
                                        # Send if we've reached 20 chars or if we get a clear end marker
                                        if len(
                                            initial_buffer
                                        ) >= 20 or msg.data.endswith(
                                            ("\n", ". ", "! ", "? ")
                                        ):
                                            text_queue.put(initial_buffer)
                                            initial_buffer_sent = True
                                    else:
                                        text_queue.put(msg.data)
                                accumulated_response += msg.data
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                if text_queue:
                                    if not initial_buffer_sent and initial_buffer:
                                        # Small delay for very short responses
                                        if len(initial_buffer) <= 20:
                                            await asyncio.sleep(0.1)
                                        text_queue.put(initial_buffer)
                                    text_queue.put("__END__")
                                break

                except Exception as e:
                    if text_queue:
                        text_queue.put("__END__")
                    raise e
                finally:
                    if "ws" in locals():
                        await ws.close()
                    if self.tts_thread and self.tts_thread.is_alive():
                        self.tts_thread.join(timeout=1.0)  # Add timeout to thread join
                    keyboard_thread.join(
                        timeout=1.0
                    )  # Add timeout to keyboard thread join

                print("\n")
                if get_stats_callback:
                    stats = get_stats_callback()
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

                if "ws" in locals():
                    await ws.close()

    def initialize_tts(self):
        """Initialize TTS if enabled."""
        if self.enable_tts:
            try:
                from gaia.audio.kokoro_tts import KokoroTTS

                self.tts = KokoroTTS()
                self.log.debug("TTS initialized successfully")
            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize TTS:\n{e}\nYou can also use --no-tts option to disable TTS"
                )

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
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://{self.host}:{self.llm_port}/halt"
                ) as response:
                    if response.status == 200:
                        self.log.debug("Successfully halted generation")
                        print("\nGeneration interrupted.")
                    else:
                        self.log.warning(
                            f"Failed to halt generation: {response.status}"
                        )
        except Exception as e:
            self.log.error(f"Error halting generation: {e}")
