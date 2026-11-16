# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Long JSON answers must survive parsing and streaming intact.

A model answering in ``{"thought": ..., "answer": "..."}`` form often emits
JSON that strict ``json.loads`` rejects: raw newlines inside the answer, or an
object cut off by the output cap. The answer must then be decoded with its
escapes honoured — never cut at the first ``\\"`` — and streamed as clean text
with no literal ``\\n`` and no trailing ``"}``.
"""

import json

import pytest

from gaia.agents.base.agent import TRUNCATED_ANSWER_NOTE, Agent
from gaia.ui.sse_handler import SSEOutputHandler, _clean_answer_json
from gaia.utils.json_string import (
    JsonStringStreamDecoder,
    decode_json_string_prefix,
    extract_json_string_field,
)

# A long-form answer as a model writes it: markdown headings, embedded quotes,
# an em dash and an emoji, Windows path.
ESSAY = (
    "## The Printing Press\n\n"
    'The press became the "engine" of the Reformation \u2014 pamphlets '
    'spread faster than any "heresy" could be suppressed.\n\n'
    "## Legacy\n\n"
    "Literacy rose sharply \U0001f4da, and archives such as C:\\Users\\scribe "
    "kept the record."
)


def _escaped(text: str) -> str:
    """The JSON string body a model emits for ``text`` (no outer quotes)."""
    return json.dumps(text)[1:-1]


def _with_raw_newlines(text: str) -> str:
    """JSON body with literal newlines, as models commonly emit it."""
    return _escaped(text).replace("\\n", "\n")


def _bare_agent():
    class _ConcreteAgent(Agent):
        def _get_system_prompt(self):
            return ""

        def _register_tools(self):
            return None

    obj = _ConcreteAgent.__new__(_ConcreteAgent)
    obj.model_id = None
    obj.error_history = []
    obj.api_mode = False
    return obj


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _chunk_text(events):
    return "".join(e["content"] for e in events if e and e["type"] == "chunk")


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------


class TestDecodeJsonStringPrefix:
    def test_escaped_quotes_do_not_end_the_value(self):
        decoded, consumed, closed = decode_json_string_prefix(
            r'say \"hi\" twice", "goal": "x"}'
        )
        assert decoded == 'say "hi" twice'
        assert closed
        assert consumed == len(r'say \"hi\" twice"')

    def test_matches_json_loads_on_valid_strings(self):
        body = _escaped(ESSAY)
        decoded, _, closed = decode_json_string_prefix(body + '"')
        assert closed
        assert decoded == ESSAY

    def test_unicode_and_surrogate_pair(self):
        decoded, _, _ = decode_json_string_prefix(r'a\u2014b \ud83d\ude00"')
        assert decoded == "a\u2014b \U0001f600"

    def test_lone_surrogate_becomes_replacement_char(self):
        decoded, _, closed = decode_json_string_prefix(r'x\ud83dy"')
        assert closed
        assert decoded == "x\ufffdy"

    def test_invalid_escape_kept_verbatim(self):
        decoded, _, _ = decode_json_string_prefix(r'C:\Users\me"')
        assert decoded == r"C:\Users\me"

    def test_raw_newlines_kept(self):
        decoded, _, _ = decode_json_string_prefix('line 1\nline 2"')
        assert decoded == "line 1\nline 2"

    @pytest.mark.parametrize(
        "partial", ["abc\\", "abc\\u20", "abc\\ud83d", "abc\\ud83d\\u"]
    )
    def test_incomplete_trailing_escape_left_unconsumed(self, partial):
        decoded, consumed, closed = decode_json_string_prefix(partial)
        assert decoded == "abc"
        assert consumed == 3
        assert not closed

    def test_truncated_value_reports_not_closed(self):
        decoded, _, closed = decode_json_string_prefix('The press became the \\"engine')
        assert decoded == 'The press became the "engine'
        assert not closed


class TestExtractJsonStringField:
    def test_ignores_escaped_key_inside_another_value(self):
        text = r'{"thought": "write the \"answer\": \"no\"", "answer": "yes"}'
        assert extract_json_string_field(text, "answer") == ("yes", True)

    def test_missing_key(self):
        assert extract_json_string_field('{"thought": "x"}', "answer") is None

    def test_unescaped_inner_quotes_kept(self):
        text = '{"thought": "t", "answer": "The press became the "engine" of it.\n"}'
        assert extract_json_string_field(text, "answer") == (
            'The press became the "engine" of it.\n',
            True,
        )

    def test_unescaped_quote_before_comma_is_not_the_close(self):
        text = '{"answer": "Pick "red", "green" or "blue".", "goal": "g"}'
        assert extract_json_string_field(text, "answer") == (
            'Pick "red", "green" or "blue".',
            True,
        )

    def test_value_followed_by_next_key_closes(self):
        text = '{"thought": "plan it", "answer": "done"'
        assert extract_json_string_field(text, "thought") == ("plan it", True)

    def test_truncated_after_unescaped_quote_is_incomplete(self):
        text = '{"answer": "The press became the "engine'
        assert extract_json_string_field(text, "answer") == (
            'The press became the "engine',
            False,
        )

    def test_trailing_bare_backslash_is_not_an_escaped_quote(self):
        text = r'{"answer": "Saved to C:\Users\me\"}'
        assert extract_json_string_field(text, "answer") == (
            "Saved to C:\\Users\\me\\",
            True,
        )


class TestStreamDecoder:
    def test_every_split_point_decodes_identically(self):
        raw = _escaped(ESSAY) + '"}'
        for cut in range(len(raw) + 1):
            dec = JsonStringStreamDecoder()
            out = dec.feed(raw[:cut]) + dec.feed(raw[cut:])
            assert out == ESSAY, f"split at {cut}"
            assert dec.closed
            assert dec.tail == "}"

    def test_text_after_close_goes_to_tail(self):
        dec = JsonStringStreamDecoder()
        assert dec.feed('done"') == "done"
        assert dec.feed("}\n") == ""
        assert dec.tail == "}\n"


# ---------------------------------------------------------------------------
# Agent._parse_llm_response — the non-streaming final answer
# ---------------------------------------------------------------------------


class TestParseLongAnswer:
    def test_raw_newlines_and_quotes_keep_whole_answer(self):
        response = (
            '{"thought": "Write the essay", "goal": "essay", '
            f'"answer": "{_with_raw_newlines(ESSAY)}"}}'
        )
        with pytest.raises(json.JSONDecodeError):
            json.loads(response)
        assert _bare_agent()._parse_llm_response(response)["answer"] == ESSAY

    def test_trailing_junk_keeps_whole_answer(self):
        response = (
            f'{{"thought": "t", "answer": "{_with_raw_newlines(ESSAY)}"}}\n'
            "Hope this helps!"
        )
        assert _bare_agent()._parse_llm_response(response)["answer"] == ESSAY

    def test_truncated_json_keeps_everything_and_says_so(self):
        body = _escaped(ESSAY)
        cut = body.index("heresy") + len("heresy")
        response = '{"thought": "Write it", "answer": "' + body[:cut]
        parsed = _bare_agent()._parse_llm_response(response)
        assert parsed["answer"].startswith(
            '## The Printing Press\n\nThe press became the "engine" of the '
            'Reformation \u2014 pamphlets spread faster than any "heresy'
        )
        assert parsed["answer"].endswith(TRUNCATED_ANSWER_NOTE)
        assert "\\n" not in parsed["answer"]
        assert parsed["thought"] == "Write it"

    def test_unescaped_quotes_keep_whole_answer(self):
        response = (
            '{"thought": "t", "answer": "The press became the "engine" of '
            'the Reformation.\n\nIt spread "heresy" fast."}'
        )
        assert _bare_agent()._parse_llm_response(response)["answer"] == (
            'The press became the "engine" of the Reformation.\n\n'
            'It spread "heresy" fast.'
        )

    def test_complete_answer_never_gets_truncation_note(self):
        response = r'{"thought": "t", "answer": "Saved it to C:\Users\me\"}'
        parsed = _bare_agent()._parse_llm_response(response)
        assert parsed["answer"] == "Saved it to C:\\Users\\me\\"

    def test_truncated_mid_escape_drops_only_the_partial_escape(self):
        response = '{"answer": "Caf\\u00e9 and \\u00'
        parsed = _bare_agent()._parse_llm_response(response)
        assert parsed["answer"] == "Caf\u00e9 and" + TRUNCATED_ANSWER_NOTE

    def test_tool_call_still_wins_over_answer(self):
        response = (
            '{"thought": "look \\"first\\"", "tool": "search_file", '
            '"tool_args": {"file_pattern": "*.pdf"}, "answer": "I will search\n"}'
            "garbage"
        )
        parsed = _bare_agent()._parse_llm_response(response)
        assert parsed["tool"] == "search_file"
        assert parsed["tool_args"] == {"file_pattern": "*.pdf"}
        assert parsed["thought"] == 'look "first"'


# ---------------------------------------------------------------------------
# SSEOutputHandler — the streamed answer
# ---------------------------------------------------------------------------


def _stream(handler, text, size):
    for i in range(0, len(text), size):
        handler.print_streaming_text(text[i : i + size])


class TestStreamedJsonAnswer:
    @pytest.mark.parametrize("size", [1, 3, 7, 64])
    def test_long_answer_streams_clean_from_the_start(self, size):
        # Longer than the 4 KB safety limit that used to drop the opening.
        essay = "\n\n".join([ESSAY] * 20)
        assert len(essay) > 4096
        raw = (
            '{"thought": "Write it", "goal": "essay", "answer": "'
            + _escaped(essay)
            + '"}'
        )
        handler = SSEOutputHandler()
        _stream(handler, raw, size)
        handler.print_streaming_text("", end_of_stream=True)
        text = _chunk_text(_drain(handler))
        assert text == essay
        assert "\\n" not in text
        assert not text.endswith('"}')

    def test_stop_mid_answer_keeps_clean_partial_text(self):
        body = _escaped(ESSAY)
        cut = body.index("\\u2014") + 3  # stop inside the \u2014 escape
        handler = SSEOutputHandler()
        _stream(handler, '{"thought": "t", "answer": "' + body[:cut], 5)
        handler.signal_done()
        events = _drain(handler)
        assert events[-1] is None
        assert _chunk_text(events) == (
            '## The Printing Press\n\nThe press became the "engine" of the Reformation '
        )

    def test_next_stream_is_not_treated_as_answer(self):
        handler = SSEOutputHandler()
        handler.print_streaming_text('{"answer": "first"}')
        handler.print_streaming_text("", end_of_stream=True)
        handler.print_streaming_text("Plain second reply.")
        handler.print_streaming_text("", end_of_stream=True)
        chunks = [e["content"] for e in _drain(handler) if e and e["type"] == "chunk"]
        assert chunks == ["first", "Plain second reply."]

    def test_final_answer_event_matches_streamed_text(self):
        raw = '{"thought": "t", "answer": "' + _with_raw_newlines(ESSAY) + '"}'
        handler = SSEOutputHandler()
        handler.print_final_answer(raw)
        answer = [e for e in _drain(handler) if e and e["type"] == "answer"][0]
        assert answer["content"] == ESSAY


class TestCleanAnswerJson:
    def test_thought_before_answer_with_raw_newlines(self):
        raw = '{"thought": "t", "answer": "' + _with_raw_newlines(ESSAY) + '"}'
        assert _clean_answer_json(raw) == ESSAY

    def test_truncated_envelope_keeps_received_text(self):
        assert _clean_answer_json('{"answer": "He said \\"wait') == 'He said "wait'

    def test_valid_json_without_top_level_answer_unchanged(self):
        text = '{"tool": "reply", "tool_args": {"answer": "hi"}}'
        assert _clean_answer_json(text) == text
