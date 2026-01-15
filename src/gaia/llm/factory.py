# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""LLM client factory."""

from typing import Optional

from .base_client import LLMClient

_PROVIDERS: dict[str, str] = {
    "lemonade": "gaia.llm.providers.lemonade.LemonadeProvider",
    "openai": "gaia.llm.providers.openai_provider.OpenAIProvider",
    "claude": "gaia.llm.providers.claude.ClaudeProvider",
}


def create_client(
    provider: Optional[str] = None,
    use_claude: bool = False,
    use_openai: bool = False,
    **kwargs,
) -> LLMClient:
    """
    Create an LLM client, auto-detecting provider from parameters.

    Args:
        provider: Explicit provider name ("lemonade", "openai", or "claude").
                  If not specified, auto-detected from use_claude/use_openai flags.
        use_claude: If True, use Claude provider (ignored if provider is specified)
        use_openai: If True, use OpenAI provider (ignored if provider is specified)
        **kwargs: Provider-specific arguments (base_url, model, api_key, etc.)

    Note:
        The design using these flags maintains backward compatibility
        while allowing explicit provider selection. If both use_claude and
        use_openai are False and provider is not specified, the default
        provider "lemonade" is used. This was deemed better than updating all
        existing callers with conditional logic and multiple `create_client` calls.

    Returns:
        LLMClient instance for the specified or detected provider

    Raises:
        ValueError: If provider is not recognized or both use_claude and use_openai are True
    """
    # Auto-detect provider from flags if not explicitly specified
    if provider is None:
        if use_claude and use_openai:
            raise ValueError(
                "Cannot specify both use_claude and use_openai. Please choose one."
            )
        elif use_claude:
            provider = "claude"
        elif use_openai:
            provider = "openai"
        else:
            provider = "lemonade"

    provider_lower = provider.lower()

    if provider_lower not in _PROVIDERS:
        available = ", ".join(_PROVIDERS.keys())
        raise ValueError(f"Unknown provider: {provider}. Available: {available}")

    import importlib

    module_path, class_name = _PROVIDERS[provider_lower].rsplit(".", 1)
    module = importlib.import_module(module_path)
    provider_class = getattr(module, class_name)

    return provider_class(**kwargs)
