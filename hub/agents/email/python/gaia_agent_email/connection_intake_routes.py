# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""OAuth forward-OUT intake for the email sidecar (issue #2154 / V2-14).

The **sidecar side** of the role inversion (design §0.6): the daemon (custody
home) owns the OAuth refresh token and forwards SHORT-LIVED access tokens OUT to
these routes. The sidecar stores them in memory (``forwarded_credentials``) and
answers mailbox calls with them — it never holds a refresh token.

- ``POST /v1/connections/{provider}``   — accept a forwarded access token.
- ``GET  /v1/connections``              — metadata-only list (never tokens).
- ``DELETE /v1/connections/{provider}`` — withdraw a forward (revoke/uninstall).

Part of the frozen sidecar contract (schema 2.5, additive), so unlike the
playground's connector routes these ARE in the OpenAPI schema. The caller-token
gate is applied by ``server.build_app`` (same per-session bearer as every other
mailbox-touching router) — only the daemon holding the sidecar bearer can forward
a credential. Loud, actionable errors; no silent fallbacks.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from gaia_agent_email import forwarded_credentials
from gaia_agent_email.connector_routes import SUPPORTED_PROVIDERS
from gaia_agent_email.contract import (
    ForwardedConnectionRequest,
    ForwardedConnectionsResponse,
    ForwardedConnectionSummary,
    ForwardedConnectionWithdrawResponse,
)

from gaia.connectors.errors import ConnectorsError
from gaia.logger import get_logger

log = get_logger(__name__)

router = APIRouter(prefix="/v1/connections", tags=["email-forward-out"])


def _require_supported(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"unknown provider {provider!r}; forwardable providers: "
                f"{', '.join(SUPPORTED_PROVIDERS)}"
            ),
        )


@router.post("/{provider}", response_model=ForwardedConnectionSummary)
async def import_forwarded_connection(
    provider: str, body: ForwardedConnectionRequest
) -> ForwardedConnectionSummary:
    """Accept a daemon-forwarded short-lived access token for ``provider``.

    Stores it in memory only. A malformed forward (empty token / non-positive
    expiry) is rejected loudly (400) rather than deferred to an opaque 401 in a
    later mailbox call.
    """
    _require_supported(provider)
    try:
        cred = forwarded_credentials.set_forwarded(
            provider,
            access_token=body.access_token,
            scopes=list(body.scopes),
            expires_at=body.expires_at,
            account_email=body.account_email or "",
        )
    except ConnectorsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ForwardedConnectionSummary(
        provider=provider,
        scopes=sorted(cred.scopes),
        account_email=cred.account_email or None,
        expires_at=cred.expires_at,
    )


@router.get("", response_model=ForwardedConnectionsResponse)
async def list_forwarded_connections() -> ForwardedConnectionsResponse:
    """Metadata-only view of the forwarded connections. NEVER returns tokens."""
    return ForwardedConnectionsResponse(
        connections=[
            ForwardedConnectionSummary(
                provider=item["provider"],
                scopes=item["scopes"],
                account_email=item["account_email"],
                expires_at=item["expires_at"],
            )
            for item in forwarded_credentials.list_forwarded()
        ]
    )


@router.delete("/{provider}", response_model=ForwardedConnectionWithdrawResponse)
async def withdraw_forwarded_connection(
    provider: str,
) -> ForwardedConnectionWithdrawResponse:
    """Withdraw a forwarded credential (daemon revoke/uninstall). Idempotent."""
    _require_supported(provider)
    withdrawn = forwarded_credentials.withdraw(provider)
    return ForwardedConnectionWithdrawResponse(provider=provider, withdrawn=withdrawn)


__all__ = ["router"]
