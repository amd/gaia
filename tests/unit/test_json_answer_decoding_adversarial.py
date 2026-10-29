# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Hostile model output for JSON answer parsing and streaming.

Unescaped quotes, trailing backslashes, Windows paths, JSON inside the answer,
an ``"answer"`` key quoted inside another value, two objects back to back,
trailing garbage, real versus apparent truncation, a tool call wearing an
answer's clothes, and a 1 MB answer.
"""

import json
import time

import pytest

from gaia.agents.base.agent import TRUNCATED_ANSWER_NOTE
from gaia.ui.sse_handler import SSEOutputHandler, _clean_answer_json
from gaia.utils.json_string import (
    JsonStringStreamDecoder,
    decode_json_string_prefix,
    extract_json_string_field,
)
from tests.unit.test_json_answer_decoding import _bare_agent, _chunk_text, _drain


def _parse(response):
    return _bare_agent()._parse_llm_response(response)


def _answer(response):
    return _parse(response)["answer"]


# ── Unescaped quotes ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "response, expected",
    [
        ('{"answer": "a "b" c"}', 'a "b" c'),
        ('{"answer": ""quoted at start""}', '"quoted at start"'),
        ('{"answer": "ends with "quote""}', 'ends with "quote"'),
        ('{"answer": "only " one"}', 'only " one'),
        ('{"answer": "x\n"y"\nz"}', 'x\n"y"\nz'),
    ],
)
def test_unescaped_quotes_never_truncate_the_answer(response, expected):
    answer = _answer(response)
    assert answer.startswith(expected[:3])
    assert TRUNCATED_ANSWER_NOTE not in answer
    assert answer == expected


# ── Backslashes and Windows paths ────────────────────────────────────────


@pytest.mark.parametrize(
    "response, expected",
    [
        (r'{"answer": "C:\Users\me\file.txt"}', "C:\\Users\\me\\file.txt"),
        (r'{"answer": "C:\\Users\\me"}', "C:\\Users\\me"),
        (r'{"answer": "dir C:\temp\"}', "dir C:\\temp\\"),
        (r'{"answer": "UNC \\server\share\"}', "UNC \\server\\share\\"),
        (r'{"answer": "\x41 \q \U0001"}', "\\x41 \\q \\U0001"),
        ('{"answer": "two \\\\\\\\ slashes"}', "two \\\\ slashes"),
    ],
)
def test_backslashes_and_paths_survive(response, expected):
    assert _answer(response) == expected


@pytest.mark.parametrize(
    "tail, kept",
    [
        ("\\", ""),
        ("\\\\\\", "\\"),  # an escaped backslash, then a lone one
        ("\\u", ""),
        ("\\u00", ""),
        ("\\ud83d", ""),
        ("\\ud83d\\u", ""),
    ],
)
def test_cut_off_on_a_partial_escape_is_truncated_not_garbled(tail, kept):
    answer = _answer('{"answer": "keep this' + tail)
    assert answer == "keep this" + kept + TRUNCATED_ANSWER_NOTE


def test_unescaped_path_keeps_backslashes_but_prose_newlines_still_decode():
    response = (
        r'{"answer": "Saved to C:\Users\me\new\file.txt and done.\nNext: C:\temp"}'
    )
    assert _answer(response) == (
        "Saved to C:\\Users\\me\\new\\file.txt and done.\nNext: C:\\temp"
    )


def test_properly_escaped_path_in_a_cut_off_answer_still_decodes_newlines():
    answer = _answer(r'{"answer": "Open C:\\temp\nthen the next line')
    assert answer == "Open C:\\temp\nthen the next line" + TRUNCATED_ANSWER_NOTE


# ── JSON inside the answer ───────────────────────────────────────────────


def test_escaped_json_inside_the_answer_is_kept_whole():
    inner = {"tool": "delete_file", "tool_args": {"path": "C:/x"}, "answer": "no"}
    text = "Example payload:\n" + json.dumps(inner, indent=2)
    response = json.dumps({"thought": "t", "answer": text})
    parsed = _parse(response)
    assert parsed["answer"] == text
    assert "tool" not in parsed


def test_escaped_json_inside_a_malformed_answer_is_not_a_tool_call():
    """Raw newlines make the envelope invalid; the escaped tool JSON quoted in
    the answer must not be picked up as a call to delete_file."""
    inner = json.dumps({"tool": "delete_file", "tool_args": {"path": "C:/x"}})
    body = json.dumps("Never run this:\n" + inner)[1:-1].replace("\\n", "\n")
    response = '{"thought": "t", "answer": "' + body + '"}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(response)
    parsed = _parse(response)
    assert "tool" not in parsed
    assert parsed["answer"] == "Never run this:\n" + inner


def test_answer_key_quoted_inside_thought_is_not_the_answer():
    response = (
        '{"thought": "I will set \\"answer\\": \\"decoy\\" later\n", '
        '"answer": "the real one"}'
    )
    assert _parse(response)["answer"] == "the real one"


def test_answer_key_inside_tool_args_is_not_an_answer():
    response = json.dumps(
        {"tool": "reply", "tool_args": {"answer": "not a final answer"}}
    )
    parsed = _parse(response)
    assert parsed.get("tool") == "reply"
    assert "answer" not in parsed


# ── Several objects, trailing garbage ────────────────────────────────────


@pytest.mark.parametrize(
    "response",
    [
        '{"answer": "first"}{"answer": "second"}',
        '{"answer": "first"}\n{"answer": "second"}',
        '{"answer": "first"} trailing words "with quotes" }}}',
        '{"answer": "first"}\n```',
    ],
)
def test_first_complete_answer_wins_and_garbage_is_dropped(response):
    answer = _answer(response)
    assert answer == "first"


# ── Truncation note only when really cut off ─────────────────────────────


@pytest.mark.parametrize(
    "response",
    [
        '{"answer": "done"}',
        '{"answer": "done"',  # closing brace missing, value closed
        '{"answer": "done"   ',
        '{"answer": "done", "thought": "unterminated',
        '{"thought": "x", "answer": "done",\n"goal": "g"}',
        '{"answer": "done"}\n\n',
        '{"answer": "done\n"}',
    ],
)
def test_complete_answers_never_get_the_cut_off_note(response):
    assert TRUNCATED_ANSWER_NOTE not in _answer(response)


@pytest.mark.parametrize(
    "response",
    [
        '{"answer": "half a sent',
        '{"answer": "unterminated with "inner" quotes',
        '{"answer": "ends on a space ',
        '{"thought": "t", "answer": "x\n\n## Heading\n- item',
    ],
)
def test_really_cut_off_answers_get_the_note_once(response):
    answer = _answer(response)
    assert answer.count(TRUNCATED_ANSWER_NOTE) == 1
    assert answer.endswith(TRUNCATED_ANSWER_NOTE)


# ── Tool call that looks like an answer ──────────────────────────────────


def test_malformed_tool_call_with_answer_text_stays_a_tool_call():
    response = (
        '{"thought": "need data\n", "tool": "read_file", '
        '"tool_args": {"path": "a.txt"}, "answer": "I will read "a.txt" first"}'
    )
    parsed = _parse(response)
    assert parsed["tool"] == "read_file"
    assert parsed["tool_args"] == {"path": "a.txt"}


def test_answer_naming_a_tool_in_prose_is_still_an_answer():
    response = '{"answer": "Use the "tool" called search_file\n to find it"}'
    parsed = _parse(response)
    assert "tool" not in parsed
    assert parsed["answer"] == 'Use the "tool" called search_file\n to find it'


# ── Size ─────────────────────────────────────────────────────────────────


def test_one_megabyte_answer_parses_whole_and_fast():
    text = ('Line with a "quote" and C:\\path\\ \u2014 \U0001f4da\n' * 20000)[: 1 << 20]
    body = json.dumps(text)[1:-1].replace("\\n", "\n")
    response = '{"thought": "t", "answer": "' + body + '"}'
    start = time.monotonic()
    answer = _answer(response)
    assert time.monotonic() - start < 10
    assert answer == text


def test_one_megabyte_truncated_answer_parses_fast():
    body = json.dumps('x " y \\ ' * 150_000)[1:-1]
    start = time.monotonic()
    answer = _answer('{"answer": "' + body)
    assert time.monotonic() - start < 10
    assert answer.endswith(TRUNCATED_ANSWER_NOTE)


def test_many_unescaped_quotes_do_not_go_quadratic():
    text = 'a "b" ' * 50_000
    start = time.monotonic()
    field = extract_json_string_field('{"answer": "' + text + '"}', "answer")
    assert time.monotonic() - start < 10
    assert field == (text, True)


# ── Streaming the same hostile shapes ────────────────────────────────────


def _stream_all(raw, size):
    handler = SSEOutputHandler()
    for i in range(0, len(raw), size):
        handler.print_streaming_text(raw[i : i + size])
    handler.print_streaming_text("", end_of_stream=True)
    return _chunk_text(_drain(handler))


@pytest.mark.parametrize("size", [1, 2, 5, 13])
@pytest.mark.parametrize(
    "text",
    [
        "C:\\Users\\me\\",
        'He said "hi"',
        "emoji \U0001f600 and \u00e9",
        '{"tool": "delete_file"}',
        "ends with backslash \\",
    ],
)
def test_streamed_valid_answer_matches_its_value_at_every_split(size, text):
    raw = json.dumps({"answer": text})
    assert _stream_all(raw, size) == text


def test_stream_never_leaks_closing_structure_or_trailing_garbage():
    out = _stream_all('{"answer": "done"} and then {"answer": "again"}', 3)
    assert out == "done"


@pytest.mark.parametrize("size", [1, 4, 9])
def test_stream_decoder_matches_json_loads_for_hostile_strings(size):
    for text in ['"', "\\", '\\\\"', "\u2028\u2029", "\x00\x1f", "\ud7ff\ue000"]:
        raw = json.dumps(text)[1:] + "}"
        decoder = JsonStringStreamDecoder()
        out = "".join(decoder.feed(raw[i : i + size]) for i in range(0, len(raw), size))
        assert out == text
        assert decoder.closed and decoder.tail == "}"


def test_decode_prefix_of_empty_and_quote_only():
    assert decode_json_string_prefix("") == ("", 0, False)
    assert decode_json_string_prefix('"') == ("", 1, True)


# ── _clean_answer_json on the final-answer path ──────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ('{"answer": 42}', '{"answer": 42}'),
        ('{"answer": null}', '{"answer": null}'),
        ('{"answer": ["a"]}', '{"answer": ["a"]}'),
        ('{"answer": "ok"} extra', "ok"),
        ("not json at all", "not json at all"),
        ("", ""),
    ],
)
def test_clean_answer_json_leaves_non_string_answers_alone(raw, expected):
    assert _clean_answer_json(raw) == expected
