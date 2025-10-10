#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Lemonade Server Client for GAIA.

This module provides a client for interacting with the Lemonade server's
OpenAI-compatible API and additional functionality.
"""

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from threading import Thread
from typing import Any, Dict, Generator, List, Optional, Union

import openai  # For exception types
import psutil
import requests

# Import OpenAI client for internal use
from openai import OpenAI

from gaia.logger import get_logger

# =========================================================================
# Server Configuration Defaults
# =========================================================================
# Default server host and port
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8000

# API version supported by this client
LEMONADE_API_VERSION = "v1"

# =========================================================================
# Model Configuration Defaults
# =========================================================================
# Default model for text generation - lightweight CPU model for testing
DEFAULT_MODEL_NAME = "Qwen2.5-0.5B-Instruct-CPU"
# DEFAULT_MODEL_NAME = "Llama-3.2-3B-Instruct-Hybrid"

# =========================================================================
# Request Configuration Defaults
# =========================================================================
# Default timeout in seconds for regular API requests
DEFAULT_REQUEST_TIMEOUT = 120
# Default timeout in seconds for model loading operations
DEFAULT_MODEL_LOAD_TIMEOUT = 180


class LemonadeClientError(Exception):
    """Base exception for Lemonade client errors."""


def kill_process_on_port(port):
    """Kill any process that is using the specified port."""
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            connections = proc.net_connections()
            for conn in connections:
                if conn.laddr.port == port:
                    proc_name = proc.name()
                    proc_pid = proc.pid
                    proc.kill()
                    print(
                        f"Killed process {proc_name} (PID: {proc_pid}) using port {port}"
                    )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue


class LemonadeClient:
    """Client for interacting with the Lemonade server REST API."""

    def __init__(
        self,
        model: Optional[str] = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        verbose: bool = True,
        keep_alive: bool = False,
    ):
        """
        Initialize the Lemonade client.

        Args:
            model: Name of the model to load (optional)
            host: Host address of the Lemonade server
            port: Port number of the Lemonade server
            verbose: If False, reduce logging verbosity during initialization
            keep_alive: If True, don't terminate server in __del__
        """
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}/api/{LEMONADE_API_VERSION}"
        self.model = model
        self.server_process = None
        self.log = get_logger(__name__)
        self.keep_alive = keep_alive

        # Set logging level based on verbosity
        if not verbose:
            self.log.setLevel(logging.WARNING)

        self.log.info(f"Initialized Lemonade client for {host}:{port}")
        if model:
            self.log.info(f"Initial model set to: {model}")

    def launch_server(self, log_level="info", background="none"):
        """
        Launch the Lemonade server using subprocess.

        Args:
            log_level: Logging level for the server ('critical', 'error', 'warning', 'info', 'debug', 'trace').
                       Defaults to 'info'.
            background: How to run the server:
                       - "terminal": Launch in a new terminal window
                       - "silent": Run in background with output to log file
                       - "none": Run in foreground (default)

        This method follows the approach in test_lemonade_server.py.
        """
        self.log.info("Starting Lemonade server...")

        # Ensure we kill anything using the port
        kill_process_on_port(self.port)

        # Build the base command
        base_cmd = ["lemonade-server", "serve"]
        if log_level != "info":
            base_cmd.extend(["--log-level", log_level])

        if background == "terminal":
            # Launch in a new terminal window
            cmd = f'start cmd /k "{" ".join(base_cmd)}"'
            self.server_process = subprocess.Popen(cmd, shell=True)
        elif background == "silent":
            # Run in background with subprocess
            log_file = open("lemonade.log", "w", encoding="utf-8")
            self.server_process = subprocess.Popen(
                base_cmd,
                stdout=log_file,
                stderr=log_file,
                text=True,
                bufsize=1,
                shell=True,
            )
        else:  # "none" or any other value
            # Run in foreground with real-time output
            self.server_process = subprocess.Popen(
                base_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                shell=True,
            )

            # Print stdout and stderr in real-time only for foreground mode
            def print_output():
                while True:
                    if self.server_process is None:
                        break
                    try:
                        stdout = self.server_process.stdout.readline()
                        stderr = self.server_process.stderr.readline()
                        if stdout:
                            self.log.debug(f"[Server stdout] {stdout.strip()}")
                        if stderr:
                            self.log.warning(f"[Server stderr] {stderr.strip()}")
                        if (
                            not stdout
                            and not stderr
                            and self.server_process is not None
                            and self.server_process.poll() is not None
                        ):
                            break
                    except AttributeError:
                        # This happens if server_process becomes None while we're executing this function
                        break

            output_thread = Thread(target=print_output, daemon=True)
            output_thread.start()

        # Wait for the server to start by checking port
        start_time = time.time()
        while True:
            if time.time() - start_time > 60:
                self.log.error("Server failed to start within 60 seconds")
                raise TimeoutError("Server failed to start within 60 seconds")
            try:
                conn = socket.create_connection((self.host, self.port))
                conn.close()
                break
            except socket.error:
                time.sleep(1)

        # Wait a few other seconds after the port is available
        time.sleep(5)
        self.log.info("Lemonade server started successfully")

    def terminate_server(self):
        """Terminate the Lemonade server process if it exists."""
        if not self.server_process:
            return

        try:
            self.log.info("Terminating Lemonade server...")

            # Handle different process types
            if hasattr(self.server_process, "join"):
                # Handle multiprocessing.Process objects
                self.server_process.terminate()
                self.server_process.join(timeout=5)
            else:
                # For subprocess.Popen
                if sys.platform.startswith("win") and self.server_process.pid:
                    # On Windows, use taskkill to ensure process tree is terminated
                    os.system(f"taskkill /F /PID {self.server_process.pid} /T")
                elif self.server_process.pid:
                    # On Linux/Unix, kill the process group to terminate child processes
                    try:
                        os.killpg(os.getpgid(self.server_process.pid), signal.SIGTERM)
                        # Wait a bit for graceful termination
                        try:
                            self.server_process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            # Force kill if graceful termination failed
                            os.killpg(
                                os.getpgid(self.server_process.pid), signal.SIGKILL
                            )
                    except (OSError, ProcessLookupError):
                        # Process or process group doesn't exist, try individual kill
                        try:
                            self.server_process.kill()
                        except ProcessLookupError:
                            pass  # Process already terminated
                else:
                    # Fallback: try to kill normally
                    self.server_process.kill()
                # Wait for process to terminate
                try:
                    self.server_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.log.warning("Process did not terminate within timeout")

            # Ensure port is free
            kill_process_on_port(self.port)

            # Reset reference
            self.server_process = None
            self.log.info("Lemonade server terminated successfully")
        except Exception as e:
            self.log.error(f"Error terminating server process: {e}")
            # Reset reference even on error
            self.server_process = None

    def __del__(self):
        """Cleanup server process on deletion."""
        # Check if keep_alive attribute exists (might not if __init__ failed early)
        if hasattr(self, "keep_alive") and not self.keep_alive:
            self.terminate_server()
        elif hasattr(self, "server_process") and self.server_process:
            if hasattr(self, "log"):
                self.log.info("Not terminating server because keep_alive=True")

    def chat_completions(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_completion_tokens: Optional[int] = None,
        max_tokens: Optional[int] = None,
        stop: Optional[Union[str, List[str]]] = None,
        stream: bool = False,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        logprobs: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """
        Call the chat completions endpoint.

        Args:
            model: The model to use for completion
            messages: List of conversation messages with 'role' and 'content'
            temperature: Controls randomness (higher = more random)
            max_completion_tokens: Maximum number of output tokens to generate (preferred)
            max_tokens: Maximum number of output tokens to generate (deprecated, use max_completion_tokens)
            stop: Sequences where generation should stop
            stream: Whether to stream the response
            timeout: Request timeout in seconds
            logprobs: Whether to include log probabilities
            tools: List of tools the model may call
            **kwargs: Additional parameters to pass to the API

        Returns:
            For non-streaming: Dict with completion data
            For streaming: Generator yielding completion chunks

        Example response (non-streaming):
        {
          "id": "0",
          "object": "chat.completion",
          "created": 1742927481,
          "model": "model-name",
          "choices": [{
            "index": 0,
            "message": {
              "role": "assistant",
              "content": "Response text here"
            },
            "finish_reason": "stop"
          }]
        }
        """
        # Handle max_tokens vs max_completion_tokens
        if max_completion_tokens is None and max_tokens is None:
            max_completion_tokens = 1000  # Default value
        elif max_completion_tokens is not None and max_tokens is not None:
            self.log.warning(
                "Both max_completion_tokens and max_tokens provided. Using max_completion_tokens."
            )
        elif max_tokens is not None:
            max_completion_tokens = max_tokens

        # Use the OpenAI client for streaming if requested
        if stream:
            return self._stream_chat_completions_with_openai(
                model=model,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                stop=stop,
                timeout=timeout,
                logprobs=logprobs,
                tools=tools,
                **kwargs,
            )

        # Note: self.base_url already includes /api/v1
        url = f"{self.base_url}/chat/completions"
        data = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
            "stream": stream,
            **kwargs,
        }

        if stop:
            data["stop"] = stop

        if logprobs:
            data["logprobs"] = logprobs

        if tools:
            data["tools"] = tools

        try:
            self.log.debug(f"Sending chat completion request to model: {model}")
            response = requests.post(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )

            if response.status_code != 200:
                error_msg = f"Error in chat completions (status {response.status_code}): {response.text}"
                self.log.error(error_msg)
                raise LemonadeClientError(error_msg)

            result = response.json()
            if "choices" in result and len(result["choices"]) > 0:
                token_count = len(
                    result["choices"][0].get("message", {}).get("content", "")
                )
                self.log.debug(
                    f"Chat completion successful. Approximate response length: {token_count} characters"
                )

            return result

        except requests.exceptions.RequestException as e:
            self.log.error(f"Request failed: {str(e)}")
            raise LemonadeClientError(f"Request failed: {str(e)}")

    def _stream_chat_completions_with_openai(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_completion_tokens: int = 1000,
        stop: Optional[Union[str, List[str]]] = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        logprobs: Optional[bool] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Stream chat completions using the OpenAI client.

        Returns chunks in the format:
        {
            "id": "...",
            "object": "chat.completion.chunk",
            "created": 1742927481,
            "model": "...",
            "choices": [{
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": "..."
                },
                "finish_reason": null
            }]
        }
        """
        # Create a client just for this request
        client = OpenAI(
            base_url=self.base_url,
            api_key="lemonade",  # required, but unused
            timeout=timeout,
        )

        # Create request parameters
        request_params = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
            "stream": True,
            **kwargs,
        }

        if stop:
            request_params["stop"] = stop

        if logprobs:
            request_params["logprobs"] = logprobs

        if tools:
            request_params["tools"] = tools

        try:
            # Use the client to stream responses
            self.log.debug(f"Starting streaming chat completion with model: {model}")
            stream = client.chat.completions.create(**request_params)

            # Convert OpenAI client responses to our format
            tokens_generated = 0
            for chunk in stream:
                tokens_generated += 1
                # Convert to dict format expected by our API
                yield {
                    "id": chunk.id,
                    "object": "chat.completion.chunk",
                    "created": chunk.created,
                    "model": chunk.model,
                    "choices": [
                        {
                            "index": choice.index,
                            "delta": {
                                "role": (
                                    choice.delta.role
                                    if hasattr(choice.delta, "role")
                                    and choice.delta.role
                                    else None
                                ),
                                "content": (
                                    choice.delta.content
                                    if hasattr(choice.delta, "content")
                                    and choice.delta.content
                                    else None
                                ),
                            },
                            "finish_reason": choice.finish_reason,
                        }
                        for choice in chunk.choices
                    ],
                }

            self.log.debug(
                f"Completed streaming chat completion. Generated {tokens_generated} tokens."
            )

        except (openai.APIError, openai.APIConnectionError, openai.RateLimitError) as e:
            error_type = e.__class__.__name__
            self.log.error(f"OpenAI {error_type}: {str(e)}")
            # Re-raise as our custom error type
            raise LemonadeClientError(f"OpenAI {error_type}: {str(e)}")
        except Exception as e:
            self.log.error(f"Error using OpenAI client for streaming: {str(e)}")
            # Re-raise as our custom error type
            raise LemonadeClientError(f"Streaming request failed: {str(e)}")

    def completions(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        stop: Optional[Union[str, List[str]]] = None,
        stream: bool = False,
        echo: bool = False,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        logprobs: Optional[bool] = None,
        **kwargs,
    ) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """
        Call the completions endpoint.

        Args:
            model: The model to use for completion
            prompt: The prompt to generate a completion for
            temperature: Controls randomness (higher = more random)
            max_tokens: Maximum number of tokens to generate (including input tokens)
            stop: Sequences where generation should stop
            stream: Whether to stream the response
            echo: Whether to include the prompt in the response
            timeout: Request timeout in seconds
            logprobs: Whether to include log probabilities
            **kwargs: Additional parameters to pass to the API

        Returns:
            For non-streaming: Dict with completion data
            For streaming: Generator yielding completion chunks

        Example response:
        {
          "id": "0",
          "object": "text_completion",
          "created": 1742927481,
          "model": "model-name",
          "choices": [{
            "index": 0,
            "text": "Response text here",
            "finish_reason": "stop"
          }]
        }
        """
        # Use the OpenAI client for streaming if requested
        if stream:
            return self._stream_completions_with_openai(
                model=model,
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop,
                echo=echo,
                timeout=timeout,
                logprobs=logprobs,
                **kwargs,
            )

        # Note: self.base_url already includes /api/v1
        url = f"{self.base_url}/completions"
        data = {
            "model": model,
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
            "echo": echo,
            **kwargs,
        }

        if stop:
            data["stop"] = stop

        if logprobs:
            data["logprobs"] = logprobs

        try:
            self.log.debug(f"Sending text completion request to model: {model}")
            response = requests.post(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )

            if response.status_code != 200:
                error_msg = f"Error in completions (status {response.status_code}): {response.text}"
                self.log.error(error_msg)
                raise LemonadeClientError(error_msg)

            result = response.json()
            if "choices" in result and len(result["choices"]) > 0:
                token_count = len(result["choices"][0].get("text", ""))
                self.log.debug(
                    f"Text completion successful. Approximate response length: {token_count} characters"
                )

            return result

        except requests.exceptions.RequestException as e:
            self.log.error(f"Request failed: {str(e)}")
            raise LemonadeClientError(f"Request failed: {str(e)}")

    def _stream_completions_with_openai(
        self,
        model: str,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        stop: Optional[Union[str, List[str]]] = None,
        echo: bool = False,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        logprobs: Optional[bool] = None,
        **kwargs,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Stream completions using the OpenAI client.

        Returns chunks in the format:
        {
            "id": "...",
            "object": "text_completion",
            "created": 1742927481,
            "model": "...",
            "choices": [{
                "index": 0,
                "text": "...",
                "finish_reason": null
            }]
        }
        """
        client = OpenAI(
            base_url=self.base_url,
            api_key="lemonade",  # required, but unused
            timeout=timeout,
        )

        try:
            self.log.debug(f"Starting streaming text completion with model: {model}")
            # Create request parameters
            request_params = {
                "model": model,
                "prompt": prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stop": stop,
                "echo": echo,
                "stream": True,
                **kwargs,
            }

            if logprobs is not None:
                request_params["logprobs"] = logprobs

            response = client.completions.create(**request_params)

            tokens_generated = 0
            for chunk in response:
                tokens_generated += 1
                yield chunk.model_dump()

            self.log.debug(
                f"Completed streaming text completion. Generated {tokens_generated} tokens."
            )

        except (openai.APIError, openai.APIConnectionError, openai.RateLimitError) as e:
            error_type = e.__class__.__name__
            self.log.error(f"OpenAI {error_type}: {str(e)}")
            raise LemonadeClientError(f"OpenAI {error_type}: {str(e)}")
        except Exception as e:
            self.log.error(f"Error in OpenAI completion streaming: {str(e)}")
            raise LemonadeClientError(f"Error in OpenAI completion streaming: {str(e)}")

    def list_models(self) -> Dict[str, Any]:
        """
        List available models from the server.

        Returns:
            Dict containing the list of available models
        """
        url = f"{self.base_url}/models"
        return self._send_request("get", url)

    def pull_model(
        self,
        model_name: str,
        checkpoint: Optional[str] = None,
        recipe: Optional[str] = None,
        reasoning: Optional[bool] = None,
        mmproj: Optional[str] = None,
        timeout: int = DEFAULT_MODEL_LOAD_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Install a model on the server.

        Args:
            model_name: Model name to install
            checkpoint: HuggingFace checkpoint to install (for registering new models)
            recipe: Lemonade API recipe to load the model with (for registering new models)
            reasoning: Whether the model is a reasoning model (for registering new models)
            mmproj: Multimodal Projector file for vision models (for registering new models)
            timeout: Request timeout in seconds (longer for model installation)

        Returns:
            Dict containing the status of the pull operation

        Raises:
            LemonadeClientError: If the model installation fails
        """
        self.log.info(f"Installing {model_name}")

        request_data = {"model_name": model_name}

        if checkpoint:
            request_data["checkpoint"] = checkpoint
        if recipe:
            request_data["recipe"] = recipe
        if reasoning is not None:
            request_data["reasoning"] = reasoning
        if mmproj:
            request_data["mmproj"] = mmproj

        url = f"{self.base_url}/pull"
        try:
            response = self._send_request("post", url, request_data, timeout=timeout)
            self.log.info(f"Installed {model_name} successfully: response={response}")
            return response
        except Exception as e:
            message = f"Failed to install {model_name}: {e}"
            self.log.error(message)
            raise LemonadeClientError(message)

    def delete_model(
        self,
        model_name: str,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Delete a model from the server.

        Args:
            model_name: Model name to delete
            timeout: Request timeout in seconds

        Returns:
            Dict containing the status of the delete operation

        Raises:
            LemonadeClientError: If the model deletion fails
        """
        self.log.info(f"Deleting {model_name}")

        request_data = {"model_name": model_name}

        url = f"{self.base_url}/delete"
        try:
            response = self._send_request("post", url, request_data, timeout=timeout)
            self.log.info(f"Deleted {model_name} successfully: response={response}")
            return response
        except Exception as e:
            message = f"Failed to delete {model_name}: {e}"
            self.log.error(message)
            raise LemonadeClientError(message)

    def responses(
        self,
        model: str,
        input: Union[str, List[Dict[str, str]]],
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        stream: bool = False,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        **kwargs,
    ) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        """
        Call the responses endpoint.

        Args:
            model: The model to use for the response
            input: A string or list of dictionaries input for the model to respond to
            temperature: Controls randomness (higher = more random)
            max_output_tokens: Maximum number of output tokens to generate
            stream: Whether to stream the response
            timeout: Request timeout in seconds
            **kwargs: Additional parameters to pass to the API

        Returns:
            For non-streaming: Dict with response data
            For streaming: Generator yielding response events

        Example response (non-streaming):
        {
          "id": "0",
          "created_at": 1746225832.0,
          "model": "model-name",
          "object": "response",
          "output": [{
            "id": "0",
            "content": [{
              "annotations": [],
              "text": "Response text here"
            }]
          }]
        }
        """
        # Note: self.base_url already includes /api/v1
        url = f"{self.base_url}/responses"
        data = {
            "model": model,
            "input": input,
            "temperature": temperature,
            "stream": stream,
            **kwargs,
        }

        if max_output_tokens:
            data["max_output_tokens"] = max_output_tokens

        try:
            self.log.debug(f"Sending responses request to model: {model}")
            response = requests.post(
                url,
                json=data,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )

            if response.status_code != 200:
                error_msg = f"Error in responses (status {response.status_code}): {response.text}"
                self.log.error(error_msg)
                raise LemonadeClientError(error_msg)

            if stream:
                # For streaming responses, we need to handle server-sent events
                # This is a simplified implementation - full SSE parsing might be needed
                return self._parse_sse_stream(response)
            else:
                result = response.json()
                if "output" in result and len(result["output"]) > 0:
                    content = result["output"][0].get("content", [])
                    if content and len(content) > 0:
                        text_length = len(content[0].get("text", ""))
                        self.log.debug(
                            f"Response successful. Approximate response length: {text_length} characters"
                        )
                return result

        except requests.exceptions.RequestException as e:
            self.log.error(f"Request failed: {str(e)}")
            raise LemonadeClientError(f"Request failed: {str(e)}")

    def _parse_sse_stream(self, response) -> Generator[Dict[str, Any], None, None]:
        """
        Parse server-sent events from streaming responses endpoint.

        This is a simplified implementation that may need enhancement
        for full SSE specification compliance.
        """
        for line in response.iter_lines(decode_unicode=True):
            if line.startswith("data: "):
                try:
                    data = line[6:]  # Remove "data: " prefix
                    if data.strip() == "[DONE]":
                        break
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue

    def load_model(
        self,
        model_name: str,
        timeout: int = DEFAULT_MODEL_LOAD_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Load a model on the server.

        Args:
            model_name: Model name to load
            timeout: Request timeout in seconds (longer for model loading)

        Returns:
            Dict containing the status of the load operation

        Raises:
            LemonadeClientError: If the model_name loading fails
        """
        self.log.info(f"Loading {model_name}")

        request_data = {"model_name": model_name}

        url = f"{self.base_url}/load"
        try:
            response = self._send_request("post", url, request_data, timeout=timeout)
            self.log.info(f"Loaded {model_name} successfully: response={response}")
            self.model = model_name
            return response
        except Exception as e:
            # Preserve the original error details from the server
            original_error = str(e)
            self.log.error(f"Failed to load {model_name}: {original_error}")

            # Don't double-wrap LemonadeClientErrors - just re-raise with additional context
            if isinstance(e, LemonadeClientError):
                raise LemonadeClientError(
                    f"Model loading failed for '{model_name}': {original_error}"
                )
            else:
                raise LemonadeClientError(
                    f"Failed to load {model_name}: {original_error}"
                )

    def unload_model(self) -> Dict[str, Any]:
        """
        Unload the current model from the server.

        Returns:
            Dict containing the status of the unload operation
        """
        url = f"{self.base_url}/unload"
        response = self._send_request("post", url)
        self.model = None
        self.log.info(f"Model unloaded successfully: {response}")
        return response

    def set_params(
        self,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        do_sample: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Set generation parameters for text completion.

        Args:
            temperature: Controls randomness (higher = more random)
            top_p: Controls diversity via nucleus sampling
            top_k: Controls diversity by limiting to k most likely tokens
            min_length: Minimum length of generated text in tokens
            max_length: Maximum length of generated text in tokens
            do_sample: Whether to use sampling or greedy decoding

        Returns:
            Dict containing the status and updated parameters
        """
        request_data = {}

        if temperature is not None:
            request_data["temperature"] = temperature
        if top_p is not None:
            request_data["top_p"] = top_p
        if top_k is not None:
            request_data["top_k"] = top_k
        if min_length is not None:
            request_data["min_length"] = min_length
        if max_length is not None:
            request_data["max_length"] = max_length
        if do_sample is not None:
            request_data["do_sample"] = do_sample

        url = f"{self.base_url}/params"
        return self._send_request("post", url, request_data)

    def health_check(self) -> Dict[str, Any]:
        """
        Check server health.

        Returns:
            Dict containing the server status and loaded model

        Raises:
            LemonadeClientError: If the health check fails
        """
        url = f"{self.base_url}/health"
        return self._send_request("get", url)

    def get_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics from the last request.

        Returns:
            Dict containing performance statistics
        """
        url = f"{self.base_url}/stats"
        return self._send_request("get", url)

    def ready(self) -> bool:
        """
        Check if the client is ready for use.

        Returns:
            bool: True if the client exists and the server is healthy, False otherwise
        """
        try:
            # Check if client exists and server is healthy
            health = self.health_check()
            return health.get("status") == "ok"
        except Exception:
            return False

    def _send_request(
        self,
        method: str,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
    ) -> Dict[str, Any]:
        """
        Send a request to the server and return the response.

        Args:
            method: HTTP method (get, post, etc.)
            url: URL to send the request to
            data: Request payload
            timeout: Request timeout in seconds

        Returns:
            Response as a dict

        Raises:
            LemonadeClientError: If the request fails
        """
        try:
            headers = {"Content-Type": "application/json"}

            if method.lower() == "get":
                response = requests.get(url, headers=headers, timeout=timeout)
            elif method.lower() == "post":
                response = requests.post(
                    url, json=data, headers=headers, timeout=timeout
                )
            else:
                raise LemonadeClientError(f"Unsupported HTTP method: {method}")

            if response.status_code >= 400:
                raise LemonadeClientError(
                    f"Request failed with status {response.status_code}: {response.text}"
                )

            return response.json()

        except requests.exceptions.RequestException as e:
            raise LemonadeClientError(f"Request failed: {str(e)}")
        except json.JSONDecodeError:
            raise LemonadeClientError(
                f"Failed to parse response as JSON: {response.text}"
            )


def create_lemonade_client(
    model: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    auto_start: bool = False,
    auto_load: bool = False,
    auto_pull: bool = True,
    verbose: bool = True,
    background: str = "terminal",
    keep_alive: bool = False,
) -> LemonadeClient:
    """
    Factory function to create and configure a LemonadeClient instance.

    This function provides a simplified way to create a LemonadeClient instance
    with proper configuration from environment variables and/or explicit parameters.

    Args:
        model: Name of the model to use (defaults to env var LEMONADE_MODEL or DEFAULT_MODEL_NAME)
        host: Host address for the Lemonade server (defaults to env var LEMONADE_HOST or DEFAULT_HOST)
        port: Port number for the Lemonade server (defaults to env var LEMONADE_PORT or DEFAULT_PORT)
        auto_start: Automatically start the server
        auto_load: Automatically load the model
        auto_pull: Whether to automatically pull the model if it's not available (when auto_load=True)
        verbose: Whether to enable verbose logging
        background: How to run the server if auto_start is True:
                   - "terminal": Launch in a new terminal window (default)
                   - "silent": Run in background with output to log file
                   - "none": Run in foreground
        keep_alive: If True, don't terminate server when client is deleted

    Returns:
        A configured LemonadeClient instance
    """
    # Get configuration from environment variables with fallbacks to defaults
    env_model = os.environ.get("LEMONADE_MODEL")
    env_host = os.environ.get("LEMONADE_HOST")
    env_port = os.environ.get("LEMONADE_PORT")

    # Prioritize explicit parameters over environment variables over defaults
    model_name = model or env_model or DEFAULT_MODEL_NAME
    server_host = host or env_host or DEFAULT_HOST
    server_port = port or (int(env_port) if env_port else DEFAULT_PORT)

    # Create the client
    client = LemonadeClient(
        model=model_name,
        host=server_host,
        port=server_port,
        verbose=verbose,
        keep_alive=keep_alive,
    )

    # Auto-start server if requested
    if auto_start:
        try:
            # Check if server is already running
            try:
                client.health_check()
                client.log.info("Lemonade server is already running")
            except LemonadeClientError:
                # Server not running, start it
                client.log.info(
                    f"Starting Lemonade server at {server_host}:{server_port}"
                )
                client.launch_server(background=background)

                # Perform a health check to verify the server is running
                client.health_check()
        except Exception as e:
            client.log.error(f"Failed to start Lemonade server: {str(e)}")
            raise LemonadeClientError(f"Failed to start Lemonade server: {str(e)}")

    # Auto-load model if requested
    if auto_load:
        try:
            # Check if auto_pull is enabled and model needs to be pulled first
            if auto_pull:
                # Check if model is available
                models_response = client.list_models()
                available_models = [
                    model.get("id", "") for model in models_response.get("data", [])
                ]

                if model_name not in available_models:
                    client.log.info(
                        f"Model '{model_name}' not found in registry. Available models: {available_models}"
                    )
                    client.log.info(
                        f"Attempting to pull model '{model_name}' before loading..."
                    )

                    try:
                        # Try to pull the model first
                        pull_result = client.pull_model(
                            model_name, timeout=300
                        )  # 5 min timeout for download
                        client.log.info(f"Successfully pulled model: {pull_result}")
                    except Exception as pull_error:
                        client.log.warning(
                            f"Failed to pull model '{model_name}': {pull_error}"
                        )
                        client.log.info(
                            "Proceeding with load anyway - server may auto-install"
                        )
                else:
                    client.log.info(
                        f"Model '{model_name}' found in registry, proceeding with load"
                    )

            # Now attempt to load the model
            client.load_model(model_name, timeout=60)
        except Exception as e:
            # Extract detailed error information
            error_details = str(e)
            client.log.error(f"Failed to load {model_name}: {error_details}")

            # Try to get more details about available models for debugging
            try:
                models_response = client.list_models()
                available_models = [
                    model.get("id", "unknown")
                    for model in models_response.get("data", [])
                ]
                client.log.error(f"Available models: {available_models}")
                client.log.error(f"Attempted to load: {model_name}")
                if available_models:
                    client.log.error(
                        "Consider using one of the available models instead"
                    )
            except Exception as list_error:
                client.log.error(f"Could not list available models: {list_error}")

            # Include both original error and context in the raised exception
            enhanced_message = f"Failed to load {model_name}: {error_details}"
            if "available_models" in locals() and available_models:
                enhanced_message += f" (Available models: {available_models})"

            raise LemonadeClientError(enhanced_message)

    return client


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Use the new factory function instead of direct instantiation
    client = create_lemonade_client(
        model=DEFAULT_MODEL_NAME,
        auto_start=True,
        auto_load=True,
        verbose=True,
    )

    try:
        # Check server health
        try:
            health = client.health_check()
            print(f"Server health: {health}")
        except Exception as e:
            print(f"Health check failed: {e}")

        # List available models
        try:
            print("\nListing available models:")
            models_list = client.list_models()
            print(json.dumps(models_list, indent=2))
        except Exception as e:
            print(f"Failed to list models: {e}")

        # Example: Using chat completions
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"},
        ]

        try:
            print("\nNon-streaming response:")
            response = client.chat_completions(
                model=DEFAULT_MODEL_NAME, messages=messages, timeout=30
            )
            print(response["choices"][0]["message"]["content"])
        except Exception as e:
            print(f"Chat completion failed: {e}")

        try:
            print("\nStreaming response:")
            for chunk in client.chat_completions(
                model=DEFAULT_MODEL_NAME, messages=messages, stream=True, timeout=30
            ):
                if "choices" in chunk and chunk["choices"][0].get("delta", {}).get(
                    "content"
                ):
                    print(chunk["choices"][0]["delta"]["content"], end="", flush=True)
        except Exception as e:
            print(f"Streaming chat completion failed: {e}")

        print("\n\nDone!")

    except Exception as e:
        print(f"Error occurred: {e}")
    finally:
        # Make sure to terminate the server when done
        client.terminate_server()
