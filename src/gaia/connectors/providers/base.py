# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Provider abstraction for ``gaia.connectors``.

Defines:
- ``ConnectorRequirement``: declared on agent classes via the
  ``REQUIRED_CONNECTORS`` ClassVar; surfaced to AgentUI's consent dialog and
  to the CLI grant commands.
- ``OAuthProvider``: a structural ``Protocol`` describing the static and
  runtime surface the connections core relies on. Each concrete provider
  (``GoogleOAuthProvider``, future Microsoft/etc.) implements this protocol
  without inheriting from it — duck-typed, matching GAIA's mixin style.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class ConnectorRequirement:
    """
    Declared on agent classes as ``REQUIRED_CONNECTORS = [ConnectorRequirement(...)]``.

    ``connector_id`` must match a ``ConnectorSpec.id`` in the catalog (e.g.
    ``"google"``). Frozen + hashable so it can live in sets and serve as a
    dict key. ``scopes`` is normalized to a tuple in ``__post_init__`` so two
    requirements built from different list instances compare equal.
    """

    connector_id: str
    scopes: Sequence[str]
    reason: str = field(default="")

    def __post_init__(self):
        # Frozen dataclass — bypass setattr via object.__setattr__.
        object.__setattr__(self, "scopes", tuple(self.scopes))


@runtime_checkable
class OAuthProvider(Protocol):
    """
    Static + runtime surface every concrete OAuth provider must implement.

    The runtime registry (``providers/__init__.py``) returns an instance of
    this protocol. ``flow.py``, ``tokens.py``, and ``store.py`` consume it
    without knowing about Google specifics — provider-specific extras like
    Google's ``access_type=offline`` come from ``authorization_params()``.
    """

    provider_id: str
    auth_url: str
    token_url: str
    client_id: str
    client_id_hash: str
    default_scopes: Sequence[str]

    def authorization_url(
        self,
        redirect_uri: str,
        challenge: str,
        state: str,
        scopes: Iterable[str],
    ) -> str: ...

    def token_request_body(
        self, code: str, verifier: str, redirect_uri: str
    ) -> dict: ...

    def refresh_request_body(self, refresh_token: str) -> dict: ...

    def authorization_params(self) -> dict: ...
