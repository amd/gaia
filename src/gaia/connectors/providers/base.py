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

import zlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlencode


@dataclass(frozen=True)
class ConnectorRequirement:
    """
    Declared on agent classes as ``REQUIRED_CONNECTORS = [ConnectorRequirement(...)]``.

    ``connector_id`` must match a ``ConnectorSpec.id`` in the catalog (e.g.
    ``"google"``). Frozen + hashable so it can live in sets and serve as a
    dict key. ``scopes`` is normalized to a tuple in ``__post_init__`` so two
    requirements built from different list instances compare equal.

    ``required_scopes`` (#2730 D5) is the subset actually ENFORCED at token-
    mint time — ``scopes`` stays what is REQUESTED at consent. Left unset (or
    passed empty), it defaults to ``scopes``, so every existing construction
    site keeps today's all-required semantics unchanged.
    """

    connector_id: str
    scopes: Sequence[str]
    reason: str = field(default="")
    required_scopes: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self):
        # Frozen dataclass — bypass setattr via object.__setattr__.
        object.__setattr__(self, "scopes", tuple(self.scopes))
        object.__setattr__(
            self, "required_scopes", tuple(self.required_scopes) or self.scopes
        )


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
    # Public per-app revoke endpoint, or None when the provider has none
    # (Microsoft today). Required, never optional: ``flow.revoke_provider_token``
    # reads it directly, so a provider that omits it raises instead of silently
    # reporting "revoke not supported" for a grant that is still live (#2591).
    revoke_url: str | None

    def authorization_url(
        self,
        redirect_uri: str,
        challenge: str,
        state: str,
        scopes: Iterable[str],
        extra_params: Mapping[str, str] | None = None,
    ) -> str: ...

    def account_prompt_params(
        self,
        *,
        select_account: bool,
        login_hint: str | None,
        account_type: str | None,
    ) -> dict:
        """Authorization-URL params that pick the account: the provider's
        account picker when adding one, or a hint when reconnecting one."""
        ...

    def token_request_body(
        self, code: str, verifier: str, redirect_uri: str
    ) -> dict: ...

    def refresh_request_body(self, refresh_token: str) -> dict: ...

    def authorization_params(self) -> dict: ...


def client_id_hash(client_id: str) -> str:
    """CRC32 fingerprint of an OAuth client id, for the stored-connection
    tripwire and log correlation only. Not a security measure."""
    return format(zlib.crc32(client_id.encode()), "08x")


class PkceRequests:
    """The OAuth 2.0 PKCE requests that are the same for every provider.

    A provider class supplies ``client_id``, ``client_secret``, ``auth_url`` and
    ``authorization_params()``; provider-specific parameters belong there.
    """

    client_id: str
    client_secret: str | None
    auth_url: str

    def authorization_params(self) -> dict:
        raise NotImplementedError(
            f"{type(self).__name__} must define authorization_params()"
        )

    def authorization_url(
        self,
        redirect_uri: str,
        challenge: str,
        state: str,
        scopes: Iterable[str],
        extra_params: Mapping[str, str] | None = None,
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": " ".join(scopes),
        }
        params.update(self.authorization_params())
        params.update(extra_params or {})
        return f"{self.auth_url}?{urlencode(params)}"

    def token_request_body(self, code: str, verifier: str, redirect_uri: str) -> dict:
        body: dict = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
        }
        if self.client_secret:
            body["client_secret"] = self.client_secret
        return body

    def refresh_request_body(self, refresh_token: str) -> dict:
        body: dict = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        if self.client_secret:
            body["client_secret"] = self.client_secret
        return body
