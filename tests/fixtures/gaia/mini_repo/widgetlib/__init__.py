"""widgetlib — tiny fixture package for code-index eval scenarios."""

from widgetlib.inventory import calculate_reorder_threshold, merge_duplicate_skus
from widgetlib.pricing import apply_seasonal_discount, convert_currency_rounded
from widgetlib.reports import render_quarterly_summary

__all__ = [
    "apply_seasonal_discount",
    "calculate_reorder_threshold",
    "convert_currency_rounded",
    "merge_duplicate_skus",
    "render_quarterly_summary",
]
