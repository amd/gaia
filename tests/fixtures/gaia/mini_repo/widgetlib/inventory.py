"""Inventory management for widgetlib."""

from __future__ import annotations


def calculate_reorder_threshold(
    daily_demand: float, lead_time_days: int, safety_stock: int = 10
) -> int:
    """Units on hand at which a SKU should be reordered.

    The threshold is the demand expected during the supplier lead time plus a
    fixed safety stock, rounded up to a whole unit.
    """
    if daily_demand < 0 or lead_time_days < 0 or safety_stock < 0:
        raise ValueError("demand, lead time, and safety stock must be non-negative")
    expected = daily_demand * lead_time_days + safety_stock
    return int(expected) + (0 if expected == int(expected) else 1)


def merge_duplicate_skus(records: list[dict]) -> list[dict]:
    """Collapse records sharing a SKU into one row with summed quantities.

    The first occurrence's descriptive fields win; only ``quantity`` is
    aggregated. Order of first appearance is preserved.
    """
    merged: dict[str, dict] = {}
    for record in records:
        sku = record["sku"]
        if sku in merged:
            merged[sku]["quantity"] += record.get("quantity", 0)
        else:
            merged[sku] = dict(record)
    return list(merged.values())
