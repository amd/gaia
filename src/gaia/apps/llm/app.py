#!/usr/bin/env python3
"""
Simple LLM App using the existing LLMClient wrapper to call Lemonade localhost backend.
"""

import argparse
import sys
from typing import Optional, Union, Iterator

from gaia.logger import get_logger
from gaia.llm.llm_client import LLMClient


class LlmApp:
    """Simple LLM application wrapper using LLMClient."""

    def __init__(self, system_prompt: Optional[str] = None):
        """Initialize the LLM app."""
        self.log = get_logger(__name__)
        self.client = LLMClient(use_local=True, system_prompt=system_prompt)
        self.log.debug("LLM app initialized")

    def query(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 512,
        stream: bool = False,
        **kwargs,
    ) -> Union[str, Iterator[str]]:
        """Send a query to the LLM and get a response."""
        if not prompt.strip():
            raise ValueError("Prompt cannot be empty")

        self.log.debug(f"Processing query with model: {model or 'default'}")

        # Prepare arguments
        generate_kwargs = dict(kwargs)
        if max_tokens:
            generate_kwargs["max_tokens"] = max_tokens

        # Generate response
        return self.client.generate(
            prompt=prompt.strip(), model=model, stream=stream, **generate_kwargs
        )

    def get_stats(self):
        """Get performance statistics."""
        return self.client.get_performance_stats() or {}


def main(
    query: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = 512,
    system_prompt: Optional[str] = None,
    stream: bool = True,
) -> str:
    """Main function to run the LLM app."""
    if not query:
        raise ValueError("Query is required")

    app = LlmApp(system_prompt=system_prompt)
    response = app.query(
        prompt=query, model=model, max_tokens=max_tokens, stream=stream
    )

    if stream:
        # Handle streaming response
        full_response = ""
        for chunk in response:
            print(chunk, end="", flush=True)
            full_response += chunk
        print()  # Add newline
        return full_response
    else:
        return response


def cli_main():
    """Command line interface."""
    parser = argparse.ArgumentParser(description="Simple LLM App")

    parser.add_argument("query", help="Query to send to the LLM")
    parser.add_argument("--model", help="Model name to use")
    parser.add_argument(
        "--max-tokens", type=int, default=512, help="Max tokens (default: 512)"
    )
    parser.add_argument("--system-prompt", help="System prompt")
    parser.add_argument("--stream", action="store_true", help="Stream response")
    parser.add_argument("--stats", action="store_true", help="Show stats")
    parser.add_argument(
        "--logging-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Setup logging
    import logging
    from gaia.logger import log_manager

    log_manager.set_level("gaia", getattr(logging, args.logging_level))

    try:
        app = LlmApp(system_prompt=args.system_prompt)

        response = app.query(
            prompt=args.query,
            model=args.model,
            max_tokens=args.max_tokens,
            stream=args.stream,
        )

        if args.stream:
            # Already printed during streaming
            pass
        else:
            print(f"\n{'='*50}")
            print("LLM Response:")
            print("=" * 50)
            print(response)
            print("=" * 50)

        if args.stats:
            stats = app.get_stats()
            if stats:
                print(f"\n{'='*50}")
                print("Performance Statistics:")
                print("=" * 50)
                for key, value in stats.items():
                    print(f"{key}: {value}")
                print("=" * 50)

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
