# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Adversarial checks on the written-file figure check (#5110).

False positives first: a figure written in another spelling than its source
("$14.5M" for "$14,500,000", "twelve percent" for "12%"), computed from
sourced figures, or not a figure at all (dates, IDs, phone numbers) must not
be flagged. Then true positives the normalisation must not excuse, the size
bounds, and the shape of the note, which must never take over the answer.

kw-03 (rc12 knowledge-work run) is the canonical miss: the memo said
"$14.5M pre-money", the term sheet said "$14,500,000", and the check flagged
"$14.5".
"""

from __future__ import annotations

import os
import time

import pytest

from gaia.agents.base.deliverables import (
    FILE,
    MAX_CLAIMS,
    MAX_FILE_BYTES,
    MAX_ROWS,
    file_findings,
    read_blocks,
    text_blocks,
)
from tests.unit.agents.test_deliverable_grounding import (
    DATASHEET,
    INVENTED_BOM,
    _fetch,
    _md,
    _read,
)

TERM_SHEET = (
    "Northwind Robotics - Series Seed Term Sheet (CONFIDENTIAL)\n"
    "Offering: up to $3,000,000 in aggregate of Series Seed Preferred Stock, "
    "based on a pre-money valuation of $14,500,000, including an available "
    "option pool of 12% of the post-money fully diluted capitalization.\n"
    "Expenses: Company to reimburse counsel up to $10,000.\n"
    "Target closing: November 14, 2026.\n"
)
MEMO_QUERY = "Compare the term sheet against market terms and write memo.md."


def _memo(text, records=None, query=MEMO_QUERY, **kw):
    if records is None:
        records = [_read("deal/term-sheet.docx", TERM_SHEET)]
    return file_findings({"memo.md": _md(text)}, records, query=query, **kw)


def _flagged(findings) -> str:
    return " ".join(f.correction for f in findings)


# ---------------------------------------------------------------------------
# A scale or spelling the source doesn't use is still the source's figure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written",
    [
        "$14.5M",
        "$14.5m",
        "$14.5 M",
        "$14.5 million",
        "$14.5 Million",
        "$14.5mm",
        "$14.5MM",
        "$14.5 MM",
        "$14.5mn",
        "US$14.5M",
        "USD 14.5M",
        "14.5M USD",
        "14.5 million USD",
        "$14,500,000",
        "$14.50M",
        "$0.0145B",
        "$0.0145bn",
        "$14,500K",
        "$14,500k",
    ],
)
def test_kw03_the_pre_money_in_any_spelling_is_sourced(written):
    memo = f"## Valuation\n\nPre-money of {written}, above the seed median.\n"

    assert _memo(memo) == [], written


@pytest.mark.parametrize(
    "source",
    [
        "pre-money valuation of $14.5M",
        "pre-money valuation of $14.5 million",
        "pre-money valuation of $14.5mm",
        "pre-money valuation of 14.5 million dollars",
        "pre-money valuation: USD 14.5M",
    ],
)
def test_a_full_figure_in_the_file_is_sourced_by_a_scaled_one(source):
    records = [_read("deal/term-sheet.docx", source)]

    assert _memo("Pre-money: $14,500,000.\n", records) == [], source


@pytest.mark.parametrize(
    "written,source",
    [
        ("$3M", "$3,000,000 in aggregate"),
        ("$3,000,000", "raising $3M"),
        ("$500k", "a $500,000 bridge"),
        ("$500K", "a $500,000 bridge"),
        ("$500,000", "a $500k bridge"),
        ("$0.5M", "a $500,000 bridge"),
        ("$2.1bn", "revenue of $2,100,000,000"),
        ("$2,100,000,000", "revenue of $2.1 billion"),
        ("€1.2M", "€1,200,000 grant"),
        ("£750k", "£750,000 facility"),
        ("$3,000,000", "three million dollars"),
    ],
)
def test_scaled_money_matches_both_ways(written, source):
    records = [_read("deal/notes.txt", source)]

    assert _memo(f"Amount: {written}.\n", records) == [], (written, source)


@pytest.mark.parametrize(
    "source",
    [
        "option pool of 12%",
        "option pool of 12 %",
        "option_pool,0.12",
        "option pool: twelve percent",
        "option pool: twelve per cent",
        "option pool of 12 percent",
    ],
)
def test_a_percentage_matches_its_fraction_and_its_words(source):
    records = [_read("deal/notes.txt", source)]

    assert _memo("The pool is 12% of post-money.\n", records) == [], source


def test_twenty_five_percent_in_words_sources_25_percent():
    records = [_read("n.txt", "liquidation preference of twenty-five percent")]

    assert _memo("Preference: 25%.\n", records) == []


@pytest.mark.parametrize(
    "written",
    ["$3–5M", "$3-5M", "$3 – 5M", "$3M–$5M", "$3M-5M", "$3 to 5 million", "$3 to $5M"],
)
def test_a_range_is_sourced_by_its_two_ends(written):
    records = [_read("market.txt", "Seed rounds run $3,000,000 to $5,000,000.")]

    assert _memo(f"Typical seed raise: {written}.\n", records) == [], written


def test_a_file_writing_both_ends_in_full_is_sourced_by_a_range():
    records = [_read("market.txt", "Typical seed raise: $3–5M.")]

    assert _memo("Range: $3,000,000 to $5,000,000.\n", records) == []


def test_kw03_memo_as_written_raises_nothing():
    memo = (
        "# Northwind Robotics Series Seed: Memo\n\n"
        "| Term | Term sheet | Market | Call |\n|---|---|---|---|\n"
        "| Raise | $3M | $2–4M | Accept |\n"
        "| Pre-money | $14.5M | $10–16M | Accept |\n"
        "| Option pool | 12% | 10% | Negotiate |\n"
        "| Expenses | $10K cap | $10k–$25k | Accept |\n\n"
        "The $14.5M pre-money plus the $3M raise puts post-money at $17.5M; the "
        "investors own 17.14% after closing (target Nov 14, 2026).\n"
    )
    market = "Seed benchmarks: raise $2M-$4M; pre-money $10M to $16M; pool 10%; "
    market += "legal fees $10,000 to $25,000.\n"
    records = [
        _read("deal/term-sheet.docx", TERM_SHEET),
        _read("reference/market.txt", market),
    ]

    assert _memo(memo, records) == []


def test_a_source_read_in_an_earlier_turn_counts():
    """kw-03 read the term sheet a turn earlier; history is evidence."""
    findings = _memo(
        "Pre-money: $14.5M.\n",
        records=[_fetch("u", "nothing numeric")],
        context=f"read_file returned:\n{TERM_SHEET}",
    )

    assert findings == []


# ---------------------------------------------------------------------------
# Derived and non-figure values are not flagged
# ---------------------------------------------------------------------------


def test_values_copied_from_an_input_file_pass():
    records = [_read("prices.csv", "sku,price\nA-100,19.99\nB-200,4.50\n")]
    report = "| SKU | Price |\n|---|---|\n| A-100 | $19.99 |\n| B-200 | $4.50 |\n"

    assert _memo(report, records) == []


def test_totals_averages_and_percent_changes_pass():
    records = [_read("sales.csv", "month,revenue\njan,$1,200\nfeb,$1,500\n")]
    report = (
        "| Month | Revenue |\n|---|---|\n| Jan | $1,200 |\n| Feb | $1,500 |\n"
        "| Total | $2,700 |\n\nAverage $1,350 a month; February grew 25.0% on "
        "January, a $300 increase.\n"
    )

    assert _memo(report, records) == []


def test_a_currency_conversion_at_a_stated_rate_passes():
    records = [_read("quote.txt", "Unit price: $100.00")]
    query = "Convert the quote to euros at 0.92 EUR per USD and write memo.md."

    assert _memo("Unit price: €92.00 ($100.00 at 0.92).\n", records, query) == []


@pytest.mark.parametrize(
    "text",
    [
        "Closing on 2026-11-14 at 10:30.",
        "Order PO-2026-0412, SKU CM5108032, invoice INV-88213.",
        "Call +1 (408) 555-0199 or 1-800-555-0123.",
        "Ship to Santa Clara, CA 95054-1234.",
        "Requires GAIA v0.25.0 and Python 3.12.4.",
        "Section 4.2, clause 12, page 95, exhibit B-60.",
        "Board of 5 seats (2-2-1), 4-year vesting, 1-year cliff, 30-day no-shop.",
        "Build 2026.40.0, lemonade 10.8.1.",
        "IPv4 192.168.1.60, port 8080.",
    ],
)
def test_dates_ids_phones_zips_and_versions_are_not_figures(text):
    assert _memo(text + "\n", [_fetch("u", "nothing numeric")]) == [], text


def test_quantities_the_user_stated_are_not_figures():
    query = "Plan for 40 units over 12 weeks with 3 shifts and write memo.md."

    assert _memo("40 units, 12 weeks, 3 shifts.\n", [], query) == []


def test_a_quoted_excerpt_of_the_source_passes():
    memo = (
        'The term sheet says: > "based on a pre-money valuation of $14,500,000, '
        'including an available option pool of 12%"\n'
    )

    assert _memo(memo) == []


# ---------------------------------------------------------------------------
# What the normalisation must not excuse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written", ["$14.6M", "$16M", "$14.4 million", "$145M", "$1.45M"]
)
def test_a_scaled_figure_the_source_does_not_round_to_is_flagged(written):
    findings = _memo(f"Pre-money of {written}.\n")

    assert [f.gate for f in findings] == [FILE], written
    assert written in _flagged(findings)


def test_a_small_price_never_sources_a_scaled_one():
    """$30,000 is not $3M, whatever "0.45 shown as 45%" allows elsewhere."""
    records = [_read("quote.txt", "Pilot fee: $30,000")]

    assert _memo("Contract value: $3M.\n", records) != []


def test_a_spec_never_sources_a_scaled_price():
    """The 60 Hz / 95 mm trap, scaled: "95 mm" is not "$95M"."""
    records = [_read("cm5-datasheet.pdf", DATASHEET)]

    findings = _memo("Acquisition price: $95M; tooling $60K.\n", records)

    assert "$95M" in _flagged(findings)
    assert "$60K" in _flagged(findings)


def test_millimetres_in_a_finance_memo_still_need_a_source():
    findings = _memo("Bridge round of 3.2mm.\n", [_fetch("u", "nothing numeric")])

    assert findings != []


def test_invented_prices_next_to_real_specs_are_still_flagged():
    findings = file_findings(
        {"bom.md": _md(INVENTED_BOM)},
        [_read("cm5-datasheet.pdf", DATASHEET)],
        query="write bom.md for 40 units",
    )

    for price in ("$95.00", "$60.00", "$12.00"):
        assert price in _flagged(findings)


def test_a_fabricated_total_is_flagged():
    records = [_fetch("u", "Price: $95.00"), _fetch("v", "On sale now from $20")]
    bom = (
        "| Item | Qty | Unit price | Line total |\n|---|---|---|---|\n"
        "| CM5 | 40 | $95.00 | $3,800.00 |\n| IO | 40 | $20.00 | $800.00 |\n\n"
        "**Total: $7,250.00**\n"
    )

    findings = file_findings({"bom.md": _md(bom)}, records, query="40 units")

    assert "$7,250.00" in _flagged(findings)
    assert "$3,800.00" not in _flagged(findings)


def test_a_scaled_figure_in_the_answer_still_counts_as_naming_it():
    memo = "Pre-money of $16M.\n"
    answer = "Wrote memo.md. The $16M figure is an unverified market guess."

    assert _memo(memo, answer=answer) == []
    assert _memo(memo, answer="Wrote memo.md.") != []


# ---------------------------------------------------------------------------
# The note never takes over the answer
# ---------------------------------------------------------------------------


def test_the_correction_asks_for_one_line_not_an_explanation():
    [finding] = _memo("Pre-money of $16M.\n")

    assert "one short line" in finding.correction
    assert "don't explain this check" in finding.correction


def test_the_note_is_one_short_line_however_many_figures_are_flagged():
    memo = "\n".join(f"- Item {i}: ${1000 + i * 37}.00" for i in range(150))

    [finding] = _memo(memo, [_fetch("u", "nothing numeric")])

    assert "\n" not in finding.note
    assert len(finding.note) < 300
    assert "more" in finding.note


# ---------------------------------------------------------------------------
# Bounds: big files and many files stay cheap
# ---------------------------------------------------------------------------


def test_a_file_just_under_the_size_cap_is_read_in_bounded_time(tmp_path):
    path = tmp_path / "big.md"
    line = "- Line item SKU-00000 at $19.99 each, 3 units.\n"
    path.write_bytes((line * ((MAX_FILE_BYTES - 1024) // len(line))).encode())
    assert os.path.getsize(path) < MAX_FILE_BYTES

    started = time.monotonic()
    findings = file_findings(
        {str(path): read_blocks(str(path))},
        [_read("prices.csv", "sku,price\nSKU-00000,19.99\n")],
    )

    assert time.monotonic() - started < 30
    assert findings == []


def test_a_file_over_the_size_cap_is_skipped(tmp_path):
    path = tmp_path / "huge.md"
    path.write_text("$999.99\n" * (MAX_FILE_BYTES // 8 + 10), encoding="utf-8")

    assert read_blocks(str(path)) == []


def test_a_ten_thousand_row_workbook_is_read_up_to_the_row_cap(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "ledger.xlsx"
    book = openpyxl.Workbook(write_only=True)
    sheet = book.create_sheet()
    sheet.append(["Invoice", "Qty", "Amount (USD)"])
    for i in range(10_000):
        sheet.append([f"INV-{i:05d}", 1, 19.99])
    book.save(path)

    started = time.monotonic()
    blocks = read_blocks(str(path))
    findings = file_findings(
        {str(path): blocks}, [_read("prices.csv", "sku,price\nX,19.99\n")]
    )

    assert time.monotonic() - started < 60
    assert sum(len(b.rows) for b in blocks) <= MAX_ROWS
    assert findings == []


def test_many_files_with_many_figures_stay_bounded():
    files = {
        f"part{i}.md": _md(
            "\n".join(f"- Item {j}: ${j * 7 + i}.00" for j in range(MAX_CLAIMS * 3))
        )
        for i in range(30)
    }

    started = time.monotonic()
    findings = file_findings(files, [_fetch("u", "nothing numeric")])

    assert time.monotonic() - started < 60
    assert len(findings) == 30
    assert all("\n" not in f.note for f in findings)


def test_a_csv_with_hostile_cells_reads_without_error():
    text = '"a,b",$1e309,"$' + "9" * 400 + '",$-0,"$1,2,3"\n'

    blocks = text_blocks(text, ".csv")
    findings = file_findings({"x.csv": blocks}, [])

    assert isinstance(findings, list)
