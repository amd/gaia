# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""LLM client factory."""

from typing import Optional

from .base_client import LLMClient

_PROVIDERS: dict[str, str] = {
    "lemonade": "gaia.llm.providers.lemonade.LemonadeProvider",
    "claude": "gaia.llm.providers.claude.ClaudeProvider",
}


REMOVED_PROVIDER_MESSAGE = (
    "The openai/litellm providers and use_openai/use_chatgpt options were removed "
    "because they discarded tool calls. Configure your local or on-prem model "
    "in Lemonade and select provider='lemonade' with its base_url and model ID. "
    "See https://amd-gaia.ai/docs/sdk/sdks/llm#gateway-migration."
)


def create_client(
    provider: Optional[str] = None,
    use_claude: bool = False,
    use_openai: bool = False,
    **kwargs,
) -> LLMClient:
    """Create a Lemonade or Claude client.

    ``use_openai`` is retained only to reject legacy callers with migration
    guidance; it cannot select a backend, even with an explicit provider.
    """
    if use_openai or (provider and provider.lower() in {"openai", "litellm"}):
        raise ValueError(REMOVED_PROVIDER_MESSAGE)
    if provider is None:
        provider = "claude" if use_claude else "lemonade"

    provider_lower = provider.lower()

    if provider_lower not in _PROVIDERS:
        available = ", ".join(_PROVIDERS.keys())
        raise ValueError(f"Unknown provider: {provider}. Available: {available}")

    import importlib

    module_path, class_name = _PROVIDERS[provider_lower].rsplit(".", 1)
    module = importlib.import_module(module_path)
    provider_class = getattr(module, class_name)

    return provider_class(**kwargs)  # type: ignore[no-any-return]
