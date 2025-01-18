# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

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
from pathlib import Path
from pprint import pprint
import requests
import psutil
import aiohttp
from aiohttp import ClientTimeout
from requests.exceptions import RequestException

from gaia.logger import get_logger
from gaia.llm.server import launch_llm_server
from gaia.agents.agent import launch_agent_server
from gaia.audio.whisper_asr import WhisperAsr


# Set debug level for the logger
logging.getLogger("gaia").setLevel(logging.INFO)

# Add the parent directory to sys.path to import gaia modules
current_dir = Path(__file__).resolve().parent
parent_dir = current_dir.parent.parent.parent
sys.path.append(str(parent_dir))

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
        silence_threshold=2.0,
        show_stats=False,
    ):
        self.log = self.__class__.log  # Use the class-level logger for instances
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

        self.log.info("Gaia CLI client initialized with the following settings:")
        self.log.info(
            f"agent_name: {self.agent_name}\n host: {self.host}\n"
            f"port: {self.port}\n llm_port: {self.llm_port}\n"
            f"ollama_port: {self.ollama_port}\n model: {self.model}\n"
            f"max_new_tokens: {self.max_new_tokens}\n backend: {self.backend}\n"
            f"device: {self.device}\n dtype: {self.dtype}"
        )

    def start(self):
        self.log.info("Starting servers...")
        if self.enable_agent_server:
            self.start_agent_server()

        if self.backend == "ollama":
            self.start_ollama_servers()
        else:
            self.start_llm_server()

        self.log.info("Waiting for servers to start...")
        self.wait_for_servers()

        # Save server information
        self.save_server_info()

    def wait_for_servers(
        self, model_download_timeout=3600, server_ready_timeout=120, check_interval=5
    ):
        self.log.info("Waiting for model downloads and servers to be ready...")

        # First, wait for model downloads to complete
        start_time = time.time()
        time.sleep(10)
        while time.time() - start_time < model_download_timeout:
            if not self.check_models_downloading():
                self.log.info("Model downloads completed.")
                break
            self.log.info("Models are still downloading. Continuing to wait...")
            time.sleep(check_interval)
        else:
            error_message = f"Model download did not complete within {model_download_timeout} seconds."
            self.log.error(error_message)
            raise TimeoutError(error_message)

        # Then, check for server readiness
        start_time = time.time()
        while time.time() - start_time < server_ready_timeout:
            if self.check_servers_ready():
                self.log.info("All servers are ready.")
                return
            self.log.info("Servers are not ready yet. Continuing to wait...")
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
        }
        with open(".gaia_servers.json", "w", encoding="utf-8") as f:
            json.dump(server_info, f)

    def start_agent_server(self):
        self.log.info(f"Starting {self.agent_name} server...")
        self.agent_server = multiprocessing.Process(
            target=launch_agent_server,
            kwargs={
                "agent_name": self.agent_name,
                "host": self.host,
                "port": self.port,
                "model": self.model,
                "cli_mode": self.cli_mode,
            },
        )
        self.agent_server.start()
        self.server_pids["agent"] = self.agent_server.pid
        self.log.debug(f"agent_server.pid: {self.agent_server.pid}")

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
            target=launch_llm_server, kwargs=llm_server_kwargs
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

        # Additional cleanup to ensure all child processes are terminated
        for pid in self.server_pids.values():
            try:
                parent = psutil.Process(pid)
                children = parent.children(recursive=True)
                for child in children:
                    child.terminate()
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
            self.log.info("Initializing voice chat...")
            print(
                f"Starting voice chat with {self.agent_name}. Say 'exit' to quit, or 'restart' to clear chat history."
            )

            # Initialize WhisperAsr with the transcription queue
            self.whisper_asr = WhisperAsr(
                model_size=self.whisper_model_size,
                device_index=self.audio_device_index,
                transcription_queue=self.transcription_queue,
            )

            device_name = self.whisper_asr.get_device_name()
            self.log.info(f"Using audio device: {device_name}")
            print(f"Using device: {device_name}")

            # Start recording first
            self.log.info("Starting audio recording...")
            self.whisper_asr.start_recording()

            # Start the processing thread after recording is initialized
            self.log.info("Starting audio processing thread...")
            process_thread = threading.Thread(target=self.process_audio_wrapper)
            process_thread.daemon = True
            process_thread.start()

            # Keep the main thread alive while processing
            self.log.info("Listening for voice input...")
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

                # Connect to websocket for new message
                ws = await session.ws_connect(f"ws://{self.host}:{self.port}/ws")
                self.log.debug(f"Sending message: {text[:50]}...")

                print(f"\n{self.agent_name}: ", end="", flush=True)
                await ws.send_str(text)

                try:
                    while True:
                        # Create a task for receiving the next message
                        receive_task = asyncio.create_task(ws.receive())

                        # Wait for either the message or a short timeout
                        done, pending = await asyncio.wait([receive_task], timeout=0.1)

                        # Cancel pending tasks
                        for task in pending:
                            task.cancel()

                        # Check if we have new audio input
                        if self.transcription_queue.qsize() > 0:
                            self.log.debug(
                                "New input detected during generation, halting..."
                            )
                            async with session.get(
                                f"http://{self.host}:{self.llm_port}/halt"
                            ) as halt_response:
                                if halt_response.status == 200:
                                    self.log.debug(
                                        "\nGeneration interrupted for new input."
                                    )
                                    return

                        # Process the message if we got one
                        if receive_task in done:
                            msg = await receive_task
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                if msg.data == "":
                                    break
                                print(msg.data, end="", flush=True)
                            elif msg.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                self.log.error(f"WebSocket closed or error: {msg.type}")
                                break

                except Exception as e:
                    self.log.error(f"Error processing voice input: {str(e)}")
                    print("\nError: Failed to get response from agent")
                finally:
                    if "ws" in locals():
                        await ws.close()

                print("\n")
                if hasattr(self, "show_stats") and self.show_stats:
                    stats = self.get_stats()
                    formatted_stats = {
                        k: round(v, 1) if isinstance(v, float) else v
                        for k, v in stats.items()
                    }
                    pprint(formatted_stats)

            except Exception as e:
                self.log.error(f"Error processing voice input: {str(e)}")
                print("\nError: Failed to get response from agent")
            finally:
                if "ws" in locals():
                    await ws.close()
        print("\n")

    async def chat(self):
        """Text-based chat interface"""
        print(
            f"Starting text chat with {self.agent_name}. Type 'exit' to quit, 'restart' to clear chat history."
        )
        while True:
            user_input = input("You: ").strip()
            if user_input.lower().rstrip(".") == "exit":
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
        print(
            f"Starting voice chat with {self.agent_name}. Say 'exit' to quit, or 'restart' to clear chat history."
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

            while self.whisper_asr and self.whisper_asr.is_recording:
                try:
                    text = self.transcription_queue.get(timeout=0.1)

                    current_time = time.time()
                    time_since_last = current_time - last_transcription_time
                    cleaned_text = text.lower().strip().rstrip(".!?")

                    # Handle special commands
                    if cleaned_text in ["exit", "quit"]:
                        print("\nExiting voice chat...")
                        self.whisper_asr.stop_recording()
                        break

                    # Normal text processing - only if it's not a system message
                    if text != current_display:
                        print(f"\nYou: {text}", end="", flush=True)
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
                            asyncio.run(self.process_voice_input(complete_text))
                            accumulated_text = []
                            current_display = ""
                            print()  # Add a newline after processing

                except queue.Empty:
                    if (
                        accumulated_text
                        and (time.time() - last_transcription_time)
                        > self.silence_threshold
                    ):
                        complete_text = accumulated_text[
                            -1
                        ]  # Use only the last transcription
                        asyncio.run(self.process_voice_input(complete_text))
                        accumulated_text = []
                        current_display = ""
                        print()  # Add a newline after processing

        except Exception as e:
            self.log.error(f"Error in process_audio_wrapper: {str(e)}")
        finally:
            if self.whisper_asr:
                self.whisper_asr.stop_recording()


async def async_main(action, message=None, **kwargs):
    if action in ["start", "stop"]:
        if action == "start":
            client = GaiaCliClient(**kwargs)
            client.start()
            return "Servers started successfully."
        else:  # stop
            client = await GaiaCliClient.load_existing_client()
            if client:
                client.stop()
                Path(".gaia_servers.json").unlink(missing_ok=True)
                return "Servers stopped successfully."
            else:
                return "No running servers found."

    client = await GaiaCliClient.load_existing_client()
    if not client:
        return "Error: Servers are not running. Please start the servers first using 'gaia-cli start'"

    if action == "prompt":
        if not message:
            return "Error: Message is required for prompt action."
        response = ""
        async for chunk in client.prompt(message):
            response += chunk
        if kwargs.get("show_stats", False):
            stats = client.get_stats()
            if stats:
                return {"response": response, "stats": stats}
        return {"response": response}
    elif action == "chat":
        # Text-only chat mode
        await client.chat()
        return "Chat session ended."
    elif action == "talk":
        # Voice-only chat mode
        await client.talk()
        return "Voice chat session ended."
    elif action == "stats":
        stats = client.get_stats()
        if stats:
            return {"stats": stats}
        return {"stats": {}}


def run_cli(action, message=None, **kwargs):
    return asyncio.run(async_main(action, message, **kwargs))


def start_servers():
    # Start the servers
    print("Starting servers...")
    start_result = run_cli("start")
    assert start_result == "Servers started successfully."


def stop_servers():
    # Stop the servers
    print("\nStopping servers...")
    stop_result = run_cli("stop")
    assert stop_result == "Servers stopped successfully."


def main():
    parser = argparse.ArgumentParser(
        description="Gaia CLI - Interact with Gaia AI agents",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "action",
        choices=["chat", "talk", "prompt", "start", "stop", "stats"],
        help="Action to perform",
        nargs="?",  # Make action optional
    )
    # Add download transcript option
    parser.add_argument(
        "--download-transcript",
        metavar="URL",
        help="Download transcript from a YouTube URL",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file path for transcript (optional, default: transcript_<video_id>.txt)",
    )
    # Move the other arguments here, before adding them to the main parser
    parser.add_argument(
        "--agent_name",
        default="Chaty",
        help="Name of the Gaia agent to use (e.g., Llm, Chaty, Joker, Clip, etc.)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address for the Agent server (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Port for the Agent server (default: 8001)",
    )
    parser.add_argument(
        "--model",
        default="llama3.2:1b",
        help="Model to use for the agent (default: llama3.2:1b)",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=512,
        help="Maximum number of new tokens to generate (default: 512)",
    )
    parser.add_argument(
        "--backend",
        default="ollama",
        choices=["oga", "hf", "ollama"],
        help="Backend to use for model inference (default: ollama)",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        choices=["cpu", "npu", "gpu", "hybrid"],
        help="Device to use for model inference (default: cpu)",
    )
    parser.add_argument(
        "--dtype",
        default="int4",
        choices=["float32", "float16", "bfloat16", "int8", "int4"],
        help="Data type to use for model inference (default: int4)",
    )
    parser.add_argument(
        "--whisper-model-size",
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size for voice recognition (default: base)",
    )
    parser.add_argument(
        "--audio-device-index",
        type=int,
        default=1,
        help="Index of the audio input device to use (default: 1)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show performance statistics after generation",
    )
    parser.add_argument("message", nargs="?", help="Message for prompt action")

    args = parser.parse_args()

    # Handle transcript download if requested
    if args.download_transcript:
        print(f"Downloading transcript from {args.download_transcript}")
        from llama_index.readers.youtube_transcript import YoutubeTranscriptReader

        doc = YoutubeTranscriptReader().load_data(ytlinks=[args.download_transcript])
        output_path = args.output or "transcript.txt"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(doc[0].text)
        print(f"Transcript downloaded to: {output_path}")
        return

    if not args.action:
        parser.print_help()
        return

    result = run_cli(
        args.action,
        args.message,
        agent_name=args.agent_name,
        host=args.host,
        port=args.port,
        model=args.model,
        max_new_tokens=args.max_new_tokens,
        backend=args.backend,
        device=args.device,
        dtype=args.dtype,
        whisper_model_size=args.whisper_model_size,
        audio_device_index=args.audio_device_index,
        show_stats=args.stats,
    )

    if result:
        print(result)


if __name__ == "__main__":
    main()
