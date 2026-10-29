# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Cloud prices before a model is picked, this month's spending, and the limit."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import spending
from ..database import ChatDatabase
from ..dependencies import get_db

router = APIRouter(tags=["spending"])


def _report(db: ChatDatabase) -> Dict[str, Any]:
    try:
        return spending.month_report(db)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/spending")
def get_spending(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """This month's cloud spending at list price, the limit, and where it stands."""
    return _report(db)


class LimitRequest(BaseModel):
    """``limit_usd`` None removes the limit."""

    limit_usd: Optional[float] = None


@router.put("/api/spending/limit")
def set_spending_limit(
    body: LimitRequest, db: ChatDatabase = Depends(get_db)
) -> Dict[str, Any]:
    """Set or clear the monthly limit for cloud chats."""
    try:
        spending.set_limit(db, body.limit_usd)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _report(db)


@router.post("/api/spending/allow-month")
def allow_spending_this_month(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """Let cloud chats go on past the limit until the month ends."""
    spending.allow_this_month(db)
    return _report(db)


@router.get("/api/spending/prices/{provider}")
def cloud_prices(provider: str) -> Dict[str, Any]:
    """List prices per million tokens for a provider's models, before connecting."""
    if provider not in ("fireworks", "amd"):
        raise HTTPException(status_code=404, detail=f"Unknown provider {provider!r}.")
    models = spending.price_list(provider)
    return {
        "provider": provider,
        "models": models,
        "source": spending.PRICE_SOURCE if models else None,
        # The gateway bills through the organization that runs it.
        "note": (
            None
            if models
            else "Prices are set by the organization that runs the gateway; GAIA doesn't know them."
        ),
    }
