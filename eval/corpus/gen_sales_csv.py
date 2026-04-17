#!/usr/bin/env python3
"""
Generate sales_data_2025.csv with exact required totals.

Embedded facts (from manifest.json):
  - Q1 2025 total revenue:              $342,150  (verified)
  - Best-selling product in March 2025: Widget Pro X, 142 units, $28,400 (verified)
  - Top-performing salesperson noted:   Sarah Chen, $67,200 (verified)

SPEC NOTE: Q1=$342,150 with 5 salespeople averages $68,430/person.
For Sarah ($67,200) to be the true maximum, the other 4 would need to average
<$67,200 each, but they must total $274,950 (avg $68,737 > Sarah).
This is mathematically impossible, so Sarah will NOT be the #1 earner in the raw data.
The ground truth for "top_salesperson" in the manifest is embedded as the known
intended answer; the spec inconsistency is documented in phase1_complete.md.
"""
import random
import csv
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

# ─── constants ────────────────────────────────────────────────────────────────
PRICES = {
    "Widget Pro X": 200,
    "Widget Basic": 50,
    "Gadget Plus": 150,
    "Gadget Lite": 75,
    "Service Pack": 300,
}
PRODUCTS = list(PRICES.keys())
# In March, other salespeople only sell cheap products to keep their March
# unit counts below WPX's 142.  Widget Basic (50) & Gadget Lite (75) only.
MARCH_OTHER_PRODS = ["Widget Basic", "Gadget Lite"]
REGIONS = ["North", "South", "East", "West"]
OTHER_SP = ["John Smith", "Maria Garcia", "David Kim", "Emily Brown"]

ALL_DATES = [date(2025, 1, 1) + timedelta(days=i) for i in range(91)]
JAN_FEB_DATES = [d for d in ALL_DATES if d.month in (1, 2)]
MARCH_DATES   = [d for d in ALL_DATES if d.month == 3]

# ─── Sarah Chen fixed rows — exactly 24, exactly $67,200 ──────────────────────
# Widget Pro X in March: 10 rows = 142 units = $28,400
# 15+14+13+14+15+14+13+16+14+14 = 142
SARAH_WPX_MARCH = [
    ("2025-03-03", "Widget Pro X", 15, 200, 3000, "North"),
    ("2025-03-06", "Widget Pro X", 14, 200, 2800, "East"),
    ("2025-03-08", "Widget Pro X", 13, 200, 2600, "South"),
    ("2025-03-11", "Widget Pro X", 14, 200, 2800, "West"),
    ("2025-03-13", "Widget Pro X", 15, 200, 3000, "North"),
    ("2025-03-17", "Widget Pro X", 14, 200, 2800, "East"),
    ("2025-03-19", "Widget Pro X", 13, 200, 2600, "South"),
    ("2025-03-21", "Widget Pro X", 16, 200, 3200, "West"),
    ("2025-03-24", "Widget Pro X", 14, 200, 2800, "North"),
    ("2025-03-27", "Widget Pro X", 14, 200, 2800, "East"),
]

# Extra Sarah rows — 14 rows, sum = $38,800
# Running sum after each row listed in comment
SARAH_EXTRA = [
    ("2025-01-06", "Service Pack",  10, 300,  3000, "North"),  #  3000
    ("2025-01-08", "Service Pack",  12, 300,  3600, "East"),   #  6600
    ("2025-01-13", "Widget Pro X",  15, 200,  3000, "West"),   #  9600
    ("2025-01-15", "Service Pack",  14, 300,  4200, "South"),  # 13800
    ("2025-01-20", "Gadget Plus",   18, 150,  2700, "North"),  # 16500
    ("2025-01-27", "Service Pack",   8, 300,  2400, "East"),   # 18900
    ("2025-02-03", "Widget Pro X",  12, 200,  2400, "West"),   # 21300
    ("2025-02-05", "Widget Pro X",  18, 200,  3600, "South"),  # 24900
    ("2025-02-10", "Gadget Plus",   20, 150,  3000, "North"),  # 27900
    ("2025-02-17", "Service Pack",   9, 300,  2700, "East"),   # 30600
    ("2025-02-24", "Widget Basic",  20,  50,  1000, "West"),   # 31600
    ("2025-03-04", "Service Pack",  12, 300,  3600, "South"),  # 35200
    ("2025-03-26", "Service Pack",   1, 300,   300, "North"),  # 35500
    ("2025-03-28", "Gadget Plus",   22, 150,  3300, "East"),   # 38800
]
# 28400 + 38800 = 67200 ✓


def mk_sarah(t):
    return {"date": t[0], "product": t[1], "units": t[2],
            "unit_price": t[3], "revenue": t[4],
            "region": t[5], "salesperson": "Sarah Chen"}


def mk_row(d, product, units, region, sp):
    price = PRICES[product]
    return {"date": d.isoformat() if isinstance(d, date) else d,
            "product": product, "units": units,
            "unit_price": price, "revenue": units * price,
            "region": region, "salesperson": sp}


def adj_rows_for(amount: int, sp: str, use_march: bool = False) -> list[dict]:
    """
    Build rows for salesperson `sp` summing to exactly `amount` (multiple of 25).
    Always uses January/February dates to avoid touching March stats.
    """
    assert amount >= 0 and amount % 25 == 0, f"bad amount={amount}"
    if amount == 0:
        return []
    rows = []
    rem = amount
    # Date pool: Jan/Feb only (never March) to keep March stats clean
    date_pool = ["2025-01-31", "2025-01-30", "2025-01-29", "2025-02-28",
                 "2025-02-27", "2025-02-26", "2025-01-28", "2025-02-25"]
    di = 0

    # Greedy fill with Service Pack ($300), then smaller
    for product, price in sorted(PRICES.items(), key=lambda x: -x[1]):
        if rem <= 0:
            break
        while rem >= price:
            units = min(rem // price, 100)   # cap at 100 units per row
            rows.append(mk_row(date_pool[di % len(date_pool)], product, units, "North", sp))
            di += 1
            rem -= units * price

    # Remainder < 50 and > 0 must be handled (only multiples of 25 possible)
    # rem=25 cannot be expressed as non-negative combo of {50,75,150,200,300}.
    # Fix: reduce any existing row by 1 unit (-price) then add back (price+25) using
    # Widget Basic + optional Gadget Lite.  Always works as long as rows is non-empty.
    if rem == 25:
        # Try Widget Basic first (easiest: reduce 1 WB, add 1 GL → net +25)
        fixed = False
        for i in reversed(range(len(rows))):
            if rows[i]["product"] == "Widget Basic" and rows[i]["units"] > 1:
                rows[i]["units"] -= 1
                rows[i]["revenue"] -= 50
                rows.append(mk_row(date_pool[di % len(date_pool)], "Gadget Lite", 1, "East", sp))
                rem = 0
                fixed = True
                break
        if not fixed:
            # Replace last Service Pack row: remove 1 SP ($300), add WB×5+GL×1 ($325)
            # net change = -300 + 325 = +25 ✓
            for i in reversed(range(len(rows))):
                if rows[i]["product"] == "Service Pack" and rows[i]["units"] > 0:
                    rows[i]["units"] -= 1
                    rows[i]["revenue"] -= 300
                    if rows[i]["units"] == 0:
                        rows.pop(i)
                    rows.append(mk_row(date_pool[di % len(date_pool)], "Widget Basic", 5, "North", sp))
                    di += 1
                    rows.append(mk_row(date_pool[di % len(date_pool)], "Gadget Lite", 1, "East", sp))
                    rem = 0
                    fixed = True
                    break
        if not fixed:
            # Last resort: replace any product row — reduce by 1, add back with +25
            if rows:
                last = rows[-1]
                price_l = PRICES[last["product"]]
                # We need to add (price_l + 25) using WB($50) and GL($75)
                target = price_l + 25
                k = target // 25
                if k % 2 == 0:
                    a_u, b_u = k // 2, 0
                else:
                    a_u, b_u = (k - 3) // 2, 1
                last["units"] -= 1
                last["revenue"] -= price_l
                if last["units"] == 0:
                    rows.pop()
                if a_u > 0:
                    rows.append(mk_row(date_pool[di % len(date_pool)], "Widget Basic", a_u, "North", sp))
                    di += 1
                if b_u > 0:
                    rows.append(mk_row(date_pool[di % len(date_pool)], "Gadget Lite", b_u, "East", sp))
                rem = 0
            else:
                raise ValueError(f"Cannot handle rem=25 for sp={sp}, rows empty")

    assert rem == 0, f"adj_rows_for: rem={rem} after decomposition"
    assert sum(r["revenue"] for r in rows) == amount
    return rows


def main():
    random.seed(42)

    # ── Build Sarah's fixed rows ───────────────────────────────────────────────
    sarah_rows = [mk_sarah(t) for t in SARAH_WPX_MARCH + SARAH_EXTRA]
    assert len(sarah_rows) == 24
    sarah_total = sum(r["revenue"] for r in sarah_rows)
    assert sarah_total == 67200, f"Sarah total={sarah_total}"
    sarah_wpx_mar = sum(r["units"] for r in sarah_rows
                        if r["product"] == "Widget Pro X" and r["date"].startswith("2025-03"))
    assert sarah_wpx_mar == 142

    # ── Generate random rows for other salespeople ─────────────────────────────
    # We generate exactly 468 random rows (leaving 8 slots for adjustment rows).
    # March rows: only Widget Basic/Gadget Lite, 1–2 units
    #   → max ~162 * (1/2 products) * 2 units ≈ 82 units per March product < 142 ✓
    # Non-March rows: any product, 1–5 units (seed 42)

    N_RANDOM = 468
    random_rows = []
    for _ in range(N_RANDOM):
        d = random.choice(ALL_DATES)
        if d.month == 3:
            product = random.choice(MARCH_OTHER_PRODS)
            units = random.randint(1, 2)
        else:
            product = random.choice(PRODUCTS)
            units = random.randint(1, 5)
        region = random.choice(REGIONS)
        sp = random.choice(OTHER_SP)
        random_rows.append(mk_row(d, product, units, region, sp))

    TARGET_Q1     = 342150
    TARGET_OTHERS = TARGET_Q1 - sarah_total   # 274950
    rand_total    = sum(r["revenue"] for r in random_rows)
    remaining     = TARGET_OTHERS - rand_total

    # Per-salesperson random totals
    sp_rand = defaultdict(int)
    for r in random_rows:
        sp_rand[r["salesperson"]] += r["revenue"]

    print(f"Random {N_RANDOM} rows : ${rand_total:,}")
    print(f"Target others        : ${TARGET_OTHERS:,}")
    print(f"Remaining            : ${remaining:,}  (div25={remaining % 25 == 0})")
    print(f"\nPer-sp random totals:")
    for sp in OTHER_SP:
        print(f"  {sp}: ${sp_rand[sp]:,}")

    assert remaining >= 0, f"Random rows exceed target! remaining={remaining}"
    assert remaining % 25 == 0, f"remaining={remaining} not divisible by 25"

    # ── Distribute adjustment to minimize max-salesperson discrepancy ──────────
    # Greedily top up each salesperson to at most $67,199, then give rest to John.
    TARGET_MAX = 67199   # keep each other-sp just below Sarah if possible
    adj_all = []
    rem = remaining

    # Sort by random total descending — fill up the highest first, so adjustment
    # is spread rather than piled on one person
    for sp in sorted(OTHER_SP, key=lambda s: sp_rand[s], reverse=True):
        if rem <= 0:
            break
        room = TARGET_MAX - sp_rand[sp]
        if room <= 0:
            continue
        give = min(rem, (room // 25) * 25)
        if give > 0:
            rows = adj_rows_for(give, sp)
            adj_all.extend(rows)
            rem -= give
            print(f"  Give {sp}: ${give:,} ({len(rows)} rows)")

    # If still rem > 0 (spec inconsistency — others needed > 4*$67,199), give to John
    if rem > 0:
        print(f"  Spec overflow ${rem:,} -> John Smith (math inconsistency)")
        rows = adj_rows_for(rem, "John Smith")
        adj_all.extend(rows)
        rem = 0

    adj_total = sum(r["revenue"] for r in adj_all)
    assert adj_total == remaining, f"adj_total={adj_total} != remaining={remaining}"
    print(f"\nAdjustment: {len(adj_all)} rows, ${adj_total:,}")

    # ── Assemble final 500 rows ────────────────────────────────────────────────
    other_rows = random_rows + adj_all
    total_rows = len(sarah_rows) + len(other_rows)   # 24 + N_RANDOM + len(adj_all)

    if total_rows > 500:
        # Trim from the END of random_rows (which are already seeded, so order doesn't matter)
        excess = total_rows - 500
        trimmed_rev = 0
        for _ in range(excess):
            r = random_rows.pop()
            trimmed_rev += r["revenue"]
        # Recompute adjustment with new remaining
        new_remaining = remaining + trimmed_rev
        assert new_remaining % 25 == 0
        adj_all = []
        rem2 = new_remaining
        for sp in sorted(OTHER_SP, key=lambda s: sp_rand[s], reverse=True):
            if rem2 <= 0:
                break
            # recompute sp_rand after trim (conservative: just reuse original)
            room = TARGET_MAX - sp_rand[sp]
            if room <= 0:
                continue
            give = min(rem2, (room // 25) * 25)
            if give > 0:
                rows = adj_rows_for(give, sp)
                adj_all.extend(rows)
                rem2 -= give
        if rem2 > 0:
            adj_all.extend(adj_rows_for(rem2, "John Smith"))
        other_rows = random_rows + adj_all

    all_rows = sarah_rows + other_rows
    assert len(all_rows) == 500, f"Row count = {len(all_rows)}"
    random.shuffle(all_rows)

    # ── Final verification ─────────────────────────────────────────────────────
    total_rev = sum(r["revenue"] for r in all_rows)
    s_total   = sum(r["revenue"] for r in all_rows if r["salesperson"] == "Sarah Chen")
    wpx_m_u   = sum(r["units"] for r in all_rows
                    if r["product"] == "Widget Pro X" and r["date"].startswith("2025-03"))
    wpx_m_rev = sum(r["revenue"] for r in all_rows
                    if r["product"] == "Widget Pro X" and r["date"].startswith("2025-03"))

    sp_totals        = defaultdict(int)
    prod_march_units = defaultdict(int)
    for r in all_rows:
        sp_totals[r["salesperson"]] += r["revenue"]
        if r["date"].startswith("2025-03"):
            prod_march_units[r["product"]] += r["units"]

    print(f"\n=== FINAL VERIFICATION ===")
    print(f"Total rows        : {len(all_rows)} (target: 500)")
    print(f"Q1 total revenue  : ${total_rev:,} (target: $342,150)")
    print(f"Sarah Chen total  : ${s_total:,} (target: $67,200)")
    print(f"WPX March units   : {wpx_m_u} (target: 142)")
    print(f"WPX March revenue : ${wpx_m_rev:,} (target: $28,400)")
    print(f"\nSalesperson totals (ranked):")
    for sp, tot in sorted(sp_totals.items(), key=lambda x: -x[1]):
        flag = " <== TOP" if tot == max(sp_totals.values()) else ""
        print(f"  {sp}: ${tot:,}{flag}")
    print(f"\nMarch units by product (ranked):")
    for p, u in sorted(prod_march_units.items(), key=lambda x: -x[1]):
        flag = " <== BEST" if u == max(prod_march_units.values()) else ""
        print(f"  {p}: {u}{flag}")

    # Hard assertions
    assert total_rev == 342150,  f"Q1 total: {total_rev}"
    assert s_total   == 67200,   f"Sarah total: {s_total}"
    assert wpx_m_u   == 142,     f"WPX March units: {wpx_m_u}"
    assert wpx_m_rev == 28400,   f"WPX March rev: {wpx_m_rev}"
    assert len(all_rows) == 500, f"Row count: {len(all_rows)}"
    assert wpx_m_u == max(prod_march_units.values()), (
        f"WPX NOT best-selling in March! {dict(prod_march_units)}"
    )

    top_sp = max(sp_totals, key=lambda k: sp_totals[k])
    if top_sp != "Sarah Chen":
        print(
            f"\nNOTE: Sarah Chen (${s_total:,}) is NOT the top earner."
            f" Actual top: {top_sp} (${sp_totals[top_sp]:,})."
            " Spec inconsistency documented in phase1_complete.md."
        )
    else:
        print(f"\nSarah Chen IS the top salesperson [OK]")

    # ── Write CSV ──────────────────────────────────────────────────────────────
    out_path = Path(__file__).parent / "documents" / "sales_data_2025.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date", "product", "units", "unit_price", "revenue",
                        "region", "salesperson"],
        )
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nWritten to: {out_path}")


if __name__ == "__main__":
    main()
