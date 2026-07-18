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
    ConfigurationError,
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


def _validate_provider_secret(provider_id: str) -> None:
    """Raise ``ConfigurationError`` if the provider requires a client_secret
    but none is configured (env var or keyring).

    Called before starting a PKCE flow so users get an actionable error at
    connect time rather than a cryptic 401 on first token refresh (#1592 AC5).
    Only validates providers known to require a secret (currently Google);
    unknown providers are passed through without checking.
    """
    # Only Google Desktop PKCE clients are known to require the secret.
    if provider_id != "google":
        return
    from gaia.connectors.providers import get as _get_provider

    try:
        provider = _get_provider(provider_id)
    except (ConfigurationError, KeyError):
        # If the provider can't be loaded (e.g. no client_id configured yet),
        # a more specific error will surface during start_authorization.
        return
    if not getattr(provider, "client_secret", None):
        raise ConfigurationError(
            "Google OAuth client_secret is not configured. "
            "Open Settings → Connections → Google and enter the Client Secret "
            "from your Google Cloud Console Desktop-app OAuth credential, "
            "or set the GAIA_GOOGLE_CLIENT_SECRET environment variable. "
            "Without the secret, token refresh will fail with 401. "
            "See docs/runbooks/google-oauth-client.md."
        )


class OAuthPkceHandler:
    """
    Handles ``type="oauth_pkce"`` connectors via the existing PKCE flow.

    ``get_credential`` returns an access-token dict compatible with
    Google's token endpoint; the dict shape is:
      ``{"access_token": str, "scopes": [str]}``

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
        token_str = await get_or_refresh(provider_id, account_email=account_email)
        return {
            "access_token": token_str,
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

        # Validate that a client_secret is available before starting the
        # flow.  Google requires it even for Desktop PKCE clients (#1592
        # AC5): a "connected" entry without a secret will 401 on every
        # token refresh, which is confusing and hard to debug at that point.
        # Fail loudly here with an actionable message instead.
        _validate_provider_secret(provider_id)

        # Per-agent grants to commit on OAuth success (#2117). The UI router
        # resolves the {agent_id: scopes} map from the granted agents'
        # REQUIRED_CONNECTORS before calling configure, so the first-run
        # "Save & Connect" path grants the mailbox in the same flow as the
        # plain "Connect" path.
        grant_agents = config.get("grant_agents") or None

        # Start a new PKCE flow; caller will open the URL.
        return await start_authorization(
            provider_id, scopes=scopes, grant_agents=grant_agents
        )

    async def disconnect(
        self,
        spec: ConnectorSpec,
        *,
        account_id: Optional[str] = None,
    ) -> None:
        """Remove stored tokens AND per-agent grants. Keyring deletion is the
        source of truth for "is this configured" — once the blob is gone,
        ``store.peek_connection`` returns ``None`` and the catalog UI shows
        "not configured". Grant cleanup prevents silent inheritance: if the
        same ``connector_id`` is reconnected later, the new tokens must NOT
        carry the prior user's agent consents."""
        provider_id = spec.oauth_provider_ref or spec.id
        account_email = account_id or DEFAULT_ACCOUNT
        delete_connection(provider_id, account_email=account_email)

        # Wipe per-agent grants for this connector_id. Local import keeps
        # the module-level dependency graph identical to before.
        from gaia.connectors.activations import revoke_all_activations_for
        from gaia.connectors.grants import revoke_all_grants_for

        revoke_all_grants_for(spec.id)
        # Defensive sweep: OAuth connectors never accept activation writes
        # (rejected at the api layer — see ``_require_mcp_server_for_activation``),
        # so this is a no-op today. Kept as a belt-and-braces guard against
        # ledger entries that pre-date the type check or are written via
        # future migration paths — re-adding the same connector_id must
        # never silently inherit prior state.
        revoke_all_activations_for(spec.id)

        logger.info("oauth_pkce: disconnected connector_id=%s", spec.id)

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
