# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
OAuth provider registry for ``gaia.connectors``.

Lazy registration: ``get("google")`` instantiates and registers
``GoogleOAuthProvider`` on demand if the registry is empty for that id. SDK,
CLI, and AgentUI consumers never need to register the provider explicitly —
the first ``get`` does it. AgentUI's lifespan still calls a tripwire sweep
that triggers the lazy registration early so a missing env var surfaces in
the server logs at boot, but the layer never depends on a specific caller
having registered first.
"""

from __future__ import annotations

from gaia.connectors.providers.base import (  # noqa: F401  re-export
    ConnectorRequirement,
    OAuthProvider,
)

_registry: dict[str, OAuthProvider] = {}


def register(provider: OAuthProvider) -> None:
    """Insert (or overwrite) a provider in the registry."""
    _registry[provider.provider_id] = provider


def get(provider_id: str) -> OAuthProvider:
    """
    Return the registered provider, instantiating known built-ins lazily.

    Raises ``KeyError`` for unknown provider ids.
    """
    if provider_id in _registry:
        return _registry[provider_id]

    if provider_id == "google":
        # Lazy import to avoid pulling Google-specific code at module load
        # for CLI/SDK callers that only target a different provider.
        from gaia.connectors.providers.google import GoogleOAuthProvider

        provider = GoogleOAuthProvider()
        register(provider)
        return provider

    if provider_id == "microsoft":
        from gaia.connectors.providers.microsoft import MicrosoftOAuthProvider

        provider = MicrosoftOAuthProvider()
        register(provider)
        return provider

    raise KeyError(
        f"Unknown OAuth provider '{provider_id}'. Known: "
        f"{sorted(set(_registry) | {'google', 'microsoft'})}"
    )


def list_provider_ids() -> list[str]:
    """Return the ids of currently registered providers (no lazy init)."""
    return sorted(_registry)
