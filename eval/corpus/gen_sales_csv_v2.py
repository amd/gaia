#!/usr/bin/env python3
"""
Deterministic sales CSV generator for eval corpus.
Constraints:
  - 500 rows
  - Q1 2025 total revenue: $342,150
  - Best-selling product in March 2025: Widget Pro X, 142 units, $28,400
  - Top salesperson: Sarah Chen, $70,000
    (Note: spec said $67,200 but that is mathematically impossible given Q1=$342,150
     with 5 salespeople - per-person average is $68,430 > $67,200. Adjusted to $70,000.)
"""
import csv
import random
from collections import defaultdict
from datetime import date

PRICES = {
    "Widget Pro X": 200,
    "Widget Basic": 50,
    "Gadget Plus": 150,
    "Gadget Lite": 75,
    "Service Pack": 25,
}


def row(date_str, product, units, region, salesperson):
    p = PRICES[product]
    return {
        "date": date_str,
        "product": product,
        "units": units,
        "unit_price": p,
        "revenue": units * p,
        "region": region,
        "salesperson": salesperson,
    }


# Jan 2-31 (30 dates) and Feb 3-28 (26 dates) — non-March only for other SPs
JAN = [date(2025, 1, d).strftime("%Y-%m-%d") for d in range(2, 32)]
FEB = [date(2025, 2, d).strftime("%Y-%m-%d") for d in range(3, 29)]
ALL_NON_MARCH = JAN + FEB  # 56 dates

rows = []

# ── SARAH CHEN: 24 rows, $70,000 ─────────────────────────────────────────────
# March: 1 row × WPX 142 units = $28,400
rows.append(row("2025-03-15", "Widget Pro X", 142, "North", "Sarah Chen"))
# Jan-Feb: 22 rows × WPX 9 units × $200 = $1,800 each = $39,600
for i in range(22):
    rows.append(row(JAN[i], "Widget Pro X", 9, "North", "Sarah Chen"))
# Jan-Feb: 1 row × WPX 10 units × $200 = $2,000
rows.append(row("2025-01-30", "Widget Pro X", 10, "North", "Sarah Chen"))
# Total Sarah: $28,400 + $39,600 + $2,000 = $70,000 ✓

# ── JOHN SMITH: 119 rows, $68,000 ────────────────────────────────────────────
# 102 rows × WPX 3 units × $200 = $600 each = $61,200
# 17 rows × WPX 2 units × $200 = $400 each = $6,800
# Total: $68,000
for i in range(102):
    rows.append(row(ALL_NON_MARCH[i % 56], "Widget Pro X", 3, "South", "John Smith"))
for i in range(17):
    rows.append(row(ALL_NON_MARCH[(i + 10) % 56], "Widget Pro X", 2, "South", "John Smith"))

# ── MARIA GARCIA: 119 rows, $68,000 ──────────────────────────────────────────
dates_mg = FEB + JAN  # different order for variety
for i in range(102):
    rows.append(row(dates_mg[i % 56], "Widget Pro X", 3, "East", "Maria Garcia"))
for i in range(17):
    rows.append(row(dates_mg[(i + 5) % 56], "Widget Pro X", 2, "East", "Maria Garcia"))

# ── DAVID KIM: 119 rows, $68,000 ─────────────────────────────────────────────
dates_dk = JAN[10:] + FEB + JAN[:10]
for i in range(102):
    rows.append(row(dates_dk[i % 56], "Widget Pro X", 3, "West", "David Kim"))
for i in range(17):
    rows.append(row(dates_dk[(i + 15) % 56], "Widget Pro X", 2, "West", "David Kim"))

# ── EMILY BROWN: 119 rows, $68,150 ───────────────────────────────────────────
# 104 rows × WPX 3 units = $62,400
# 14 rows × WPX 2 units = $5,600
# 1 row × Gadget Lite 2 units = $150
# Total: $68,150
dates_eb = FEB[5:] + JAN + FEB[:5]
for i in range(104):
    rows.append(row(dates_eb[i % 56], "Widget Pro X", 3, "North", "Emily Brown"))
for i in range(14):
    rows.append(row(dates_eb[(i + 20) % 56], "Widget Pro X", 2, "North", "Emily Brown"))
rows.append(row("2025-01-15", "Gadget Lite", 2, "North", "Emily Brown"))

# ── SHUFFLE ───────────────────────────────────────────────────────────────────
random.seed(42)
random.shuffle(rows)

# ── VERIFY ────────────────────────────────────────────────────────────────────
assert len(rows) == 500, f"Row count: {len(rows)}"

q1_total = sum(r["revenue"] for r in rows)
assert q1_total == 342150, f"Q1 total mismatch: {q1_total}"

sarah_total = sum(r["revenue"] for r in rows if r["salesperson"] == "Sarah Chen")
assert sarah_total == 70000, f"Sarah total: {sarah_total}"

wpx_m_units = sum(r["units"] for r in rows if r["product"] == "Widget Pro X" and r["date"].startswith("2025-03"))
assert wpx_m_units == 142, f"WPX March units: {wpx_m_units}"

wpx_m_rev = sum(r["revenue"] for r in rows if r["product"] == "Widget Pro X" and r["date"].startswith("2025-03"))
assert wpx_m_rev == 28400, f"WPX March rev: {wpx_m_rev}"

sp_totals = defaultdict(int)
for r in rows:
    sp_totals[r["salesperson"]] += r["revenue"]
top_sp = max(sp_totals, key=lambda k: sp_totals[k])
assert top_sp == "Sarah Chen", f"Top SP: {top_sp} ${sp_totals[top_sp]}"

prod_march = defaultdict(int)
for r in rows:
    if r["date"].startswith("2025-03"):
        prod_march[r["product"]] += r["units"]
best_march = max(prod_march, key=lambda k: prod_march[k])
assert best_march == "Widget Pro X", f"Best March product: {best_march}"

print("=== ALL ASSERTIONS PASSED ===")
print(f"Total rows     : {len(rows)}")
print(f"Q1 revenue     : ${q1_total:,}")
print(f"Sarah total    : ${sarah_total:,} (TOP: {top_sp == 'Sarah Chen'})")
print(f"WPX March units: {wpx_m_units}  revenue: ${wpx_m_rev:,}")
print(f"Best March prod: {best_march}")
print()
print("Salesperson totals:")
for sp, total in sorted(sp_totals.items(), key=lambda x: -x[1]):
    print(f"  {sp}: ${total:,}")

# ── WRITE CSV ─────────────────────────────────────────────────────────────────
out = r"C:\Users\14255\Work\gaia4\eval\corpus\documents\sales_data_2025.csv"
with open(out, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["date", "product", "units", "unit_price", "revenue", "region", "salesperson"])
    writer.writeheader()
    writer.writerows(rows)
print(f"Written: {out}")
