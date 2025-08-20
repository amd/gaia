# Standard library imports
import logging
import os
from typing import Optional, Dict, Any, Literal, Union, Iterator

# Third-party imports
import requests
from dotenv import load_dotenv
from openai import OpenAI

# Local imports
from .lemonade_client import DEFAULT_MODEL_NAME

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Explicitly set module logger level

# Load environment variables from .env file
load_dotenv()


class LLMClient:
    def __init__(
        self,
        use_local: bool = False,
        system_prompt: Optional[str] = None,
        base_url: Optional[str] = "http://localhost:8000/api/v0",
    ):
        """
        Initialize the LLM client.

        Args:
            use_local: If True, uses the local LLM server. Otherwise uses OpenAI API.
            system_prompt: Default system prompt to use for all generation requests.
        """
        logger.debug(
            f"Initializing LLMClient with use_local={use_local}, base_url={base_url}"
        )
        if use_local:
            self.client = OpenAI(base_url=base_url, api_key="None")
            self.endpoint = "completions"
            # self.endpoint = "responses" TODO: Put back once new Lemonade version is released.
            self.default_model = DEFAULT_MODEL_NAME
            logger.debug(f"Using local LLM with model={self.default_model}")
        else:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY not found in environment variables. Please add it to your .env file."
                )
            self.client = OpenAI(api_key=api_key)
            self.endpoint = "responses"
            self.default_model = "gpt-4.1"
            logger.debug(f"Using OpenAI API with model={self.default_model}")

        self.base_url = base_url
        self.system_prompt = system_prompt
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

        if endpoint_to_use == "completions":
            # For local LLM, combine system prompt and user prompt if system prompt exists
            effective_prompt = prompt
            if effective_system_prompt:
                # Use Llama 3 format
                effective_prompt = (
                    f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n"
                    f"{effective_system_prompt}\n"
                    f"<|eot_id|><|start_header_id|>user<|end_header_id|>\n"
                    f"{prompt}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n"
                )
                logger.debug(
                    f"Formatted prompt for local LLM: {effective_prompt[:200]}..."
                )

            try:
                # Set stream parameter in the API call
                response = self.client.completions.create(
                    model=model,
                    prompt=effective_prompt,
                    stop=[
                        "<|eot_id|>",
                        "<|start_header_id|>",
                    ],  # Stop at end of turn or next header
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
                    logger.debug(f"Local LLM response: {result[:200]}...")
                    return result
            except Exception as e:
                logger.error(f"Error generating response from local LLM: {str(e)}")
                raise
        else:
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


def main():
    # Example usage with local LLM
    system_prompt = "You are a creative assistant who specializes in short stories."

    local_llm = LLMClient(use_local=True, system_prompt=system_prompt)

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

    # Example usage with OpenAI API
    openai_llm = LLMClient(use_local=False, system_prompt=system_prompt)

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
