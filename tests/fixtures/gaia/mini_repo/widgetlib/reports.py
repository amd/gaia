"""Reporting for widgetlib."""

from __future__ import annotations


def render_quarterly_summary(quarter: str, sales: list[dict]) -> str:
    """Render a plain-text quarterly sales summary.

    *sales* rows need ``product`` and ``revenue`` keys; the summary lists
    products by descending revenue with a grand total.
    """
    by_product: dict[str, float] = {}
    for row in sales:
        by_product[row["product"]] = by_product.get(row["product"], 0.0) + float(
            row["revenue"]
        )
    lines = [f"Quarterly summary — {quarter}", "=" * 30]
    for product, revenue in sorted(
        by_product.items(), key=lambda item: item[1], reverse=True
    ):
        lines.append(f"{product:<20} {revenue:>9.2f}")
    lines.append("-" * 30)
    lines.append(f"{'TOTAL':<20} {sum(by_product.values()):>9.2f}")
    return "\n".join(lines)
