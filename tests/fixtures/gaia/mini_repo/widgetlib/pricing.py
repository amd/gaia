"""Pricing helpers for widgetlib."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

#: Seasonal discount rates by season keyword.
SEASONAL_RATES = {"spring": 0.05, "summer": 0.10, "autumn": 0.05, "winter": 0.20}


def apply_seasonal_discount(price: float, season: str) -> float:
    """Discount *price* by the rate for *season* (winter is the deepest cut).

    Raises ValueError for a season outside ``SEASONAL_RATES`` rather than
    silently charging full price.
    """
    key = season.strip().lower()
    if key not in SEASONAL_RATES:
        raise ValueError(
            f"unknown season {season!r}; expected one of {sorted(SEASONAL_RATES)}"
        )
    return round(price * (1 - SEASONAL_RATES[key]), 2)


def convert_currency_rounded(amount: float, rate: float, decimals: int = 2) -> float:
    """Convert *amount* by *rate* using banker's-shop rounding (half up).

    Uses Decimal so 2.675 → 2.68 instead of float's 2.67 surprise.
    """
    if rate <= 0:
        raise ValueError("exchange rate must be positive")
    quantum = Decimal(1).scaleb(-decimals)
    converted = Decimal(str(amount)) * Decimal(str(rate))
    return float(converted.quantize(quantum, rounding=ROUND_HALF_UP))
