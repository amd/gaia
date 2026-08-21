"""One-shot generator for sales.csv + ground_truth.json (run once, committed output)."""

import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

ROWS = [
    # date, region, product, units, unit_price
    ("2026-04-03", "North", "Widget Basic", 120, 9.50),
    ("2026-04-05", "South", "Widget Basic", 80, 9.50),
    ("2026-04-08", "East", "Widget Pro", 35, 24.00),
    ("2026-04-11", "West", "Widget Basic", 95, 9.50),
    ("2026-04-14", "North", "Widget Pro", 40, 24.00),
    ("2026-04-18", "South", "Gadget Mini", 210, 4.25),
    ("2026-04-21", "East", "Widget Basic", 60, 9.50),
    ("2026-04-25", "West", "Gadget Mini", 150, 4.25),
    ("2026-05-02", "North", "Gadget Mini", 190, 4.25),
    ("2026-05-06", "South", "Widget Pro", 25, 24.00),
    ("2026-05-09", "East", "Gadget Mini", 175, 4.25),
    ("2026-05-13", "West", "Widget Pro", 30, 24.00),
    ("2026-05-16", "North", "Widget Basic", 110, 9.50),
    ("2026-05-20", "South", "Widget Basic", 70, 9.50),
    ("2026-05-24", "East", "Widget Pro", 45, 24.00),
    ("2026-05-28", "West", "Widget Basic", 85, 9.50),
    ("2026-06-01", "North", "Widget Pro", 55, 24.00),
    ("2026-06-05", "South", "Gadget Mini", 230, 4.25),
    ("2026-06-09", "East", "Widget Basic", 75, 9.50),
    ("2026-06-13", "West", "Gadget Mini", 165, 4.25),
    ("2026-06-17", "North", "Gadget Mini", 205, 4.25),
    ("2026-06-21", "South", "Widget Pro", 20, 24.00),
    ("2026-06-25", "East", "Gadget Mini", 185, 4.25),
    ("2026-06-29", "West", "Widget Pro", 50, 24.00),
]


def main() -> None:
    with open(HERE / "sales.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "region", "product", "units", "unit_price", "revenue"])
        for date, region, product, units, price in ROWS:
            writer.writerow(
                [date, region, product, units, f"{price:.2f}", f"{units * price:.2f}"]
            )

    def rev(rows):
        return round(sum(u * p for *_x, u, p in rows), 2)

    regions = sorted({r[1] for r in ROWS})
    products = sorted({r[2] for r in ROWS})
    by_region = {reg: rev([r for r in ROWS if r[1] == reg]) for reg in regions}
    units_by_product = {
        prod: sum(r[3] for r in ROWS if r[2] == prod) for prod in products
    }
    revenue_by_product = {
        prod: rev([r for r in ROWS if r[2] == prod]) for prod in products
    }
    months = sorted({r[0][:7] for r in ROWS})
    revenue_by_month = {m: rev([r for r in ROWS if r[0].startswith(m)]) for m in months}
    truth = {
        "_comment": (
            "Known aggregates for sales.csv, used as ground truth by gaia_data "
            "and data-explore eval scenarios. Regenerate with _gen_sales.py if "
            "the CSV ever changes — never edit these numbers by hand."
        ),
        "row_count": len(ROWS),
        "total_units": sum(r[3] for r in ROWS),
        "total_revenue": rev(ROWS),
        "revenue_by_region": by_region,
        "top_region_by_revenue": max(by_region, key=by_region.get),
        "units_by_product": units_by_product,
        "revenue_by_product": revenue_by_product,
        "best_selling_product_by_units": max(
            units_by_product, key=units_by_product.get
        ),
        "top_product_by_revenue": max(revenue_by_product, key=revenue_by_product.get),
        "revenue_by_month": revenue_by_month,
        "distinct_regions": regions,
        "distinct_products": products,
    }
    (HERE / "ground_truth.json").write_text(
        json.dumps(truth, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", HERE / "sales.csv", "and", HERE / "ground_truth.json")


if __name__ == "__main__":
    main()
