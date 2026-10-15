# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the deliverable-figure check (``gaia.agents.base.deliverables``).

The flagged shape is kw-02 from the rc12 knowledge-work run: a BOM whose unit
prices ($95, $60, $12) no tool result held, written through ``run_python``.
The quiet shapes are ordinary deliverables: values copied from an input, a
fetched price, totals and per-unit splits computed from them, dates, IDs, and
quantities the user stated.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.deliverables import (
    FILE,
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

#: What the tools showed in kw-02: specs everywhere, two real prices.
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
# The kw-02 shape is flagged
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


def test_invented_prices_in_a_written_file_get_one_correction_then_a_note(bom_agent):
    sent = _stub_chat(
        bom_agent,
        _call("fetch_page", url="https://example.com/cm5"),
        _call("write_file", file_path="bom.md", content=INVENTED_BOM),
        _answer(SEAM_ANSWER),
        _answer(SEAM_ANSWER),
    )

    result = bom_agent.process_query(SEAM_QUERY, max_steps=10)

    assert len(sent) == 4, "the file gate corrects once, never loops"
    correction = sent[3][-1]["content"]
    assert correction.startswith("[check:grounding] ")
    assert "`bom.md`" in correction and "$60.00" in correction
    assert "$95.00" not in correction  # the fetched page has it
    assert strip_verification_scope(result["result"]).strip() == SEAM_ANSWER
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
