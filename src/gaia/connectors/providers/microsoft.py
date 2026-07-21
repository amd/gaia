# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft identity platform (v2.0) OAuth provider for ``gaia.connectors``.

Foundation for the Outlook mailbox (#1275) and calendar (#1276) agents:
unlocks MS Graph (mail, calendar, OneDrive, Teams, SharePoint) for any agent
through the same generic ``oauth_pkce`` handler that already drives Google.

NO module-level side effects: instantiating the provider reads
``GAIA_MICROSOFT_CLIENT_ID`` and computes ``client_id_hash``. Importing this
module registers nothing — registration is lazy on first ``get("microsoft")``
(see ``providers/__init__.py``), matching ``GoogleOAuthProvider``.

Tenant is ``consumers`` — personal Microsoft accounts (Outlook.com / Hotmail /
Live). Work/school (Entra ID) tenants are out of scope for v1.

Public-client PKCE: per the Microsoft identity platform docs, public clients
(native/desktop, single-page apps) MUST NOT send a ``client_secret`` when
redeeming an authorization code. This is the key difference from Google, which
*requires* a secret even for installed apps. So the setup form asks only for a
Client ID; ``token_request_body`` / ``refresh_request_body`` omit the secret
unless one is explicitly configured (a confidential web-app edge case).

The shared ``flow.py`` requires a refresh token (Microsoft returns one only
when ``offline_access`` is requested) and decodes the account email from the
id_token (returned only when ``openid`` is requested). Both scopes are in
``default_scopes`` so a first connect succeeds without any flow.py change.
"""

from __future__ import annotations

import os
import zlib
from typing import Iterable, Sequence
from urllib.parse import urlencode

from gaia.connectors.errors import OAuthClientNotConfiguredError

# Personal-account tenant. Pinned in both endpoint URLs.
_TENANT = "consumers"

# Plain-language descriptions for the AgentUI consent dialog, mirroring the
# Google provider's SCOPE_DESCRIPTIONS. The router/CLI render these strings;
# agents declare the Graph scope URLs in REQUIRED_CONNECTORS.
SCOPE_DESCRIPTIONS: dict[str, str] = {
    "https://graph.microsoft.com/Mail.Read": "Read your email",
    "https://graph.microsoft.com/Mail.Send": "Send email on your behalf",
    "https://graph.microsoft.com/Mail.ReadWrite": (
        "Read, organize, and manage your email"
    ),
    "https://graph.microsoft.com/Calendars.Read": "Read your calendar events",
    "https://graph.microsoft.com/Calendars.ReadWrite": "Manage your calendar events",
    "https://graph.microsoft.com/Files.Read": "Read your OneDrive files",
    "https://graph.microsoft.com/User.Read": "See your basic profile",
    "openid": "Verify your identity",
    "profile": "See your basic profile",
    "email": "See your email address",
    "offline_access": "Maintain access to data you've granted it access to",
}


class MicrosoftOAuthProvider:
    """
    Concrete provider for the Microsoft identity platform (``consumers``
    tenant). Implements the ``OAuthProvider`` Protocol structurally — no
    inheritance, matching ``GoogleOAuthProvider``.

    Reads ``GAIA_MICROSOFT_CLIENT_ID`` at instantiation time, NOT at import
    time. ``client_id_hash`` is a non-cryptographic CRC32 fingerprint used
    only for log correlation / the ``store.load_connection`` tripwire compare.
    """

    provider_id: str = "microsoft"
    auth_url: str = f"https://login.microsoftonline.com/{_TENANT}/oauth2/v2.0/authorize"
    token_url: str = f"https://login.microsoftonline.com/{_TENANT}/oauth2/v2.0/token"
    # offline_access => refresh token; openid => id_token (account email).
    # The shared flow depends on both; keep them in the default set so a bare
    # connect (no explicit scopes) still works end-to-end.
    default_scopes: Sequence[str] = (
        "openid",
        "offline_access",
        "https://graph.microsoft.com/User.Read",
    )

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        # Resolution order matches GoogleOAuthProvider (user-friendliness
        # first):
        #   1. Explicit kwargs (tests / library callers).
        #   2. Keyring credentials saved via the AgentUI setup form.
        #   3. Env vars (GAIA_MICROSOFT_CLIENT_ID / _SECRET) for CI / scripted
        #      setups. Never required for new users.
        if client_id is None or client_secret is None:
            # Lazy import to avoid a connectors -> providers -> store cycle at
            # module load time.
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
            raise OAuthClientNotConfiguredError(
                "microsoft",
                provider_label="Microsoft",
                console_steps=(
                    "  1. Register an app at https://portal.azure.com -> "
                    "Microsoft Entra ID -> App registrations\n"
                    "  2. Set the supported account type to include personal "
                    "accounts ('consumers'), and add a http://localhost redirect "
                    "URI under Authentication -> Mobile & desktop applications\n"
                    "  3. Add the Microsoft Graph delegated permissions you need "
                    "(e.g. Mail.ReadWrite, Mail.Send)\n"
                    "  4. Copy the Application (client) ID. Public desktop "
                    "clients need no secret; a confidential registration also "
                    "needs a client secret under Certificates & secrets"
                ),
                example_grant=(
                    "installed:email "
                    "--scopes https://graph.microsoft.com/Mail.ReadWrite"
                ),
                docs="https://amd-gaia.ai/docs/connectors/microsoft",
            )
        self.client_id: str = resolved_id
        # CRC32 fingerprint for log correlation / tripwire comparison only.
        # Non-cryptographic by design — not used for security.
        self.client_id_hash: str = format(zlib.crc32(resolved_id.encode()), "08x")
        # Public PKCE clients send NO secret. Empty string => omitted from the
        # token/refresh bodies. A non-empty value is the confidential-app
        # opt-in (operator set GAIA_MICROSOFT_CLIENT_SECRET / saved one).
        self.client_secret: str = (
            client_secret
            if client_secret is not None
            else stored.get("client_secret")
            or os.environ.get("GAIA_MICROSOFT_CLIENT_SECRET", "")
        )

    def authorization_params(self) -> dict:
        """
        Microsoft-specific extras for the authorization URL.

        ``response_mode=query`` — the loopback ``/callback`` handler reads the
        code from the query string (``?code=...``). Microsoft defaults to
        ``fragment`` in some hybrid-flow cases, and browsers do not forward the
        fragment to the loopback server, so we pin ``query`` explicitly.

        Note: unlike Google, Microsoft does NOT need ``access_type=offline`` /
        ``prompt=consent`` to issue a refresh token — the ``offline_access``
        scope alone does that, and it is in ``default_scopes``.
        """
        return {"response_mode": "query"}

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
        body: dict = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
        }
        # Public client: omit unless a confidential-app secret is configured.
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
