# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mailbox-connector routes for the email playground.

Always mounted on the sidecar. The playground page is itself always served, so
gating only its connector routes would leave the page with a dead Connectors
panel — same lifecycle for both is simpler and consistent. Reuses GAIA's
connector framework (``gaia.connectors``) — the same OAuth flow the Agent UI
uses — so a developer can connect a Gmail/Outlook mailbox from the playground
and exercise live ``/v1/email/send``.

Excluded from the OpenAPI schema: a playground convenience, not part of the
frozen email REST contract. ``gaia.connectors`` is already linked into the
binary (the send path resolves the mailbox through it), so these routes add a
surface, not a dependency. The connection itself lives in a machine-global
keyring store shared by every GAIA surface (Agent UI, CLI, this playground), so
a real consuming app can establish it elsewhere and the sidecar's send just
reads it from that shared store.

OAuth completes entirely inside ``gaia.connectors.flow`` — it stands up its own
loopback redirect listener and opens the browser — so this module hosts no
callback route. It only starts the flow and waits for it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("gaia_agent_email.connectors")

# Connections are granted to the email agent's namespaced id so the send path
# (which resolves the mailbox under this agent) can use them. Mirrors
# ``gaia-agent.yaml`` (``id: email``) → ``installed:email``.
EMAIL_AGENT_ID = "installed:email"
SUPPORTED_PROVIDERS = ("google", "microsoft")

router = APIRouter(
    prefix="/v1/email", tags=["email-connectors"], include_in_schema=False
)


class ConfigureRequest(BaseModel):
    client_id: str = Field(
        ..., min_length=1, description="OAuth client id (user-supplied)."
    )
    client_secret: str = Field(
        default="",
        description="OAuth client secret (Google requires it even for PKCE).",
    )
    scopes: Optional[List[str]] = Field(
        default=None, description="Override the provider's default scopes."
    )


class CompleteRequest(BaseModel):
    flow_id: str = Field(
        ..., min_length=1, description="flow_id returned by /configure."
    )


def _require_supported(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown provider {provider!r}; supported: {', '.join(SUPPORTED_PROVIDERS)}",
        )


@router.get("/connectors")
async def list_email_connectors() -> Dict[str, Any]:
    """Status of the mailbox connectors the email agent can send from."""
    from gaia.connectors.api import connected_mailbox_providers, get_connection
    from gaia.connectors.store import DEFAULT_ACCOUNT

    connected = set(connected_mailbox_providers())
    providers: List[Dict[str, Any]] = []
    for pid in SUPPORTED_PROVIDERS:
        conn = get_connection(pid) or {}
        # DEFAULT_ACCOUNT ("default") is the store's no-account-email sentinel —
        # surface it as absent so the UI shows "connected", not "connected · default".
        email = conn.get("account_email") or None
        if email == DEFAULT_ACCOUNT:
            email = None
        providers.append(
            {
                "provider": pid,
                "connected": pid in connected,
                "account_email": email,
                "scopes": conn.get("scopes", []),
            }
        )
    return {"agent_id": EMAIL_AGENT_ID, "providers": providers}


@router.post("/connectors/{provider}/configure")
async def configure_email_connector(
    provider: str, body: ConfigureRequest
) -> Dict[str, Any]:
    """Persist the user's OAuth client creds and start the PKCE flow.

    Returns ``{flow_id, authorization_url}``. The connector framework opens the
    browser and stands up its own loopback callback; call ``/complete`` next.
    """
    _require_supported(provider)
    from gaia.connectors.handler import configure

    config: Dict[str, Any] = {
        "client_id": body.client_id,
        "client_secret": body.client_secret,
    }
    if body.scopes:
        config["scopes"] = body.scopes
    try:
        return await configure(provider, config)
    except Exception as e:  # surface the framework's actionable error to the page
        raise HTTPException(status_code=400, detail=f"configure {provider}: {e}") from e


@router.post("/connectors/{provider}/complete")
async def complete_email_connector(
    provider: str, body: CompleteRequest
) -> Dict[str, Any]:
    """Wait for the OAuth redirect, then grant the mailbox to the email agent."""
    _require_supported(provider)
    from gaia.connectors.api import complete_authorization, grant_agent

    try:
        state = await complete_authorization(body.flow_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth completion: {e}") from e
    # Without this grant the connection exists but the email agent can't use it
    # at send time (token access is scoped per granted agent).
    scopes = list(state.get("scopes") or [])
    try:
        grant_agent(provider, EMAIL_AGENT_ID, scopes)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"connected {provider} but granting to {EMAIL_AGENT_ID} failed: {e}",
        ) from e
    return {"connected": True, **state}


@router.delete("/connectors/{provider}")
async def disconnect_email_connector(provider: str) -> Dict[str, Any]:
    """Disconnect ``provider`` — removes its stored tokens AND per-agent grants,
    so a later reconnect can't silently inherit stale consent (#1592)."""
    _require_supported(provider)
    from gaia.connectors.handler import disconnect

    await disconnect(provider)
    return {"provider": provider, "connected": False}


__all__ = ["router", "EMAIL_AGENT_ID", "SUPPORTED_PROVIDERS"]
