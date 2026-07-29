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

    ``google`` stays a literal check (one provider, one connector id).
    Every other id is resolved through the connector catalog's
    ``ConnectorSpec.oauth_impl`` (plan amendment A1, #2628): dispatch is on
    WHICH PROVIDER CLASS implements a spec, never on the connector id
    itself, so a fourth Microsoft-audience connector (or any future spec
    sharing an existing provider class) needs no edit here — only a new
    catalog entry with the same ``oauth_impl``.

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

    # A15: defensive catalog import — this module has no module-level
    # dependency on the catalog/registry, so a caller that reaches ``get()``
    # without having imported ``gaia.connectors.catalog`` first (e.g. a unit
    # test constructing a provider directly) would otherwise see REGISTRY
    # empty and get a bare KeyError instead of the real error. Mirrors the
    # idiom at store.py's list_connections / api.py.
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.registry import REGISTRY

    try:
        spec = REGISTRY.get(provider_id)
    except KeyError:
        spec = None

    if spec is not None and spec.oauth_impl == "microsoft":
        from gaia.connectors.providers.microsoft import MicrosoftOAuthProvider

        # A16: pass default_tenant under its OWN name, never a resolved
        # "tenant=" — MicrosoftOAuthProvider.__init__ owns the full
        # explicit -> stored -> default three-tier chain. Pre-resolving
        # here would make the stored-override tier permanently
        # unreachable (D5's entire reason for existing).
        provider = MicrosoftOAuthProvider(
            provider_id=provider_id, default_tenant=spec.oauth_tenant
        )
        register(provider)
        return provider

    raise KeyError(
        f"Unknown OAuth provider '{provider_id}'. Known: "
        f"{sorted(set(_registry) | {'google', 'microsoft', 'microsoft_work'})}"
    )


def list_provider_ids() -> list[str]:
    """Return the ids of currently registered providers (no lazy init)."""
    return sorted(_registry)
