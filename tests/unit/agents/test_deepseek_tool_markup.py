# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""DeepSeek's native tool-call markup written as text must run, never be the answer.

On the gateway's ``amd.DeepSeek-V4.1-Flash``, after ~20 ordinary calls the model
wrote its next call as DSML markup in plain text; GAIA delivered the markup as
the final answer and the task never finished (kw-06, amd/gaia #5087).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    _MAX_UNFINISHED_ANSWER_REPROMPTS,
    _TOOL_MARKUP_FAILURE_ANSWER,
    Agent,
    _unfinished_answer_kind,
    _without_tool_markup,
)
from gaia.agents.base.tool_markup import (
    MalformedToolMarkup,
    parse_deepseek_tool_calls,
    strip_tool_markup,
)
from gaia.agents.base.verification import strip_verification_scope

CSV = "C:/runs/kw-06-expense-audit/claims/expense_lines.csv"
KW06_CODE = (
    "import pandas as pd\n"
    "pd.set_option('display.max_rows', 400)\n"
    f"df = pd.read_csv('{CSV}')\n"
    "for cid, g in df.groupby('claim_id', sort=False):\n"
    "    print('===', cid, g['traveler'].iloc[0], g['trip_id'].iloc[0])\n"
)

# The kw-06 final answer (trimmed), fullwidth bar U+FF5C as the model wrote it.
KW06_MARKUP = (
    "<｜DSML｜ calls>\n"
    '<｜DSML｜ invoke name="run_python">\n'
    f'<｜DSML｜ parameter name="code" string="true">{KW06_CODE}</｜DSML｜ parameter>\n'
    '<｜DSML｜ parameter name="goal" string="true">Print all expense lines grouped '
    "by claim</｜DSML｜ parameter>\n"
    '<｜DSML｜ parameter name="tool" string="true">run_python</｜DSML｜ parameter>\n'
    "</｜DSML｜ invoke>\n"
    "</｜DSML｜ calls>"
)

# The same reply cut off mid-parameter.
KW06_TRUNCATED = KW06_MARKUP[: KW06_MARKUP.index('name="goal"') + 4]

ASCII_MARKUP = (
    "Checking the totals.\n"
    "<|DSML|function_calls>\n"
    '<|DSML|invoke name="run_python">\n'
    '<|DSML|parameter name="code" string="true">print(1 + 1)</|DSML|parameter>\n'
    '<|DSML|parameter name="timeout" string="false">30</|DSML|parameter>\n'
    "</|DSML|invoke>\n"
    "</|DSML|function_calls>"
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_fullwidth_dsml_block_parses_to_the_call():
    parsed = parse_deepseek_tool_calls(KW06_MARKUP)

    assert [c.name for c in parsed.calls] == ["run_python"]
    assert parsed.calls[0].args["code"] == KW06_CODE
    assert parsed.prose == ""


def test_ascii_dsml_block_parses_with_json_values_and_prose():
    parsed = parse_deepseek_tool_calls(ASCII_MARKUP)

    assert parsed.calls[0].name == "run_python"
    assert parsed.calls[0].args == {"code": "print(1 + 1)", "timeout": 30}
    assert parsed.prose == "Checking the totals."


def test_dsml_with_two_invokes_returns_both():
    markup = (
        "<｜DSML｜function_calls>"
        '<｜DSML｜invoke name="read_file"><｜DSML｜parameter name="path" '
        'string="true">a.txt</｜DSML｜parameter></｜DSML｜invoke>'
        '<｜DSML｜invoke name="read_file"><｜DSML｜parameter name="path" '
        'string="true">b.txt</｜DSML｜parameter></｜DSML｜invoke>'
        "</｜DSML｜function_calls>"
    )
    parsed = parse_deepseek_tool_calls(markup)

    assert [c.args["path"] for c in parsed.calls] == ["a.txt", "b.txt"]


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(KW06_TRUNCATED, id="cut-off-mid-parameter-tag"),
        pytest.param(KW06_MARKUP[: KW06_MARKUP.index("pd.set_option")], id="in-value"),
        pytest.param(
            KW06_MARKUP[: KW06_MARKUP.rindex("</｜DSML｜ invoke>")], id="no-close"
        ),
        pytest.param(
            '<|DSML|invoke name="x"><|DSML|parameter name="n" string="false">'
            "not json</|DSML|parameter></|DSML|invoke>",
            id="bad-json-value",
        ),
    ],
)
def test_broken_dsml_raises(markup):
    with pytest.raises(MalformedToolMarkup):
        parse_deepseek_tool_calls(markup)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>get_weather<｜tool▁sep｜>"
            '{"city": "Tokyo"}<｜tool▁call▁end｜><｜tool▁calls▁end｜>',
            id="v3.1",
        ),
        pytest.param(
            "<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>"
            'get_weather\n```json\n{"city": "Tokyo"}\n```<｜tool▁call▁end｜>'
            "<｜tool▁calls▁end｜>",
            id="v3",
        ),
    ],
)
def test_older_deepseek_special_tokens_parse(text):
    parsed = parse_deepseek_tool_calls(text)

    assert parsed.calls[0].name == "get_weather"
    assert parsed.calls[0].args == {"city": "Tokyo"}


def test_two_dsml_blocks_both_run():
    block = (
        '<｜DSML｜function_calls><｜DSML｜invoke name="read_file"><｜DSML｜parameter '
        'name="path" string="true">{}</｜DSML｜parameter></｜DSML｜invoke>'
        "</｜DSML｜function_calls>"
    )
    parsed = parse_deepseek_tool_calls(
        "First.\n" + block.format("a.txt") + "\nThen.\n" + block.format("b.txt")
    )

    assert [c.args["path"] for c in parsed.calls] == ["a.txt", "b.txt"]
    assert parsed.prose == "First.\n\nThen."


def test_call_followed_by_a_fenced_example_runs_only_the_call():
    example = 'Example:\n```\n<｜DSML｜ calls>\n<｜DSML｜ invoke name="x">\n```\nDone.'
    parsed = parse_deepseek_tool_calls(ASCII_MARKUP + "\n" + example)

    assert [c.name for c in parsed.calls] == ["run_python"]
    assert parsed.prose.endswith(example)
    stripped = strip_tool_markup("A\n" + KW06_MARKUP + "\n" + example)
    assert stripped == "A\n\n" + example


@pytest.mark.parametrize(
    "text",
    [
        "The audit found 12 violations totalling $1,234.50.",
        "Use `<invoke>` tags? No — plain text here.",
        "DSML looks like this:\n```\n<｜DSML｜ calls>\n```",
        "DeepSeek wraps calls in `<｜DSML｜function_calls>` tags.\n\nThe total is $412.",
        'DeepSeek writes `<｜DSML｜invoke name="x">`, then the parameters.',
        "Qwen wraps calls in `<tool_call>` tags.\n\nThe total is $412.",
        "Claude-style markup starts with `<function_calls>`. The total is $412.",
        "Old DeepSeek used `<｜tool▁calls▁begin｜>`. The total is $412.",
        "Example:\n~~~\n<tool_call>{}\n~~~\nThe total is $412.",
        "A bare <｜DSML｜invoke> mention names no tool, so it is not a call.",
    ],
)
def test_text_without_live_markup_is_unchanged(text):
    assert parse_deepseek_tool_calls(text) is None
    assert strip_tool_markup(text) == text


# ---------------------------------------------------------------------------
# Answer-path defence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "Total to recover: $412.\n\n" + KW06_MARKUP,
        "Total to recover: $412.\n\n" + KW06_TRUNCATED,
        "Total to recover: $412.\n<tool_call>\n"
        '{"name": "run_python", "arguments": {"code": "1"}}\n</tool_call>',
        'Total to recover: $412.\n<function_calls>\n<invoke name="x">',
        "Total to recover: $412.<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>x",
    ],
)
def test_strip_removes_every_markup_format(answer):
    assert strip_tool_markup(answer) == "Total to recover: $412."


def test_markup_only_answer_becomes_an_explanation_not_markup():
    assert _without_tool_markup(KW06_TRUNCATED) == _TOOL_MARKUP_FAILURE_ANSWER
    assert _without_tool_markup("All done.") == "All done."


@pytest.mark.parametrize("answer", [KW06_MARKUP, "Done.\n<tool_call>{}</tool_call>"])
def test_markup_answers_are_flagged_unfinished(answer):
    assert _unfinished_answer_kind(answer) == "tool_markup"


def test_inline_mention_of_markup_is_not_flagged_unfinished():
    answer = "Qwen wraps calls in `<tool_call>` and DeepSeek in `<｜DSML｜calls>`."
    assert _unfinished_answer_kind(answer) is None


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


class _DummyAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        return a


def _register_run_python(agent):
    calls = []

    def run_python(code: str, timeout: int = 60):
        calls.append({"code": code, "timeout": timeout})
        return {"status": "success", "stdout": "ok"}

    agent._instance_tools = {
        "run_python": {
            "name": "run_python",
            "description": "stub",
            "parameters": {
                "code": {"type": "string", "required": True},
                "timeout": {"type": "integer", "required": False},
            },
            "function": run_python,
            "atomic": True,
        }
    }
    return calls


def _stub_chat(agent, *responses):
    responses = list(responses)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        resp = MagicMock()
        resp.text = responses.pop(0)
        resp.stats = {}
        resp.reasoning = None
        return resp

    chat = MagicMock()
    chat.send_messages = MagicMock(side_effect=_send)
    agent.chat = chat
    return sent


def _answer(text):
    return json.dumps({"thought": "", "answer": text})


def _final_text(result):
    return strip_verification_scope(result["result"]).strip()


def test_parse_drops_json_envelope_keys_the_tool_does_not_take(agent):
    _register_run_python(agent)

    parsed = agent._parse_llm_response(KW06_MARKUP)

    assert parsed["tool"] == "run_python"
    assert parsed["tool_args"] == {"code": KW06_CODE}
    assert "answer" not in parsed


def test_kw06_reply_runs_the_tool_instead_of_becoming_the_answer(agent):
    # run_python is confirmation-gated; the markup call goes through that gate.
    agent.console.auto_approve_gated_tools = True
    calls = _register_run_python(agent)
    # As the model sent it: reasoning closed by </think>, then the DSML call.
    reply = '{"thought": "Let me print all lines.</think>\n\n' + KW06_MARKUP
    _stub_chat(agent, reply, _answer("Total to recover: $412.50."))

    result = agent.process_query("Audit the expense lines", max_steps=10)

    assert calls == [{"code": KW06_CODE, "timeout": 60}]
    assert _final_text(result) == "Total to recover: $412.50."


def test_truncated_markup_is_retried_not_answered(agent):
    calls = _register_run_python(agent)
    sent = _stub_chat(agent, KW06_TRUNCATED, _answer("Total to recover: $412.50."))

    result = agent.process_query("Audit the expense lines", max_steps=10)

    assert calls == []
    assert len(sent) == 2
    retry = sent[1][-1]["content"]
    assert "tool-call markup" in retry and "cut off" in retry
    assert "DSML" not in sent[1][-2]["content"]
    assert _final_text(result) == "Total to recover: $412.50."


def test_markup_retry_is_bounded_and_never_shows_markup(agent):
    _register_run_python(agent)
    sent = _stub_chat(agent, KW06_TRUNCATED, KW06_TRUNCATED, _answer("unused"))

    result = agent.process_query("Audit the expense lines", max_steps=10)

    assert len(sent) == 2
    assert _final_text(result) == _TOOL_MARKUP_FAILURE_ANSWER


def test_answer_still_carrying_markup_after_reprompts_is_stripped(agent):
    leaked = 'Total to recover: $412.\n<tool_call>\n{"name": "x"}\n</tool_call>'
    sent = _stub_chat(
        agent, *[_answer(leaked)] * (_MAX_UNFINISHED_ANSWER_REPROMPTS + 1)
    )

    result = agent.process_query("Audit the expense lines", max_steps=10)

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    assert _final_text(result) == "Total to recover: $412."


def test_markup_call_still_needs_approval(agent):
    calls = _register_run_python(agent)
    _stub_chat(agent, KW06_MARKUP, _answer("I could not run the script."))

    result = agent.process_query("Audit the expense lines", max_steps=10)

    assert calls == []
    assert "DSML" not in result["result"]


def test_json_tool_call_quoting_markup_is_not_reparsed(agent):
    reply = json.dumps(
        {
            "tool": "write_file",
            "tool_args": {"path": "notes.md", "content": KW06_MARKUP},
        }
    )

    parsed = agent._parse_llm_response(reply)

    assert parsed["tool"] == "write_file"
    assert parsed["tool_args"]["content"] == KW06_MARKUP


def test_markup_retry_budget_resets_after_a_clean_call(agent):
    calls = _register_run_python(agent)
    agent.console.auto_approve_gated_tools = True
    sent = _stub_chat(
        agent, KW06_TRUNCATED, KW06_MARKUP, KW06_TRUNCATED, _answer("Total: $412.")
    )

    result = agent.process_query("Audit the expense lines", max_steps=10)

    assert len(sent) == 4
    assert len(calls) == 1
    assert _final_text(result) == "Total: $412."
