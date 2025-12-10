# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Standard library imports
import logging
import os
import time
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Literal,
    Optional,
    TypeVar,
    Union,
)

import httpx

# Third-party imports
import requests
from dotenv import load_dotenv
from openai import OpenAI

from ..version import LEMONADE_VERSION

# Local imports
from .lemonade_client import DEFAULT_MODEL_NAME

# Default Lemonade server URL (can be overridden via LEMONADE_BASE_URL env var)
DEFAULT_LEMONADE_URL = "http://localhost:8000/api/v1"

# Type variable for retry decorator
T = TypeVar("T")

# Conditional import for Claude
try:
    from ..eval.claude import ClaudeClient as AnthropicClaudeClient

    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Explicitly set module logger level

# Load environment variables from .env file
load_dotenv()


class LLMClient:
    def __init__(
        self,
        use_claude: bool = False,
        use_openai: bool = False,
        system_prompt: Optional[str] = None,
        base_url: Optional[str] = None,
        claude_model: str = "claude-sonnet-4-20250514",
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ):
        """
        Initialize the LLM client.

        Args:
            use_claude: If True, uses Anthropic Claude API.
            use_openai: If True, uses OpenAI ChatGPT API.
            system_prompt: Default system prompt to use for all generation requests.
            base_url: Base URL for local LLM server (defaults to LEMONADE_BASE_URL env var).
            claude_model: Claude model to use (e.g., "claude-sonnet-4-20250514").
            max_retries: Maximum number of retry attempts on connection errors.
            retry_base_delay: Base delay in seconds for exponential backoff.

        Note: Uses local LLM server by default unless use_claude or use_openai is True.
              Context size is configured when starting the Lemonade server with --ctx-size parameter.
        """
        # Use provided base_url, fall back to env var, then default
        if base_url is None:
            base_url = os.getenv("LEMONADE_BASE_URL", DEFAULT_LEMONADE_URL)

        # Normalize base_url to ensure it has the /api/v1 suffix for Lemonade server
        # This allows users to specify just "http://localhost:8000" for convenience
        if base_url and not base_url.endswith("/api/v1"):
            # Remove trailing slash if present
            base_url = base_url.rstrip("/")
            # Add /api/v1 if the URL looks like a Lemonade server (localhost or IP with port)
            # but doesn't already have a path beyond the port
            from urllib.parse import urlparse

            parsed = urlparse(base_url)
            # Only add /api/v1 if path is empty or just "/"
            if not parsed.path or parsed.path == "/":
                base_url = f"{base_url}/api/v1"
                logger.debug(f"Normalized base_url to: {base_url}")

        # Compute use_local: True if neither claude nor openai is selected
        use_local = not (use_claude or use_openai)

        logger.debug(
            f"Initializing LLMClient with use_local={use_local}, use_claude={use_claude}, use_openai={use_openai}, base_url={base_url}"
        )

        self.use_claude = use_claude
        self.use_openai = use_openai
        self.base_url = base_url
        self.system_prompt = system_prompt
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

        if use_local:
            # Configure timeout for local LLM server
            # For streaming: timeout between chunks (read timeout)
            # For non-streaming: total timeout for the entire response
            self.client = OpenAI(
                base_url=base_url,
                api_key="None",
                timeout=httpx.Timeout(
                    connect=15.0,  # 15 seconds to establish connection
                    read=120.0,  # 120 seconds between data chunks (matches Lemonade DEFAULT_REQUEST_TIMEOUT)
                    write=15.0,  # 15 seconds to send request
                    pool=15.0,  # 15 seconds to acquire connection from pool
                ),
                max_retries=0,  # Disable retries to fail fast on connection issues
            )
            # Use completions endpoint for pre-formatted prompts (ChatSDK compatibility)
            # Use chat endpoint when messages array is explicitly provided
            self.endpoint = "completions"
            logger.debug("Using Lemonade completions endpoint")
            self.default_model = DEFAULT_MODEL_NAME
            self.claude_client = None
            logger.debug(f"Using local LLM with model={self.default_model}")
        elif use_claude and CLAUDE_AVAILABLE:
            # Use Claude API
            self.claude_client = AnthropicClaudeClient(model=claude_model)
            self.client = None
            self.endpoint = "claude"
            self.default_model = claude_model
            logger.debug(f"Using Claude API with model={self.default_model}")
        elif use_claude and not CLAUDE_AVAILABLE:
            raise ValueError(
                "Claude support requested but anthropic library not available. Install with: pip install anthropic"
            )
        elif use_openai:
            # Use OpenAI API
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY not found in environment variables. Please add it to your .env file."
                )
            self.client = OpenAI(api_key=api_key)
            self.claude_client = None
            self.endpoint = "openai"
            self.default_model = "gpt-4o"  # Updated to latest model
            logger.debug(f"Using OpenAI API with model={self.default_model}")
        else:
            # This should not happen with the new logic, but keep as fallback
            raise ValueError("Invalid LLM provider configuration")
        if system_prompt:
            logger.debug(f"System prompt set: {system_prompt[:100]}...")

    def _retry_with_exponential_backoff(
        self,
        func: Callable[..., T],
        *args,
        **kwargs,
    ) -> T:
        """
        Execute a function with exponential backoff retry on connection errors.

        Args:
            func: The function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            The result of the function call

        Raises:
            The last exception if all retries are exhausted
        """
        delay = self.retry_base_delay
        max_delay = 60.0
        exponential_base = 2.0

        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except (
                ConnectionError,
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.NetworkError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as e:
                if attempt == self.max_retries:
                    logger.error(
                        f"Max retries ({self.max_retries}) reached for {func.__name__}. "
                        f"Last error: {str(e)}"
                    )
                    raise

                # Calculate next delay with exponential backoff
                wait_time = min(delay, max_delay)
                logger.warning(
                    f"Connection error in {func.__name__} (attempt {attempt + 1}/{self.max_retries + 1}): {str(e)}. "
                    f"Retrying in {wait_time:.1f}s..."
                )

                time.sleep(wait_time)
                delay *= exponential_base

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        endpoint: Optional[Literal["completions", "chat", "claude", "openai"]] = None,
        system_prompt: Optional[str] = None,
        stream: bool = False,
        messages: Optional[List[Dict[str, str]]] = None,
        **kwargs: Any,
    ) -> Union[str, Iterator[str]]:
        """
        Generate a response from the LLM.

        Args:
            prompt: The user prompt/query to send to the LLM. For chat endpoint,
                   if messages is not provided, this is treated as a pre-formatted
                   prompt string that already contains the full conversation.
            model: The model to use (defaults to endpoint-appropriate model)
            endpoint: Override the endpoint to use (completions, chat, claude, or openai)
            system_prompt: System prompt to use for this specific request (overrides default)
            stream: If True, returns a generator that yields chunks of the response as they become available
            messages: Optional list of message dicts with 'role' and 'content' keys.
                     If provided, these are used directly for chat completions instead of prompt.
            **kwargs: Additional parameters to pass to the API

        Returns:
            If stream=False: The complete generated text as a string
            If stream=True: A generator yielding chunks of the response as they become available
        """
        model = model or self.default_model
        endpoint_to_use = endpoint or self.endpoint
        logger.debug(
            f"LLMClient.generate() called with model={model}, endpoint={endpoint_to_use}, stream={stream}"
        )

        # Use provided system_prompt, fall back to instance default if not provided
        effective_system_prompt = (
            system_prompt if system_prompt is not None else self.system_prompt
        )
        logger.debug(
            f"Using system prompt: {effective_system_prompt[:100] if effective_system_prompt else 'None'}..."
        )

        if endpoint_to_use == "claude":
            # For Claude API, construct the prompt appropriately
            if effective_system_prompt:
                # Claude handles system prompts differently in messages format
                full_prompt = f"System: {effective_system_prompt}\n\nHuman: {prompt}"
            else:
                full_prompt = prompt

            logger.debug(f"Using Claude API with prompt: {full_prompt[:200]}...")

            try:
                if stream:
                    logger.warning(
                        "Streaming not yet implemented for Claude API, falling back to non-streaming"
                    )

                # Use Claude client with retry logic
                logger.debug("Making request to Claude API")

                # Use retry logic for the API call
                result = self._retry_with_exponential_backoff(
                    self.claude_client.get_completion, full_prompt
                )

                # Claude returns a list of content blocks, extract text
                if isinstance(result, list) and len(result) > 0:
                    # Each content block has a 'text' attribute
                    text_parts = []
                    for content_block in result:
                        if hasattr(content_block, "text"):
                            text_parts.append(content_block.text)
                        else:
                            text_parts.append(str(content_block))
                    result = "".join(text_parts)
                elif isinstance(result, str):
                    pass  # result is already a string
                else:
                    result = str(result)

                # Check for empty responses
                if not result or not result.strip():
                    logger.warning("Empty response from Claude API")

                # Debug: log the response structure for troubleshooting
                logger.debug(f"Claude response length: {len(result)}")
                logger.debug(f"Claude response preview: {result[:300]}...")

                # Claude sometimes returns valid JSON followed by additional text
                # Try to extract just the JSON part if it exists
                result = self._clean_claude_response(result)

                return result
            except Exception as e:
                logger.error(f"Error generating response from Claude API: {str(e)}")
                raise
        elif endpoint_to_use == "completions":
            # For local LLM with pre-formatted prompts (ChatSDK uses this)
            # The prompt already contains the full formatted conversation
            logger.debug(
                f"Using completions endpoint: prompt_length={len(prompt)} chars"
            )

            try:
                # Use retry logic for the API call
                response = self._retry_with_exponential_backoff(
                    self.client.completions.create,
                    model=model,
                    prompt=prompt,
                    temperature=0.1,
                    stream=stream,
                    **kwargs,
                )

                if stream:
                    # Return a generator that yields chunks
                    def stream_generator():
                        for chunk in response:
                            if (
                                hasattr(chunk.choices[0], "text")
                                and chunk.choices[0].text
                            ):
                                yield chunk.choices[0].text

                    return stream_generator()
                else:
                    # Return the complete response
                    result = response.choices[0].text
                    if not result or not result.strip():
                        logger.warning("Empty response from local LLM")
                    return result
            except (
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as e:
                logger.error(f"Network error connecting to local LLM server: {str(e)}")
                error_msg = f"LLM Server Connection Error: {str(e)}"
                raise ConnectionError(error_msg) from e
            except Exception as e:
                error_str = str(e)
                logger.error(f"Error generating response from local LLM: {error_str}")

                if "404" in error_str:
                    if (
                        "endpoint" in error_str.lower()
                        or "not found" in error_str.lower()
                    ):
                        raise ConnectionError(
                            f"API endpoint error: {error_str}\n\n"
                            f"This may indicate:\n"
                            f"  1. Lemonade Server version mismatch (try updating to {LEMONADE_VERSION})\n"
                            f"  2. Model not properly loaded or corrupted\n\n"
                            f"To fix model issues, try:\n"
                            f"  lemonade model remove <model-name>\n"
                            f"  lemonade model download <model-name>\n"
                        ) from e

                if "network" in error_str.lower() or "connection" in error_str.lower():
                    raise ConnectionError(f"LLM Server Error: {error_str}") from e
                raise
        elif endpoint_to_use == "chat":
            # For local LLM using chat completions format (Lemonade v9+)
            if messages:
                # Use provided messages directly (proper chat history support)
                chat_messages = list(messages)
                # Prepend system prompt if provided and not already in messages
                if effective_system_prompt and (
                    not chat_messages or chat_messages[0].get("role") != "system"
                ):
                    chat_messages.insert(
                        0, {"role": "system", "content": effective_system_prompt}
                    )
            else:
                # Treat prompt as pre-formatted string (legacy ChatSDK support)
                # Pass as single user message - the prompt already contains formatted history
                chat_messages = []
                if effective_system_prompt:
                    chat_messages.append(
                        {"role": "system", "content": effective_system_prompt}
                    )
                chat_messages.append({"role": "user", "content": prompt})
            logger.debug(
                f"Using chat completions for local LLM: {len(chat_messages)} messages"
            )

            try:
                # Use retry logic for the API call
                response = self._retry_with_exponential_backoff(
                    self.client.chat.completions.create,
                    model=model,
                    messages=chat_messages,
                    temperature=0.1,
                    stream=stream,
                    **kwargs,
                )

                if stream:
                    # Return a generator that yields chunks
                    def stream_generator():
                        for chunk in response:
                            if (
                                hasattr(chunk.choices[0].delta, "content")
                                and chunk.choices[0].delta.content
                            ):
                                yield chunk.choices[0].delta.content

                    return stream_generator()
                else:
                    # Return the complete response
                    result = response.choices[0].message.content
                    if not result or not result.strip():
                        logger.warning("Empty response from local LLM")
                    return result
            except (
                httpx.ConnectError,
                httpx.TimeoutException,
                httpx.NetworkError,
            ) as e:
                logger.error(f"Network error connecting to local LLM server: {str(e)}")
                error_msg = f"LLM Server Connection Error: {str(e)}"
                raise ConnectionError(error_msg) from e
            except Exception as e:
                error_str = str(e)
                logger.error(f"Error generating response from local LLM: {error_str}")

                # Check for 404 errors which might indicate endpoint or model issues
                if "404" in error_str:
                    if (
                        "endpoint" in error_str.lower()
                        or "not found" in error_str.lower()
                    ):
                        raise ConnectionError(
                            f"API endpoint error: {error_str}\n\n"
                            f"This may indicate:\n"
                            f"  1. Lemonade Server version mismatch (try updating to {LEMONADE_VERSION})\n"
                            f"  2. Model not properly loaded or corrupted\n\n"
                            f"To fix model issues, try:\n"
                            f"  lemonade model remove <model-name>\n"
                            f"  lemonade model download <model-name>\n"
                        ) from e

                if "network" in error_str.lower() or "connection" in error_str.lower():
                    raise ConnectionError(f"LLM Server Error: {error_str}") from e
                raise
        elif endpoint_to_use == "openai":
            # For OpenAI API, use the messages format
            messages = []
            if effective_system_prompt:
                messages.append({"role": "system", "content": effective_system_prompt})
            messages.append({"role": "user", "content": prompt})
            logger.debug(f"OpenAI API messages: {messages}")

            try:
                # Use retry logic for the API call
                response = self._retry_with_exponential_backoff(
                    self.client.chat.completions.create,
                    model=model,
                    messages=messages,
                    stream=stream,
                    **kwargs,
                )

                if stream:
                    # Return a generator that yields chunks
                    def stream_generator():
                        for chunk in response:
                            if (
                                hasattr(chunk.choices[0].delta, "content")
                                and chunk.choices[0].delta.content
                            ):
                                yield chunk.choices[0].delta.content

                    return stream_generator()
                else:
                    # Return the complete response as before
                    result = response.choices[0].message.content
                    logger.debug(f"OpenAI API response: {result[:200]}...")
                    return result
            except Exception as e:
                logger.error(f"Error generating response from OpenAI API: {str(e)}")
                raise
        else:
            raise ValueError(
                f"Unsupported endpoint: {endpoint_to_use}. Supported endpoints: 'completions', 'chat', 'claude', 'openai'."
            )

    def get_performance_stats(self) -> Dict[str, Any]:
        """
        Get performance statistics from the last LLM request.

        Returns:
            Dictionary containing performance statistics like:
            - time_to_first_token: Time in seconds until first token is generated
            - tokens_per_second: Rate of token generation
            - input_tokens: Number of tokens in the input
            - output_tokens: Number of tokens in the output
        """
        if not self.base_url:
            # Return empty stats if not using local LLM
            return {
                "time_to_first_token": None,
                "tokens_per_second": None,
                "input_tokens": None,
                "output_tokens": None,
            }

        try:
            # Use the Lemonade API v1 stats endpoint
            # This returns both timing stats and token counts
            stats_url = f"{self.base_url}/stats"
            response = requests.get(stats_url)

            if response.status_code == 200:
                stats = response.json()
                # Remove decode_token_times as it's too verbose
                if "decode_token_times" in stats:
                    del stats["decode_token_times"]
                return stats
            else:
                logger.warning(
                    f"Failed to get stats: {response.status_code} - {response.text}"
                )
                return {}
        except Exception as e:
            logger.warning(f"Error fetching performance stats: {str(e)}")
            return {}

    def is_generating(self) -> bool:
        """
        Check if the local LLM is currently generating.

        Returns:
            bool: True if generating, False otherwise

        Note:
            Only available when using local LLM (use_local=True).
            Returns False for OpenAI API usage.
        """
        if not self.base_url:
            logger.debug("is_generating(): Not using local LLM, returning False")
            return False

        try:
            # Check the generating endpoint
            # Remove /api/v1 suffix to access root-level endpoints
            base = self.base_url.replace("/api/v1", "")
            generating_url = f"{base}/generating"
            response = requests.get(generating_url)
            if response.status_code == 200:
                response_data = response.json()
                is_gen = response_data.get("is_generating", False)
                logger.debug(f"Generation status check: {is_gen}")
                return is_gen
            else:
                logger.warning(
                    f"Failed to check generation status: {response.status_code} - {response.text}"
                )
                return False
        except Exception as e:
            logger.warning(f"Error checking generation status: {str(e)}")
            return False

    def halt_generation(self) -> bool:
        """
        Halt current generation on the local LLM server.

        Returns:
            bool: True if halt was successful, False otherwise

        Note:
            Only available when using local LLM (use_local=True).
            Does nothing for OpenAI API usage.
        """
        if not self.base_url:
            logger.debug("halt_generation(): Not using local LLM, nothing to halt")
            return False

        try:
            # Send halt request
            # Remove /api/v1 suffix to access root-level endpoints
            base = self.base_url.replace("/api/v1", "")
            halt_url = f"{base}/halt"
            response = requests.get(halt_url)
            if response.status_code == 200:
                logger.debug("Successfully halted current generation")
                return True
            else:
                logger.warning(
                    f"Failed to halt generation: {response.status_code} - {response.text}"
                )
                return False
        except Exception as e:
            logger.warning(f"Error halting generation: {str(e)}")
            return False

    def _clean_claude_response(self, response: str) -> str:
        """
        Extract valid JSON from Claude responses that may contain extra content after the JSON.

        Args:
            response: The raw response from Claude API

        Returns:
            Cleaned response with only the JSON portion
        """
        import json

        if not response or not response.strip():
            return response

        # Try to parse as-is first
        try:
            json.loads(response.strip())
            return response.strip()
        except json.JSONDecodeError:
            pass

        # Look for JSON object patterns
        # Find the first { and try to extract a complete JSON object
        start_idx = response.find("{")
        if start_idx == -1:
            # No JSON object found, return as-is
            return response

        # Find the matching closing brace by counting braces
        brace_count = 0
        end_idx = -1

        for i in range(start_idx, len(response)):
            char = response[i]
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i
                    break

        if end_idx == -1:
            # No complete JSON object found
            return response

        # Extract the JSON portion
        json_portion = response[start_idx : end_idx + 1]

        # Validate that it's valid JSON
        try:
            json.loads(json_portion)
            logger.debug(
                f"Extracted JSON from Claude response: {len(json_portion)} chars vs original {len(response)} chars"
            )
            return json_portion
        except json.JSONDecodeError:
            # If extracted portion is not valid JSON, return original
            logger.debug(
                "Could not extract valid JSON from Claude response, returning original"
            )
            return response


def main():
    # Example usage with local LLM
    system_prompt = "You are a creative assistant who specializes in short stories."

    local_llm = LLMClient(system_prompt=system_prompt)

    # Non-streaming example
    result = local_llm.generate("Write a one-sentence bedtime story about a unicorn.")
    print(f"Local LLM response:\n{result}")
    print(f"Local LLM stats:\n{local_llm.get_performance_stats()}")

    # Halt functionality demo (only for local LLM)
    print(f"\nHalt functionality available: {local_llm.is_generating()}")

    # Streaming example
    print("\nLocal LLM streaming response:")
    for chunk in local_llm.generate(
        "Write a one-sentence bedtime story about a dragon.", stream=True
    ):
        print(chunk, end="", flush=True)
    print("\n")

    # Example usage with Claude API
    if CLAUDE_AVAILABLE:
        claude_llm = LLMClient(use_claude=True, system_prompt=system_prompt)

        # Non-streaming example
        result = claude_llm.generate(
            "Write a one-sentence bedtime story about a unicorn."
        )
        print(f"\nClaude API response:\n{result}")

    # Example usage with OpenAI API
    openai_llm = LLMClient(use_openai=True, system_prompt=system_prompt)

    # Non-streaming example
    result = openai_llm.generate("Write a one-sentence bedtime story about a unicorn.")
    print(f"\nOpenAI API response:\n{result}")

    # Streaming example
    print("\nOpenAI API streaming response:")
    for chunk in openai_llm.generate(
        "Write a one-sentence bedtime story about a dragon.", stream=True
    ):
        print(chunk, end="", flush=True)
    print("\n")


if __name__ == "__main__":
    main()
