# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import sys
import argparse
import time
import json
import socket
import asyncio
import queue
import logging
import subprocess
from pathlib import Path

import requests
import aiohttp
from aiohttp import ClientTimeout
from requests.exceptions import RequestException

from gaia.logger import get_logger
from gaia.audio.audio_client import AudioClient

from gaia.version import version

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

        self.agent_name = agent_name
        self.enable_agent_server = enable_agent_server
        self.host = host
        self.port = port
        self.llm_port = 8000
        self.agent_url = f"http://{host}:{port}"
        self.llm_url = f"http://{host}:{self.llm_port}"
        self.model = model
        self.max_new_tokens = max_new_tokens
        self.agent_server = None
        self.cli_mode = True  # Set this to True for CLI mode
        self.server_pids = {}
        self.show_stats = show_stats
        self.input_file = input_file

        # Initialize audio client for voice functionality
        self.audio_client = AudioClient(
            whisper_model_size=whisper_model_size,
            audio_device_index=audio_device_index,
            silence_threshold=silence_threshold,
            enable_tts=enable_tts,
            host=host,
            port=port,
            llm_port=self.llm_port,
            agent_name=agent_name,
            logging_level=logging_level,
        )

        self.log.info("Gaia CLI client initialized.")
        self.log.debug(
            f"agent_name: {self.agent_name}\n host: {self.host}\n"
            f"port: {self.port}\n llm_port: {self.llm_port}\n"
            f"model: {self.model}\n max_new_tokens: {self.max_new_tokens}"
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
                # TODO: Implement agent server startup logic
                self.log.info("Agent server startup logic not implemented")
                time.sleep(2)  # Wait to catch potential port binding errors

            self.log.info("Waiting for servers to start...")
            self.wait_for_servers()

            # Save server information
            self.save_server_info()
        except Exception as e:
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
        # Check if LLM server is still downloading models
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
            (f"http://localhost:{self.llm_port}/health", "LLM server"),
        ]

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
            "server_pids": self.server_pids,
            "input_file": self.input_file,  # Save input file info
        }
        with open(".gaia_servers.json", "w", encoding="utf-8") as f:
            json.dump(server_info, f)

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

    async def start_voice_chat(self):
        """Start a voice-based chat session."""
        await self.audio_client.start_voice_chat(self.process_voice_input)

    async def process_voice_input(self, text):
        """Process transcribed voice input and get AI response"""
        # Create callback for stats if needed
        get_stats_callback = None
        if hasattr(self, "show_stats") and self.show_stats:
            get_stats_callback = self.get_stats

        await self.audio_client.process_voice_input(text, get_stats_callback)

    async def halt_generation(self):
        """Send a request to halt the current generation."""
        await self.audio_client.halt_generation()

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
                print("Chat restart functionality not implemented")
            else:
                print(f"{self.agent_name}:", end=" ", flush=True)
                async for _ in self.send_message(user_input):
                    pass
                print()

    async def talk(self):
        """Voice-based chat interface"""
        self.audio_client.initialize_tts()
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

        # Handle hybrid shortcut flag
        if kwargs.pop("hybrid", False):
            model = "Llama-3.2-3B-Instruct-Hybrid"
            kwargs["model"] = model
            print(f"Using optimal GAIA configuration with model: {model}")
        elif not kwargs.get("model"):
            # Set default optimal configuration if no model specified
            model = "Qwen3-4B-GGUF"
            kwargs["model"] = model
            print(f"Using GAIA configuration with model: {model}")

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

        # Use longer timeout for RAG agents (timeout handling not currently implemented)
        # server_timeout = 1800 if kwargs.get("agent_name", "").lower() == "rag" else 120

        if launch_in_background == "terminal":
            print("Starting Gaia servers in background terminal...")
            print(
                "Note: A new terminal window will open to handle the server processes."
            )

            cmd = f'start cmd /k "echo Starting GAIA servers... Feel free to minimize this window. && {base_cmd}"'
            try:
                subprocess.Popen(cmd, shell=True)
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
            # TODO: Implement server stop functionality
            log.info("Server stop functionality not implemented")
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


def main():

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
        help="Shortcut for optimal configuration (sets model to hybrid variant)",
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
