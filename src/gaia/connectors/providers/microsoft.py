# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft OAuth 2.0 provider for ``gaia.connectors`` (MS Graph).

NO module-level side effects: instantiating the provider reads
``GAIA_MICROSOFT_CLIENT_ID`` and computes ``client_id_hash``. Importing this
module does not register anything — registration happens in
``providers/__init__.py`` lazily on first ``get("microsoft")`` call.

Microsoft identity platform v2.0, PKCE for **public clients**: unlike Google,
the token endpoint does NOT require a ``client_secret`` for a "Mobile and
desktop applications" (native/public) Azure app registration — PKCE replaces
it. So the setup form collects a Client ID only and no secret is ever sent.

A refresh token is issued only when the ``offline_access`` scope is requested
(MS Graph's equivalent of Google's ``access_type=offline``); the catalog spec
keeps ``offline_access`` in ``default_scopes`` so the flow always gets one.

``TENANT`` is ``consumers`` (personal Microsoft accounts — Outlook.com /
Hotmail / Live.com) per issue #1105. Work/school (Azure AD org) access is
tracked separately (#1280 / #1281); switching this provider to those audiences
is a one-line change to ``TENANT`` (``organizations`` for org-only, ``common``
for both — note ``common`` trips conditional access on some tenants).

Per AC23, ``SCOPE_DESCRIPTIONS`` pins the plain-language label for each scope
so the AgentUI consent dialog and the CLI grant subcommand both render the
same human-readable string for a given scope.
"""

from __future__ import annotations

import os
import zlib
from typing import Iterable, Sequence
from urllib.parse import urlencode

from gaia.connectors.errors import ConfigurationError

# Personal-account audience per #1105. See module docstring for org/common.
TENANT = "consumers"

# Plain-language descriptions for the AgentUI consent dialog (AC23). Every
# scope in MICROSOFT_SPEC.available_scopes must have an entry here — enforced
# by ``test_microsoft_provider.py::TestMicrosoftScopeDescriptions``.
SCOPE_DESCRIPTIONS: dict[str, str] = {
    "https://graph.microsoft.com/Mail.Read": "Read your email",
    "https://graph.microsoft.com/Mail.Send": "Send email on your behalf",
    "https://graph.microsoft.com/Calendars.ReadWrite": "Read and manage your calendar events",
    "https://graph.microsoft.com/Files.Read": "Read your OneDrive files",
    "https://graph.microsoft.com/User.Read": "See your basic profile",
    "offline_access": "Stay signed in so GAIA can refresh access without re-prompting",
    "openid": "Verify your identity",
    "profile": "See your basic profile",
    "email": "See your email address",
}


class MicrosoftOAuthProvider:
    """
    Concrete provider for ``login.microsoftonline.com``. Implements
    ``OAuthProvider`` structurally — no inheritance.

    Reads ``GAIA_MICROSOFT_CLIENT_ID`` at instantiation time, NOT at import
    time. The hash of the client id is precomputed so the tripwire check in
    ``store.load_connection`` is a constant-time string compare.

    Public-client PKCE — there is no ``client_secret`` attribute and none is
    ever placed in the token / refresh request bodies.
    """

    provider_id: str = "microsoft"
    auth_url: str = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize"
    token_url: str = f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token"
    default_scopes: Sequence[str] = (
        "openid",
        "profile",
        "email",
        "offline_access",
        "https://graph.microsoft.com/User.Read",
    )

    def __init__(self, client_id: str | None = None):
        # Resolution order (mirrors GoogleOAuthProvider; user-friendliness
        # first):
        #   1. Explicit kwarg (used by tests and library callers).
        #   2. Keyring-stored credentials saved via the AgentUI's
        #      Settings → Connections → Microsoft → "Save & Connect" form.
        #   3. Env var (GAIA_MICROSOFT_CLIENT_ID) — fallback for CI and
        #      scripted setups; never required for new users.
        if client_id is None:
            # Lazy import to avoid a connectors → providers → store cycle
            # at module load time.
            from gaia.connectors.store import peek_provider_credentials

            stored = peek_provider_credentials("microsoft") or {}
        else:
            stored = {}

        resolved_id = (
            client_id
            if client_id is not None
            else stored.get("client_id")
            or os.environ.get("GAIA_MICROSOFT_CLIENT_ID", "")
        )
        if not resolved_id:
            raise ConfigurationError(
                "Microsoft OAuth client is not configured. Open Settings → "
                "Connections → Microsoft in the AgentUI and paste the "
                "Application (client) ID from your Azure app registration "
                "(Mobile and desktop applications platform, redirect "
                "http://localhost). No client secret is needed. (Power users "
                "may also set the GAIA_MICROSOFT_CLIENT_ID env var before "
                "launching GAIA.) See docs/runbooks/microsoft-oauth-client.md."
            )
        self.client_id: str = resolved_id
        # CRC32 fingerprint for log correlation / tripwire comparison only.
        # Non-cryptographic by design — not used for security.
        self.client_id_hash: str = format(zlib.crc32(resolved_id.encode()), "08x")

    def authorization_params(self) -> dict:
        """
        Microsoft needs no extra authorization-URL params for a refresh
        token — that is driven by the ``offline_access`` scope (see
        ``default_scopes``), not by a query param the way Google's
        ``access_type=offline`` is.
        """
        return {}

    def authorization_url(
        self,
        redirect_uri: str,
        challenge: str,
        state: str,
        scopes: Iterable[str],
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
        return f"{self.auth_url}?{urlencode(params)}"

    def token_request_body(self, code: str, verifier: str, redirect_uri: str) -> dict:
        # Public client: no client_secret. PKCE's code_verifier authenticates
        # the exchange.
        return {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
        }

    def refresh_request_body(self, refresh_token: str) -> dict:
        return {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
