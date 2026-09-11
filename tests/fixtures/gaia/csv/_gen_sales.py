"""One-shot generator for sales.csv + ground_truth.json (run once, committed output).

The six aggregates are the contract (eval/scenarios/GAIA_FIXTURE_VALUES.md);
this script ASSERTS them all before writing, so a row edit that breaks the
contract fails here instead of shipping a wrong ground truth.
"""

import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

ROWS = [
    # date, region, product, units, revenue
    ("2026-01-05", "North", "Gadget Pro", 5, 1500),
    ("2026-01-12", "South", "Gadget Lite", 8, 1200),
    ("2026-01-19", "West", "Gadget Max", 6, 900),
    ("2026-01-26", "North", "Gadget Lite", 12, 1800),
    ("2026-02-04", "South", "Gadget Pro", 7, 2100),
    ("2026-02-11", "West", "Gadget Lite", 9, 1350),
    ("2026-02-18", "North", "Gadget Max", 7, 1050),
    ("2026-02-25", "South", "Gadget Lite", 11, 1650),
    ("2026-03-03", "West", "Gadget Pro", 8, 2400),
    ("2026-03-10", "North", "Gadget Pro", 4, 1200),
    ("2026-03-17", "South", "Gadget Max", 19, 2850),
    ("2026-03-24", "North", "Gadget Lite", 4, 600),
]


def main() -> None:
    def rev(rows):
        return sum(r[4] for r in rows)

    products = sorted({r[2] for r in ROWS})
    by_product = {p: rev([r for r in ROWS if r[2] == p]) for p in products}
    by_month = {
        m: rev([r for r in ROWS if r[0].startswith(m)])
        for m in sorted({r[0][:7] for r in ROWS})
    }
    top_product = max(by_product, key=by_product.get)
    top_month = max(by_month, key=by_month.get)
    north = rev([r for r in ROWS if r[1] == "North"])

    # The contract's six aggregates — fail loudly before writing anything.
    assert len(ROWS) == 12, len(ROWS)
    assert rev(ROWS) == 18600, rev(ROWS)
    assert (top_product, by_product[top_product]) == ("Gadget Pro", 7200), by_product
    assert north == 6150, north
    assert (top_month, by_month[top_month]) == ("2026-03", 7050), by_month
    assert len(products) == 3, products

    with open(HERE / "sales.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "region", "product", "units", "revenue"])
        writer.writerows(ROWS)

    truth = {
        "_comment": (
            "The six contract aggregates for sales.csv "
            "(eval/scenarios/GAIA_FIXTURE_VALUES.md). Regenerate with "
            "_gen_sales.py if the CSV ever changes — never edit by hand."
        ),
        "row_count": 12,
        "total_revenue": 18600,
        "top_product_by_revenue": {"product": "Gadget Pro", "revenue": 7200},
        "north_region_revenue": 6150,
        "top_month_by_revenue": {"month": "2026-03", "revenue": 7050},
        "distinct_products": 3,
    }
    (HERE / "ground_truth.json").write_text(
        json.dumps(truth, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", HERE / "sales.csv", "and", HERE / "ground_truth.json")


if __name__ == "__main__":
    main()
