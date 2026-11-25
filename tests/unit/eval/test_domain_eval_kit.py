# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The domain eval kit: a dry run on a scripted model reproduces a fixed table."""

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool
from gaia.eval.domain import Case, Report, Verdict, run_cases
from gaia.testing import ScriptedLLMClient

USAGE = {"prompt_tokens": 1000, "completion_tokens": 100}


class _NativeClient(ScriptedLLMClient):
    """A host client on Fireworks, the one provider whose prices GAIA carries."""

    supports_native_tools = True

    @property
    def provider_name(self) -> str:
        return "fireworks"


class _SupportAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You answer questions about orders."

    def _register_tools(self) -> None:
        local = {}

        @tool(registry=local)
        def get_order(order_number: str) -> dict:
            """Look up an order by its number."""
            return {"status": "shipped", "order": order_number}

        self._instance_tools = local


def _agent(script, model_id="deepseek-v4p1-flash"):
    model = _NativeClient()
    for step in script:
        model.push(usage=USAGE, **step)
    return _SupportAgent(
        llm_client=model, model_id=model_id, host="conversation", silent_mode=True
    )


SCRIPTS = {
    "T001": [
        {"call": "get_order", "order_number": "1001"},
        {"say": "It shipped."},
    ],
    "T002": [{"say": "I can't see that order."}],
    "T003": [
        {"say": "Hello."},
        {"call": "get_order", "order_number": "3003"},
        {"say": "It shipped."},
    ],
}
CASES = [
    Case("T001", ["Where is order 1001?"], expect="get_order"),
    Case("T002", ["Where is order 2002?"], expect="get_order"),
    Case("T003", ["hi", "Where is order 3003?"], expect="get_order"),
]


def looked_up_the_order(case, turns):
    called = [call["name"] for turn in turns for call in turn.tool_calls]
    return Verdict(case.expect in called, "" if case.expect in called else "no lookup")


EXPECTED_TABLE = """\
| Case | Result | Turns | Model calls | Tool calls | Tokens in/out | Cost | Why |
|---|---|---|---|---|---|---|---|
| T001 | pass | 1 | 2 | 1 | 2000/200 | $0.0008 |  |
| T002 | FAIL | 1 | 1 | 0 | 1000/100 | $0.0004 | no lookup |
| T003 | pass | 2 | 3 | 1 | 3000/300 | $0.0013 |  |

Passed 2 of 3 (66.7%). Model calls per turn: 1.50. Turns that did not succeed: 0. Re-prompts: 0.
Cost: $0.0025 total, $0.0013 per passed case."""


def test_dry_run_reproduces_a_fixed_table():
    report = run_cases(
        lambda case: _agent(SCRIPTS[case.id]), CASES, looked_up_the_order
    )

    assert report.table(timing=False) == EXPECTED_TABLE
    assert report.table().splitlines()[-1].startswith("Turn time: p50 ")


def test_summary_numbers_match_the_rows():
    report = run_cases(
        lambda case: _agent(SCRIPTS[case.id]), CASES, looked_up_the_order
    )

    summary = report.summary()

    assert summary["cases"] == 3
    assert summary["passed"] == 2
    assert summary["turns"] == 4
    assert summary["model_calls_per_turn"] == pytest.approx(1.5)
    assert summary["cost_usd"] == pytest.approx(6 * 0.00042)
    assert summary["cost_per_pass_usd"] == pytest.approx(3 * 0.00042)
    assert summary["turn_seconds_p95"] >= summary["turn_seconds_p50"] >= 0


def test_turn_record_carries_what_a_grader_needs():
    report = run_cases(
        lambda case: _agent(SCRIPTS[case.id]), CASES[:1], looked_up_the_order
    )

    turn = report.results[0].turns[0]

    assert turn.user == "Where is order 1001?"
    assert turn.reply == "It shipped."
    assert (turn.status, turn.stop_reason) == ("success", "answered")
    assert turn.tool_calls[0]["args"] == {"order_number": "1001"}
    assert turn.result["cost_usd"] == turn.cost_usd


def test_unpriced_model_shows_no_cost_rather_than_a_guess():
    report = run_cases(
        lambda case: _agent(SCRIPTS[case.id], model_id="no-such-model"),
        CASES[:1],
        looked_up_the_order,
    )

    assert report.summary()["cost_usd"] is None
    assert "| n/a |" in report.table(timing=False)
    assert "Cost: n/a total, n/a per passed case." in report.table(timing=False)


def test_each_case_gets_its_own_agent():
    built = []

    def make(case):
        built.append(case.id)
        return _agent(SCRIPTS[case.id])

    run_cases(make, CASES, looked_up_the_order)

    assert built == ["T001", "T002", "T003"]


def test_grader_must_return_a_verdict():
    with pytest.raises(TypeError, match="must return a Verdict"):
        run_cases(lambda case: _agent(SCRIPTS[case.id]), CASES[:1], lambda c, t: True)


def test_repeated_case_ids_are_refused():
    with pytest.raises(ValueError, match="repeated: T001"):
        run_cases(lambda case: None, [CASES[0], CASES[0]], looked_up_the_order)


@pytest.mark.parametrize("turns", [[], "one string"])
def test_case_needs_a_list_of_turns(turns):
    with pytest.raises(ValueError, match="needs turns"):
        Case("T009", turns)


def test_factory_that_returns_something_else_is_named():
    class _NotAnAgent:
        def process_query(self, text):
            return {"answer": "hi"}

    with pytest.raises(ValueError, match="not a\\s+process_query"):
        run_cases(lambda case: _NotAnAgent(), CASES[:1], looked_up_the_order)


def test_empty_run_has_an_empty_report():
    summary = Report([]).summary()

    assert summary["cases"] == 0
    assert summary["pass_rate"] == 0.0
    assert summary["cost_usd"] is None
