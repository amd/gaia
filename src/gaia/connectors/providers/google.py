# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Google OAuth 2.0 provider for ``gaia.connectors``.

NO module-level side effects: instantiating the provider reads
``GAIA_GOOGLE_CLIENT_ID`` and computes ``client_id_hash``. Importing this
module does not register anything — registration happens in
``providers/__init__.py`` lazily on first ``get("google")`` call (or via an
explicit ``register()`` call from a caller that wants strict startup).

Desktop-app PKCE flow. Google requires ``client_secret`` even for Desktop-type
clients — it is "not truly confidential" for installed apps but the token
endpoint rejects requests that omit it. Set ``GAIA_GOOGLE_CLIENT_SECRET`` to
the value shown in Cloud Console → Credentials → your Desktop client.

Per AC23, ``SCOPE_DESCRIPTIONS`` pins the plain-language label for each scope
so the AgentUI consent dialog and the CLI grant subcommand both render the
same human-readable string for a given scope. A unit test in
``test_scope_descriptions.py`` enforces that every scope used in any agent's
``REQUIRED_CONNECTORS`` has an entry here.
"""

from __future__ import annotations

import hashlib
import os
from typing import Iterable, Sequence
from urllib.parse import urlencode

from gaia.connectors.errors import ConfigurationError

# Plain-language descriptions for the AgentUI consent dialog (AC23). The
# router and the CLI both surface this map; agents declare scope URLs in
# REQUIRED_CONNECTORS; the UI/CLI render the description, never the URL.
SCOPE_DESCRIPTIONS: dict[str, str] = {
    "https://www.googleapis.com/auth/gmail.readonly": "Read your email",
    "https://www.googleapis.com/auth/gmail.send": "Send email on your behalf",
    "https://www.googleapis.com/auth/gmail.compose": "Draft and send email on your behalf",
    "https://www.googleapis.com/auth/gmail.modify": "Read, modify, and send email on your behalf",
    "https://www.googleapis.com/auth/calendar.readonly": "Read your calendar events",
    "https://www.googleapis.com/auth/calendar.events": "Manage your calendar events",
    "https://www.googleapis.com/auth/drive.readonly": "Read your Google Drive files",
    "https://www.googleapis.com/auth/drive.file": "Manage Drive files this app creates",
    "https://www.googleapis.com/auth/userinfo.email": "See your email address",
    "https://www.googleapis.com/auth/userinfo.profile": "See your basic profile",
    "openid": "Verify your identity",
}


class GoogleOAuthProvider:
    """
    Concrete provider for ``accounts.google.com``. Implements ``OAuthProvider``
    structurally — no inheritance.

    Reads ``GAIA_GOOGLE_CLIENT_ID`` at instantiation time, NOT at import time.
    The hash of the client id is precomputed so the tripwire check in
    ``store.load_connection`` is a constant-time string compare.
    """

    provider_id: str = "google"
    auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url: str = "https://oauth2.googleapis.com/token"
    default_scopes: Sequence[str] = (
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
    )

    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        resolved = (
            client_id
            if client_id is not None
            else os.environ.get("GAIA_GOOGLE_CLIENT_ID", "")
        )
        if not resolved:
            raise ConfigurationError(
                "GAIA_GOOGLE_CLIENT_ID is not set. The Google OAuth provider "
                "cannot be initialized without a Cloud Console Desktop-app "
                "client id. Set the env var, or document the value in "
                "docs/runbooks/google-oauth-client.md and source it before "
                "starting GAIA. See docs/runbooks/google-oauth-client.md."
            )
        self.client_id: str = resolved
        self.client_id_hash: str = hashlib.sha256(resolved.encode()).hexdigest()
        # Google requires client_secret even for Desktop-type PKCE clients.
        self.client_secret: str = (
            client_secret
            if client_secret is not None
            else os.environ.get("GAIA_GOOGLE_CLIENT_SECRET", "")
        )

    def authorization_params(self) -> dict:
        """
        Google-specific extras for the authorization URL.

        - ``access_type=offline`` — issue a refresh token alongside the
          access token (otherwise we get only a 1-hour access token and no
          way to refresh).
        - ``prompt=consent`` — force the consent screen on every connect, so
          we always receive a refresh token (Google issues a refresh token
          ONLY on the first consent unless ``prompt=consent`` is set).
        """
        return {"access_type": "offline", "prompt": "consent"}

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
