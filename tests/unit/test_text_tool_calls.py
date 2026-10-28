# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tool calls a model writes as text in Anthropic XML or Hermes/Qwen JSON.

Claude through an OpenAI-compatible gateway sometimes answers with
``<invoke name=...>`` text instead of a native call. These used to be stripped
and never run, so the turn ended with the work undone.
"""

import pytest

from gaia.agents.base.tool_markup import (
    MalformedToolMarkup,
    has_tool_markup,
    parse_text_tool_calls,
    strip_tool_markup,
)

XML = (
    'Reading it now.\n<function_calls>\n<invoke name="read_file">\n'
    '<parameter name="file_path">src/a.py</parameter>\n'
    '<parameter name="limit">80</parameter>\n</invoke>\n</function_calls>'
)


def test_anthropic_invoke_becomes_a_call():
    parsed = parse_text_tool_calls(XML)
    assert [(c.name, c.args) for c in parsed.calls] == [
        ("read_file", {"file_path": "src/a.py", "limit": "80"})
    ]
    assert parsed.calls[0].raw_strings is True
    assert parsed.prose == "Reading it now."


def test_bare_invoke_without_container_is_a_call():
    parsed = parse_text_tool_calls(
        '<invoke name="x"><parameter name="a">1</parameter></invoke>'
    )
    assert parsed.calls[0].name == "x"


def test_several_invokes_in_one_block():
    text = (
        '<function_calls><invoke name="a"><parameter name="p">1</parameter></invoke>'
        '<invoke name="b"></invoke></function_calls>'
    )
    assert [c.name for c in parse_text_tool_calls(text).calls] == ["a", "b"]


def test_multiline_content_keeps_inner_newlines():
    text = (
        '<invoke name="write_file"><parameter name="file_path">o.md</parameter>'
        '<parameter name="content">\n# Title\n\nbody\n</parameter></invoke>'
    )
    args = parse_text_tool_calls(text).calls[0].args
    assert args["content"] == "# Title\n\nbody"


def test_content_that_mentions_xml_tags_survives():
    text = (
        '<invoke name="write_file"><parameter name="content">use <b>bold</b> '
        "and &lt;tags&gt;</parameter></invoke>"
    )
    assert parse_text_tool_calls(text).calls[0].args["content"] == (
        "use <b>bold</b> and &lt;tags&gt;"
    )


@pytest.mark.parametrize(
    "text",
    [
        '<invoke name="write_file"><parameter name="content">half a fi',
        '<invoke name="write_file"><parameter name="content">x</parameter>',
        '<invoke name=""><parameter name="a">1</parameter></invoke>',
    ],
)
def test_cut_off_or_nameless_xml_is_malformed_not_an_answer(text):
    with pytest.raises(MalformedToolMarkup):
        parse_text_tool_calls(text)


@pytest.mark.parametrize(
    "text",
    [
        'Claude wraps a call in <invoke name="tool"> tags.',
        "A block opens with <function_calls> and closes later.",
    ],
)
def test_prose_about_the_xml_format_is_not_a_call(text):
    assert parse_text_tool_calls(text) is None


def test_xml_inside_code_is_an_example_not_a_call():
    text = 'The model emits `<invoke name="x">` like:\n```\n<invoke name="x"></invoke>\n```'
    assert parse_text_tool_calls(text) is None


def test_hermes_json_call():
    parsed = parse_text_tool_calls(
        'ok <tool_call>{"name": "grep", "arguments": {"pattern": "def"}}</tool_call>'
    )
    assert (parsed.calls[0].name, parsed.calls[0].args) == ("grep", {"pattern": "def"})
    assert parsed.prose == "ok"


def test_hermes_arguments_as_a_json_string():
    parsed = parse_text_tool_calls(
        '<tool_call>{"name": "g", "arguments": "{\\"a\\": 1}"}</tool_call>'
    )
    assert parsed.calls[0].args == {"a": 1}


@pytest.mark.parametrize(
    "text",
    [
        "<tool_call>{not json}</tool_call>",
        '<tool_call>{"arguments": {}}</tool_call>',
        '<tool_call>{"name": "g", "arguments": [1]}</tool_call>',
        '<tool_call>{"name": "g"',
    ],
)
def test_bad_hermes_calls_are_malformed(text):
    with pytest.raises(MalformedToolMarkup):
        parse_text_tool_calls(text)


def test_plain_answers_have_no_calls():
    assert parse_text_tool_calls("Approve. The fix is correct.") is None
    assert parse_text_tool_calls("") is None


def test_deepseek_dialect_still_parses():
    text = (
        '<｜DSML｜function_calls><｜DSML｜invoke name="run_python">'
        '<｜DSML｜parameter name="code" string="true">print(1)</｜DSML｜parameter>'
        "</｜DSML｜invoke></｜DSML｜function_calls>"
    )
    assert parse_text_tool_calls(text).calls[0].args == {"code": "print(1)"}


def test_markup_detection_and_stripping_agree_with_parsing():
    assert has_tool_markup(XML)
    assert strip_tool_markup(XML) == "Reading it now."


# ---------------------------------------------------------------------------
# Agent-side handling
# ---------------------------------------------------------------------------


def test_xml_values_are_typed_against_the_tool_schema():
    from gaia.agents.base.agent import _typed_markup_args

    declared = {
        "limit": {"type": "integer"},
        "flags": {"type": "array"},
        "path": {"type": "string"},
        "deep": {"type": "boolean"},
    }
    args = {"limit": "80", "flags": '["a"]', "path": "80", "deep": "true", "extra": "x"}
    assert _typed_markup_args(args, declared) == {
        "limit": 80,
        "flags": ["a"],
        "path": "80",
        "deep": True,
        "extra": "x",
    }


def test_a_value_that_is_not_json_for_its_type_stays_text_for_the_tool_to_reject():
    from gaia.agents.base.agent import _typed_markup_args

    assert _typed_markup_args({"limit": "eighty"}, {"limit": {"type": "integer"}}) == {
        "limit": "eighty"
    }


@pytest.mark.parametrize(
    "text, complete",
    [
        ('<invoke name="a"><parameter name="p">1</parameter></invoke>', True),
        ('<invoke name="a"><parameter name="p">half', False),
        ("A long answer the model never finished because it ran out of", False),
    ],
)
def test_only_a_complete_text_call_survives_a_length_stop(text, complete):
    from gaia.agents.base.agent import _holds_complete_text_call

    assert _holds_complete_text_call(text) is complete


def test_a_later_invoke_inside_a_code_fence_is_an_example_not_a_call():
    text = (
        '<invoke name="read_file"><parameter name="file_path">a</parameter></invoke>\n'
        "For example:\n```\n"
        '<invoke name="run_shell_command"><parameter name="command">rm -rf x</parameter></invoke>\n'
        "```"
    )
    assert [c.name for c in parse_text_tool_calls(text).calls] == ["read_file"]


# ---------------------------------------------------------------------------
# Adversarial: text that only looks like a call never runs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Here is how a call looks:\n```xml\n"
        '<invoke name="run_shell_command">'
        '<parameter name="command">rm -rf ~</parameter></invoke>\n```',
        "~~~\n<function_calls>\n"
        '<invoke name="write_file"><parameter name="file_path">x</parameter>'
        "</invoke>\n</function_calls>\n~~~",
        '```\n<tool_call>{"name": "write_file", "arguments": {}}</tool_call>\n```',
    ],
)
def test_a_complete_call_inside_a_code_fence_is_never_run(text):
    from gaia.agents.base.agent import _holds_complete_text_call

    assert parse_text_tool_calls(text) is None
    assert _holds_complete_text_call(text) is False


@pytest.mark.parametrize(
    "text",
    [
        'Call read_file(file_path="a.py") to see it.',
        "The write_file() tool overwrites; edit_file (v2) patches.",
        "Use `run_shell_command(command='ls')` from the shell.",
    ],
)
def test_prose_naming_a_tool_with_parentheses_is_not_a_text_call(text):
    assert parse_text_tool_calls(text) is None
