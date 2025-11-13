# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Standard library imports
import logging
import os
from typing import Any, Dict, Iterator, Literal, Optional, Union

import httpx

# Third-party imports
import requests
from dotenv import load_dotenv
from openai import OpenAI

# Local imports
from .lemonade_client import DEFAULT_MODEL_NAME

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
        base_url: Optional[str] = "http://localhost:8000/api/v0",
        claude_model: str = "claude-sonnet-4-20250514",
    ):
        """
        Initialize the LLM client.

        Args:
            use_claude: If True, uses Anthropic Claude API.
            use_openai: If True, uses OpenAI ChatGPT API.
            system_prompt: Default system prompt to use for all generation requests.
            base_url: Base URL for local LLM server.
            claude_model: Claude model to use (e.g., "claude-sonnet-4-20250514").

        Note: Uses local LLM server by default unless use_claude or use_openai is True.
              Context size is configured when starting the Lemonade server with --ctx-size parameter.
        """
        # Compute use_local: True if neither claude nor openai is selected
        use_local = not (use_claude or use_openai)

        logger.debug(
            f"Initializing LLMClient with use_local={use_local}, use_claude={use_claude}, use_openai={use_openai}, base_url={base_url}"
        )

        self.use_claude = use_claude
        self.use_openai = use_openai
        self.base_url = base_url
        self.system_prompt = system_prompt

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
            self.endpoint = "completions"
            # self.endpoint = "responses" TODO: Put back once new Lemonade version is released.
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

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        endpoint: Optional[Literal["completions", "responses"]] = None,
        system_prompt: Optional[str] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[str, Iterator[str]]:
        """
        Generate a response from the LLM.

        Args:
            prompt: The user prompt/query to send to the LLM
            model: The model to use (defaults to endpoint-appropriate model)
            endpoint: Override the endpoint to use (completions or responses)
            system_prompt: System prompt to use for this specific request (overrides default)
            stream: If True, returns a generator that yields chunks of the response as they become available
            **kwargs: Additional parameters to pass to the API

        Returns:
            If stream=False: The complete generated text as a string
            If stream=True: A generator yielding chunks of the response as they become available
        """
        model = model or self.default_model
        endpoint_to_use = endpoint or self.endpoint
        logger.debug(
            f"Generating response with model={model}, endpoint={endpoint_to_use}, stream={stream}"
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

                # Use Claude client
                logger.debug("Making request to Claude API")
                result = self.claude_client.get_completion(full_prompt)

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
            # For local LLM, use the prompt as-is
            # ChatSDK handles all formatting including system prompts
            effective_prompt = prompt
            logger.debug(f"Using raw prompt for local LLM: {effective_prompt[:200]}...")

            try:
                # Set stream parameter in the API call
                # Stop tokens should be provided by caller if needed
                logger.debug(
                    f"Making LLM request to {self.base_url} with timeout settings"
                )
                response = self.client.completions.create(
                    model=model,
                    prompt=effective_prompt,
                    temperature=0.1,  # Lower temperature for more consistent JSON output
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
                    # Return the complete response as before
                    result = response.choices[0].text

                    # Check for empty responses
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
                logger.error(f"Error generating response from local LLM: {str(e)}")
                # Check if this is a network-related error
                if "network" in str(e).lower() or "connection" in str(e).lower():
                    raise ConnectionError(f"LLM Server Error: {str(e)}") from e
                raise
        elif endpoint_to_use == "openai":
            # For OpenAI API, use the messages format
            messages = []
            if effective_system_prompt:
                messages.append({"role": "system", "content": effective_system_prompt})
            messages.append({"role": "user", "content": prompt})
            logger.debug(f"OpenAI API messages: {messages}")

            try:
                # Set stream parameter in the API call
                response = self.client.chat.completions.create(
                    model=model, messages=messages, stream=stream, **kwargs
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
                f"Unsupported endpoint: {endpoint_to_use}. Supported endpoints: 'completions', 'claude', 'openai'."
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
            # Extract the base URL from client configuration
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
            generating_url = f"{self.base_url.replace('/api/v0', '')}/generating"
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
            halt_url = f"{self.base_url.replace('/api/v0', '')}/halt"
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
