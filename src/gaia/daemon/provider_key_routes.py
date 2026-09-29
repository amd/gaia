# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/providers/{provider}/*`` — keep a cloud API key for the TUI.

The TUI's ``/provider`` panel hands a Fireworks or AMD key to Lemonade, which
holds it in memory only — so the next TUI, or the next embedded-Lemonade start,
had to be given it again. The credential store is Python-only and go-keyring
cannot read what python-keyring writes (see ``gateway_routes``), so the key
reaches it through here.

The key travels over authenticated loopback, the channel the TUI already uses,
and is never logged, echoed back, or written to a daemon file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from gaia.logger import get_logger

log = get_logger(__name__)


class RememberKeyRequest(BaseModel):
    """The key to keep. Never logged, never returned."""

    key: str = Field(min_length=1)


def build_provider_key_router(token: str) -> APIRouter:
    """Routes that keep and replay a cloud provider's key on the TUI's behalf."""
    from gaia.daemon.app import build_require_token

    router = APIRouter(
        prefix="/daemon/v1/providers",
        tags=["providers"],
        dependencies=[Depends(build_require_token(token))],
    )

    def _known(provider: str) -> None:
        from gaia.llm.cloud_keys import PROVIDERS

        if provider not in PROVIDERS:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown provider {provider!r}. Known: {', '.join(PROVIDERS)}.",
            )

    @router.post("/{provider}/key")
    def remember(provider: str, body: RememberKeyRequest) -> dict:
        """Keep the key in the OS credential store."""
        from gaia.llm.cloud_keys import CloudKeyError, remember_key

        _known(provider)
        try:
            remember_key(provider, body.key)
        except CloudKeyError as e:
            # 503: the TUI says the key works for this session only.
            raise HTTPException(status_code=503, detail=str(e)) from e
        log.info(f"{provider} key stored in the OS credential store.")
        return {"remembered": True}

    @router.post("/{provider}/authenticate")
    def authenticate(provider: str) -> dict:
        """Replay the kept key into Lemonade. The key never leaves this process."""
        import requests

        from gaia.llm.cloud_keys import CloudKeyError, ensure_authenticated
        from gaia.llm.gateway import GatewayError

        _known(provider)
        try:
            return {"authenticated": ensure_authenticated(provider)}
        except (CloudKeyError, GatewayError, requests.RequestException) as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    @router.delete("/{provider}/key")
    def forget(provider: str) -> dict:
        """Remove the kept key. Idempotent."""
        from gaia.connectors.errors import ConnectorsError
        from gaia.llm.cloud_keys import forget_key

        _known(provider)
        try:
            return {"removed": forget_key(provider)}
        except ConnectorsError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e

    return router
