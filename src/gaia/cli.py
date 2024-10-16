import sys
import argparse
import time
import json
import asyncio
import multiprocessing
from pathlib import Path
import requests
import psutil
import aiohttp
from aiohttp import ClientTimeout
from requests.exceptions import RequestException

from gaia.logger import get_logger
from gaia.llm.server import launch_llm_server
from gaia.llm.ollama_server import launch_ollama_client_server, launch_ollama_model_server
from gaia.agents.agent import launch_agent_server


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
            dtype="int4"
        ):
        self.log = self.__class__.log  # Use the class-level logger for instances
        self.agent_name = agent_name
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
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

    def start(self):
        self.log.info("Starting servers...")
        self.start_agent_server()

        if self.backend == "ollama":
            self.start_ollama_servers()
        else:
            self.start_llm_server()

        self.log.info("Waiting for servers to start...")
        self.wait_for_servers()

        # Save server information
        self.save_server_info()

    def wait_for_servers(self, timeout=60, check_interval=2):
        self.log.info(f"Waiting up to {timeout} seconds for servers to be ready...")
        start_time = time.time()
        time.sleep(10)
        while time.time() - start_time < timeout:
            if self.check_servers_ready():
                self.log.info("All servers are ready.")
                return
            time.sleep(check_interval)
        raise TimeoutError("Servers failed to start within the specified timeout.")

    def check_servers_ready(self):
        servers_to_check = [
            (f"http://{self.host}:{self.port}/health", "Agent server"),
        ]

        if self.backend == "ollama":
            servers_to_check.extend([
                ("http://localhost:11434/api/version", "Ollama model server"),
                ("http://localhost:8000/health", "Ollama client server"),
            ])
        else:
            # TODO: Add LLM server health check
            # servers_to_check.append((f"http://localhost:8000/health", "LLM server"))
            pass

        for url, server_name in servers_to_check:
            try:
                response = requests.get(url, timeout=5)
                if response.status_code != 200:
                    self.log.warning(f"{server_name} not ready. Status code: {response.status_code}")
                    return False
            except RequestException as e:
                self.log.warning(f"Failed to connect to {server_name}: {str(e)}")
                return False

        self.log.info("All servers are ready.")
        return True

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
            "server_pids": self.server_pids
        }
        with open('.gaia_servers.json', 'w', encoding='utf-8') as f:
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
                "cli_mode": self.cli_mode
            }
        )
        self.agent_server.start()
        self.server_pids['agent'] = self.agent_server.pid
        self.log.debug(f"agent_server.pid: {self.agent_server.pid}")

    def start_ollama_servers(self):
        self.log.info("Starting Ollama servers...")
        self.ollama_model_server = multiprocessing.Process(
            target=launch_ollama_model_server,
            kwargs={"host": "http://localhost", "port": 11434}
        )
        self.ollama_model_server.start()
        self.server_pids['ollama_model'] = self.ollama_model_server.pid
        self.log.debug(f"ollama_model_server.pid: {self.ollama_model_server.pid}")

        self.ollama_client_server = multiprocessing.Process(
            target=launch_ollama_client_server,
            kwargs={"model": self.model, "host": "http://localhost", "port": 8000}
        )
        self.ollama_client_server.start()
        self.server_pids['ollama_client'] = self.ollama_client_server.pid
        self.log.debug(f"ollama_client_server.pid: {self.ollama_client_server.pid}")

    def start_llm_server(self):
        self.log.info("Starting LLM server...")
        llm_server_kwargs = {
            "backend": self.backend,
            "checkpoint": self.model,
            "max_new_tokens": self.max_new_tokens,
            "device": self.device,
            "dtype": self.dtype,
        }
        self.llm_server = multiprocessing.Process(
            target=launch_llm_server,
            kwargs=llm_server_kwargs
        )
        self.llm_server.start()
        self.server_pids['llm'] = self.llm_server.pid
        self.log.debug(f"llm_server.pid: {self.llm_server.pid}")

    async def send_message(self, message):
        url = f"{self.base_url}/prompt"
        data = {"prompt": message}
        try:
            async with aiohttp.ClientSession(timeout=ClientTimeout(total=3600)) as session:
                async with session.post(url, json=data) as response:
                    if response.status == 200:
                        async for chunk in response.content.iter_any():
                            chunk = chunk.decode('utf-8')
                            print(chunk, end='', flush=True)
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

    def restart_chat(self):
        url = f"{self.base_url}/restart"
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
                self.log.info(f"{server_name} server process not found. It may have already terminated.")
            except psutil.TimeoutExpired:
                self.log.warning(f"{server_name} server did not terminate gracefully. Forcing termination...")
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

    async def chat(self):
        print(f"Starting chat with {self.agent_name}. Type 'exit' to quit, 'restart' to clear chat history.")
        while True:
            user_input = input("You: ").strip()
            if user_input.lower() == 'exit':
                break
            elif user_input.lower() == 'restart':
                print(await self.restart_chat())
            else:
                async for _ in self.send_message(user_input):
                    pass  # The chunks are printed in send_message

    async def prompt(self, message):
        async for chunk in self.send_message(message):
            yield chunk

    @classmethod
    async def load_existing_client(cls):
        json_path = Path('.gaia_servers.json')
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                server_info = json.load(f)
            if server_info:
                client = cls(**{k: v for k, v in server_info.items() if k != 'server_pids'})
                client.server_pids = server_info.get('server_pids', {})
                return client
            return None
        except FileNotFoundError:
            cls.log.error(f"Server information file ({json_path}) not found.")
            return None
        except json.JSONDecodeError:
            cls.log.error(f"Server information file ({json_path}) is corrupted.")
            return None

    async def prompt_and_capture(self, message):
        async for chunk in self.send_message(message):
            self.log.info(f"{chunk}")
            yield chunk

async def async_main(action, message=None, **kwargs):
    if action in ['start', 'stop']:
        if action == 'start':
            client = GaiaCliClient(**kwargs)
            client.start()
            return "Servers started successfully."
        else:  # stop
            client = await GaiaCliClient.load_existing_client()
            if client:
                client.stop()
                Path('.gaia_servers.json').unlink(missing_ok=True)
                return "Servers stopped successfully."
            else:
                return "No running servers found."

    client = await GaiaCliClient.load_existing_client()
    if not client:
        return "Error: Servers are not running. Please start the servers first using 'gaia-cli start'"

    if action == 'prompt':
        if not message:
            return "Error: Message is required for prompt action."
        response = ""
        async for chunk in client.prompt_and_capture(message):
            response += chunk
        return response
    elif action == 'chat':
        # Note: Chat mode doesn't return a response, it's interactive
        await client.chat()
        return "Chat session ended."

def run_cli(action, message=None, **kwargs):
    return asyncio.run(async_main(action, message, **kwargs))

def main():
    parser = argparse.ArgumentParser(
        description="Gaia CLI - Interact with Gaia AI agents",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "action",
        choices=['chat', 'prompt', 'start', 'stop'],
        help="Action to perform"
    )
    parser.add_argument(
        "--agent_name",
        default="Chaty",
        help="Name of the Gaia agent to use (e.g., Chaty, Clip, Datalin, etc.)"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address for the Agent server (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Port for the Agent server (default: 8001)"
    )
    parser.add_argument(
        "--model",
        default="llama3.2:1b",
        help="Model to use for the agent (default: llama3.2:1b)"
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=512,
        help="Maximum number of new tokens to generate (default: 512)"
    )
    parser.add_argument(
        "--backend",
        default="ollama",
        choices=["lemonade", "ollama"],
        help="Backend to use for model inference (default: ollama)"
    )
    parser.add_argument(
        "--device",
        default="cpu",
        choices=["cpu", "npu", "gpu"],
        help="Device to use for model inference (default: cpu)"
    )
    parser.add_argument(
        "--dtype",
        default="int4",
        choices=["float32", "float16", "bfloat16", "int8", "int4"],
        help="Data type to use for model inference (default: int4)"
    )
    parser.add_argument(
        "message",
        nargs='?',
        help="Message for prompt action"
    )

    args = parser.parse_args()

    result = run_cli(args.action, args.message,
                     agent_name=args.agent_name,
                     host=args.host,
                     port=args.port,
                     model=args.model,
                     max_new_tokens=args.max_new_tokens,
                     backend=args.backend,
                     device=args.device,
                     dtype=args.dtype)

    if result:
        print(result)

if __name__ == "__main__":
    main()
