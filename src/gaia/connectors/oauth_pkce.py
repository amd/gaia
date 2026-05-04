# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
OAuthPkceHandler — ConnectorHandler implementation for ``type="oauth_pkce"``.

Wraps the existing flow.py / tokens.py / store.py primitives from #915
under the ``ConnectorHandler`` Protocol so the framework dispatcher can
route ``get_credential`` / ``configure`` / ``disconnect`` / ``test`` to
the right implementation without knowing OAuth internals.

Registration happens at module import via ``register_handler``; callers
only need to ``import gaia.connectors.oauth_pkce`` (done by catalog/__init__.py).

The grant check is NOT performed here — the dispatcher in handler.py does
it before calling any handler method.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from gaia.connectors.errors import (
    AuthRequiredError,
    ConnectorsError,
)
from gaia.connectors.flow import (
    complete_authorization,
    start_authorization,
)
from gaia.connectors.handler import register_handler
from gaia.connectors.spec import ConnectorSpec
from gaia.connectors.store import DEFAULT_ACCOUNT, delete_connection
from gaia.connectors.tokens import get_or_refresh

logger = logging.getLogger(__name__)


class OAuthPkceHandler:
    """
    Handles ``type="oauth_pkce"`` connectors via the existing PKCE flow.

    ``get_credential`` returns an access-token dict compatible with
    Google's token endpoint; the dict shape is:
      ``{"access_token": str, "expires_at": int, "scopes": [str]}``

    This class is stateless — it delegates all persistent state to
    ``tokens.py`` (in-memory cache) and ``store.py`` (keyring; the
    keyring blob is also the source of truth for the catalog UI's
    "configured" state via ``store.peek_connection``).
    """

    # ------------------------------------------------------------------
    # ConnectorHandler Protocol implementation
    # ------------------------------------------------------------------

    async def get_credential(
        self,
        spec: ConnectorSpec,
        *,
        required_scopes: Optional[List[str]] = None,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return a live access token for the connector's OAuth provider.

        ``spec.oauth_provider_ref`` identifies the ``OAuthProvider`` in the
        provider registry (e.g. ``"google"``). Falls back to ``spec.id``.
        """
        provider_id = spec.oauth_provider_ref or spec.id
        account_email = account_id or DEFAULT_ACCOUNT
        token_str, expires_at = await get_or_refresh(
            provider_id, account_email=account_email
        )
        return {
            "access_token": token_str,
            "expires_at": expires_at,
            "scopes": list(required_scopes or spec.default_scopes),
        }

    async def configure(
        self,
        spec: ConnectorSpec,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Persist OAuth-client credentials (if supplied), then start a PKCE flow.

        Three call shapes:
          1. ``{client_id, client_secret}`` — first-run path from the
             AgentUI "Save & Connect" form. We persist the app
             credentials in the keyring, evict the cached provider
             instance, then start a fresh PKCE flow.
          2. ``{flow_id, code}`` — completion path for callers that
             drove the browser step themselves.
          3. ``{}`` (or just ``scopes``) — start a new PKCE flow using
             whatever provider credentials are already on disk
             (keyring / env vars).

        The keyring blob written by ``flow._exchange_code_for_tokens``
        remains the source of truth for "configured"; this method does
        not write the connection blob itself.
        """
        provider_id = spec.oauth_provider_ref or spec.id

        # First-run "Save & Connect": persist client credentials and
        # invalidate the provider cache so the next get_provider() call
        # picks up the new id/secret instead of a stale instance.
        client_id = config.get("client_id")
        client_secret = config.get("client_secret", "")
        if client_id:
            from gaia.connectors.providers import _registry as _provider_registry
            from gaia.connectors.store import save_provider_credentials

            save_provider_credentials(
                provider_id,
                client_id=client_id,
                client_secret=client_secret,
            )
            _provider_registry.pop(provider_id, None)

        scopes = config.get("scopes") or list(spec.default_scopes)

        if "flow_id" in config and "code" in config:
            # Caller has already handled the browser step.
            return await complete_authorization(config["flow_id"])

        # Start a new PKCE flow; caller will open the URL.
        return await start_authorization(provider_id, scopes=scopes)

    async def disconnect(
        self,
        spec: ConnectorSpec,
        *,
        account_id: Optional[str] = None,
    ) -> None:
        """Remove stored tokens. The keyring deletion is the source of
        truth — once the blob is gone, ``store.peek_connection`` returns
        ``None`` and the catalog UI shows "not configured" automatically."""
        provider_id = spec.oauth_provider_ref or spec.id
        account_email = account_id or DEFAULT_ACCOUNT
        delete_connection(provider_id, account_email=account_email)
        logger.info(
            "oauth_pkce: disconnected connector_id=%s provider=%s",
            spec.id,
            provider_id,  # lgtm[py/clear-text-logging-sensitive-data]
        )

    async def test(self, spec: ConnectorSpec) -> Dict[str, Any]:
        """
        Verify the connector by attempting a token refresh.

        Returns ``{"ok": True, "detail": "token_valid"}`` on success, or
        ``{"ok": False, "detail": "<error message>"}`` on failure.
        """
        provider_id = spec.oauth_provider_ref or spec.id
        try:
            await get_or_refresh(provider_id)
            return {"ok": True, "detail": "token_valid"}
        except AuthRequiredError as e:
            return {"ok": False, "detail": str(e)}
        except ConnectorsError as e:
            return {"ok": False, "detail": str(e)}


# Register the handler singleton at import time.
register_handler("oauth_pkce", OAuthPkceHandler())
