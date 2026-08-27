# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/gateway/token`` — persist an LLM-gateway token for the TUI.

The credential store is Python-only, so the Go TUI cannot write to it. The two
keyring libraries do not interoperate either: verified on Windows, a value
written by ``github.com/zalando/go-keyring`` is invisible to ``python-keyring``
and vice versa, because they compose different Credential Manager target names.
Without this route a token typed into the TUI worked for one session and was
gone after the next Lemonade restart, while the same token entered through
``gaia gateway auth`` persisted — an asymmetry with no defensible reason.

The token reaches the daemon over authenticated loopback, which is the channel
the TUI already uses for everything else, and is no wider an exposure than what
the TUI already does: it sends the same token over loopback to Lemonade on the
line before. The daemon then hands it to the same ``remember_token`` the CLI
uses, so there is one implementation of "where a gateway token lives".

The value is never logged, never echoed back, and never written to a daemon
file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from gaia.logger import get_logger

log = get_logger(__name__)


class RememberTokenRequest(BaseModel):
    """The token to persist. Never logged, never returned."""

    token: str = Field(min_length=1)


def build_gateway_router(token: str) -> APIRouter:
    """Routes for persisting a gateway token on the TUI's behalf.

    Guarded by the daemon's client token, exactly like the rest of
    ``/daemon/v1``.
    """
    from gaia.daemon.app import build_require_token

    require_token = build_require_token(token)
    router = APIRouter(
        prefix="/daemon/v1/gateway",
        tags=["gateway"],
        dependencies=[Depends(require_token)],
    )

    @router.post("/token")
    def remember(body: RememberTokenRequest) -> dict:
        """Persist the token in the OS credential store."""
        from gaia.llm.gateway import GatewayError, remember_token

        try:
            remember_token(body.token)
        except GatewayError as e:
            # The store is unavailable (headless Linux, locked keychain, a null
            # backend). Report it so the TUI can say the token works for this
            # session only, rather than implying it was kept.
            raise HTTPException(status_code=503, detail=str(e)) from e
        except Exception as e:  # noqa: BLE001 - surfaced with context, not hidden
            raise HTTPException(
                status_code=500,
                detail=f"Could not store the gateway token: {e}",
            ) from e
        log.info("Gateway token stored in the OS credential store.")
        return {"remembered": True}

    @router.post("/authenticate")
    def authenticate() -> dict:
        """Replay the stored token into Lemonade, which forgets it on restart.

        The token is read and used entirely inside this process — it is never
        returned to the caller.
        """
        from gaia.llm.gateway import GatewayError, GatewayManager

        try:
            return {"authenticated": GatewayManager().ensure_authenticated()}
        except GatewayError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    @router.delete("/token")
    def forget() -> dict:
        """Remove the stored token. Idempotent."""
        from gaia.llm.gateway import forget_token

        return {"removed": forget_token()}

    return router
