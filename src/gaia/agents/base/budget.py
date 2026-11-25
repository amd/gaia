# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Limits a host sets on one turn.

A :class:`TurnBudget` is checked before every model request the turn makes,
whichever part of the loop makes it. When a limit is reached no further
request is sent and the result's ``stop_reason`` names the limit. The user's
message is never changed by it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from gaia.agents.base.turn_outcome import (
    STOP_COST,
    STOP_DEADLINE,
    STOP_MODEL_CALLS,
    STOP_OUTPUT_TOKENS,
)
from gaia.chat.sdk import RequestRefused
from gaia.llm.pricing import price, rate_card

#: Stop reasons a :class:`TurnBudget` produces.
BUDGET_STOP_REASONS = frozenset(
    {STOP_MODEL_CALLS, STOP_OUTPUT_TOKENS, STOP_DEADLINE, STOP_COST}
)


class TurnBudgetExceeded(RequestRefused):
    """A model request was refused because the turn's budget has no room for it."""

    def __init__(self, reason: str):
        super().__init__(f"turn budget reached: {reason}")
        self.reason = reason


class BudgetNeedsUsage(RuntimeError):
    """A token or dollar limit is set, but the client reported no token usage."""


@dataclass(frozen=True)
class TurnBudget:
    """What one turn may spend. ``None`` leaves a limit off.

    Args:
        max_model_calls: Model requests the turn may make, retries included.
        max_output_tokens: Output tokens across the whole turn. Each request's
            own cap is lowered to what is left.
        deadline_s: Wall-clock seconds from the start of the turn. Checked
            before each model request; one already in flight is not cut short.
        max_cost_usd: Dollars at the model's list price. The model must be on
            the rate card (:mod:`gaia.llm.pricing`), or the turn is refused
            when it starts.
    """

    max_model_calls: Optional[int] = None
    max_output_tokens: Optional[int] = None
    deadline_s: Optional[float] = None
    max_cost_usd: Optional[float] = None

    def __post_init__(self) -> None:
        for name in ("max_model_calls", "max_output_tokens"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(
                    f"TurnBudget.{name} must be a positive integer or None, "
                    f"got {value!r}."
                )
        for name in ("deadline_s", "max_cost_usd"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(
                    f"TurnBudget.{name} must be a positive number or None, "
                    f"got {value!r}."
                )

    def require_price(self, model: Optional[str]) -> None:
        """Refuse a dollar limit on a model the rate card cannot price."""
        if self.max_cost_usd is None:
            return
        if not model or rate_card(model) is None:
            raise ValueError(
                f"TurnBudget.max_cost_usd is set, but model {model!r} has no "
                "price on the rate card, so its cost cannot be limited. Add "
                "the model to RATES in gaia/llm/pricing.py, or limit the turn "
                "with max_output_tokens or max_model_calls instead."
            )


@dataclass
class TurnSpend:
    """What a turn has used so far, from each model request's reported usage."""

    model_calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0

    def add_usage(self, usage: Any) -> None:
        """Add one request's OpenAI-shape ``usage``; anything else is ignored."""
        if not isinstance(usage, dict):
            return
        self.input_tokens += _count(usage.get("prompt_tokens"))
        self.output_tokens += _count(usage.get("completion_tokens"))
        cached = usage.get("cached_tokens")
        details = usage.get("prompt_tokens_details")
        if cached is None and isinstance(details, dict):
            cached = details.get("cached_tokens")
        self.cached_tokens += _count(cached)

    def cost_usd(self, model: Optional[str]) -> Optional[float]:
        """Dollars so far at *model*'s list price; ``None`` when it has none."""
        if not model:
            return None
        return price(model, self.input_tokens, self.cached_tokens, self.output_tokens)

    def as_dict(self) -> Dict[str, int]:
        return {
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "output_tokens": self.output_tokens,
        }


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def exhausted(
    budget: Optional[TurnBudget],
    spend: TurnSpend,
    elapsed_s: float,
    model: Optional[str],
) -> Optional[str]:
    """The stop reason when *budget* has no room for another model request."""
    if budget is None:
        return None
    if budget.deadline_s is not None and elapsed_s >= budget.deadline_s:
        return STOP_DEADLINE
    if (
        budget.max_model_calls is not None
        and spend.model_calls >= budget.max_model_calls
    ):
        return STOP_MODEL_CALLS
    if (
        budget.max_output_tokens is not None
        and spend.output_tokens >= budget.max_output_tokens
    ):
        return STOP_OUTPUT_TOKENS
    if budget.max_cost_usd is not None:
        cost = spend.cost_usd(model)
        if cost is not None and cost >= budget.max_cost_usd:
            return STOP_COST
    return None
