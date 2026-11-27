# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import logging

try:
    import anthropic
except ImportError:
    anthropic = None


from gaia.env import load_env
from gaia.eval.claude_credential import announce, api_key_credential
from gaia.eval.config import DEFAULT_CLAUDE_MODEL, claude_pricing
from gaia.llm.local_only import assert_provider_permitted
from gaia.logger import get_logger

load_env()


def first_text_block(blocks, model):
    """Return the text of the first text block in an Anthropic content list.

    Thinking-enabled models (the default judge among them) answer with
    ``[thinking, text]``, so block zero has no ``.text``.

    Args:
        blocks: The ``content`` list of an Anthropic ``Message``.
        model (str): Model that produced the response, for the error message.

    Returns:
        str: The first text block's text.

    Raises:
        ValueError: No block in the response carried text.
    """
    for block in blocks:
        text = getattr(block, "text", None)
        if text is not None:
            return text

    block_types = [getattr(block, "type", type(block).__name__) for block in blocks]
    raise ValueError(
        f"No text block in the response from model '{model}' "
        f"(block types received: {block_types}).\n"
        "A text reply is required to score with. Pass a model that returns one "
        "to ClaudeClient(model=...), or check DEFAULT_CLAUDE_MODEL in "
        "src/gaia/eval/config.py."
    )


class ClaudeClient:
    log = get_logger(__name__)

    def __init__(self, model=None, max_tokens=1024, max_retries=3, temperature=None):
        """
        Initialize Claude client with retry support.

        Args:
            model: Claude model to use (defaults to DEFAULT_CLAUDE_MODEL)
            max_tokens: Maximum tokens in response (default: 1024)
            max_retries: Maximum number of retry attempts for API calls with exponential backoff (default: 3)
            temperature: Sampling temperature forwarded to every completion call.
                Left as None (the default), no temperature is sent and the API
                default applies — generation callers depend on that diversity.
                Leave it unset for current models: they reject sampling params
                with a 400, so pinning a value fails the call outright.
        """
        # Check for required dependencies
        if anthropic is None:
            error_msg = (
                "\n❌ Error: Missing required package 'anthropic'\n\n"
                "Please install the eval dependencies:\n"
                '  uv pip install -e ".[eval]"\n\n'
                "Or install anthropic directly:\n"
                "  uv pip install anthropic\n"
            )
            raise ImportError(error_msg)

        if model is None:
            model = DEFAULT_CLAUDE_MODEL
        self.log = self.__class__.log  # Use the class-level logger for instances
        found = api_key_credential()
        if found is None:
            error_msg = (
                "ANTHROPIC_API_KEY is not set in the environment or a .env file.\n\n"
                "ClaudeClient calls the Messages API and needs a console API key:\n"
                "  export ANTHROPIC_API_KEY=sk-ant-...\n"
                "To use a Claude Code subscription instead, build the client with\n"
                "gaia.eval.judge_client.make_judge_client(), which drives the\n"
                "logged-in `claude` CLI (or CLAUDE_CODE_OAUTH_TOKEN). A subscription\n"
                "token is not an API key; do not export it as ANTHROPIC_API_KEY.\n"
            )
            self.log.error(error_msg)
            raise ValueError(error_msg)
        announce(found)
        self.api_key = found.api_key
        # Initialize Anthropic client with retry support
        # The SDK handles exponential backoff automatically
        assert_provider_permitted("Anthropic Claude")
        self.client = anthropic.Anthropic(
            api_key=self.api_key,
            max_retries=max_retries,
            timeout=300.0,  # 5 minute timeout for large documents
        )
        self.model = model
        self._pricing = claude_pricing(model)
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.temperature = temperature
        self.log.info(
            f"Initialized ClaudeClient with model: {model}, max_retries: {max_retries}"
        )

    def _sampling_kwargs(self):
        """Sampling kwargs for a completion call: ``temperature`` only if pinned.

        Omitting the kwarg entirely (rather than passing None) preserves the
        API default for callers that never asked for a pin.
        """
        if self.temperature is None:
            return {}
        return {"temperature": self.temperature}

    def calculate_cost(self, input_tokens, output_tokens):
        """
        Calculate the cost of an API call based on token usage.

        Args:
            input_tokens (int): Number of input tokens
            output_tokens (int): Number of output tokens

        Returns:
            dict: Cost breakdown with input_cost, output_cost, and total_cost
        """
        pricing = self._pricing

        # Calculate costs (convert tokens to millions)
        input_cost = (input_tokens / 1_000_000) * pricing["input_per_mtok"]
        output_cost = (output_tokens / 1_000_000) * pricing["output_per_mtok"]
        total_cost = input_cost + output_cost

        return {
            "input_cost": round(input_cost, 6),
            "output_cost": round(output_cost, 6),
            "total_cost": round(total_cost, 6),
        }

    def get_completion(self, prompt):
        self.log.debug("Getting completion from Claude")
        # count_tokens is a second billed round-trip — only pay it if the debug
        # line will actually be emitted.
        if self.log.isEnabledFor(logging.DEBUG):
            self.log.debug(f"Prompt token count: {self.count_tokens(prompt)}")
        try:
            assert_provider_permitted("Anthropic Claude")
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
                **self._sampling_kwargs(),
            )
            return message.content
        except Exception as e:
            self.log.error(f"Error getting completion: {e}")
            raise

    def get_completion_with_usage(self, prompt):
        """
        Get completion from Claude and return both content and usage/cost information.

        Args:
            prompt (str): The prompt to send to Claude

        Returns:
            dict: Contains 'content', 'usage', and 'cost' keys
        """
        self.log.info("Getting completion with usage tracking from Claude")
        try:
            assert_provider_permitted("Anthropic Claude")
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
                **self._sampling_kwargs(),
            )

            # Extract usage information
            usage = {
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
                "total_tokens": message.usage.input_tokens
                + message.usage.output_tokens,
            }

            # Calculate cost
            cost = self.calculate_cost(usage["input_tokens"], usage["output_tokens"])

            self.log.info(
                f"Usage: {usage['input_tokens']} input + {usage['output_tokens']} output = {usage['total_tokens']} total tokens"
            )
            self.log.info(
                f"Cost: ${cost['input_cost']:.4f} input + ${cost['output_cost']:.4f} output = ${cost['total_cost']:.4f} total"
            )

            return {"content": message.content, "usage": usage, "cost": cost}
        except Exception as e:
            self.log.error(f"Error getting completion with usage: {e}")
            raise

    def list_models(self):
        self.log.info("Retrieving available models")
        try:
            models = self.client.models.list(limit=20)
            self.log.info(f"Successfully retrieved {len(models)} models")
            return models
        except Exception as e:
            self.log.error(f"Error listing models: {e}")
            raise

    def count_tokens(self, prompt):
        assert_provider_permitted("Anthropic Claude")
        return self.client.messages.count_tokens(
            model=self.model, messages=[{"role": "user", "content": prompt}]
        )
