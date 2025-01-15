# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import time
import math
import argparse
import asyncio
import subprocess
from threading import Event
from typing import Union, List, Dict, Any, Optional
from urllib.parse import urlparse

import requests
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from starlette.websockets import WebSocketDisconnect
from pydantic import BaseModel

import ollama
from ollama import Client
from ollama._types import ResponseError

from gaia.logger import get_logger
from gaia.interface.util import UIMessage


class OllamaClient:
    """
    A client for interacting with Ollama models.
    API details: https://github.com/ollama/ollama/blob/main/docs/api.md

    This class provides methods to generate text, chat, create embeddings,
    and manage models using the Ollama API.

    Attributes:
        supported_models (List[str]): A list of supported model names.
        model (str): The currently selected model.
        client (ollama.Client): The Ollama client instance.

    Args:
        model (str): The name of the model to use. Defaults to 'llama3.2:3b'.
        host (str): The host URL for the Ollama API. Defaults to 'http://localhost'.
        port (int): The port for the Ollama API. Defaults to 11434.

    Raises:
        AssertionError: If the specified model is not in the list of supported models.
    """

    def __init__(
        self,
        model: str = "llama3.2:3b",
        host: str = "http://localhost",
        port: int = 11434,
        cli_mode: bool = False,
    ):
        self.log = get_logger(__name__)
        self.model = model
        self.host = host
        self.port = port
        self.cli_mode = cli_mode
        self.model_downloading = False
        self.client = Client(host=f"{self.host}:{self.port}")
        self.stats = {
            "input_tokens": 0,
            "output_tokens": 0,
            "time_to_first_token": 0,
            "tokens_per_second": 0,
        }
        self.ensure_ollama_running()
        self.ensure_model_available()
        self.stop_event = Event()
        self.active_response = None

    def ensure_ollama_running(self):
        try:
            response = requests.get(f"{self.host}:{self.port}/api/version", timeout=5)
            if response.status_code == 200:
                return
        except requests.RequestException:
            self.log.error("Ollama server is not responding.")

    def ensure_model_available(self):
        try:
            # Try to get model info, which will fail if the model is not available
            self.client.show(self.model)
        except ollama._types.ResponseError as e:  # pylint:disable=W0212
            if "not found" in str(e):
                print(f"Model {self.model} not found. Downloading now...")
                self.model_downloading = True
                progress_dialog, update_progress = UIMessage.progress(
                    message=f"Downloading model {self.model}...",
                    title="Downloading Model",
                    cli_mode=self.cli_mode,
                )
                total_size = None

                try:
                    for progress in self.client.pull(self.model, stream=True):
                        status = progress.get("status", "")

                        if "status" in progress:
                            total_size = progress.get("total", 0)
                            downloaded = progress.get("completed", 0)
                            if total_size > 0 and downloaded > 0:
                                total_size = int(total_size)
                                downloaded = int(downloaded)
                                percentage = min(
                                    100, math.floor((downloaded / total_size) * 100)
                                )
                                # Convert bytes to GB
                                downloaded_gb = round(
                                    downloaded / (1024 * 1024 * 1024), 2
                                )
                                total_gb = round(total_size / (1024 * 1024 * 1024), 2)
                                if self.cli_mode:
                                    progress_message = f"{status} {downloaded_gb:.2f} GB / {total_gb:.2f} GB"
                                else:
                                    progress_message = f"\n{status}\n{downloaded_gb:.2f} GB / {total_gb:.2f} GB"
                            else:
                                percentage = 100
                                progress_message = f"\n{status}"

                            update_progress(percentage, 100, progress_message)

                        if progress_dialog is not None:
                            if progress_dialog.wasCanceled():
                                raise Exception("Download cancelled by user")

                    if progress_dialog is not None:
                        progress_dialog.close()
                    if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                        UIMessage.info(
                            "Model downloaded successfully.", cli_mode=self.cli_mode
                        )
                    self.model_downloading = False
                except Exception as download_error:
                    if progress_dialog is not None:
                        progress_dialog.close()
                    UIMessage.error(f"{str(download_error)}", cli_mode=self.cli_mode)
                    self.model_downloading = False
                    raise
            else:
                raise

    def set_model(self, model: str):
        self.model = model
        self.ensure_model_available()

    def _update_stats(self, response: Dict[str, Any]):
        """Helper method to update stats from a response"""
        if response.get("done", False):
            eval_count = response["eval_count"]
            eval_duration_ns = response["eval_duration"]
            prompt_eval_duration_ns = response["prompt_eval_duration"]

            time_to_first_token_s = prompt_eval_duration_ns / 10**9
            tokens_per_second = eval_count / eval_duration_ns * 10**9

            self.stats["input_tokens"] = response["prompt_eval_count"]
            self.stats["output_tokens"] = eval_count
            self.stats["time_to_first_token"] = time_to_first_token_s
            self.stats["tokens_per_second"] = tokens_per_second

    def generate(self, prompt: str, stream: bool = True, **kwargs):
        """
        Generate a response from the ollama model.

        Args:
            prompt (str): The prompt to generate a response for.
            stream (bool): Whether to stream the response.
            **kwargs: Additional arguments to pass to the ollama model.

        Returns:
            The response from the ollama model.
        """
        # Reset stop event before starting generation
        self.stop_event.clear()

        response_generator = self.client.generate(
            model=self.model, prompt=prompt, stream=stream, **kwargs
        )

        # Store the response generator for potential stopping
        self.active_response = response_generator

        if stream:
            last_response = None
            try:
                for response in response_generator:
                    # Check if generation should be stopped
                    if self.stop_event.is_set():
                        response_generator.close()
                        break

                    last_response = response
                    yield response

                if last_response:
                    self._update_stats(last_response)
            finally:
                # Clean up the active response reference
                if hasattr(self, "active_response"):
                    delattr(self, "active_response")
        else:
            response = response_generator
            self._update_stats(response)
            return response

    def chat(
        self,
        query: str,
        system_prompt: Optional[str] = None,
        stream: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Generate a response from the ollama model.

        Args:
            query (str): The query to generate a response for.
            system_prompt (Optional[str]): The system prompt to use.
            stream (bool): Whether to stream the response.
            **kwargs: Additional arguments to pass to the ollama model.
        """

        messages = [{"role": "user", "content": query}]
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})
        try:
            return self.client.chat(
                model=self.model, messages=messages, stream=stream, **kwargs
            )
        except ollama._types.ResponseError as e:  # pylint:disable=W0212
            if "not found" in str(e):
                self.ensure_model_available()
                return self.client.chat(
                    model=self.model, messages=messages, stream=stream, **kwargs
                )
            else:
                raise

    def embed(self, input: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        return self.client.embeddings(model=self.model, prompt=input, **kwargs)

    def create_model(self, name: str, modelfile: str, **kwargs) -> Dict[str, Any]:
        return self.client.create(name, modelfile=modelfile, **kwargs)

    def list_local_models(self) -> Dict[str, Any]:
        return self.client.list()

    def show_model_info(self, name: str) -> Dict[str, Any]:
        return self.client.show(name)

    def copy_model(self, source: str, destination: str) -> Dict[str, Any]:
        return self.client.copy(source=source, destination=destination)

    def delete_model(self, name: str) -> Dict[str, Any]:
        try:
            # First, check if the model exists
            self.client.show(name)
            # If the above doesn't raise an exception, the model exists, so we can delete it
            return self.client.delete(name)
        except ResponseError as e:
            if "not found" in str(e).lower():
                print(f"Model '{name}' not found. Skipping deletion.")
                return {"status": "Model not found", "name": name}
            else:
                # If it's a different kind of error, re-raise it
                raise

    def pull_model(self, name: str, **kwargs) -> Dict[str, Any]:
        return self.client.pull(name, **kwargs)

    def push_model(self, name: str, **kwargs) -> Dict[str, Any]:
        return self.client.push(name, **kwargs)

    def get_stats(self) -> Dict[str, Any]:
        return self.stats

    def stop_generation(self) -> Dict[str, Any]:
        """Stop the current generation."""
        try:
            self.stop_event.set()
            # Close any active response
            if hasattr(self, "active_response"):
                self.active_response.close()
            return {"terminated": True}
        except Exception as e:
            self.log.error(f"Error stopping generation: {str(e)}")
            return {"terminated": False, "error": str(e)}


class OllamaClientServer:
    """
    Open a web server that apps can use to communicate with Ollama models.

    There are two ways to interact with the server:
    - Send an HTTP request to "http://localhost:8000/generate" and
      receive back a response with the complete prompt.
    - Open a WebSocket with "ws://localhost:8000/ws" and receive a
      streaming response to the prompt.

    The WebSocket functionality is demonstrated by the webpage served at
    http://localhost:8000, which you can visit with a web browser after
    opening the server.

    Required input:
        - model: The name of the Ollama model to use.

    Output: None (runs indefinitely until stopped)
    """

    def __init__(
        self, host: str = "http://localhost", port: int = 8000, cli_mode: bool = False
    ):
        self.host = host
        self.port = port
        self.cli_mode = cli_mode
        self.log = get_logger(__name__)
        self.app = FastAPI()
        self.ollama_client = None
        self.is_generating = False
        self.setup_routes()

    def get_host_port(self):
        return self.host, self.port

    @staticmethod
    def parser(add_help: bool = True) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description="Open an HTTP server for Ollama models",
            add_help=add_help,
        )

        parser.add_argument(
            "--model",
            required=False,
            type=str,
            default="llama3.2:3b",
            help="Name of the Ollama model to use (default: llama3.2:3b)",
        )

        return parser

    def setup_routes(self):
        html = """
        <!DOCTYPE html>
        <html>
            <head>
                <title>Ollama Chat</title>
            </head>
            <body>
                <h1>Ollama Chat</h1>
                <form action="" onsubmit="sendMessage(event)">
                    <input type="text" id="messageText" autocomplete="off"/>
                    <button type="submit">Send</button>
                </form>
                <p id="allMessages"></p>
                <script>
                    const messageQueue = [];
                    const allMessagesContainer = document.getElementById('allMessages');
                    var ws = new WebSocket("ws://localhost:8000/ws");
                    ws.onmessage = function(event) {
                        const message = event.data;
                        messageQueue.push(message);
                        displayAllMessages();
                    };
                    function displayAllMessages() {
                        if (messageQueue.length > 0) {
                            const allMessages = messageQueue.join(' ');
                            allMessagesContainer.textContent = allMessages;
                        }
                    }
                    function sendMessage(event) {
                        var input = document.getElementById("messageText")
                        ws.send(input.value)
                        input.value = ''
                        event.preventDefault()
                    }
                </script>
            </body>
        </html>
        """

        @self.app.get("/")
        async def get():
            return HTMLResponse(html)

        class Message(BaseModel):
            text: str

        @self.app.post("/generate")
        async def generate_response(message: Message):
            response = self.ollama_client.generate(prompt=message.text, stream=False)
            return {"response": response}

        @self.app.websocket("/ws")
        async def stream_response(websocket: WebSocket):
            await websocket.accept()
            websocket_closed = False
            try:
                while True:
                    message = await websocket.receive_text()

                    if message == "done":
                        break

                    self.is_generating = True
                    self.ollama_client.stop_event.clear()
                    stream = self.ollama_client.generate(prompt=message, stream=True)

                    for chunk in stream:
                        if websocket_closed:
                            self.ollama_client.stop_generation()
                            break

                        new_text = chunk["response"]
                        print(new_text, end="", flush=True)
                        await asyncio.sleep(0.1)  # Add a small delay (adjust as needed)
                        await websocket.send_text(new_text)
                        if chunk["done"]:
                            await websocket.send_text("</s>")
                    print("\n")

                    self.is_generating = False

            except WebSocketDisconnect:
                self.is_generating = False
                self.log.info("WebSocket disconnected")
                websocket_closed = True
                self.ollama_client.stop_generation()
            except Exception as e:
                self.log.error(f"An error occurred: {str(e)}")
                self.ollama_client.stop_generation()
            finally:
                if not websocket_closed:
                    await websocket.close()

        @self.app.get("/health")
        async def health_check():
            if self.ollama_client is None:
                return {
                    "status": "error",
                    "message": "Ollama client is not initialized",
                }
            try:
                # Try to get model info as a simple health check
                self.ollama_client.client.show(self.ollama_client.model)
                self.log.info(
                    f"Model downloading: {self.ollama_client.model_downloading}"
                )
                if self.ollama_client.model_downloading:
                    return {"status": "downloading", "model": self.ollama_client.model}
                else:
                    return {"status": "ok", "model": self.ollama_client.model}
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"Error checking Ollama client: {str(e)}",
                }

        @self.app.get("/stats")
        async def stats():
            return self.ollama_client.get_stats()

        @self.app.get("/halt")
        async def halt():
            """Stop an in-progress generation."""
            return self.ollama_client.stop_generation()

        @self.app.get("/generating")
        async def generation_status():
            return {"is_generating": self.is_generating}

    def run(self, model: str):
        self.ollama_client = OllamaClient(model=model, cli_mode=self.cli_mode)
        self.log.info(f"Launching Ollama Server with model: {model}")

        # Parse the host to remove any protocol
        parsed_host = urlparse(self.host)
        clean_host = parsed_host.netloc or parsed_host.path

        uvicorn.run(self.app, host=clean_host, port=self.port)


class OllamaModelServer:
    def __init__(
        self, host: str = "http://localhost", port: int = 11434, cli_mode: bool = False
    ):
        self.log = get_logger(__name__)
        self.host = host
        self.port = port
        self.cli_mode = cli_mode
        self.ollama_process = None

    def get_host_port(self):
        return self.host, self.port

    def start_ollama_model_server(self):
        self.log.info("Attempting to start Ollama server...")
        try:
            # Check if the server is already running
            try:
                response = requests.get(
                    f"{self.host}:{self.port}/api/version", timeout=5
                )
                if response.status_code == 200:
                    version = response.json().get("version", "Unknown")
                    self.log.info(
                        f"Ollama server is already running. Version: {version}"
                    )
                    return True
            except requests.RequestException:
                pass  # Server is not running, continue with startup

            # Start the server
            self.log.info("Starting ollama model server.")
            self.ollama_process = subprocess.Popen(
                ["ollama", "serve"], creationflags=subprocess.CREATE_NEW_CONSOLE
            )

            # Wait for the server to start
            for _ in range(30):  # Try for 30 seconds
                time.sleep(1)
                try:
                    response = requests.get(
                        f"{self.host}:{self.port}/api/version", timeout=5
                    )
                    if response.status_code == 200:
                        self.log.info("Ollama model server started successfully.")
                        return True
                except requests.RequestException:
                    pass
            self.log.error("Failed to start Ollama model server after 30 seconds.")
            return False
        except FileNotFoundError:
            self.log.error(
                "Ollama executable not found."
                "Please make sure Ollama is installed and can be run from the command line (ollama serve).\n"
                "You can download Ollama from https://ollama.ai/download"
            )
            return False

    def stop_ollama_model_server(self):
        if self.ollama_process:
            self.log.info("Stopping Ollama model server...")
            self.ollama_process.terminate()
            self.ollama_process.wait()
            self.ollama_process = None
            self.log.info("Ollama model server stopped.")

    def run(self):
        # If we're here, the server isn't running or responding. Try to start it.
        if not self.start_ollama_model_server():
            error_message = (
                "Unable to start Ollama model server. "
                "Please make sure Ollama is installed and can be run from the command line (ollama serve).\n"
                "You can download Ollama from https://ollama.ai/download"
            )
            UIMessage.error(error_message, cli_mode=self.cli_mode)
            raise ConnectionError(error_message)
        else:
            return self.ollama_process


def launch_ollama_model_server(
    host: str = "http://localhost", port: int = 11434, cli_mode: bool = False
):
    try:
        ollama_model_server = OllamaModelServer(host, port, cli_mode)
        ollama_model_server.run()
        return ollama_model_server
    except Exception as e:
        UIMessage.error(f"An unexpected error occurred:\n\n{str(e)}", cli_mode=cli_mode)
        return


def launch_ollama_client_server(
    model: str = "llama3.2:3b",
    host: str = "http://localhost",
    port: int = 8000,
    cli_mode: bool = False,
):
    try:
        ollama_client_server = OllamaClientServer(host, port, cli_mode)
        ollama_client_server.run(model=model)
        return ollama_client_server
    except Exception as e:
        UIMessage.error(f"An unexpected error occurred:\n\n{str(e)}", cli_mode=cli_mode)
        return


if __name__ == "__main__":
    # launch_ollama_model_server()
    launch_ollama_client_server(cli_mode=True)
