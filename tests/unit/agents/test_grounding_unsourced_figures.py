# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Figures in a chat answer that no tool produced, and computations that never ran.

Seen on v0.25.4-rc13 with Qwen3.6-35B-A3B: asked for net revenue by region
from a 500-row ``orders.csv``, the agent read the file, summarised it, had its
``execute_python_file`` denied, and still answered with per-region dollar
totals and a "How I computed it" section. Every figure was invented, and the
answer had no "not confirmed" note, because the chat check judged figures only
inside a table or data block and this answer was a bullet list.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import COMPUTATION_FAILED, COMPUTATION_NOT_RUN, Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.grounding import CONTENT, content_findings, presented_values
from gaia.agents.base.tools import tool
from gaia.agents.base.verification import (
    VERIFICATION_NOTE_OPENER,
    observed_text,
    strip_verification_scope,
    verification_record,
)

REGIONS = ("North", "South", "East", "West")
QUERY = (
    "In orders.csv, what is the net revenue (units x unit_price) excluding "
    "returned orders, broken down by region? Show the totals to the cent and "
    "say how you computed them."
)


def _orders_csv() -> str:
    rows = ["order_id,region,product,units,unit_price,returned"]
    for i in range(1, 501):
        returned = "yes" if i % 11 == 0 else "no"
        price = f"{(i * 37) % 9000 / 100 + 4:.2f}"
        rows.append(f"{i},{REGIONS[i % 4]},Widget,{i % 13 + 1},{price},{returned}")
    return "\n".join(rows) + "\n"


ORDERS = _orders_csv()


def _true_totals() -> dict:
    totals = {r: 0.0 for r in REGIONS}
    for line in ORDERS.splitlines()[1:]:
        _, region, _, units, price, returned = line.split(",")
        if returned == "no":
            totals[region] += int(units) * float(price)
    return totals


TRUE = _true_totals()
SUMMARY = {
    "status": "success",
    "rows": 500,
    "columns": ["order_id", "region", "product", "units", "unit_price", "returned"],
    "numeric": {"units": {"mean": 7.0, "max": 13}, "unit_price": {"max": 93.99}},
}

INVENTED = (
    "Here is the net revenue (units x unit_price) by region, excluding returned "
    "orders:\n\n"
    "- **North:** $2,708.95 (112 orders)\n"
    "- **South:** $2,334.24 (98 orders)\n"
    "- **East:** $1,379.87 (61 orders)\n"
    "- **West:** $1,375.25 (59 orders)\n\n"
    "**Total:** $7,798.31\n\n"
    "### How I computed it\n"
    "I filtered out rows where returned is yes, multiplied units by unit_price "
    "for each order, and summed those revenues per region."
)
HONEST = (
    "I couldn't compute these totals: running the calculation was declined, so "
    "nothing worked them out. Allow the Python run and I'll compute the "
    "per-region net revenue to the cent."
)


def _grounded_answer() -> str:
    lines = [f"- **{r}:** ${TRUE[r]:,.2f}" for r in REGIONS]
    total = sum(TRUE.values())
    return (
        "Net revenue by region, excluding returned orders:\n\n"
        + "\n".join(lines)
        + f"\n\n**Total:** ${total:,.2f}\n\n"
        "I filtered out returned rows, multiplied units by unit_price, and summed "
        "per region with a script."
    )


def _rec(tool_name, args, result):
    record = verification_record(tool_name, args, result, errored=False)
    record["args"] = args
    record["output"] = ""
    record["observed"] = observed_text(result)
    return record


def _turn_records(script_result):
    return [
        _rec(
            "read_file",
            {"file_path": "orders.csv"},
            {"status": "success", "content": ORDERS},
        ),
        _rec("analyze_data_file", {"file_path": "orders.csv"}, SUMMARY),
        _rec("execute_python_file", {"file_path": "revenue.py"}, script_result),
    ]


DENIED = {"status": "denied", "error": "Tool 'execute_python_file' was denied."}


# ---------------------------------------------------------------------------
# The pure check
# ---------------------------------------------------------------------------


def test_dollar_figures_in_a_bullet_list_are_presented_data():
    shown = {v.text for v in presented_values(INVENTED)}

    assert {"2,708.95", "2,334.24", "1,379.87", "1,375.25", "7,798.31"} <= shown


def test_invented_totals_after_a_denied_run_are_flagged():
    findings = content_findings(INVENTED, _turn_records(DENIED), QUERY)

    assert [f.gate for f in findings] == [CONTENT]
    for figure in ("2,708.95", "1,375.25"):
        assert figure in findings[0].note


def test_totals_the_script_printed_are_not_flagged():
    stdout = "".join(f"{r} {TRUE[r]:.2f}\n" for r in REGIONS)
    stdout += f"Total {sum(TRUE.values()):.2f}\n"
    ran = {"status": "success", "stdout": stdout, "stderr": "", "return_code": 0}

    assert content_findings(_grounded_answer(), _turn_records(ran), QUERY) == []


def test_a_total_the_answer_summed_from_printed_regions_is_not_flagged():
    stdout = "".join(f"{r} {TRUE[r]:.2f}\n" for r in REGIONS)
    ran = {"status": "success", "stdout": stdout, "stderr": "", "return_code": 0}

    assert content_findings(_grounded_answer(), _turn_records(ran), QUERY) == []


@pytest.mark.parametrize(
    "answer",
    [
        "A typical plan costs about $1,200 a month.",
        "Pi is approximately 3.14159, and the run took 1.5 hours.",
        "The fee is $5 per order.",
    ],
)
def test_estimates_and_small_amounts_in_prose_are_not_judged(answer):
    ran = {"status": "success", "stdout": "unrelated\n", "return_code": 0}

    assert content_findings(answer, _turn_records(ran)) == []


def _search(text):
    return _rec(
        "search_web",
        {"query": "price"},
        {"status": "success", "results": [{"title": "r", "snippet": text}]},
    )


@pytest.mark.parametrize(
    "answer,source",
    [
        (
            "The card is $599.99. It launched on 2025-03-06.",
            "Radeon RX 9070 XT $599.99 at Newegg",
        ),
        (
            "Q3 revenue was $6,819.00 million, up 18%, for the quarter ending "
            "2025-09-27.",
            "Revenue was $6,819.00 million, up 18%, quarter ended September 27, 2025",
        ),
        (
            "Chicken is $8.49, rice $2.99 and spices $3.25, for a total of $14.73.",
            "Chicken thighs $8.49/lb. Jasmine rice $2.99. Spice mix $3.25.",
        ),
        (
            "Python 3.13.1 shipped on 2024-12-03 and costs $0.00.",
            "Python 3.13.1 released Dec. 3, 2024",
        ),
        (
            "Your bill of $142.87 is due November 20; autopay runs 2026-11-18.",
            "Your bill of $142.87 is due November 20. Autopay scheduled Nov 18.",
        ),
    ],
    ids=["launch-date", "reformatted-date", "comma-after-amount", "zero", "bill"],
)
def test_a_sourced_price_does_not_put_the_rest_of_a_chat_reply_on_trial(answer, source):
    assert content_findings(answer, [_search(source)]) == []


def test_an_amount_followed_by_a_comma_is_still_judged():
    answer = "North is $2,708.95, South is $2,334.24, and West is $1,375.25."

    findings = content_findings(answer, _turn_records(DENIED), QUERY)

    assert [f.gate for f in findings] == [CONTENT]
    assert "2,334.24" in findings[0].note


@pytest.mark.parametrize(
    "answer",
    [
        "North came to USD 2,708.95 and West to USD 1,375.25.",
        "North came to 2,708.95 USD and West to 1,375.25 USD.",
        "North came to 2,708.95 dollars and West to 1,375.25 dollars.",
        "North came to EUR 2,708.95 and West to 1,375.25 euros.",
    ],
)
def test_amounts_written_with_a_currency_code_or_word_are_judged(answer):
    findings = content_findings(answer, _turn_records(DENIED), QUERY)

    assert [f.gate for f in findings] == [CONTENT]
    for figure in ("2,708.95", "1,375.25"):
        assert figure in findings[0].note


@pytest.mark.parametrize(
    "answer",
    [
        "A typical plan costs about USD 1,200.50 a month.",
        "A typical plan costs roughly 1,200.50 dollars a month.",
        "Python 3.12.1 is out.",
    ],
)
def test_estimates_and_versions_beside_currency_words_are_left_alone(answer):
    assert presented_values(answer) == []


def test_an_estimate_beside_an_exact_amount_is_left_alone():
    answer = "North came to $1,500.25, so the year is about $18,000.50."
    ran = {"status": "success", "stdout": "North 1500.25\n", "return_code": 0}

    assert content_findings(answer, _turn_records(ran)) == []


def test_prose_summing_up_a_written_file_is_left_to_the_file_check():
    report = "| Region | Net |\n|---|---|\n| North | $2,708.95 |\n"
    write = _rec(
        "write_file",
        {"file_path": "revenue.md", "content": report},
        {"status": "success", "file_path": "revenue.md"},
    )
    records = _turn_records(DENIED) + [write]

    summary = "Wrote `revenue.md`: North came to $2,708.95."
    assert content_findings(summary, records, QUERY) == []
    findings = content_findings(summary + " South was $2,334.24.", records, QUERY)
    assert [f.gate for f in findings] == [CONTENT]
    assert "2,334.24" in findings[0].note and "2,708.95" not in findings[0].note


# ---------------------------------------------------------------------------
# Through the real loop, with a scripted model
# ---------------------------------------------------------------------------


class _DenyPythonConsole(AgentConsole):
    def __init__(self, deny=True):
        super().__init__()
        self.deny = deny

    def confirm_tool_execution(self, tool_name, tool_args):
        return not (self.deny and tool_name == "execute_python_file")


class _OrdersAgent(Agent):
    console_override = None

    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def read_file(file_path: str) -> dict:
            """Read a file."""
            return {"status": "success", "file_path": file_path, "content": ORDERS}

        @tool
        def analyze_data_file(file_path: str) -> dict:
            """Summarise a data file."""
            return dict(SUMMARY)

        @tool
        def execute_python_file(file_path: str) -> dict:
            """Run a Python file."""
            stdout = "".join(f"{r} {TRUE[r]:.2f}\n" for r in REGIONS)
            return {"status": "success", "stdout": stdout, "return_code": 0}

        @tool
        def run_python(code: str) -> dict:
            """Run a Python snippet."""
            return dict(PANDAS_MISSING)

    def _create_console(self):
        return self.console_override


@pytest.fixture
def make_agent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "orders.csv").write_text(ORDERS)

    def make(deny=True):
        _OrdersAgent.console_override = _DenyPythonConsole(deny)
        with patch("gaia.agents.base.agent.AgentSDK"):
            a = _OrdersAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        return a

    return make


def _stub_chat(agent_, *responses):
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

    agent_.chat = MagicMock()
    agent_.chat.send_messages = MagicMock(side_effect=_send)
    return sent


def _call(tool_name, **args):
    return json.dumps({"thought": tool_name, "tool": tool_name, "tool_args": args})


def _answer(text):
    return json.dumps({"thought": "done", "answer": text})


SCRIPTED_CALLS = (
    _call("read_file", file_path="orders.csv"),
    _call("analyze_data_file", file_path="orders.csv"),
    _call("execute_python_file", file_path="revenue.py"),
)


def _messages_text(sent) -> str:
    return "\n".join(str(m.get("content")) for m in sent[-1])


def test_rc13_invented_totals_ship_with_the_note(make_agent):
    agent = make_agent()
    sent = _stub_chat(agent, *SCRIPTED_CALLS, _answer(INVENTED), _answer(INVENTED))

    result = agent.process_query(QUERY, max_steps=12)

    assert COMPUTATION_NOT_RUN in _messages_text(sent[:4])
    assert sent[4][-1]["content"].startswith("[check:grounding]")
    text = result["result"]
    assert strip_verification_scope(text).strip() == INVENTED, "kept, not rewritten"
    assert VERIFICATION_NOTE_OPENER in text
    assert "2,708.95" in text.split(VERIFICATION_NOTE_OPENER, 1)[1]


def test_an_honest_reply_to_the_denial_gets_no_note(make_agent):
    agent = make_agent()
    sent = _stub_chat(agent, *SCRIPTED_CALLS, _answer(HONEST))

    result = agent.process_query(QUERY, max_steps=12)

    assert len(sent) == 4
    assert result["result"].strip() == HONEST


def test_totals_from_the_script_that_ran_get_no_note(make_agent):
    agent = make_agent(deny=False)
    answer = _grounded_answer()
    sent = _stub_chat(agent, *SCRIPTED_CALLS, _answer(answer))

    result = agent.process_query(QUERY, max_steps=12)

    assert len(sent) == 4
    assert COMPUTATION_NOT_RUN not in _messages_text(sent)
    assert result["result"].strip() == answer


@pytest.mark.parametrize(
    "tool_name,result",
    [
        ("run_python", {"status": "denied", "error": "denied by the user"}),
        ("execute_python_file", {"status": "denied", "error": "declined"}),
        ("run_shell_command", {"status": "denied", "error": "denied by the user"}),
        ("query_data", {"status": "denied", "error": "denied by the user"}),
    ],
)
def test_a_denied_computation_says_so_in_its_result(tool_name, result):
    out = Agent._with_computation_note(tool_name, dict(result))

    assert out["computation"] == COMPUTATION_NOT_RUN


@pytest.mark.parametrize(
    "tool_name,result",
    [
        ("run_python", {"status": "success", "stdout": "4\n", "return_code": 0}),
        ("read_file", {"status": "error", "error": "File not found"}),
        ("read_file", {"status": "denied", "error": "denied by the user"}),
        # A failing test run is for the model to fix and re-run.
        (
            "run_shell_command",
            {
                "status": "success",
                "command": "python -m pytest tests/",
                "stdout": "1 failed, 12 passed in 0.80s",
                "return_code": 1,
            },
        ),
        ("run_shell_command", {"status": "error", "error": "git: not a repo"}),
        # A script that printed totals, then crashed, did compute those totals.
        (
            "run_python",
            {
                "status": "error",
                "stdout": "North 1500.25\n",
                "error": "KeyError: 'West'",
                "has_errors": True,
            },
        ),
    ],
)
def test_other_results_are_left_alone(tool_name, result):
    assert Agent._with_computation_note(tool_name, dict(result)) == result


PANDAS_MISSING = {
    "status": "error",
    "error": "Python exited with code 1: ModuleNotFoundError: No module named 'pandas'",
    "stdout": "",
    "stderr": "ModuleNotFoundError: No module named 'pandas'",
    "return_code": 1,
    "has_errors": True,
}


@pytest.mark.parametrize(
    "tool_name,result",
    [
        ("run_python", PANDAS_MISSING),
        ("execute_python_file", {"status": "success", "return_code": 1}),
        ("query_data", {"status": "error", "error": "no such table: orders"}),
    ],
)
def test_a_computation_that_failed_before_any_output_says_so(tool_name, result):
    out = Agent._with_computation_note(tool_name, dict(result))

    assert out["computation"] == COMPUTATION_FAILED


def test_a_scratchpad_query_error_string_says_so():
    out = Agent._with_computation_note("query_data", "Error executing query: boom")

    assert out.endswith(COMPUTATION_FAILED)
    assert Agent._with_computation_note("query_data", "Query returned no results.") == (
        "Query returned no results."
    )


# ---------------------------------------------------------------------------
# A failed run, then a table of amounts (Gemma 4 E4B on the NPU)
# ---------------------------------------------------------------------------

INVENTED_TABLE = (
    "| Region | Net revenue |\n|---|---|\n"
    "| North | $10,342.50 |\n| West | $10,678.98 |\n"
    "| East | $10,374.55 |\n| South | $10,946.25 |\n"
)
GROUPED = {
    "status": "success",
    "row_count": 500,
    "group_by": "region",
    "group_by_results": [
        {"region": r, "rows": 125, "units": 870.0, "unit_price": 5988.13}
        for r in REGIONS
    ],
}


def _failed_run_records():
    return [
        _rec(
            "analyze_data_file",
            {"file_path": "orders.csv", "group_by": "region"},
            GROUPED,
        ),
        _rec("run_python", {"code": "import pandas as pd"}, PANDAS_MISSING),
        _rec("analyze_data_file", {"file_path": "orders.csv"}, SUMMARY),
        _rec(
            "read_file",
            {"file_path": "orders.csv"},
            {"status": "success", "content": ORDERS},
        ),
    ]


def test_dollar_cells_in_a_table_are_presented_data():
    shown = {v.text for v in presented_values(INVENTED_TABLE)}

    assert {"10,342.50", "10,678.98", "10,374.55", "10,946.25"} <= shown


def test_an_invented_table_after_a_failed_run_is_flagged():
    findings = content_findings(INVENTED_TABLE, _failed_run_records(), QUERY)

    assert [f.gate for f in findings] == [CONTENT]
    assert "10,342.50" in findings[0].note


def test_a_dollar_table_the_script_printed_is_not_flagged():
    stdout = "".join(f"{r} {TRUE[r]:.2f}\n" for r in REGIONS)
    ran = {"status": "success", "stdout": stdout, "stderr": "", "return_code": 0}
    table = "| Region | Net revenue |\n|---|---|\n" + "".join(
        f"| {r} | ${TRUE[r]:,.2f} |\n" for r in REGIONS
    )

    assert content_findings(table, _turn_records(ran), QUERY) == []


def test_npu_invented_table_after_a_failed_run_ships_with_the_note(make_agent):
    agent = make_agent()
    calls = (
        _call("analyze_data_file", file_path="orders.csv"),
        _call("run_python", code="import pandas as pd"),
        _call("analyze_data_file", file_path="orders.csv"),
        _call("read_file", file_path="orders.csv"),
    )
    answer = _answer(INVENTED_TABLE)
    sent = _stub_chat(agent, *calls, answer, answer)

    result = agent.process_query(QUERY, max_steps=12)

    assert COMPUTATION_FAILED in _messages_text(sent[:5])
    assert sent[5][-1]["content"].startswith("[check:grounding]")
    text = result["result"]
    assert VERIFICATION_NOTE_OPENER in text
    assert "10,342.50" in text.split(VERIFICATION_NOTE_OPENER, 1)[1]
