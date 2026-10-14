# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""DSML parsing must not run what it shouldn't, nor lose what it should run."""

import json
import time

import pytest

from gaia.agents.base.tool_markup import (
    MalformedToolMarkup,
    has_tool_markup,
    parse_deepseek_tool_calls,
    strip_tool_markup,
)
from tests.unit.agents.test_deepseek_tool_markup import (  # noqa: F401
    KW06_MARKUP,
    _answer,
    _final_text,
    _register_run_python,
    _stub_chat,
    agent,
)

P_CLOSE = "</｜DSML｜parameter>"


def _invoke(name, value, bar="｜", string="true"):
    return (
        f'<{bar}DSML{bar}invoke name="{name}">'
        f'<{bar}DSML{bar}parameter name="code" string="{string}">{value}'
        f"</{bar}DSML{bar}parameter></{bar}DSML{bar}invoke>"
    )


def _block(*invokes, bar="｜"):
    return (
        f"<{bar}DSML{bar}function_calls>"
        + "".join(invokes)
        + f"</{bar}DSML{bar}function_calls>"
    )


# ------------------------------------------------------- examples, not calls


@pytest.mark.parametrize(
    "text",
    [
        "Here it is:\n```xml\n" + KW06_MARKUP + "\n```\nThat is the format.",
        "Here it is:\n~~~\n" + KW06_MARKUP + "\n~~~",
        "Unclosed example:\n```\n" + KW06_MARKUP,
        "Inline: `" + _invoke("run_python", "print(1)") + "` is one call.",
    ],
)
def test_markup_inside_code_is_never_a_call_and_never_stripped(text):
    assert parse_deepseek_tool_calls(text) is None
    assert not has_tool_markup(text)
    assert strip_tool_markup(text) == text


@pytest.mark.parametrize(
    "text",
    [
        'Anthropic\'s format wraps a call in <invoke name="tool"> tags.',
        'Write <parameter name="x"> inside the invoke.',
        "The <|DSML| prefix marks DeepSeek markup; the bar is fullwidth.",
    ],
)
def test_prose_about_markup_is_not_parsed_as_a_call(text, agent):
    assert parse_deepseek_tool_calls(text) is None
    parsed = agent._parse_llm_response(text)
    assert "tool" not in parsed and "tool_calls" not in parsed


# --------------------------------------------------------------- structure


def test_two_blocks_with_mixed_bars_both_parse():
    text = (
        "One.\n"
        + _block(_invoke("run_python", "a"), bar="|")
        + "\nTwo.\n"
        + _block(_invoke("run_python", "b"))
    )
    parsed = parse_deepseek_tool_calls(text)
    assert [c.args["code"] for c in parsed.calls] == ["a", "b"]
    assert parsed.prose == "One.\n\nTwo."


def test_a_bar_mix_inside_one_tag_still_parses():
    text = (
        '<｜DSML|invoke name="run_python"><|DSML｜parameter name="code" '
        'string="true">x</｜DSML|parameter></|DSML｜invoke>'
    )
    assert parse_deepseek_tool_calls(text).calls[0].args == {"code": "x"}


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            _block(_invoke("run_python", "ok"))
            + "\n"
            + _block(_invoke("run_python", "x"))[:-25],
            id="second-block-truncated",
        ),
        pytest.param(
            '<｜DSML｜invoke name="a"><｜DSML｜invoke name="b"></｜DSML｜invoke>'
            "</｜DSML｜invoke>",
            id="invoke-inside-invoke",
        ),
        pytest.param(
            "<｜DSML｜function_calls><｜DSML｜function_calls>"
            + _invoke("a", "x")
            + "</｜DSML｜function_calls></｜DSML｜function_calls>",
            id="container-inside-container",
        ),
        pytest.param('<｜DSML｜invoke name="">', id="empty-tool-name"),
        pytest.param(
            "<｜DSML｜function_calls></｜DSML｜function_calls>", id="no-invoke"
        ),
        pytest.param(
            '<｜DSML｜invoke name="a"><｜DSML｜parameter string="true">x'
            "</｜DSML｜parameter></｜DSML｜invoke>",
            id="parameter-without-name",
        ),
    ],
)
def test_broken_structure_raises_rather_than_running_part_of_it(text):
    with pytest.raises(MalformedToolMarkup):
        parse_deepseek_tool_calls(text)


def test_a_json_value_holding_the_closing_tag_raises_not_truncates():
    value = json.dumps({"note": P_CLOSE + " then more"}, ensure_ascii=False)
    with pytest.raises(MalformedToolMarkup):
        parse_deepseek_tool_calls(_invoke("run_python", value, string="false"))


def test_a_json_value_with_the_closing_tag_escaped_round_trips():
    note = {"note": P_CLOSE + " then more"}
    markup = _invoke("run_python", json.dumps(note), string="false")
    assert parse_deepseek_tool_calls(markup).calls[0].args["code"] == note


def test_a_text_value_holding_the_closing_tag_raises_not_truncates():
    value = 'print("' + P_CLOSE + '")'
    with pytest.raises(MalformedToolMarkup):
        parse_deepseek_tool_calls(_invoke("run_python", value))


def test_a_value_holding_other_markup_keeps_it_verbatim():
    value = '<tool_call>{"x": 1}</tool_call> and <invoke name="y">'
    parsed = parse_deepseek_tool_calls(_invoke("run_python", value))
    assert parsed.calls[0].args["code"] == value


def test_unicode_and_newlines_in_a_value_survive():
    value = "naïve = '€ 12,50'\n\tprint('日本語')\n"
    parsed = parse_deepseek_tool_calls(_invoke("run_python", value))
    assert parsed.calls[0].args["code"] == value


# --------------------------------------------------------------- size


def test_a_500kb_parameter_parses_and_strips_quickly():
    value = "x = 1  # " + "a" * 500_000
    text = "Running it.\n" + _block(_invoke("run_python", value))
    start = time.perf_counter()
    parsed = parse_deepseek_tool_calls(text)
    stripped = strip_tool_markup(text)
    assert time.perf_counter() - start < 2.0
    assert parsed.calls[0].args["code"] == value
    assert stripped == "Running it."


def test_many_inline_mentions_are_not_checked_quadratically():
    """Each match used to be checked against every code span: 64 KB took ~2 s."""
    text = '`<tool_call>` and `<｜DSML｜invoke name="x">` ' * 4000
    start = time.perf_counter()
    assert not has_tool_markup(text)
    assert strip_tool_markup(text) == text
    assert parse_deepseek_tool_calls(text) is None
    assert time.perf_counter() - start < 0.5


# --------------------------------------------------------------- the loop


def test_markup_for_an_unregistered_tool_never_runs(agent):
    calls = _register_run_python(agent)
    agent.console.auto_approve_gated_tools = True
    sent = _stub_chat(
        agent,
        _block(_invoke("delete_everything", "rm -rf /")),
        _answer("I could not do that."),
    )
    result = agent.process_query("Clean up", max_steps=5)
    assert calls == []
    assert any(
        "delete_everything" in str(m.get("content"))
        for m in sent[-1]
        if m.get("role") in ("tool", "user")
    )
    assert "DSML" not in result["result"]


def test_two_gated_markup_calls_without_approval_run_neither(agent):
    calls = _register_run_python(agent)
    _stub_chat(
        agent,
        _block(_invoke("run_python", "print(1)"), _invoke("run_python", "print(2)")),
        _answer("Nothing ran."),
    )
    result = agent.process_query("Run both", max_steps=5)
    assert calls == []
    assert "DSML" not in result["result"]


def test_two_markup_calls_with_approval_run_both_in_order(agent):
    calls = _register_run_python(agent)
    agent.console.auto_approve_gated_tools = True
    _stub_chat(
        agent,
        _block(_invoke("run_python", "print(1)"), _invoke("run_python", "print(2)")),
        _answer("Both ran."),
    )
    result = agent.process_query("Run both", max_steps=5)
    assert [c["code"] for c in calls] == ["print(1)", "print(2)"]
    assert _final_text(result) == "Both ran."


def test_a_fenced_example_in_a_final_answer_is_delivered_intact(agent):
    answer = "DeepSeek writes calls like this:\n```\n" + KW06_MARKUP + "\n```"
    _stub_chat(agent, _answer(answer))
    result = agent.process_query("How does DeepSeek format a tool call?", max_steps=5)
    assert _final_text(result) == answer
