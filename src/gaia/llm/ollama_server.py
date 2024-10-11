# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

import time
import math
import argparse
import asyncio
import subprocess
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
    def __init__(self, model: str = 'llama3.2:3b', host: str = 'http://localhost', port: int = 11434):
        self.log = get_logger(__name__)
        self.model = model
        self.host = host
        self.port = port
        self.client = Client(host=f"{self.host}:{self.port}")
        self.ensure_ollama_running()
        self.ensure_model_available()

    def ensure_ollama_running(self):
        try:
            response = requests.get(f"{self.host}:{self.port}/api/version", timeout=5)
            if response.status_code == 200:
                self.log.info(f"Ollama model server is already running. Version: {response.json().get('version')}")
                return
        except requests.RequestException:
            self.log.error("Ollama server is not responding.")

    def ensure_model_available(self):
        try:
            # Try to get model info, which will fail if the model is not available
            self.client.show(self.model)
        except ollama._types.ResponseError as e: # pylint:disable=W0212
            if "not found" in str(e):
                print(f"Model {self.model} not found. Downloading now...")
                progress_dialog, update_progress = UIMessage.progress(
                    message=f"Downloading model {self.model}...",
                    title="Downloading Model"
                )
                total_size = None

                try:
                    for progress in self.client.pull(self.model, stream=True):
                        status = progress.get('status', '')

                        if 'status' in progress:
                            total_size = progress.get('total', 0)
                            downloaded = progress.get('completed', 0)
                            if total_size > 0 and downloaded > 0:
                                total_size = int(total_size)
                                downloaded = int(downloaded)
                                percentage = min(100, math.floor((downloaded / total_size) * 100))
                                # Convert bytes to GB
                                downloaded_gb = round(downloaded / (1024 * 1024 * 1024), 2)
                                total_gb = round(total_size / (1024 * 1024 * 1024), 2)
                                progress_message = f"\n{status}\n{downloaded_gb:.2f} GB / {total_gb:.2f} GB"
                                # print(f"{progress}, {percentage}, {downloaded_gb}, {total_gb}")
                            else:
                                percentage = 100
                                progress_message = f"\n{status}"
                                # print(f"{progress}")

                            update_progress(percentage, 100, progress_message)

                        if progress_dialog.wasCanceled():
                            raise Exception("Download cancelled by user")

                    progress_dialog.close()
                    UIMessage.info("Model downloaded successfully.")
                except Exception as download_error:
                    progress_dialog.close()
                    UIMessage.error(f"{str(download_error)}")
                    raise
            else:
                raise

    def set_model(self, model:str):
        self.model = model
        self.ensure_model_available()

    def generate(self, prompt: str, stream:bool=True, **kwargs) -> Dict[str, Any]:
        """
        Generate a response from the ollama model.

        Args:
            prompt (str): The prompt to generate a response for.
            stream (bool): Whether to stream the response.
            **kwargs: Additional arguments to pass to the ollama model.

        Returns:
            Dict[str, Any]: The response from the ollama model.

        Example:
            {
                "model": "llama3.2",
                "created_at": "2023-08-04T08:52:19.385406455-07:00",
                "response": "The",
                "done": False
            }
        """
        return self.client.generate(model=self.model, prompt=prompt, stream=stream, **kwargs)

    def chat(self, query:str, system_prompt:Optional[str]=None, stream:bool=True, **kwargs) -> Dict[str, Any]:
        """
        Generate a response from the ollama model.

        Args:
            query (str): The query to generate a response for.
            system_prompt (Optional[str]): The system prompt to use.
            stream (bool): Whether to stream the response.
            **kwargs: Additional arguments to pass to the ollama model.
        """

        messages=[{'role': 'user', 'content': query}]
        if system_prompt:
            messages.insert(0, {'role': 'system', 'content': system_prompt})
        try:
            return self.client.chat(model=self.model, messages=messages, stream=stream, **kwargs)
        except ollama._types.ResponseError as e: # pylint:disable=W0212
            if "not found" in str(e):
                self.ensure_model_available()
                return self.client.chat(model=self.model, messages=messages, stream=stream, **kwargs)
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

    def __init__(self, host:str="http://localhost", port:int=8000):
        self.host = host
        self.port = port
        self.log = get_logger(__name__)
        self.app = FastAPI()
        self.ollama_client = None
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
            return {"response": response['response']}

        @self.app.websocket("/ws")
        async def stream_response(websocket: WebSocket):
            await websocket.accept()
            websocket_closed = False
            try:
                while True:
                    message = await websocket.receive_text()

                    if message == "done":
                        break

                    stream = self.ollama_client.generate(prompt=message, stream=True)

                    for chunk in stream:
                        new_text = chunk['response']
                        print(new_text, end="", flush=True)
                        await asyncio.sleep(0.1)  # Add a small delay (adjust as needed)
                        await websocket.send_text(new_text)
                        if chunk['done']: # end of message
                            await websocket.send_text("</s>") # indicates end of message
                    print("\n")

            except WebSocketDisconnect:
                self.log.info("WebSocket disconnected")
                websocket_closed = True
            except Exception as e:
                self.log.error(f"An error occurred: {str(e)}")
            finally:
                if not websocket_closed:
                    await websocket.close()

    def run(self, model:str):
        self.ollama_client = OllamaClient(model=model)
        self.log.info(f"Launching Ollama Server with model: {model}")

        # Parse the host to remove any protocol
        parsed_host = urlparse(self.host)
        clean_host = parsed_host.netloc or parsed_host.path

        uvicorn.run(self.app, host=clean_host, port=self.port)


class OllamaModelServer:
    def __init__(self, host:str='http://localhost', port:int=11434):
        self.log = get_logger(__name__)
        self.host = host
        self.port = port
        self.ollama_process = None


    def get_host_port(self):
        return self.host, self.port


    def start_ollama_model_server(self):
        self.log.info("Attempting to start Ollama server...")
        try:
            # Check if the server is already running
            try:
                response = requests.get(f"{self.host}:{self.port}/api/version", timeout=5)
                if response.status_code == 200:
                    version = response.json().get('version', 'Unknown')
                    self.log.warning(f"Ollama server is already running. Version: {version}")
                    return True
            except requests.RequestException:
                pass  # Server is not running, continue with startup

            # Start the server
            self.log.info("Starting ollama model server.")
            self.ollama_process = subprocess.Popen(['ollama', 'serve'], creationflags=subprocess.CREATE_NEW_CONSOLE)

            # Wait for the server to start
            for _ in range(30):  # Try for 30 seconds
                time.sleep(1)
                try:
                    response = requests.get(f"{self.host}:{self.port}/api/version", timeout=5)
                    if response.status_code == 200:
                        self.log.info("Ollama server started successfully.")
                        return True
                except requests.RequestException:
                    pass
            self.log.error("Failed to start Ollama server after 30 seconds.")
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
            self.log.info("Stopping Ollama server...")
            self.ollama_process.terminate()
            self.ollama_process.wait()
            self.ollama_process = None
            self.log.info("Ollama server stopped.")

    def run(self):
        # If we're here, the server isn't running or responding. Try to start it.
        if not self.start_ollama_model_server():
            error_message = (
                "Unable to start Ollama server. "
                "Please make sure Ollama is installed and can be run from the command line (ollama serve).\n"
                "You can download Ollama from https://ollama.ai/download"
            )
            UIMessage.error(error_message)
            raise ConnectionError(error_message)
        else:
            return self.ollama_process


def launch_ollama_model_server(host:str="http://localhost", port:int=11434):
    try:
        ollama_model_server = OllamaModelServer(host, port)
        ollama_model_server.run()
        return ollama_model_server
    except Exception as e:
        UIMessage.error(f"An unexpected error occurred:\n\n{str(e)}")
        return


def launch_ollama_client_server(model:str, host:str="http://localhost", port:int=8000):
    try:
        ollama_client_server = OllamaClientServer(host, port)
        ollama_client_server.run(model=model)
        return ollama_client_server
    except Exception as e:
        UIMessage.error(f"An unexpected error occurred:\n\n{str(e)}")
        return
