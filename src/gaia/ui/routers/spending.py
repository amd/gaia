# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Cloud prices before a model is picked, this month's spending, and the limit."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import spending
from ..database import ChatDatabase
from ..dependencies import get_db
from ..models import WireModel

router = APIRouter(tags=["spending"])

_Number = Union[int, float]


class PriceCard(WireModel):
    """List price in dollars per million tokens, with where it comes from."""

    input_per_million: float
    cached_input_per_million: float
    output_per_million: float
    source: str


class SpendingModelRow(WireModel):
    """One model's share of this month's cloud spending."""

    model: str
    provider: str
    turns: int
    usd: float
    unpriced_turns: int
    unreported_turns: int


class SpendingReport(WireModel):
    """This month's cloud spending, the limit, and where it stands."""

    month: str
    total_usd: _Number
    turns: int
    unpriced_turns: int
    unreported_turns: int
    limit_usd: Optional[float]
    state: Literal["no_limit", "allowed_over", "reached", "near", "ok"]
    by_model: List[SpendingModelRow]
    price_source: str


class ChatSpendItem(WireModel):
    """One answer's cost in a chat."""

    message_id: int
    created_at: str
    model: Optional[str]
    kind: Optional[str]
    usd: Optional[_Number]


class ChatSpendTotals(WireModel):
    """Cost and answer counts for a set of cloud answers."""

    usd: _Number
    answers: int
    unpriced_answers: int


class ChatSpendChat(ChatSpendTotals):
    """This chat's cloud answers."""

    items: List[ChatSpendItem]


class ChatSpendToday(ChatSpendTotals):
    """Every chat's cloud answers since the start of the viewer's day."""

    chats: int
    since: str


class ChatSpendReport(WireModel):
    """What this chat's cloud answers cost, and today's across chats."""

    paid: bool
    model: Optional[str]
    chat: ChatSpendChat
    today: ChatSpendToday
    price_source: str


class PriceListEntry(WireModel):
    """A model's list price before the provider is connected."""

    model: str
    name: str
    rank: Optional[int]
    note: Optional[str]
    price: Optional[PriceCard]


class PriceListResponse(WireModel):
    """Every priced model a provider offers; ``source`` and ``note`` explain a gap."""

    provider: str
    models: List[PriceListEntry]
    source: Optional[str]
    note: Optional[str]


def _report(db: ChatDatabase) -> Dict[str, Any]:
    try:
        return spending.month_report(db)
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "/api/spending", response_model=SpendingReport, response_model_exclude_unset=True
)
def get_spending(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """This month's cloud spending at list price, the limit, and where it stands."""
    return _report(db)


@router.get(
    "/api/spending/chat/{session_id}",
    response_model=ChatSpendReport,
    response_model_exclude_unset=True,
)
def get_chat_spending(
    session_id: str,
    since: str,
    model: Optional[str] = None,
    db: ChatDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """This chat's cloud spending and every chat's since *since* (the local day's start)."""
    if not db.get_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        return spending.chat_spend(db, session_id, model, since)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


class LimitRequest(BaseModel):
    """``limit_usd`` None removes the limit."""

    limit_usd: Optional[float] = None


@router.put(
    "/api/spending/limit",
    response_model=SpendingReport,
    response_model_exclude_unset=True,
)
def set_spending_limit(
    body: LimitRequest, db: ChatDatabase = Depends(get_db)
) -> Dict[str, Any]:
    """Set or clear the monthly limit for cloud chats."""
    try:
        spending.set_limit(db, body.limit_usd)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _report(db)


@router.post(
    "/api/spending/allow-month",
    response_model=SpendingReport,
    response_model_exclude_unset=True,
)
def allow_spending_this_month(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """Let cloud chats go on past the limit until the month ends."""
    spending.allow_this_month(db)
    return _report(db)


@router.get(
    "/api/spending/prices/{provider}",
    response_model=PriceListResponse,
    response_model_exclude_unset=True,
)
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
