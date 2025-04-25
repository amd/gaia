#!/usr/bin/env python
"""
Lemonade Server Client for GAIA.

This module provides a client for interacting with the Lemonade server's
OpenAI-compatible API and additional functionality.
"""

import json
import logging
import multiprocessing
import time
from typing import Dict, List, Any, Union, Optional, Generator

import requests
from websocket import create_connection
from websocket._exceptions import WebSocketTimeoutException

from gaia.llm.lemonade_server import launch_lemonade_server

logger = logging.getLogger(__name__)


class LemonadeClient:
    """Client for interacting with the Lemonade server."""

    def __init__(
        self,
        model: str,
        host: str = "localhost",
        port: int = 8000,
        verbose: bool = True,
    ):
        """
        Initialize the Lemonade client.

        Args:
            model: Name of the model to load
            host: Host address of the Lemonade server
            port: Port number of the Lemonade server
            verbose: If False, reduce logging verbosity during initialization
        """
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.ws_url = f"ws://{host}:{port}/ws"
        self.model = model
        self.ws = None
        self.server_process = None

        # Set logging level based on verbosity
        if not verbose:
            logger.setLevel(logging.WARNING)

        logger.info(f"Initialized Lemonade client for {host}:{port}")
        if model:
            logger.info(f"Initial model set to: {model}")

    def launch_llm_server(
        self,
        backend: str = "oga",
        device: str = "hybrid",
        dtype: str = "int4",
        max_new_tokens: int = 100,
        cli_mode: bool = True,
    ) -> None:
        """
        Launch the Lemonade server with the specified configuration.

        Args:
            backend: Server backend ('oga', 'hf', or 'ollama')
            device: Device to run on ('cpu', 'npu', 'igpu', or 'hybrid')
            dtype: Data type for model ('int4', 'bfloat16', etc.)
            max_new_tokens: Maximum tokens to generate
            cli_mode: Whether to run in CLI mode

        Returns:
            None

        Raises:
            Exception: If server fails to start after maximum retries
        """
        # Launch server using multiprocessing
        server_kwargs = {
            "backend": backend,
            "checkpoint": self.model,
            "device": device,
            "dtype": dtype,
            "max_new_tokens": max_new_tokens,
            "cli_mode": cli_mode,
        }

        # Create a process with redirected output if in cli_mode
        self.server_process = multiprocessing.Process(
            target=launch_lemonade_server, kwargs=server_kwargs
        )
        self.server_process.start()

        # Wait for server to be ready
        max_retries = 30
        retry_count = 0
        server_ready = False

        while retry_count < max_retries and not server_ready:
            try:
                health_response = self.health_check()
                print(f"\nHealth check response: {health_response}")
                if (
                    health_response
                    and health_response.get("model_loaded") == self.model
                ):
                    server_ready = True
                    logger.info(
                        f"Lemonade server is ready after {retry_count + 1} attempts"
                    )
                    break
                time.sleep(1)
                retry_count += 1
            except Exception as _:
                logger.debug(
                    f"Waiting for server... (attempt {retry_count + 1}/{max_retries})"
                )
                time.sleep(1)
                retry_count += 1

        if not server_ready:
            self.terminate_server()
            raise Exception("Lemonade server failed to start after maximum retries")

    def terminate_server(self) -> None:
        """Terminate the Lemonade server process if it exists."""
        if self.server_process:
            try:
                self.server_process.terminate()
                self.server_process = None
                logger.info("Lemonade server terminated")
            except Exception as e:
                logger.warning(f"Error terminating server process: {e}")

    def _connect_websocket(self):
        """Establish websocket connection if not already connected."""
        if self.ws is None or not self.ws.connected:
            try:
                self.ws = create_connection(self.ws_url, timeout=None)
                logger.debug("Websocket connection established")
            except Exception as e:
                error_msg = f"Failed to establish websocket connection: {str(e)}"
                logger.error(error_msg)
                raise ConnectionError(error_msg)

    def _close_websocket(self):
        """Close websocket connection if open."""
        if self.ws and self.ws.connected:
            self.ws.close()
            self.ws = None
            logger.debug("Websocket connection closed")

    def __del__(self):
        """Cleanup websocket connection and server process on deletion."""
        self._close_websocket()
        self.terminate_server()

    def generate_completion(
        self,
        prompt: str,
        max_tokens: int = 256,
        temperature: float = 0.7,
        stream: bool = False,
        stop: Optional[List[str]] = None,
        timeout: int = 60,
        **kwargs,
    ) -> Union[str, Generator[str, None, None]]:
        """
        Generate a text completion using websocket streaming.

        Args:
            prompt: The prompt to complete
            max_tokens: Maximum number of tokens to generate
            temperature: Temperature for sampling
            stream: If True, stream the responses
            stop: List of strings that stop generation
            timeout: Request timeout in seconds
            **kwargs: Additional parameters

        Returns:
            Generated text as string or generator of chunks if streaming
        """
        if not self.model:
            raise ValueError("No model is loaded. Please load a model first.")

        # Format the prompt with parameters
        request = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stop": stop if stop else [],
            **kwargs,
        }

        if stream:
            return self._stream_completion(request, timeout)

        # For non-streaming, collect all chunks into one response
        chunks = list(self._stream_completion(request, timeout))
        return "".join(chunks)

    def _stream_completion(
        self, request: Dict[str, Any], timeout: int
    ) -> Generator[str, None, None]:
        """
        Stream completion response via websocket.

        Args:
            request: The formatted request
            timeout: Request timeout in seconds

        Yields:
            Text chunks from the model
        """
        try:
            self._connect_websocket()

            # Send the request
            self.ws.send(json.dumps(request))
            logger.debug(f"Sent request: {request}")

            first_chunk = True
            while True:
                try:
                    # No timeout for first chunk, then use specified timeout
                    if first_chunk:
                        self.ws.sock.settimeout(None)
                    else:
                        self.ws.sock.settimeout(timeout)

                    chunk = self.ws.recv()
                    if first_chunk:
                        first_chunk = False

                    if chunk:
                        # Handle both streaming and non-streaming responses
                        if isinstance(chunk, str):
                            if "</s>" in chunk:
                                # End of stream marker
                                chunk = chunk.replace("</s>", "")
                                if chunk:
                                    yield chunk
                                break
                            yield chunk
                        else:
                            # Handle JSON response
                            try:
                                response = json.loads(chunk)
                                if "choices" in response:
                                    yield response["choices"][0]["text"]
                                elif "text" in response:
                                    yield response["text"]
                            except json.JSONDecodeError:
                                yield chunk

                except WebSocketTimeoutException:
                    logger.debug("Stream timed out")
                    break
                except Exception as e:
                    error_msg = f"Error during streaming: {str(e)}"
                    logger.error(error_msg)
                    raise

        except Exception as e:
            error_msg = f"Streaming failed: {str(e)}"
            logger.error(error_msg)
            raise
        finally:
            self._close_websocket()

    def generate_chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 256,
        temperature: float = 0.7,
        stream: bool = False,
        stop: Optional[List[str]] = None,
        timeout: int = 60,
        **kwargs,
    ) -> Union[str, Generator[str, None, None]]:
        """
        Generate a chat completion using websocket streaming.

        Args:
            messages: List of conversation messages
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            stream: If True, stream the responses
            stop: List of stop strings
            timeout: Request timeout in seconds
            **kwargs: Additional parameters

        Returns:
            Generated text or generator of chunks if streaming
        """
        # Format messages into a prompt
        prompt = self._format_chat_messages(messages)
        return self.generate_completion(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=stream,
            stop=stop,
            timeout=timeout,
            **kwargs,
        )

    def _format_chat_messages(self, messages: List[Dict[str, str]]) -> str:
        """Format chat messages into a prompt string."""
        formatted = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                formatted.append(f"System: {content}")
            elif role == "assistant":
                formatted.append(f"Assistant: {content}")
            else:
                formatted.append(f"Human: {content}")
        return "\n".join(formatted) + "\nAssistant:"

    def health_check(self) -> Dict[str, Any]:
        """
        Check server health.

        Returns:
            Dict containing the model_loaded status

        Raises:
            requests.exceptions.RequestException: If the health check fails
        """
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            if response.status_code == 200:
                return response.json()
            raise requests.exceptions.RequestException(
                f"Health check failed with status code: {response.status_code}"
            )
        except Exception as e:
            raise requests.exceptions.RequestException(f"Health check failed: {str(e)}")

    def load_model(self, model: str) -> Dict[str, Any]:
        """Load a model on the server."""
        self.model = model
        return {"status": "success", "message": "Model loaded"}

    def unload_model(self) -> Dict[str, Any]:
        """Unload the current model."""
        self.model = None
        return {"status": "success", "message": "Model unloaded"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = LemonadeClient(
        "amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid"
    )

    # Example chat completion
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is the capital of France?"},
    ]

    print("Streaming response:")
    for chunk in client.generate_chat_completion(messages, stream=True):
        print(chunk, end="", flush=True)
    print("\nDone!")
