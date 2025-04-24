# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import sys
import argparse
import time
import json
import socket
import asyncio
import threading
import queue
import logging
import multiprocessing
import subprocess
from pathlib import Path
from pprint import pprint

import requests
import psutil
import aiohttp
from aiohttp import ClientTimeout
from requests.exceptions import RequestException

from gaia.logger import get_logger
from gaia.llm.lemonade_server import launch_lemonade_server
from gaia.agents.agent import launch_agent_server
from gaia.version import version

try:
    from gaia.llm.ollama_server import (
        launch_ollama_client_server,
        launch_ollama_model_server,
    )

    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    launch_ollama_client_server = None
    launch_ollama_model_server = None


# Set debug level for the logger
logging.getLogger("gaia").setLevel(logging.INFO)

# Add the parent directory to sys.path to import gaia modules
current_dir = Path(__file__).resolve().parent
parent_dir = current_dir.parent.parent.parent
sys.path.append(str(parent_dir))


class GaiaCliClient:
    log = get_logger(__name__)

    def __init__(
        self,
        agent_name="Chaty",
        host="127.0.0.1",
        port=8001,
        model="llama3.2:1b",
        max_new_tokens=512,
        backend="ollama",
        device="cpu",
        dtype="int4",
        enable_agent_server=True,
        whisper_model_size="base",
        audio_device_index=1,
        silence_threshold=0.5,
        show_stats=False,
        enable_tts=True,
        logging_level="INFO",
        input_file=None,
    ):
        self.log = self.__class__.log  # Use the class-level logger for instances
        # Set the logging level for this instance's logger
        self.log.setLevel(getattr(logging, logging_level))

        # Add is_speaking attribute initialization
        self.is_speaking = False
        self.tts_thread = None  # Initialize tts_thread as None

        self.agent_name = agent_name
        self.enable_agent_server = enable_agent_server
        self.host = host
        self.port = port
        self.llm_port = 8000
        self.ollama_port = 11434
        self.agent_url = f"http://{host}:{port}"
        self.llm_url = f"http://{host}:{self.llm_port}"
        self.model = model
        self.max_new_tokens = max_new_tokens
        self.backend = backend
        self.device = device
        self.dtype = dtype
        self.agent_server = None
        self.llm_server = None
        self.ollama_model_server = None
        self.ollama_client_server = None
        self.cli_mode = True  # Set this to True for CLI mode
        self.server_pids = {}
        self.whisper_asr = None
        self.whisper_model_size = whisper_model_size
        self.audio_device_index = audio_device_index
        self.transcription_queue = queue.Queue()
        self.silence_threshold = silence_threshold
        self.show_stats = show_stats
        self.enable_tts = enable_tts
        self.tts = None
        self.input_file = input_file

        self.log.info("Gaia CLI client initialized.")
        self.log.debug(
            f"agent_name: {self.agent_name}\n host: {self.host}\n"
            f"port: {self.port}\n llm_port: {self.llm_port}\n"
            f"ollama_port: {self.ollama_port}\n model: {self.model}\n"
            f"max_new_tokens: {self.max_new_tokens}\n backend: {self.backend}\n"
            f"device: {self.device}\n dtype: {self.dtype}"
        )

    def start(self):
        self.log.info(f"Starting GAIA {version}")
        self.log.info("Checking ports availability...")
        # Check if required ports are available
        ports_to_check = [
            (self.port, "Agent server"),
            (self.llm_port, "LLM server"),
        ]

        # Check ports before starting any processes
        for port, service_name in ports_to_check:
            if not self.is_port_available(port):
                self.log.error(
                    f"Port {port} required for {service_name} is already in use. "
                )
                # Attempt to kill process using the port before raising error
                kill_result = kill_process_by_port(port)
                if kill_result["success"]:
                    self.log.info(f"{kill_result['message']}. Retrying port check...")
                    # Verify port is now available
                    if not self.is_port_available(port):
                        self.log.error(
                            f"Port {port} is still in use after kill attempt"
                        )
                        raise RuntimeError(
                            f"Port {port} required for {service_name} is already in use. "
                            "Please ensure all required ports are available before starting servers."
                        )
                    else:
                        self.log.info(f"Port {port} for {service_name} is available.")
                        return {
                            "success": True,
                            "message": f"Port {port} for {service_name} is available.",
                        }
            else:
                self.log.info(f"Port {port} for {service_name} is available.")

        try:
            self.log.info("Starting servers...")
            # Start servers and wait briefly to catch any immediate startup errors
            if self.enable_agent_server:
                self.start_agent_server()
                time.sleep(2)  # Wait to catch potential port binding errors
                if not self.agent_server.is_alive():
                    raise RuntimeError("Agent server failed to start")

            if self.backend == "ollama":
                self.start_ollama_servers()
                time.sleep(2)  # Wait to catch potential port binding errors
                if self.ollama_model_server and not self.ollama_model_server.is_alive():
                    raise RuntimeError("Ollama model server failed to start")
                if (
                    self.ollama_client_server
                    and not self.ollama_client_server.is_alive()
                ):
                    raise RuntimeError("Ollama client server failed to start")
            else:
                self.start_llm_server()
                time.sleep(2)  # Wait to catch potential port binding errors
                if not self.llm_server.is_alive():
                    raise RuntimeError("LLM server failed to start")

            self.log.info("Waiting for servers to start...")
            self.wait_for_servers()

            # Save server information
            self.save_server_info()
        except Exception as e:
            self.stop()  # Clean up any started processes
            raise RuntimeError(f"Failed to start servers: {str(e)}")

    def wait_for_servers(self, server_ready_timeout=120, check_interval=5):
        """Wait for servers to be ready with extended timeout for RAG index building"""
        self.log.info("Waiting for model downloads and servers to be ready...")

        # First, wait for model downloads to complete
        start_time = time.time()
        time.sleep(10)
        # Modified to wait indefinitely for downloads to complete
        while True:
            if not self.check_models_downloading():
                self.log.info("Model downloads completed.")
                break
            elapsed_time = int(time.time() - start_time)
            self.log.info(
                f"Models are still downloading (elapsed: {elapsed_time}s). Continuing to wait..."
            )
            time.sleep(check_interval)

        # Then, check for server readiness
        start_time = time.time()
        last_status_time = 0
        status_interval = 10  # Print status every 10 seconds

        # Use longer timeout for RAG agents
        if self.agent_name.lower() == "rag":
            self.log.info(
                "RAG agent detected - using extended timeout for index building..."
            )
            server_ready_timeout = 1800  # 30 minutes for RAG

        while time.time() - start_time < server_ready_timeout:
            current_time = time.time()

            if self.check_servers_ready():
                self.log.info("All servers are ready.")
                return

            # Print status update periodically
            if current_time - last_status_time >= status_interval:
                elapsed = int(current_time - start_time)
                remaining = server_ready_timeout - elapsed
                self.log.info(
                    f"Waiting for servers... {elapsed}s elapsed, {remaining}s remaining"
                )
                last_status_time = current_time

            time.sleep(check_interval)

        error_message = (
            f"Servers failed to start within {server_ready_timeout} seconds."
        )
        self.log.error(error_message)
        raise TimeoutError(error_message)

    def check_models_downloading(self):
        # Implement this method to check if any server is still downloading models
        # Return True if any server is downloading, False otherwise
        if self.backend == "ollama":
            try:
                response = requests.get(
                    f"http://localhost:{self.llm_port}/health", timeout=5
                )
                self.log.info(response.json())
                if response.status_code == 200:
                    data = response.json()
                    return data.get("status") == "downloading"
            except requests.RequestException:
                self.log.warning("Failed to check model download status.")
                return True  # Assume model is still downloading
        return False

    def check_servers_ready(self):
        servers_to_check = [
            (f"http://{self.host}:{self.port}/health", "Agent server"),
            (f"http://localhost:{self.llm_port}/health", "LLM client server"),
        ]

        if self.backend == "ollama":
            if OLLAMA_AVAILABLE:
                servers_to_check.extend(
                    [
                        (
                            f"http://localhost:{self.ollama_port}/api/version",
                            "Ollama model server",
                        ),
                    ]
                )
            else:
                self.log.warning("Ollama backend selected but Ollama is not available.")

        for url, server_name in servers_to_check:
            try:
                response = requests.get(url, timeout=5)
                if response.status_code != 200:
                    self.log.warning(
                        f"{server_name} not ready. Status code: {response.status_code}"
                    )
                    return False

            except RequestException as e:
                self.log.warning(f"Failed to connect to {server_name}: {str(e)}")
                return False

        self.log.info("All servers are ready.")
        return True

    def check_ollama_servers_ready(self):
        """Check if the Ollama model server and client server are ready to accept requests."""
        return self.is_server_available(
            "localhost", self.ollama_port
        ) and self.is_server_available("localhost", self.llm_port)

    def check_llm_server_ready(self):
        """Check if the LLM server is ready to accept requests."""
        return self.is_server_available("localhost", self.llm_port)

    def is_server_available(self, host, port):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((host, port))
            s.close()
            return True
        except (ConnectionRefusedError, TimeoutError):
            return False

    def save_server_info(self):
        server_info = {
            "agent_name": self.agent_name,
            "host": self.host,
            "port": self.port,
            "model": self.model,
            "max_new_tokens": self.max_new_tokens,
            "backend": self.backend,
            "device": self.device,
            "dtype": self.dtype,
            "server_pids": self.server_pids,
            "input_file": self.input_file,  # Save input file info
        }
        with open(".gaia_servers.json", "w", encoding="utf-8") as f:
            json.dump(server_info, f)

    def start_agent_server(self):
        try:
            self.log.info(f"Starting {self.agent_name} server...")
            kwargs = {
                "agent_name": self.agent_name,
                "host": self.host,
                "port": self.port,
                "model": self.model,
                "cli_mode": self.cli_mode,
            }

            # Add input_file for RAG agent
            if self.agent_name.lower() == "rag" and self.input_file:
                kwargs["input_file"] = self.input_file
                self.log.info(
                    f"Initializing RAG agent with input file: {self.input_file}"
                )

            self.agent_server = multiprocessing.Process(
                target=launch_agent_server,
                kwargs=kwargs,
            )
            self.agent_server.start()

            # Add timeout for startup verification
            start_time = time.time()
            while time.time() - start_time < 30:  # 30 second timeout
                if self.agent_server.is_alive():
                    self.server_pids["agent"] = self.agent_server.pid
                    self.log.debug(f"agent_server.pid: {self.agent_server.pid}")
                    return
                time.sleep(0.5)
            raise RuntimeError("Agent server failed to start within timeout period")

        except Exception as e:
            self.log.error(f"Failed to start agent server: {str(e)}")
            raise

    def start_ollama_servers(self):
        if not OLLAMA_AVAILABLE:
            self.log.warning("Ollama is not available. Skipping Ollama server startup.")
            return

        self.log.info("Starting Ollama servers...")
        self.ollama_model_server = multiprocessing.Process(
            target=launch_ollama_model_server,
            kwargs={
                "host": "http://localhost",
                "port": self.ollama_port,
                "cli_mode": self.cli_mode,
            },
        )
        self.ollama_model_server.start()
        self.server_pids["ollama_model"] = self.ollama_model_server.pid
        self.log.debug(f"ollama_model_server.pid: {self.ollama_model_server.pid}")

        self.ollama_client_server = multiprocessing.Process(
            target=launch_ollama_client_server,
            kwargs={
                "model": self.model,
                "host": "http://localhost",
                "port": self.llm_port,
                "cli_mode": self.cli_mode,
            },
        )
        self.ollama_client_server.start()
        self.server_pids["ollama_client"] = self.ollama_client_server.pid
        self.log.debug(f"ollama_client_server.pid: {self.ollama_client_server.pid}")

    def start_llm_server(self):
        self.log.info("Starting LLM server...")
        llm_server_kwargs = {
            "backend": self.backend,
            "checkpoint": self.model,
            "max_new_tokens": self.max_new_tokens,
            "device": self.device,
            "dtype": self.dtype,
            "cli_mode": self.cli_mode,
        }
        self.llm_server = multiprocessing.Process(
            target=launch_lemonade_server, kwargs=llm_server_kwargs
        )
        self.llm_server.start()
        self.server_pids["llm"] = self.llm_server.pid
        self.log.debug(f"llm_server.pid: {self.llm_server.pid}")

    async def send_message(self, message):
        url = f"{self.agent_url}/prompt"
        data = {"prompt": message}
        try:
            async with aiohttp.ClientSession(
                timeout=ClientTimeout(total=3600)
            ) as session:
                async with session.post(url, json=data) as response:
                    if response.status == 200:
                        async for chunk in response.content.iter_any():
                            chunk = chunk.decode("utf-8")
                            print(chunk, end="", flush=True)
                            yield chunk
                    else:
                        error_text = await response.text()
                        error_message = f"Error: {response.status} - {error_text}"
                        print(error_message)
                        yield error_message
        except aiohttp.ClientError as e:
            error_message = f"Error: {str(e)}"
            self.log.error(error_message)
            yield error_message

    def get_stats(self):
        url = f"{self.llm_url}/stats"
        try:
            response = requests.get(url, timeout=10)
            self.log.debug(f"{url}: {response.json()}")
            if response.status_code == 200:
                try:
                    stats = response.json()
                    self.log.debug(f"Stats received: {stats}")
                    return stats
                except json.JSONDecodeError as je:
                    self.log.error(f"Failed to parse JSON response: {response.text}")
                    self.log.error(f"JSON decode error: {str(je)}")
                    return None
            else:
                self.log.error(
                    f"Failed to get stats. Status code: {response.status_code}"
                )
                return None
        except requests.RequestException as e:
            self.log.error(f"Error while fetching stats: {str(e)}")
            return None

    def restart_chat(self):
        url = f"{self.agent_url}/restart"
        response = requests.post(url)
        if response.status_code == 200:
            return "Chat restarted successfully."
        else:
            return f"Error restarting chat: {response.status_code} - {response.text}"

    def stop(self):
        self.log.info("Stopping servers...")

        # First attempt to kill processes by their stored PIDs
        for server_name, pid in self.server_pids.items():
            self.log.info(f"Stopping {server_name} server (PID: {pid})...")
            try:
                process = psutil.Process(pid)
                process.terminate()
                process.wait(timeout=10)
            except psutil.NoSuchProcess:
                self.log.info(
                    f"{server_name} server process not found. It may have already terminated."
                )
            except psutil.TimeoutExpired:
                self.log.warning(
                    f"{server_name} server did not terminate gracefully. Forcing termination..."
                )
                process.kill()
            except Exception as e:
                self.log.error(f"Error stopping {server_name} server: {str(e)}")

        # Additional cleanup for any zombie processes on known ports
        ports_to_check = [8000, 8001, 11434]  # LLM server, Agent server, Ollama ports
        for port in ports_to_check:
            try:
                result = kill_process_by_port(port)
                if result["success"]:
                    self.log.info(result["message"])
                else:
                    self.log.debug(result["message"])
            except Exception as e:
                self.log.error(f"Error cleaning up port {port}: {str(e)}")

        # Additional cleanup to ensure all child processes are terminated
        for pid in self.server_pids.values():
            try:
                parent = psutil.Process(pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        continue
                psutil.wait_procs(children, timeout=5)
                for child in children:
                    if child.is_running():
                        child.kill()
            except psutil.NoSuchProcess:
                pass

        self.log.info("All servers stopped.")

    async def start_voice_chat(self):
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
            process_thread = threading.Thread(target=self.process_audio_wrapper)
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

    async def process_voice_input(self, text):
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
                if hasattr(self, "show_stats") and self.show_stats:
                    stats = self.get_stats()
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

    async def chat(self):
        """Text-based chat interface"""
        print(
            f"Starting text chat with {self.agent_name}.\n"
            "Type 'stop' to quit or 'restart' to clear chat history."
        )
        while True:
            user_input = input("You: ").strip()
            if user_input.lower().rstrip(".") == "stop":
                break
            elif user_input.lower().rstrip(".") == "restart":
                print(await self.restart_chat())
            else:
                print(f"{self.agent_name}:", end=" ", flush=True)
                async for _ in self.send_message(user_input):
                    pass
                print()

    async def talk(self):
        """Voice-based chat interface"""
        if self.enable_tts:
            try:
                from gaia.audio.kokoro_tts import KokoroTTS

                self.tts = KokoroTTS()
                self.log.debug("TTS initialized successfully")
            except Exception as e:
                raise RuntimeError(
                    f"Failed to initialize TTS:\n{e}\nYou can also use --no-tts option to disable TTS"
                )
        await self.start_voice_chat()

    async def prompt(self, message):
        async for chunk in self.send_message(message):
            yield chunk

    @classmethod
    async def load_existing_client(cls):
        json_path = Path(".gaia_servers.json")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                server_info = json.load(f)
            if server_info:
                client = cls(
                    **{k: v for k, v in server_info.items() if k != "server_pids"}
                )
                client.server_pids = server_info.get("server_pids", {})
                return client
            return None
        except FileNotFoundError:
            cls.log.error(f"Server information file ({json_path}) not found.")
            return None
        except json.JSONDecodeError:
            cls.log.error(f"Server information file ({json_path}) is corrupted.")
            return None

    def process_audio_wrapper(self):
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
                            asyncio.run(self.process_voice_input(complete_text))
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
                        asyncio.run(self.process_voice_input(complete_text))
                        accumulated_text = []
                        current_display = ""

        except Exception as e:
            self.log.error(f"Error in process_audio_wrapper: {str(e)}")
        finally:
            if self.whisper_asr:
                self.whisper_asr.stop_recording()
            if self.tts_thread and self.tts_thread.is_alive():
                self.tts_thread.join(timeout=1.0)  # Add timeout to thread join

    def is_port_available(self, port):
        """Check if a port is available for use."""
        sock = None
        try:
            # Create a TCP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Enable reuse of the address/port
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Try to bind to the port on localhost
            sock.bind(("127.0.0.1", port))
            # Try to listen on the port
            sock.listen(1)
            return True
        except (socket.error, OSError):
            self.log.warning(f"Port {port} is already in use")
            return False
        finally:
            if sock:
                sock.close()


async def async_main(action, **kwargs):
    log = get_logger(__name__)
    if action == "start":
        show_stats = kwargs.pop("stats", False)
        launch_in_background = kwargs.pop("background", "silent")
        logging_level = kwargs.pop("logging_level", "INFO")  # Pop instead of get

        # Set parameters based on GAIA_MODE environment variable if not explicitly overridden
        gaia_mode = os.environ.get("GAIA_MODE", "").strip().upper()
        if gaia_mode and not (
            kwargs.get("hybrid", False) or kwargs.get("generic", False)
        ):
            if gaia_mode == "HYBRID":
                # Set optimal hybrid mode configuration
                kwargs["model"] = (
                    "amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid"
                )
                kwargs["backend"] = "oga"
                kwargs["device"] = "hybrid"
                kwargs["dtype"] = "int4"
                print(
                    f"Using optimal hybrid mode configuration from GAIA_MODE={gaia_mode}."
                )
            elif gaia_mode == "GENERIC":
                # Set optimal generic mode configuration
                kwargs["model"] = "llama3.2:1b"
                kwargs["backend"] = "ollama"
                kwargs["device"] = "cpu"
                kwargs["dtype"] = "int4"
                print(
                    f"Using optimal generic mode configuration from GAIA_MODE={gaia_mode}."
                )
            elif gaia_mode == "NPU":
                # Set optimal NPU mode configuration
                kwargs["model"] = "Llama-3.2-1B-Instruct-NPU"
                kwargs["backend"] = "oga"
                kwargs["device"] = "npu"
                kwargs["dtype"] = "int4"
                print(
                    f"Using optimal NPU mode configuration from GAIA_MODE={gaia_mode}."
                )

        # Handle hybrid mode shortcut (command-line flag takes precedence over env var)
        if kwargs.pop("hybrid", False):
            # Set optimal hybrid mode configuration
            kwargs["model"] = (
                "amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid"
            )
            kwargs["backend"] = "oga"
            kwargs["device"] = "hybrid"
            kwargs["dtype"] = "int4"
            print("Using optimal hybrid mode configuration.")

        # Handle generic mode shortcut
        if kwargs.pop("generic", False):
            # Set optimal generic mode configuration
            kwargs["model"] = "llama3.2:1b"
            kwargs["backend"] = "ollama"
            kwargs["device"] = "cpu"
            kwargs["dtype"] = "int4"
            print("Using optimal generic mode configuration.")

        # Build command with all parameters
        cmd_params = []
        for key, value in kwargs.items():
            if value is not None:
                # Convert underscores to hyphens in parameter names
                param_name = key.replace("_", "-")
                if isinstance(value, bool):
                    if value:
                        cmd_params.append(f"--{param_name}")
                else:
                    cmd_params.append(f"--{param_name} {value}")

        base_cmd = "gaia-cli start --background none " + " ".join(cmd_params)

        def wait_for_servers_file_exists(timeout=30):
            start_time = time.time()
            while not Path(".gaia_servers.json").exists():
                if time.time() - start_time > timeout:
                    raise RuntimeError(
                        "Timeout waiting for servers to start. Check "
                        + str(Path.cwd() / "gaia.cli.log for details.")
                    )
                time.sleep(1)

        # Use longer timeout for RAG agents
        server_timeout = 1800 if kwargs.get("agent_name", "").lower() == "rag" else 120

        if launch_in_background == "terminal":
            print("Starting Gaia servers in background terminal...")
            print(
                "Note: A new terminal window will open to handle the server processes."
            )

            cmd = f'start cmd /k "echo Starting GAIA servers... Feel free to minimize this window. && {base_cmd}"'
            try:
                subprocess.Popen(cmd, shell=True)
                wait_for_servers_file_exists(timeout=server_timeout)
                print("✓ Servers launched in background terminal")
                print("\nYou can now:")
                print(
                    "  1. Use 'gaia-cli chat' or 'gaia-cli talk' in this terminal to interact"
                )
                print(
                    "  2. Use 'gaia-cli stop' when you're done to shut down the servers"
                )
                return
            except Exception as e:
                log.error(f"Failed to start in background: {e}")
                raise RuntimeError(f"Failed to start in background: {e}")

        elif launch_in_background == "silent":
            print("Starting Gaia servers in background...")
            print(
                "Note: Server output will be redirected to "
                + str(Path.cwd() / "gaia.cli.log.")
            )

            with open("gaia.cli.log", "w", encoding="utf-8") as log_file:
                # Split the command into a list for Popen
                cmd_list = ["gaia-cli", "start", "--background", "none"] + [
                    param for param in " ".join(cmd_params).split()
                ]
                subprocess.Popen(
                    cmd_list,
                    stdout=log_file,
                    stderr=log_file,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                wait_for_servers_file_exists(timeout=server_timeout)
            print("✓ Servers launched in background silently")
            print("\nYou can now:")
            print("  1. Use 'gaia-cli chat' or 'gaia-cli talk' to interact")
            print("  2. Use 'gaia-cli stop' when you're done to shut down the servers")
            print("  3. Check gaia.cli.log for server output")
            return
        elif launch_in_background == "none":
            client = GaiaCliClient(
                show_stats=show_stats,
                logging_level=logging_level,
                **kwargs,
            )
            client.start()
            log.info("Servers started successfully.")
            return
        else:
            log.error(f"Invalid background option: {launch_in_background}")
            raise ValueError(f"Invalid background option: {launch_in_background}")

    elif action == "stop":
        client = await GaiaCliClient.load_existing_client()
        if client:
            client.stop()
            Path(".gaia_servers.json").unlink(missing_ok=True)
            log.info("Servers stopped successfully.")
            return
        else:
            log.error("No running servers found.")
            return

    # For all other actions, load existing client
    client = await GaiaCliClient.load_existing_client()
    if not client:
        log.error(
            "Servers are not running. Please start the servers first using 'gaia-cli start'"
        )
        raise RuntimeError(
            "Servers are not running. Please start the servers first using 'gaia-cli start'"
        )

    if action == "prompt":
        if not kwargs.get("message"):
            log.error("Message is required for prompt action.")
            raise ValueError("Message is required for prompt action.")
        response = ""
        async for chunk in client.prompt(kwargs["message"]):
            response += chunk
        if kwargs.get("show_stats", False):
            stats = client.get_stats()
            if stats:
                return {"response": response, "stats": stats}
        return {"response": response}
    elif action == "chat":
        await client.chat()
        log.info("Chat session ended.")
        return
    elif action == "talk":
        await client.talk()
        log.info("Voice chat session ended.")
        return
    elif action == "stats":
        stats = client.get_stats()
        if stats:
            return {"stats": stats}
        log.error("No stats available.")
        raise RuntimeError("No stats available.")
    else:
        log.error(f"Unknown action specified: {action}")
        raise ValueError(f"Unknown action specified: {action}")


def run_cli(action, **kwargs):
    return asyncio.run(async_main(action, **kwargs))


def check_gaia_mode():
    """Check if GAIA_MODE environment variable is set and valid.

    Returns:
        str or None: The value of GAIA_MODE if set and valid, None otherwise
    """
    gaia_mode = os.environ.get("GAIA_MODE").strip()
    if not gaia_mode:
        return None

    # Validate that it's one of the expected values (case insensitive)
    valid_modes = ["HYBRID", "GENERIC", "NPU"]
    if gaia_mode.upper() not in valid_modes:
        print(
            f"WARNING: GAIA_MODE value '{gaia_mode}' is not one of the expected values: {', '.join(valid_modes)}"
        )
        print("GAIA may not function correctly with this configuration.")

    return gaia_mode


def main():
    # Check if GAIA_MODE is set
    gaia_mode = check_gaia_mode()
    if not gaia_mode:
        print("ERROR: GAIA_MODE environment variable is not set.")
        print("Please run one of the following scripts before using gaia-cli:")
        print("  set_hybrid_mode.bat")
        print("  set_generic_mode.bat")
        print("  set_npu_mode.bat")
        sys.exit(1)

    # Create the main parser
    parser = argparse.ArgumentParser(
        description=f"Gaia CLI - Interact with Gaia AI agents. \n{version}",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # Get logger instance
    log = get_logger(__name__)

    # Add version argument
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"{version}",
        help="Show program's version number and exit",
    )

    # Create a parent parser for common arguments
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--logging-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set the logging level (default: INFO)",
    )

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")

    # Core Gaia CLI commands - add parent_parser to each subcommand
    start_parser = subparsers.add_parser(
        "start", help="Start Gaia server", parents=[parent_parser]
    )
    start_parser.add_argument(
        "--agent-name",
        default="Chaty",
        help="Name of the Gaia agent to use (e.g., Llm, Chaty, Joker, Clip, Rag, etc.)",
    )
    start_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address for the Agent server (default: 127.0.0.1)",
    )
    start_parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Port for the Agent server (default: 8001)",
    )
    start_parser.add_argument(
        "--model",
        default="llama3.2:1b",
        help="Model to use for the agent (default: llama3.2:1b)",
    )
    start_parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=512,
        help="Maximum number of new tokens to generate (default: 512)",
    )
    start_parser.add_argument(
        "--backend",
        default="ollama",
        choices=["oga", "hf", "ollama"],
        help="Backend to use for model inference (default: ollama)",
    )
    start_parser.add_argument(
        "--device",
        default="cpu",
        choices=["cpu", "npu", "gpu", "hybrid"],
        help="Device to use for model inference (default: cpu)",
    )
    start_parser.add_argument(
        "--dtype",
        default="int4",
        choices=["float32", "float16", "bfloat16", "int8", "int4"],
        help="Data type to use for model inference (default: int4)",
    )
    start_parser.add_argument(
        "--stats",
        action="store_true",
        help="Show performance statistics after generation",
    )
    start_parser.add_argument(
        "--background",
        choices=["terminal", "silent", "none"],
        default="silent",
        help="Launch servers in a background terminal window or silently",
    )
    start_parser.add_argument(
        "--input-file", help="Input file path for RAG index creation"
    )
    start_parser.add_argument(
        "--hybrid",
        action="store_true",
        help="Shortcut for optimal hybrid mode configuration (sets --model, --backend, --device, and --dtype)",
    )
    start_parser.add_argument(
        "--generic",
        action="store_true",
        help="Shortcut for optimal generic mode configuration (sets --model, --backend, --device, and --dtype)",
    )

    subparsers.add_parser("stop", help="Stop Gaia server", parents=[parent_parser])

    # Add prompt-specific options
    prompt_parser = subparsers.add_parser(
        "prompt", help="Send a single prompt to Gaia", parents=[parent_parser]
    )
    prompt_parser.add_argument(
        "message",
        help="Message to send to Gaia",
    )

    subparsers.add_parser(
        "chat", help="Start text conversation with Gaia", parents=[parent_parser]
    )

    talk_parser = subparsers.add_parser(
        "talk", help="Start voice conversation with Gaia", parents=[parent_parser]
    )
    talk_parser.add_argument(
        "--no-tts",
        action="store_true",
        help="Disable text-to-speech in voice chat mode",
    )
    talk_parser.add_argument(
        "--audio-device-index",
        type=int,
        default=1,
        help="Index of the audio input device to use",
    )
    talk_parser.add_argument(
        "--whisper-model-size",
        type=str,
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Size of the Whisper model to use (default: base)",
    )

    subparsers.add_parser(
        "stats",
        help="Show Gaia statistics from the most recent run.",
        parents=[parent_parser],
    )

    # Add utility commands to main parser instead of creating a separate parser
    test_parser = subparsers.add_parser(
        "test", help="Run various tests", parents=[parent_parser]
    )
    test_parser.add_argument(
        "--test-type",
        required=True,
        choices=[
            "tts-preprocessing",
            "tts-streaming",
            "tts-audio-file",
            "asr-file-transcription",
            "asr-microphone",
            "asr-list-audio-devices",
        ],
        help="Type of test to run",
    )
    test_parser.add_argument(
        "--test-text",
        help="Text to use for TTS tests",
    )
    test_parser.add_argument(
        "--input-audio-file",
        help="Input audio file path for ASR file transcription test",
    )
    test_parser.add_argument(
        "--output-audio-file",
        default="output.wav",
        help="Output file path for TTS audio file test (default: output.wav)",
    )
    test_parser.add_argument(
        "--recording-duration",
        type=int,
        default=10,
        help="Recording duration in seconds for ASR microphone test (default: 10)",
    )
    test_parser.add_argument(
        "--whisper-model-size",
        type=str,
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Size of the Whisper model to use (default: base)",
    )
    test_parser.add_argument(
        "--audio-device-index",
        type=int,
        default=1,
        help="Index of audio input device (optional)",
    )

    # Add YouTube-specific options
    yt_parser = subparsers.add_parser(
        "youtube", help="YouTube utilities", parents=[parent_parser]
    )
    yt_parser.add_argument(
        "--download-transcript",
        metavar="URL",
        help="Download transcript from a YouTube URL",
    )
    yt_parser.add_argument(
        "--output-path",
        help="Output file path for transcript (optional, default: transcript_<video_id>.txt)",
    )

    # Add new subparser for kill command
    kill_parser = subparsers.add_parser(
        "kill", help="Kill process running on specific port", parents=[parent_parser]
    )
    kill_parser.add_argument(
        "--port", type=int, required=True, help="Port number to kill process on"
    )

    args = parser.parse_args()

    # Check if action is specified
    if not args.action:
        log.warning("No action specified. Displaying help message.")
        parser.print_help()
        return

    # Set logging level using the GaiaLogger manager
    from gaia.logger import log_manager

    log_manager.set_level("gaia", getattr(logging, args.logging_level))
    log.info(f"Starting Gaia CLI with action: {args.action}")

    # Handle core Gaia CLI commands
    if args.action in ["start", "stop", "prompt", "chat", "talk", "stats"]:
        kwargs = {
            k: v for k, v in vars(args).items() if v is not None and k != "action"
        }
        log.debug(f"Executing {args.action} with parameters: {kwargs}")
        result = run_cli(args.action, **kwargs)
        if result:
            print(result)
        return

    # Handle utility commands
    if args.action == "test":
        log.info(f"Running test type: {args.test_type}")
        if args.test_type.startswith("tts"):
            try:
                from gaia.audio.kokoro_tts import KokoroTTS

                tts = KokoroTTS()
                log.debug("TTS initialized successfully")
            except Exception as e:
                log.error(f"Failed to initialize TTS: {e}")
                raise RuntimeError(f"Failed to initialize TTS: {e}")

            test_text = (
                args.test_text
                or """
Let's play a game of trivia. I'll ask you a series of questions on a particular topic,
and you try to answer them to the best of your ability.

Here's your first question:

**Question 1:** Which American author wrote the classic novel "To Kill a Mockingbird"?

A) F. Scott Fitzgerald
B) Harper Lee
C) Jane Austen
D) J. K. Rowling
E) Edgar Allan Poe

Let me know your answer!
"""
            )

            if args.test_type == "tts-preprocessing":
                tts.test_preprocessing(test_text)
            elif args.test_type == "tts-streaming":
                tts.test_streaming_playback(test_text)
            elif args.test_type == "tts-audio-file":
                tts.test_generate_audio_file(test_text, args.output_audio_file)

        elif args.test_type.startswith("asr"):
            try:
                from gaia.audio.whisper_asr import WhisperAsr

                asr = WhisperAsr(
                    model_size=args.whisper_model_size,
                    device_index=args.audio_device_index,
                )
                log.debug("ASR initialized successfully")
            except ImportError:
                log.error(
                    "WhisperAsr not found. Please install voice support with: pip install .[talk]"
                )
                raise
            except Exception as e:
                log.error(f"Failed to initialize ASR: {e}")
                raise RuntimeError(f"Failed to initialize ASR: {e}")

            if args.test_type == "asr-file-transcription":
                if not args.input_audio_file:
                    print(
                        "Error: --input-audio-file is required for asr-file-transcription test"
                    )
                    return
                try:
                    text = asr.transcribe_file(args.input_audio_file)
                    print("\nTranscription result:")
                    print("-" * 40)
                    print(text)
                    print("-" * 40)
                except Exception as e:
                    print(f"Error transcribing file: {e}")

            elif args.test_type == "asr-microphone":
                print(f"\nRecording for {args.recording_duration} seconds...")
                print("Speak into your microphone...")

                # Setup transcription queue and start recording
                transcription_queue = queue.Queue()
                asr.transcription_queue = transcription_queue
                asr.start_recording()

                try:
                    start_time = time.time()
                    while time.time() - start_time < args.recording_duration:
                        try:
                            text = transcription_queue.get_nowait()
                            print(f"\nTranscribed: {text}")
                        except queue.Empty:
                            time.sleep(0.1)
                            remaining = args.recording_duration - int(
                                time.time() - start_time
                            )
                            print(f"\rRecording... {remaining}s remaining", end="")
                finally:
                    asr.stop_recording()
                    print("\nRecording stopped.")

            elif args.test_type == "asr-list-audio-devices":
                from gaia.audio.audio_recorder import AudioRecorder

                recorder = AudioRecorder()
                devices = recorder.list_audio_devices()
                print("\nAvailable Audio Input Devices:")
                for device in devices:
                    print(f"Index {device['index']}: {device['name']}")
                    print(f"    Max Input Channels: {device['maxInputChannels']}")
                    print(f"    Default Sample Rate: {device['defaultSampleRate']}")
                    print()
                return

        return

    # Handle utility functions
    if args.action == "youtube":
        if args.download_youtube_transcript:
            log.info(f"Downloading transcript from {args.download_youtube_transcript}")
            from llama_index.readers.youtube_transcript import YoutubeTranscriptReader

            doc = YoutubeTranscriptReader().load_data(
                ytlinks=[args.download_youtube_transcript]
            )
            output_path = args.output_transcript_path or "transcript.txt"
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(doc[0].text)
            print(f"Transcript downloaded to: {output_path}")
            return

    # Handle kill command
    if args.action == "kill":
        log.info(f"Attempting to kill process on port {args.port}")
        result = kill_process_by_port(args.port)
        print(result)
        return

    # Log error for unknown action
    log.error(f"Unknown action specified: {args.action}")
    parser.print_help()
    return


def kill_process_by_port(port):
    """Find and kill a process running on a specific port."""
    try:
        # For Windows
        if sys.platform.startswith("win"):
            cmd = f"netstat -ano | findstr :{port}"
            output = subprocess.check_output(cmd, shell=True).decode()
            if output:
                # Split output into lines and process each line
                for line in output.strip().split("\n"):
                    # Only process lines that contain the specific port
                    if f":{port}" in line:
                        parts = line.strip().split()
                        # Get the last part which should be the PID
                        try:
                            pid = int(parts[-1])
                            if pid > 0:  # Ensure we don't try to kill PID 0
                                # Add check=True to subprocess.run
                                subprocess.run(
                                    f"taskkill /PID {pid} /F", shell=True, check=True
                                )
                                return {
                                    "success": True,
                                    "message": f"Killed process {pid} running on port {port}",
                                }
                        except (IndexError, ValueError):
                            continue
                return {
                    "success": False,
                    "message": f"Could not find valid PID for port {port}",
                }
        return {"success": False, "message": f"No process found running on port {port}"}
    except subprocess.CalledProcessError:
        return {"success": False, "message": f"No process found running on port {port}"}
    except Exception as e:
        return {
            "success": False,
            "message": f"Error killing process on port {port}: {str(e)}",
        }


if __name__ == "__main__":
    main()
