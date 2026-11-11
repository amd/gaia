# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the deliverable-figure check (``gaia.agents.base.deliverables``).

The flagged shape is a BOM deliverable whose unit
prices ($95, $60, $12) no tool result held, written through ``run_python``.
The quiet shapes are ordinary deliverables: values copied from an input, a
fetched price, totals and per-unit splits computed from them, dates, IDs, and
quantities the user stated.
"""

from __future__ import annotations

import ast
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.deliverables import (
    FILE,
    _script_opens,
    blocks_text,
    file_findings,
    input_paths,
    read_blocks,
    text_blocks,
)
from gaia.agents.base.grounding import content_findings, evidence_text
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import (
    VERIFICATION_NOTE_OPENER,
    observed_text,
    strip_verification_scope,
    verification_record,
)

BOM_QUERY = (
    "We're buying 40 digital-signage controllers that need dual 4K60 HDMI, NVMe "
    "boot and at least 8 GB RAM. Using the datasheets and current prices online, "
    "write bom.xlsx with a 40-unit BOM and a total."
)

#: What the tools showed for the BOM: specs everywhere, two real prices.
DATASHEET = (
    "Compute Module 5\nQuad-core Cortex-A76 at 2.4 GHz\nUp to 16 GB LPDDR4X\n"
    "Dual 4Kp60 HDMI\nPCIe Gen 2 x1\nOperating temperature 0-85 C\n"
    "Dimensions 55 mm x 40 mm\nInput 5 V, 12 V via IO board\n95 mm mounting pitch\n"
    "Power it from the official 27W USB-C supply.\n"
)
CM5_PAGE = (
    "Page: Raspberry Pi Compute Module 5\nCM5108032  8GB RAM, 32GB eMMC, wireless\n"
    "Price: $95.00 ... ships in 3 days\n"
)
CM5_PAGE_NO_PRICE = (
    "Page: Buy a Raspberry Pi Compute Module 5\nThe new standard for embedded "
    "computing. Available from 2 GB to 16 GB RAM, 60 Hz refresh, 12 months.\n"
)
IO_PAGE = "Compute Module 5 IO Board\nOn sale now from $20\n"


def _rec(tool_name, args, result, errored=False):
    record = verification_record(tool_name, args, result, errored=errored)
    record["args"] = args
    record["output"] = ""
    record["observed"] = observed_text(result)
    return record


def _fetch(url, text):
    return _rec("fetch_page", {"url": url}, {"status": "success", "content": text})


def _read(path, content):
    return _rec(
        "read_file", {"file_path": path}, {"status": "success", "content": content}
    )


def _search(query, text):
    return _rec(
        "search_web",
        {"query": query},
        {"summary": f'Web search results for: "{query}"\n\n{text}'},
    )


def _run_python(code, stdout=""):
    return _rec(
        "run_python",
        {"code": code},
        {"status": "success", "stdout": stdout, "stderr": "", "return_code": 0},
    )


def _md(text):
    return text_blocks(text, ".md")


def _check(blocks, records, query=BOM_QUERY, name="bom.md", **kw):
    return file_findings({name: blocks}, records, query=query, **kw)


INVENTED_BOM = (
    "| Item | Qty | Unit price | Line total |\n|---|---|---|---|\n"
    "| CM5108032 | 40 | $95.00 | $3,800.00 |\n"
    "| CM5 IO Board | 40 | $60.00 | $2,400.00 |\n"
    "| 27W PSU | 40 | $12.00 | $480.00 |\n\n**Total: $6,680.00**\n"
)


# ---------------------------------------------------------------------------
# Invented BOM prices are flagged
# ---------------------------------------------------------------------------


def test_invented_prices_are_flagged_even_when_the_digits_appear_as_specs():
    # 95 mm, 60 Hz and 12 V are in the evidence; none of them is a price.
    records = [_read("cm5-datasheet.pdf", DATASHEET), _fetch("u", CM5_PAGE_NO_PRICE)]

    findings = _check(_md(INVENTED_BOM), records)

    assert [f.gate for f in findings] == [FILE]
    for price in ("$95.00", "$60.00", "$12.00"):
        assert price in findings[0].correction
    assert "`bom.md`" in findings[0].correction
    assert findings[0].note.startswith(
        "`bom.md` has figures I found no source for this turn"
    )


def test_totals_computed_from_an_invented_price_are_not_listed_beside_it():
    records = [_fetch("u", CM5_PAGE), _fetch("v", IO_PAGE)]
    bom = INVENTED_BOM.replace("$60.00", "$20.00").replace("$2,400.00", "$800.00")
    bom = bom.replace("$6,680.00", "$5,080.00")

    findings = _check(_md(bom), records)

    assert len(findings) == 1
    correction = findings[0].correction
    assert "$12.00" in correction
    assert "$480.00" not in correction  # it is 40 x the flagged price
    assert "$5,080.00" not in correction  # the column total


def test_xlsx_written_by_a_script_with_hard_coded_prices_is_flagged(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "bom.xlsx"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Item", "Qty", "Unit Price (USD)", "Line Total (USD)"])
    for item, price in (("CM5108032", 95), ("CM5 IO Board", 60), ("27W PSU", 12)):
        sheet.append([item, 40, price, price * 40])
    sheet.append(["Total", None, None, "=SUM(D2:D4)"])
    book.save(path)
    code = (
        "import openpyxl\nwb = openpyxl.Workbook()\nws = wb.active\n"
        "rows = [('CM5108032', 40, 95), ('CM5 IO Board', 40, 60), ('27W PSU', 40, 12)]\n"
        f"wb.save(r'{path}')\nprint(rows)\n"
    )
    records = [
        _read("cm5-datasheet.pdf", DATASHEET),
        # It echoes its own literals; that is not a source.
        _run_python(code, "[('CM5108032', 40, 95), ('CM5 IO Board', 40, 60), ...]"),
        # Neither is a search whose query names the hoped-for price.
        _search("Raspberry Pi 27W USB-C Power Supply $12 official price", "No results"),
    ]

    findings = file_findings(
        {str(path): read_blocks(str(path))}, records, query=BOM_QUERY
    )

    assert [f.gate for f in findings] == [FILE]
    for price in ("95 (Unit Price (USD))", "60 (Unit Price (USD))", "12 (Unit"):
        assert price in findings[0].correction
    assert "4800" not in findings[0].correction


def test_reading_back_the_written_file_is_not_a_source():
    records = [
        _read("cm5-datasheet.pdf", DATASHEET),
        _rec("write_file", {"file_path": "bom.md", "content": INVENTED_BOM}, {"ok": 1}),
        _read("bom.md", INVENTED_BOM),
    ]

    assert _check(_md(INVENTED_BOM), records)


def test_a_file_read_before_this_turn_overwrote_it_is_still_a_source():
    old = "Item,Unit price\nCM5,95.00\nIO,60.00\nPSU,12.00\n"
    records = [
        _read("bom.csv", old),
        _rec("write_file", {"file_path": "bom.csv", "content": old}, {"ok": 1}),
    ]
    blocks = text_blocks(old + "Total,167.00\n", ".csv")

    assert _check(blocks, records, name="bom.csv") == []


# ---------------------------------------------------------------------------
# Ordinary deliverables stay quiet
# ---------------------------------------------------------------------------


def test_prices_fetched_this_turn_and_totals_from_them_pass():
    records = [_fetch("u", CM5_PAGE), _fetch("v", IO_PAGE)]
    bom = (
        "# Signage BOM (2026-10-14)\n\nRecommended: CM5108032 on the CM5 IO Board, "
        "8 GB RAM, dual 4K60.\n\n"
        "| Item | Qty | Unit price | Line total |\n|---|---|---|---|\n"
        "| CM5108032 | 40 | $95.00 | $3,800.00 |\n"
        "| CM5 IO Board | 40 | $20.00 | $800.00 |\n"
        "| **Subtotal** | | | **$4,600.00** |\n"
        "| Tax 8% | | | $368.00 |\n| **Total** | | | **$4,968.00** |\n\n"
        "Per controller: $124.20. The IO board is 17.39% of the hardware cost.\n"
    )

    assert _check(_md(bom), records) == []


def test_values_copied_from_an_input_file_pass(tmp_path):
    prices = "part,unit_price,lead_weeks\nCM5108032,95.00,6\nCM5IO,20.00,2\n"
    records = [_read(str(tmp_path / "quote.csv"), prices)]
    report = (
        "part,qty,unit_price,line_total\nCM5108032,40,95.00,3800.00\n"
        "CM5IO,40,20.00,800.00\nTOTAL,,,4600.00\n"
    )

    assert _check(text_blocks(report, ".csv"), records, name="out.csv") == []


def test_an_input_the_script_read_counts_even_if_nothing_printed_it(tmp_path):
    source = tmp_path / "quote.csv"
    source.write_text("part,price\nCM5108032,95.00\nCM5IO,20.00\n", encoding="utf-8")
    code = (
        f"import pandas as pd\ndf = pd.read_csv(r'{source}')\ndf.to_excel('out.xlsx')"
    )
    records = [_run_python(code)]

    assert input_paths(records) == [str(source), "out.xlsx"]
    inputs = [blocks_text(read_blocks(str(source)))]
    blocks = text_blocks("part,price\nCM5108032,$95.00\nCM5IO,$20.00\n", ".csv")

    assert _check(blocks, records, name="out.csv", inputs=inputs) == []
    assert _check(blocks, records, name="out.csv") != []


ORDERS_CSV = (
    "order_id,region,product,units,unit_price,returned\n"
    + "".join(f"{i},North,Widget,{i % 9 + 1},{i % 7 + 3}.50,no\n" for i in range(1, 60))
    + "251,East,Doohickey,39,35.25,no\n389,West,Widget,39,35.25,no\n"
    "492,South,Doohickey,40,35.25,no\n"
)
TOP_ORDERS_QUERY = (
    "Create report.md with a markdown table of the 3 largest orders in orders.csv "
    "(order_id, region, product, revenue), then read it back to confirm."
)
TOP_ORDERS_CODE = (
    "import csv\nfrom decimal import Decimal\n"
    "rows = list(csv.DictReader(open('orders.csv')))\n"
    "for r in rows: r['rev'] = Decimal(r['units']) * Decimal(r['unit_price'])\n"
    "rows.sort(key=lambda r: r['rev'], reverse=True)\n"
    "for r in rows[:3]: print(r['order_id'], r['region'], r['product'], r['rev'])\n"
)


def _top_orders_turn(stdout, table):
    return [
        _read("orders.csv", ORDERS_CSV),
        _run_python(TOP_ORDERS_CODE, stdout),
        _rec("write_file", {"file_path": "report.md", "content": table}, {"ok": 1}),
        _read("report.md", table),
    ]


def _top_orders_table(*rows):
    return "| order_id | region | product | revenue |\n| --- | --- | --- | --- |\n" + (
        "".join(f"| {r} |\n" for r in rows)
    )


@pytest.mark.parametrize(
    "stdout",
    [
        "492 South Doohickey 1410.00\n251 East Doohickey 1374.75\n"
        "389 West Widget 1374.75\n",
        "492 South Doohickey 1410.0\n251 East Doohickey 1374.75\n"
        "389 West Widget 1374.75\n",
        "[('492', Decimal('1410.00')), ('251', Decimal('1374.75'))]\n",
    ],
)
def test_revenue_a_script_computed_this_turn_is_sourced(stdout):
    table = _top_orders_table(
        "492 | South | Doohickey | 1410.00",
        "251 | East | Doohickey | 1374.75",
        "389 | West | Widget | 1374.75",
    )

    findings = _check(
        _md(table), _top_orders_turn(stdout, table), TOP_ORDERS_QUERY, "report.md"
    )

    assert findings == []


def test_revenue_no_tool_produced_is_still_flagged():
    stdout = "492 South Doohickey 1410.00\n251 East Doohickey 1374.75\n"
    table = _top_orders_table(
        "492 | South | Doohickey | 1410.00",
        "251 | East | Doohickey | 1374.75",
        "389 | West | Widget | 1299.40",
    )

    findings = _check(
        _md(table), _top_orders_turn(stdout, table), TOP_ORDERS_QUERY, "report.md"
    )

    assert [f.gate for f in findings] == [FILE]
    assert "1299.40 (revenue)" in findings[0].note
    assert "1410.00" not in findings[0].note
    assert "1374.75" not in findings[0].note


def test_a_unit_a_script_printed_is_still_not_a_price():
    records = [_run_python("print(open('cm5.txt').read())", "Dual 4Kp60 HDMI 60 Hz\n")]
    table = "| Item | Unit price |\n|---|---|\n| CM5 | 60 |\n"

    assert _check(_md(table), records)


ECHOED_SHEET = "CM5 datasheet\nRefresh rate up to 60\nPSU rated 27 watts peak\n"
ECHOED_TABLE = "| Item | Unit price |\n|---|---|\n| CM5 | 60 |\n| PSU | 27 |\n"


@pytest.mark.parametrize(
    "record",
    [
        _run_python("print(open('cm5-datasheet.txt').read())", ECHOED_SHEET),
        _rec(
            "run_shell_command",
            {"command": "cat cm5.txt"},
            {"status": "success", "stdout": ECHOED_SHEET},
        ),
        _rec(
            "query_data",
            {"query": "SELECT * FROM specs"},
            {"status": "success", "result": ECHOED_SHEET},
        ),
    ],
    ids=["python-print", "shell-cat", "select-star"],
)
def test_a_file_a_tool_echoed_is_not_a_price(record):
    assert _check(_md(ECHOED_TABLE), [record])


@pytest.mark.parametrize(
    "code",
    [
        "for i, l in enumerate(open('cm5.txt')): print(i+1, l)",
        "s = open('cm5.txt').read(); print(s); print(len(s)-1)",
        "print(open('cm5.txt').read())  # re-print sheet",
        "p = 'cm5-sheet.txt'\nprint(f'{open(p).read()}')",
    ],
    ids=["line-counter", "len-minus-one", "hyphen-in-comment", "hyphen-in-name"],
)
def test_a_script_that_opens_and_echoes_a_file_is_not_a_price(code):
    assert _check(_md(ECHOED_TABLE), [_run_python(code, ECHOED_SHEET)])


def _bom_script(code, stdout):
    # The script opens a file the turn read, so only its typed figures fail it.
    code = "sheet = open('cm5-datasheet.pdf').read()\n" + code
    return _check(
        _md(INVENTED_BOM),
        [_read("cm5-datasheet.pdf", DATASHEET), _run_python(code, stdout)],
    )


@pytest.mark.parametrize(
    "code,stdout",
    [
        (
            "prices = {'CM5108032': 95, 'CM5 IO Board': 60, '27W PSU': 12}\n"
            "for k, v in prices.items(): print(k, v, v*40)\n"
            "print(sum(v*40 for v in prices.values()))",
            "CM5108032 95 3800\nCM5 IO Board 60 2400\n27W PSU 12 480\n6680\n",
        ),
        ("print(95*40, 60*40, 12*40, 95*40 + 60*40 + 12*40)", "3800 2400 480 6680\n"),
        ("print(3900 - 100, 2500 - 100, 580 - 100)", "3800 2400 480\n"),
        (
            "from decimal import Decimal\n"
            "for p in ('95.00', '60.00', '12.00'): print(Decimal(p) * len('x' * 40))",
            "3800.00\n2400.00\n480.00\n",
        ),
        ("print(eval('95*40'), eval('60*40'), eval('12*40'))", "3800 2400 480\n"),
    ],
    ids=["line-items", "inline-math", "subtraction", "price-as-string", "eval"],
)
def test_prices_the_model_typed_into_a_script_are_still_flagged(code, stdout):
    findings = _bom_script(code, stdout)

    assert [f.gate for f in findings] == [FILE]
    assert "$95.00" in findings[0].correction


@pytest.mark.parametrize(
    "code",
    [
        "import glob\nfor f in glob.glob('*.txt'): print(open(f).read(), 2*3)",
        "import requests\nprint(requests.get('https://x.test/cm5').text, 2*3)",
        "import os\nprint(open(os.path.join('d', 'cm5.txt')).read(), 2*3)",
        "print(open('cm5.pdf', 'rb').read(), 2*3)",
        "for i, l in enumerate(open('cm5.txt')): print(i*1, l)",
        "s = open('cm5.txt').read(); n = len(s); print(s[:n-5], 2+2)",
    ],
    ids=["glob", "requests", "path-join", "unread-pdf", "i-times-1", "slice"],
)
def test_a_script_reading_data_the_turn_never_saw_sources_nothing(code):
    assert _check(_md(ECHOED_TABLE), [_run_python(code, ECHOED_SHEET)])


def test_a_script_echoing_a_file_the_turn_read_does_not_source_its_numbers():
    code = "for i, l in enumerate(open('cm5.txt')): print(i+1, l, 2*3)"
    records = [_read("cm5.txt", ECHOED_SHEET), _run_python(code, ECHOED_SHEET)]

    assert _check(_md(ECHOED_TABLE), records)


@pytest.mark.parametrize(
    "code",
    [
        "import csv\nfor r in csv.DictReader(open('orders.csv')):\n"
        "    print(f\"{r['region']} {float(r['units'])*float(r['unit_price']):,.2f}\")",
        "import pandas as pd\ndf = pd.read_csv('orders.csv')\n"
        "df['rev'] = df.units * df.unit_price\nprint(df.nlargest(3, 'rev'))",
    ],
    ids=["csv-fstring", "pandas"],
)
def test_a_script_computing_from_a_file_the_turn_read_sources_its_result(code):
    table = "| Region | Revenue |\n|---|---|\n| South | 1410.00 |\n"
    records = [_read("orders.csv", ORDERS_CSV), _run_python(code, "South 1,410.00\n")]

    assert _check(_md(table), records, name="r.md") == []


GM15_TABLE = (
    "| Order ID | Region | Product | Revenue |\n|---|---|---|---|\n"
    "| 492 | South | Doohickey | 1410.00 |\n| 251 | East | Doohickey | 1374.75 |\n"
    "| 389 | West | Widget | 1374.75 |\n"
)
GM15_CODE = (
    "import csv\n"
    'csv_path = r"C:\\gtest\\fx\\orders.csv"\n'
    'report_path = r"C:\\gtest\\fx\\work\\report_gm15.md"\n'
    "with open(csv_path, newline='') as f:\n"
    "    rows = list(csv.DictReader(f))\n"
    "for r in rows:\n"
    "    r['revenue'] = int(r['units']) * float(r['unit_price'])\n"
    "top = sorted(rows, key=lambda r: r['revenue'], reverse=True)[:3]\n"
    "with open(report_path, 'w') as out:\n"
    "    out.write('| Order ID | Region | Product | Revenue |\\n|---|---|---|---|\\n')\n"
    "    for r in top:\n"
    "        out.write(f\"| {r['order_id']} | {r['region']} | {r['product']} "
    "| {r['revenue']:.2f} |\\n\")\n"
)


def _gm15_turn(code=GM15_CODE, table=GM15_TABLE):
    report = r"C:\gtest\fx\work\report_gm15.md"
    failed = _run_python("import pandas as pd", "")
    failed["failed"] = True
    return report, [
        _rec(
            "analyze_data_file",
            {"file_path": r"C:\gtest\fx\orders.csv"},
            {"status": "success", "summary": "500 rows; columns order_id, units"},
        ),
        failed,
        _run_python(code, ""),
        _read(report, table),
    ]


def test_figures_a_script_computed_into_the_report_itself_are_sourced():
    report, records = _gm15_turn()

    findings = file_findings({report: _md(GM15_TABLE)}, records, query=TOP_ORDERS_QUERY)

    assert findings == []


def test_a_year_in_a_heading_the_script_writes_is_not_a_figure():
    code = GM15_CODE.replace(
        "with open(report_path, 'w') as out:\n",
        "with open(report_path, 'w') as out:\n"
        "    out.write('# Top orders 2026\\n\\n')\n",
    )
    report, records = _gm15_turn(code)

    findings = file_findings({report: _md(GM15_TABLE)}, records, query=TOP_ORDERS_QUERY)

    assert findings == []


def test_prices_typed_into_a_script_that_writes_the_report_are_still_flagged():
    code = GM15_CODE.replace(
        "    rows = list(csv.DictReader(f))\n",
        "    rows = list(csv.DictReader(f))\n"
        "rows = [{'order_id': '492', 'region': 'South', 'product': 'Doohickey',"
        " 'units': '1', 'unit_price': '1410.00'}]\n",
    )
    report, records = _gm15_turn(code)

    findings = file_findings({report: _md(GM15_TABLE)}, records, query=TOP_ORDERS_QUERY)

    assert [f.gate for f in findings] == [FILE]
    assert "1410.00 (Revenue)" in findings[0].note


@pytest.mark.parametrize(
    "report_open",
    [
        "rep = open('bom.md').read()",
        "open('bom.md', 'a').write('checked')",
        "rep = open('bom.md').read()\n"
        "open('bom.md', 'w').write(rep.replace('Total', 'Total (' + str(2) + ')'))",
        "from pathlib import Path\n"
        "Path('bom.md').write_text(Path('bom.md').read_text().replace('TBD', '1'))",
        "import pandas as pd\n"
        "df = pd.read_csv('bom.md')\ndf.loc[0, 'Qty'] = 4\ndf.to_csv('bom.md')",
        "import io\nrep = io.open('bom.md').read()\nopen('bom.md', 'w').write(rep)",
        "import codecs\nrep = codecs.open('bom.md', encoding='utf-8').read()\n"
        "open('bom.md', 'w').write(rep)",
        "from pathlib import Path\np = Path('bom.md')\nrep = p.read_text()\n"
        "open('bom.md', 'w').write(rep)",
    ],
    ids=[
        "read-back",
        "append",
        "patch-open",
        "patch-path",
        "patch-pandas",
        "io-open",
        "codecs-open",
        "path-variable",
    ],
)
def test_a_script_that_did_not_write_the_report_does_not_source_it(report_open):
    records = [
        _read("cm5-datasheet.pdf", DATASHEET),
        _rec("write_file", {"file_path": "bom.md", "content": INVENTED_BOM}, {"ok": 1}),
        _run_python(
            f"sheet = open('cm5-datasheet.pdf').read()\n{report_open}\n"
            "print('Compute' in sheet)",
            "True\n",
        ),
    ]

    findings = _check(_md(INVENTED_BOM), records)

    assert [f.gate for f in findings] == [FILE]
    assert "$95.00" in findings[0].correction


@pytest.mark.parametrize(
    "write",
    [
        "Path(report_path).write_text(table)",
        "pd.DataFrame(top).to_markdown(report_path)",
        "open(report_path, mode='x').write(table)",
    ],
    ids=["write-text", "to-markdown", "exclusive-create"],
)
def test_a_report_the_script_wrote_another_way_is_its_output(write):
    code = (
        "import csv\nfrom pathlib import Path\nimport pandas as pd\n"
        'csv_path = r"C:\\gtest\\fx\\orders.csv"\n'
        'report_path = r"C:\\gtest\\fx\\work\\report_gm15.md"\n'
        "rows = list(csv.DictReader(open(csv_path)))\n"
        "top = [float(r['units']) * float(r['unit_price']) for r in rows]\n"
        f"table = str(top)\n{write}\n"
    )
    report, records = _gm15_turn(code)

    assert (
        file_findings({report: _md(GM15_TABLE)}, records, query=TOP_ORDERS_QUERY) == []
    )


@pytest.mark.parametrize("tool", ["write_file", "run_python"])
def test_a_report_rewritten_after_the_computing_script_is_checked_again(tool):
    report, records = _gm15_turn()
    args = (
        {"file_path": report, "content": GM15_TABLE}
        if tool == "write_file"
        else {"code": f"open(r'{report}', 'w').write('| 492 | 1410.00 |')"}
    )
    records.insert(3, _rec(tool, args, {"status": "success"}))

    assert file_findings({report: _md(GM15_TABLE)}, records, query=TOP_ORDERS_QUERY)


@pytest.mark.parametrize(
    "later",
    [
        "print(open(r'{report}').read())",
        "from pathlib import Path\nprint(Path(r'{report}').read_text())",
        "open(r'C:\\gtest\\fx\\work\\final_report_gm15.md', 'w').write('x')",
    ],
    ids=["read-back", "path-read-back", "other-file"],
)
def test_a_later_script_that_leaves_the_report_alone_keeps_its_credit(later):
    report, records = _gm15_turn()
    records.insert(3, _run_python(later.format(report=report), GM15_TABLE))

    assert (
        file_findings({report: _md(GM15_TABLE)}, records, query=TOP_ORDERS_QUERY) == []
    )


def test_a_later_script_that_reads_and_rewrites_the_report_is_checked_again():
    report, records = _gm15_turn()
    code = f"rep = open(r'{report}').read()\nopen(r'{report}', 'w').write(rep)"
    records.insert(3, _run_python(code))

    assert file_findings({report: _md(GM15_TABLE)}, records, query=TOP_ORDERS_QUERY)


def test_a_script_writing_the_report_from_unread_data_sources_nothing():
    code = GM15_CODE.replace(
        'csv_path = r"C:\\gtest\\fx\\orders.csv"',
        'csv_path = r"C:\\gtest\\fx\\other.csv"',
    )
    report, records = _gm15_turn(code)

    assert file_findings({report: _md(GM15_TABLE)}, records, query=TOP_ORDERS_QUERY)


def test_a_file_only_the_script_opened_counts_once_the_check_has_read_it():
    code = "import pandas as pd\nprint(pd.read_csv('orders.csv').units.mul(2).sum())"
    table = "| Region | Revenue |\n|---|---|\n| South | 1410.00 |\n"
    records = [_run_python(code, "1410.00\n")]

    assert _check(_md(table), records, name="r.md") != []
    assert (
        _check(
            _md(table),
            records,
            name="r.md",
            inputs=[ORDERS_CSV],
            input_names=["C:/data/orders.csv"],
        )
        == []
    )


def test_a_computing_script_that_also_echoes_a_read_file_does_not_source_it():
    code = "print(open('cm5.txt').read())\nprint(4 * len(rows))"
    records = [_read("cm5.txt", ECHOED_SHEET), _run_python(code, ECHOED_SHEET)]

    assert _check(_md(ECHOED_TABLE), records)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT region, SUM(units*unit_price) FROM orders GROUP BY 1",
        "SELECT * FROM orders WHERE units*unit_price > 1000",
    ],
    ids=["aggregate", "arithmetic-in-where"],
)
def test_a_scratchpad_query_is_not_a_source_for_an_amount(query):
    # The scratchpad holds rows the model inserted, not data it read.
    records = [
        _rec(
            "query_data",
            {"query": query},
            {"status": "success", "result": "East 41572.25\nNorth 45043.62\n"},
        )
    ]
    table = (
        "| Region | Revenue |\n|---|---|\n| East | 41572.25 |\n| North | 45043.62 |\n"
    )

    assert _check(_md(table), records, name="regions.md")


@pytest.mark.parametrize(
    "code,stdout",
    [
        (
            "data = 'CM5108032,95,40;CM5 IO Board,60,40;27W PSU,12,40'\n"
            "t = 0\n"
            "for row in data.split(';'):\n"
            "    name, price, qty = row.split(',')\n"
            "    line = int(price) * int(qty)\n"
            "    t += line\n"
            "    print(name, price, line)\n"
            "print(t)",
            "CM5108032 95 3800\nCM5 IO Board 60 2400\n27W PSU 12 480\n6680\n",
        ),
        (
            "import json\n"
            'bom = json.loads(\'{"CM5108032": [95, 40], "CM5 IO Board": [60, 40],'
            ' "27W PSU": [12, 40]}\')\n'
            "for k, (p, q) in bom.items(): print(k, p * q)\n"
            "print(sum(p * q for p, q in bom.values()))",
            "CM5108032 3800\nCM5 IO Board 2400\n27W PSU 480\n6680\n",
        ),
        (
            "from decimal import Decimal\n"
            "for item in ('CM5 $95.00 x40', 'IO $60.00 x40', 'PSU $12.00 x40'):\n"
            "    name, p, q = item.split()\n"
            "    print(name, Decimal(p[1:]) * int(q[1:]))",
            "CM5 3800.00\nIO 2400.00\nPSU 480.00\n",
        ),
        (
            "prices = [9*10+5, 6*10, 1*10+2]\nqty = 4*10\n"
            "for p in prices: print(p * qty)\nprint(sum(p * qty for p in prices))",
            "3800\n2400\n480\n6680\n",
        ),
        (
            "prices = [int('9'+'5'), int('6'+'0'), int('1'+'2')]\nqty = int('4'+'0')\n"
            "for p in prices: print(p * qty)\nprint(sum(p * qty for p in prices))",
            "3800\n2400\n480\n6680\n",
        ),
        (
            "prices = [ord('_'), ord('<'), ord('\x0c')]\nqty = ord('(')\n"
            "for p in prices: print(p * qty)\nprint(sum(p * qty for p in prices))",
            "3800\n2400\n480\n6680\n",
        ),
    ],
    ids=[
        "string-split",
        "json-loads",
        "dollar-string",
        "literal-arithmetic",
        "string-concat",
        "ord",
    ],
)
def test_prices_hidden_in_a_scripts_literals_are_still_flagged(code, stdout):
    findings = _bom_script(code, stdout)

    assert [f.gate for f in findings] == [FILE]
    assert "$95.00" in findings[0].correction


REGION_TABLE = (
    "| Region | Revenue | Share |\n|---|---|---|\n"
    "| North | 1838.00 | 30.6% |\n| South | 1410.00 | 23.5% |\n"
    "| East | 1374.75 | 22.9% |\n| West | 1374.75 | 22.9% |\n"
)
REGION_STDOUT = (
    "North 1838.00 30.6\nSouth 1410.00 23.5\nEast 1374.75 22.9\nWest 1374.75 22.9\n"
)


@pytest.mark.parametrize(
    "code",
    [
        "import csv\nfrom collections import defaultdict\n"
        "with open('orders.csv', newline='') as f:\n"
        "    rows = list(csv.DictReader(f))\n"
        "rev = defaultdict(float)\n"
        "for r in rows: rev[r['region']] += int(r['units']) * float(r['unit_price'])\n"
        "total = sum(rev.values())\n"
        "for k, v in sorted(rev.items(), key=lambda kv: -kv[1]):\n"
        "    print(k, f'{round(v, 2):.2f}', round(v / total * 100, 1))",
        "import pandas as pd\ndf = pd.read_csv('orders.csv')\n"
        "s = (df.units * df.unit_price).groupby(df.region).sum()\n"
        "for k, v in s.sort_values(ascending=False).items():\n"
        "    print(k, f'{v:.2f}', round(100 * v / s.sum(), 1))",
        "import csv, io\nrows = list(csv.DictReader(io.open('orders.csv')))\n"
        "rev = {}\n"
        "for r in rows:\n"
        "    rev[r['region']] = rev.get(r['region'], 0) + "
        "int(r['units']) * float(r['unit_price'])\n"
        "total = sum(rev.values())\n"
        "for k, v in rev.items(): print(k, f'{v:.2f}', f'{v * 100 / total:.1f}')",
        "import codecs, csv\n"
        "rows = list(csv.DictReader(codecs.open('orders.csv', encoding='utf-8')))\n"
        "rev = {}\n"
        "for r in rows:\n"
        "    rev[r['region']] = rev.get(r['region'], 0) + "
        "int(r['units']) * float(r['unit_price'])\n"
        "for k, v in rev.items(): print(k, f'{v:.2f}')",
        "import csv, io\nfrom pathlib import Path\n"
        "rows = list(csv.DictReader(io.StringIO(Path('orders.csv').read_text())))\n"
        "rev = {}\n"
        "for r in rows:\n"
        "    rev[r['region']] = rev.get(r['region'], 0) + "
        "int(r['units']) * float(r['unit_price'])\n"
        "for k, v in rev.items(): print(k, f'{v:.2f}')",
        "import pandas as pd\ndf = pd.read_csv('orders.csv')\n"
        "df = df[df.order_id >= 1]\ndf = df[df.region.str.len() > 0]\n"
        "s = (df.units * df.unit_price).groupby(df.region).sum()\n"
        "print('Regional revenue 2026')\n"
        "for k, v in s.nlargest(25).items():\n"
        "    print(k, f'{v:.2f}', round(100 * v / s.sum(), 1))",
        "import csv\nrows = list(csv.DictReader(open('orders.csv')))\n"
        "cents = {}\nfor r in rows:\n"
        "    c = round(float(r['unit_price']) * 100) * int(r['units'])\n"
        "    cents[r['region']] = cents.get(r['region'], 0) + c\n"
        "total = sum(cents.values())\n"
        "for k, v in cents.items():\n"
        "    share = v / total\n"
        "    print(k, f'{v / 100:.2f}', round(share * 100, 1))",
    ],
    ids=[
        "with-open-round-percent",
        "pandas-percent",
        "io-open",
        "codecs",
        "pathlib",
        "filters-title-top-n",
        "cents-and-share",
    ],
)
def test_revenue_and_shares_a_script_computed_are_sourced(code):
    records = [_read("orders.csv", ORDERS_CSV), _run_python(code, REGION_STDOUT)]

    findings = _check(_md(REGION_TABLE), records, TOP_ORDERS_QUERY, "regions.md")

    assert findings == []


@pytest.mark.parametrize(
    "expr,computed",
    [
        ("int(r['units']) / len(rows) * 100", True),
        ("100 * int(r['units']) / len(rows)", True),
        ("int(r['units']) * 100", False),
        ("int(r['units']) * 100 / 4", False),
    ],
    ids=["ratio-times-100", "100-times-ratio", "price-100", "over-a-literal"],
)
def test_only_a_hundred_that_scales_a_ratio_is_not_a_typed_figure(expr, computed):
    code = (
        "import csv\nrows = list(csv.DictReader(open('orders.csv')))\n"
        f"for r in rows: print(r['region'], {expr})"
    )

    assert (_script_opens(_run_python(code)) is not None) is computed


_CSV = "import csv\nrows = list(csv.DictReader(open('orders.csv')))\n"
_PD = "import pandas as pd\ndf = pd.read_csv('orders.csv')\n"
_REV = "rev = sum(int(r['units']) * float(r['unit_price']) for r in rows)\n"

#: Scripts that compute only from orders.csv: their output is sourced.
CORRECT_ANALYSES = [
    _CSV
    + "rows = [r for r in rows if r['date'] >= '2026-03-01']\n"
    + _REV
    + "print(rev)",
    _PD
    + "df = df[df['date'] >= '2026-03-01']\nprint((df.units * df.unit_price).sum())",
    _PD + "q1 = df[(df.date >= '2026-01-01') & (df.date < '2026-04-01')]\n"
    "print((q1.units * q1.unit_price).sum())",
    _CSV + "from datetime import date\n"
    "rows = [r for r in rows if date.fromisoformat(r['date']) >= date(2026, 3, 1)]\n"
    + _REV
    + "print(rev)",
    _CSV
    + "rows = [r for r in rows if r['date'][:4] == '2026']\n"
    + _REV
    + "print(rev)",
    _PD + "print(df[df.year == 2026].units.sum())",
    _PD + "s = df.groupby('year').units.sum()\n"
    "cur, prev = s.iloc[-1], s.iloc[-2]\ngrowth = (cur - prev) / prev\n"
    "print(round(growth * 100, 1))",
    _CSV + "total = 0\nfor r in rows:\n"
    "    cents = round(float(r['unit_price']) * 100)\n"
    "    total += cents * int(r['units'])\nprint(total / 100)",
    _CSV + "rows.sort(key=lambda r: -float(r['unit_price']))\n"
    "for r in rows[:25]: print(r['order_id'], r['unit_price'])",
    _PD + "df['rev'] = df.units * df.unit_price\nprint(df.nlargest(25, 'rev'))",
    _CSV + "print('Revenue report 2026')\n" + _REV + "print(rev)",
    _PD + "print(df.head(20))",
    _PD + "d = pd.to_datetime(df.date)\nprint(df[d.dt.year == 2026].units.sum())",
    _PD + "m = df[df.date.str.startswith('2026-03')]\nprint(m.units.sum())",
    _PD + "m = df[pd.to_datetime(df.date) >= pd.Timestamp('2026-03-01')]\n"
    "print(m.units.sum())",
    _CSV + "by = {}\nfor r in rows: by[r['date'][:4]] = by.get(r['date'][:4], 0) + 1\n"
    "for y in range(2024, 2027): print(y, by.get(str(y), 0))",
    _CSV + _REV + "print(f'Q1 2026 revenue: {rev:.2f}')",
    _CSV + _REV + "if rev > 1000:\n    print('over budget', rev)",
    _CSV + "print('=' * 40)\n" + _REV + "print(rev)",
    _PD + "print(df.unit_price.quantile(0.95))",
    _CSV + "from datetime import datetime\n"
    "rows = [r for r in rows\n"
    "        if datetime.strptime(r['date'], '%Y-%m-%d').year == 2026]\n"
    + _REV
    + "print(rev)",
    _CSV + "by = {}\nfor r in rows: by[r['region']] = by.get(r['region'], 0) + 1\n"
    "total = sum(by.values())\n"
    "for k, v in by.items():\n    share = v / total\n    print(k, round(share * 100, 1))",
    _CSV + "TOP = 25\nYEAR = '2026'\n"
    "rows = [r for r in rows if r['date'].startswith(YEAR)][:TOP]\n"
    + _REV
    + "print(rev)",
    _CSV + "for r in rows: print(r['order_id'].ljust(20), r['units'].rjust(12))",
    _PD + "pd.set_option('display.width', 200)\nprint(df.nlargest(3, 'units'))",
    "import pandas as pd\n"
    "df = pd.read_csv('orders.csv', dtype={'units': 'int64'})\n"
    "print(df.units.sum())",
    _PD + "print(df.astype({'units': 'float64', 'unit_price': 'float32'}).sum())",
]

#: Scripts that type a price themselves: their output sources nothing.
INVENTED_FIGURES = [
    _CSV + "for r in rows: print(int(r['units']) * 95)",
    _CSV + "for r in rows: print(int(r['units']) * 95.0)",
    _CSV + "price = {'CM5': 95, 'IO': 60}\n"
    "for r in rows: print(int(r['units']) * price['CM5'])",
    _CSV + "data = 'CM5,95;IO,60'\nfor x in data.split(';'):\n"
    "    n, p = x.split(',')\n    print(n, int(p) * len(rows))",
    _CSV + "import json\np = json.loads('{\"cm5\": 95}')\nprint(p['cm5'] * len(rows))",
    _CSV + "from decimal import Decimal\nprint(Decimal('95.00') * len(rows))",
    _CSV + "print(float('95') * len(rows))",
    _CSV + "price = 9*10+5\nprint(price * len(rows))",
    _CSV + "price = int('9'+'5')\nprint(price * len(rows))",
    _CSV + "price = ord('_')\nprint(price * len(rows))",
    _CSV + "price = sum([10]*9)+5\nprint(price * len(rows))",
    _CSV + "price = int('%d%d' % (9, 5))\nprint(price * len(rows))",
    _CSV + "price = int('{}{}'.format(9, 5))\nprint(price * len(rows))",
    _CSV + "a = 9\nb = 10\nprice = a*b+5\nprint(price * len(rows))",
    _CSV + "a = 9\nb = 10\nprint(a*b*b*2 + len(rows))",
    _CSV + "price = int(''.join(['9', '5']))\nprint(price * len(rows))",
    _CSV + "price = int(''.join(map(str, [9, 5])))\nprint(price * len(rows))",
    _CSV + "price = 5*19\nprint(price * len(rows))",
    _CSV + "price = 10**2 - 5\nprint(price * len(rows))",
    _CSV + "price = abs(-9)*10+5\nprint(price * len(rows))",
    _CSV + "price = len('x' * 95)\nprint(price * len(rows))",
    _CSV + "price = int(str(9)+str(5))\nprint(price * len(rows))",
    _CSV + _REV + "print(rev * 1.08)",
    _CSV + _REV + "print(rev + 250)",
    _CSV + _REV + "print(rev - 1500)",
    _CSV + "price = max([9, 5])*10+5\nprint(price * len(rows))",
    _CSV + "for r in rows: print(r['order_id'], int(r['units']) * 100)",
    # The exemptions for dates, titles and filters must not open a door.
    _CSV + "for x in 'CM5,2049;IO,60'.split(';'):\n"
    "    n, p = x.split(',')\n    print(n, int(p) * len(rows))",
    _CSV + "print(len(rows) * 2049)",
    _CSV + "print('Total: 950')",
    _CSV + "price = 95\nn = len(rows)\nif n > price:\n    pass\nprint(n * price)",
    _CSV + "price = 95\nprint(rows[:price], len(rows) * price)",
    _CSV + "print('=' * 40, len(rows) * 40)",
    _CSV + "for x in ['CM5108032-95']:\n" "    print(int(x.split('-')[1]) * len(rows))",
    _CSV + "print('Shipping budget: $7,000', len(rows))",
    _CSV + "print(int('5,000'.replace(',', '')) // len(rows))",
    _CSV + "print(len(''.ljust(95)) * len(rows))",
    _CSV + "open('o.md', 'w').write('Unit price: 95')",
    _CSV + _REV + "print(rev / 1000)",
    _CSV + _REV + "print(rev * 0.5)",
    _PD + "print(df.units.sum() * 12)",
]


@pytest.mark.parametrize("code", CORRECT_ANALYSES)
def test_a_correct_analysis_of_the_input_is_credited(code):
    assert _script_opens(_run_python(code)) == ["orders.csv"]


@pytest.mark.parametrize("code", INVENTED_FIGURES)
def test_a_script_that_types_a_figure_is_not_credited(code):
    assert _script_opens(_run_python(code)) is None


def test_the_script_corpora_cover_enough_cases():
    assert len(CORRECT_ANALYSES) >= 20
    assert len(INVENTED_FIGURES) >= 25


def test_folding_a_scripts_names_stays_cheap():
    chain = "".join(f"x{i + 1} = x{i} * x{i}\n" for i in range(40))
    code = (
        _CSV
        + "x0 = 1\n"
        + chain
        + "big = [0] * 9 * 9 * 9 * 9 * 9 * 9 * 9 * 9\nprint(len(rows))"
    )

    assert _script_opens(_run_python(code)) == ["orders.csv"]


@pytest.mark.parametrize(
    "blowup",
    [
        "s0 = 'x' * 1000\n" + "".join(f"s{i + 1} = s{i} + s{i}\n" for i in range(16)),
        "a = 'x' * 1000\nb = a.join([a] * 10)\nc = b.join([b] * 10)\n"
        "d = c.join([c] * 10)\ne = d.join([d] * 10)",
        "y = '{:>200000000}'.format('a')",
        "y = '%200000000s' % 'a'",
        "y = '{:{}}'.format('a', 10 ** 8)",
        "y = 'a'.ljust(10 ** 8)",
        "from decimal import Decimal\ny = int(Decimal('1e10000000'))",
    ],
    ids=["concat", "join", "format", "percent", "nested-width", "pad", "decimal"],
)
def test_folding_a_huge_literal_stays_cheap(blowup):
    code = _CSV + blowup + "\nprint(len(rows))"

    started = time.perf_counter()
    _script_opens(_run_python(code))

    assert time.perf_counter() - started < 0.5


def test_a_script_is_parsed_once_per_record():
    report, records = _gm15_turn(GM15_CODE + "# parsed once\n")

    with patch("ast.parse", wraps=ast.parse) as parse:
        file_findings({report: _md(GM15_TABLE)}, records, query=TOP_ORDERS_QUERY)

    parsed = [c for c in parse.call_args_list if "# parsed once" in c.args[0]]
    assert len(parsed) == 1


@pytest.mark.parametrize(
    "tail",
    ["s = '\ud800'", "y = " + "-" * 5000 + "1", "y = " + "+".join(["1"] * 20000)],
    ids=["lone-surrogate", "deep-unary", "deep-binop"],
)
def test_a_script_that_cannot_be_parsed_is_not_credited(tail, caplog):
    code = "rows = open('orders.csv').read()\n" + tail
    table = "| Order | Revenue |\n|---|---|\n| 492 | 1410.00 |\n"
    records = [_read("orders.csv", ORDERS_CSV), _run_python(code, "492 1410.00\n")]

    with caplog.at_level("WARNING", logger="gaia.agents.base.deliverables"):
        findings = _check(_md(table), records, name="r.md")

    assert [f.gate for f in findings] == [FILE]
    assert "Cannot analyse" in caplog.text


def test_a_round_figure_with_thousands_separators_is_not_computed():
    code = _CSV + (
        "with open('plan.md', 'w') as out:\n"
        "    out.write('# Plan\\n\\nShipping budget: $7,000\\n')\n"
    )
    plan = _md("# Plan\n\nShipping budget: $7,000\n")
    records = [_read("orders.csv", ORDERS_CSV), _run_python(code)]

    findings = _check(
        plan, records, query="Write plan.md from orders.csv", name="plan.md"
    )

    assert [f.gate for f in findings] == [FILE]
    record = {"args": {"code": "b = '5,000'"}, "observed": "budget 5,000"}
    assert "5,000" not in evidence_text(record, code_literals=True)


def test_dates_ids_and_quantities_the_user_stated_are_not_figures():
    report = (
        "Order PO-2026-0412 placed 2026-10-14 for 40 units (SKU 1572864), "
        "delivery in 6 weeks, revision 3.2.\n\n| Week | Units |\n|---|---|\n"
        "| 41 | 20 |\n| 42 | 20 |\n"
    )

    assert _check(_md(report), [_fetch("u", "nothing numeric")]) == []


def test_figures_the_file_marks_as_estimates_pass():
    records = [_read("cm5-datasheet.pdf", DATASHEET)]
    marked_rows = INVENTED_BOM.replace("| CM5108032 |", "| CM5108032 (est.) |")
    marked_rows = marked_rows.replace("| CM5 IO Board |", "| CM5 IO Board ~ |")
    marked_rows = marked_rows.replace("| 27W PSU |", "| 27W PSU (unverified) |")
    marked_column = INVENTED_BOM.replace("Unit price", "Est. unit price")
    blanket = "All prices below are estimates, not quotes.\n\n" + INVENTED_BOM

    # Totals computed from a marked estimate are not new claims.
    assert _check(_md(marked_rows), records) == []
    assert _check(_md(marked_column), records) == []
    assert _check(_md(blanket), records) == []


def test_an_answer_that_names_the_unverified_figures_has_told_the_user():
    records = [_read("cm5-datasheet.pdf", DATASHEET)]
    answer = (
        "Wrote bom.md. I could not verify the prices $95, $60 and $12 — "
        "they are unverified list-price guesses."
    )

    assert _check(_md(INVENTED_BOM), records, answer=answer) == []
    partial = "Wrote bom.md. The $95 price is unverified."
    left = _check(_md(INVENTED_BOM), records, answer=partial)
    assert "$95.00" not in left[0].correction
    assert "$60.00" in left[0].correction


def test_spec_values_read_from_a_datasheet_pass():
    records = [_read("cm5-datasheet.pdf", DATASHEET)]
    spec = "- CPU: 2.4 GHz quad-core\n- RAM: 8 GB (user minimum), up to 16 GB\n- 5 V input\n"

    assert _check(_md(spec), records) == []
    assert _check(_md("- CPU: 3.0 GHz quad-core\n"), records) != []


def test_percentages_computed_from_input_numbers_pass():
    records = [_read("sales.csv", "region,q1,q2\nnorth,1200,1500\nsouth,800,760\n")]
    summary = "North grew 25.0% quarter on quarter; south fell 5%.\n"

    assert _check(_md(summary), records, name="summary.md") == []
    assert _check(_md("North grew 31% this quarter.\n"), records, name="s.md")


def test_json_deliverables_are_read_by_key():
    records = [_fetch("u", CM5_PAGE)]
    data = {"items": [{"sku": "CM5108032", "qty": 40, "unit_price": 95.0}]}
    ok = text_blocks(json.dumps(data), ".json")
    data["items"].append({"sku": "PSU", "qty": 40, "unit_price": 12.0})
    bad = text_blocks(json.dumps(data), ".json")

    assert _check(ok, records, name="bom.json") == []
    assert "12.0 (unit_price)" in _check(bad, records, name="bom.json")[0].correction


def test_docx_and_pptx_cells_are_read(tmp_path):
    docx = pytest.importorskip("docx")
    pptx = pytest.importorskip("pptx")
    document = docx.Document()
    document.add_paragraph("Recommended board: CM5108032 at $95.00 each.")
    table = document.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Item"
    table.rows[0].cells[1].text = "Price"
    table.rows[1].cells[0].text = "PSU"
    table.rows[1].cells[1].text = "12.00"
    document.save(tmp_path / "memo.docx")
    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = "Budget: $6,680 for 40 units"
    deck.save(tmp_path / "deck.pptx")

    records = [_fetch("u", CM5_PAGE)]
    files = {
        str(tmp_path / "memo.docx"): read_blocks(str(tmp_path / "memo.docx")),
        str(tmp_path / "deck.pptx"): read_blocks(str(tmp_path / "deck.pptx")),
    }
    findings = file_findings(files, records, query=BOM_QUERY)

    assert len(findings) == 2
    assert "12.00 (Price)" in findings[0].correction
    assert "$95.00" not in findings[0].correction
    assert "$6,680" in findings[1].correction


def test_unreadable_or_unsupported_files_yield_nothing(tmp_path):
    broken = tmp_path / "bom.xlsx"
    broken.write_bytes(b"not a zip")
    (tmp_path / "x.py").write_text("PRICE = 95", encoding="utf-8")

    assert read_blocks(str(broken)) == []
    assert read_blocks(str(tmp_path / "x.py")) == []
    assert read_blocks(str(tmp_path / "missing.md")) == []


def test_an_oversized_file_is_not_read(tmp_path, monkeypatch):
    import gaia.agents.base.deliverables as deliverables

    big = tmp_path / "big.md"
    big.write_text("$95.00\n" * 20, encoding="utf-8")
    monkeypatch.setattr(deliverables, "MAX_FILE_BYTES", 10)

    assert read_blocks(str(big)) == []


def test_a_total_time_column_is_not_money():
    report = "case,total_time_s,cost_usd\nsmall,12.40,\nlarge,95.10,\n"

    assert _check(text_blocks(report, ".csv"), [], name="bench.csv") == []


# ---------------------------------------------------------------------------
# A tool's own arguments are not evidence
# ---------------------------------------------------------------------------


def test_a_search_query_echo_is_not_evidence_for_the_query():
    record = _search("27W power supply price $12 OR $15", "1. Vilros 27W supply")
    assert "$12" not in evidence_text(record)
    assert "Vilros 27W" in evidence_text(record)


def test_a_quoted_find_term_echo_is_not_evidence():
    record = _rec(
        "fetch_page",
        {"url": "https://example.com/psu", "find": "$12"},
        {"content": "[No occurrence of '$12' in the page's 2,021 characters]"},
    )
    assert "$12" not in evidence_text(record)


def test_script_literals_are_not_evidence_but_its_computed_output_is():
    record = _run_python("print(95 * 40)\nprint(95)", "3800\n95\n")
    text = evidence_text(record, code_literals=True)
    assert "3800" in text
    assert "95" not in text.replace("3800", "")


def test_the_answer_check_no_longer_takes_a_write_as_proof_of_its_content():
    answer = "| Item | Price |\n|---|---|\n| PSU | 12.50 |\n"
    write = _rec(
        "write_file",
        {"file_path": "bom.md", "content": answer},
        {"status": "success", "file_path": "bom.md"},
    )

    assert content_findings(answer, [write])
    assert content_findings(answer, [write, _read("bom.md", answer)]) == []


# ---------------------------------------------------------------------------
# The seam, through the real loop
# ---------------------------------------------------------------------------


class _BomAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def fetch_page(url: str) -> dict:
            """Fetch a page."""
            return {"status": "success", "content": CM5_PAGE}

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write(content)
            return {"status": "success", "file_path": os.path.abspath(file_path)}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def bom_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _BomAgent(silent_mode=True, skip_lemonade=True)
        agent.streaming = False
        agent._tool_requires_confirmation = lambda *a, **kw: False
        yield agent


def _stub_chat(agent, *responses):
    queue = list(responses)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        if not queue:
            raise AssertionError("chat stub ran out of scripted responses")
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    agent.chat = MagicMock()
    agent.chat.send_messages = MagicMock(side_effect=_send)
    return sent


def _call(name, **args):
    return json.dumps({"thought": "working", "tool": name, "tool_args": args})


def _answer(text):
    return json.dumps({"thought": "done", "answer": text})


SEAM_QUERY = "Look up the CM5 price and write bom.md with a 40-unit BOM and a total."
SEAM_ANSWER = "Wrote `bom.md`: 40 CM5 modules, IO boards and PSUs, $6,680.00 total."


def test_invented_prices_in_a_written_file_get_a_note_never_a_rewrite(bom_agent):
    sent = _stub_chat(
        bom_agent,
        _call("fetch_page", url="https://example.com/cm5"),
        _call("write_file", file_path="bom.md", content=INVENTED_BOM),
        _answer(SEAM_ANSWER),
    )

    result = bom_agent.process_query(SEAM_QUERY, max_steps=10)

    assert len(sent) == 3, "the file check never sends the model back"
    assert not any("[check:" in str(m.get("content")) for m in sent[-1])
    assert strip_verification_scope(result["result"]).strip() == SEAM_ANSWER
    note = result["result"][len(SEAM_ANSWER) :]
    assert "`bom.md`" in note and "$60.00" in note
    assert "$95.00" not in note  # the fetched page has it
    assert result["result"].count(VERIFICATION_NOTE_OPENER) == 1
    assert "$60.00" in result["result"] and "$12.00" in result["result"]
    with open("bom.md", encoding="utf-8") as handle:
        assert handle.read() == INVENTED_BOM, "the user's file is never rewritten"


def test_a_sourced_file_passes_without_a_correction(bom_agent):
    sourced = (
        "| Item | Qty | Unit price | Line total |\n|---|---|---|---|\n"
        "| CM5108032 | 40 | $95.00 | $3,800.00 |\n"
    )
    sent = _stub_chat(
        bom_agent,
        _call("fetch_page", url="https://example.com/cm5"),
        _call("write_file", file_path="bom.md", content=sourced),
        _answer("Wrote `bom.md`: $3,800.00 for 40 modules."),
    )

    result = bom_agent.process_query(SEAM_QUERY, max_steps=10)

    assert len(sent) == 3
    assert os.path.isfile("bom.md")
    assert VERIFICATION_NOTE_OPENER not in result["result"]
