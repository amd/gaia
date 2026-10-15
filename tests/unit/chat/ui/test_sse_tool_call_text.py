# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool call the model writes as text never streams into the chat.

The fixtures reproduce the shapes a hosted model sent while the Agent UI showed
them raw: DeepSeek DSML markup (fullwidth bar, a space before the tag name),
one call after another; a narration line and an envelope in the same token; a
long "thought" with an unescaped quote; and long calls that used to sit behind
a bare timer.
"""

import time

import pytest

from gaia.ui import sse_handler as sse_mod
from gaia.ui.sse_handler import SSEOutputHandler


@pytest.fixture
def handler():
    return SSEOutputHandler()


def _drain(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _feed(handler, chunks, end=True):
    for chunk in chunks:
        handler.print_streaming_text(chunk)
    if end:
        handler.print_streaming_text("", end_of_stream=True)
    return _drain(handler)


def _shown(events):
    return "".join(
        e["content"] for e in events if e and e["type"] in ("chunk", "answer")
    )


def _status(events):
    return [e for e in events if e and e["type"] == "status"]


def _pieces(text, size):
    return [text[i : i + size] for i in range(0, len(text), size)]


_CODE = (
    "import openpyxl, os\n"
    "from collections import Counter\n\n"
    'base = r"C:\\Users\\me\\work\\invoices"\n\n'
    "# Rate card\n"
    'rc = openpyxl.load_workbook(os.path.join(base, "rates.xlsx"))["Rates"]\n'
    "rates = {}\n"
    "for row in rc.iter_rows(min_row=2, values_only=True):\n"
    "    if row[0] is None: continue\n"
    '    rates[row[1].strip()] = {"y2": row[2]}\n'
    'print("TOTAL", round(sum(r["y2"] for r in rates.values()), 2))\n'
)


def _dsml(tool="run_python", param="code", value=_CODE, bar="｜", space=" "):
    return (
        f"<{bar}DSML{bar}{space}calls>\n"
        f'<{bar}DSML{bar}{space}invoke name="{tool}">\n'
        f'<{bar}DSML{bar}{space}parameter name="{param}" string="true">'
        f"{value}</{bar}DSML{bar}{space}parameter>\n"
        f"</{bar}DSML{bar}{space}invoke>\n"
        f"</{bar}DSML{bar}{space}calls>"
    )


_LEAK_MARKERS = ("DSML", "invoke", "parameter", "openpyxl", "# Rate card", "tool_args")


def _assert_clean(text):
    for marker in _LEAK_MARKERS:
        assert marker not in text, f"{marker!r} leaked: {text[:200]!r}"


class TestNativeMarkupNeverStreams:
    @pytest.mark.parametrize("size", [1, 2, 3, 7, 16, 64, 10_000])
    def test_dsml_call_split_any_way_is_dropped(self, handler, size):
        events = _feed(handler, _pieces(_dsml(), size))
        assert _shown(events) == ""

    @pytest.mark.parametrize(
        "bar,space", [("｜", " "), ("｜", ""), ("|", ""), ("|", " ")]
    )
    def test_both_bars_with_and_without_the_space(self, handler, bar, space):
        events = _feed(handler, _pieces(_dsml(bar=bar, space=space), 5))
        assert _shown(events) == ""

    def test_fifty_calls_in_one_turn_never_show(self, handler):
        """One model call per tool call, as the agent loop streams them."""
        shown = ""
        for i in range(50):
            text = f"Step {i}: checking the next file.\n\n" + _dsml(
                tool="read_file", param="file_path", value=f"C:\\data\\f{i}.xlsx"
            )
            shown += _shown(_feed(handler, _pieces(text, 4)))
        _assert_clean(shown)
        assert "Step 49: checking the next file." in shown

    def test_answer_after_a_call_in_the_same_reply_shows(self, handler):
        text = _dsml() + "\n\nDone. Three invoices are in `invoices/`."
        events = _feed(handler, _pieces(text, 6))
        assert _shown(events).strip() == "Done. Three invoices are in `invoices/`."

    def test_narration_before_a_call_shows_whole(self, handler):
        text = "I'll start by reading the rate card.\n\n" + _dsml()
        assert _shown(_feed(handler, _pieces(text, 3))).strip() == (
            "I'll start by reading the rate card."
        )

    def test_an_invoke_without_its_container_is_dropped(self, handler):
        text = (
            '<｜DSML｜invoke name="read_file">\n'
            '<｜DSML｜parameter name="file_path" string="true">a.txt</｜DSML｜parameter>\n'
            "</｜DSML｜invoke>\nOk."
        )
        assert _shown(_feed(handler, _pieces(text, 4))).strip() == "Ok."

    @pytest.mark.parametrize(
        "markup",
        [
            "<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>get_weather<｜tool▁sep｜>"
            '{"city": "Tokyo"}<｜tool▁call▁end｜><｜tool▁calls▁end｜>',
            '<tool_call>\n{"name": "read_file", "arguments": {"path": "a"}}\n</tool_call>',
            '<function_calls>\n<invoke name="read_file">\n<parameter name="path">a'
            "</parameter>\n</invoke>\n</function_calls>",
            '<invoke name="read_file">\n<parameter name="path">a</parameter>\n</invoke>',
            "<|start|>assistant<|channel|>commentary to=functions.read_file "
            '<|constrain|>json<|message|>{"path": "a"}<|call|>',
        ],
    )
    def test_other_native_formats_are_dropped(self, handler, markup):
        text = "Reading it.\n" + markup + "\nAll set."
        for size in (1, 5, len(text)):
            shown = _shown(_feed(SSEOutputHandler(), _pieces(text, size)))
            assert shown.replace("\n", "") == "Reading it.All set.", (size, shown)

    def test_unterminated_call_at_end_of_stream_is_dropped(self, handler):
        cut = _dsml()[:-40]
        assert _shown(_feed(handler, _pieces(cut, 9))) == ""
        # The next model call is not blinded by the one that was cut off.
        assert _shown(_feed(handler, ["Here is the answer."])) == "Here is the answer."

    def test_stop_mid_call_drops_it_at_done(self, handler):
        handler.print_streaming_text(_dsml()[:200])
        handler.signal_done()
        assert _drain(handler) == [None]

    def test_stop_with_a_held_tag_tail_still_shows_the_text(self, handler):
        handler.print_streaming_text("Use a <")
        handler.signal_done()
        events = _drain(handler)
        assert _shown(events[:-1]) == "Use a <" and events[-1] is None

    def test_stray_tags_from_a_half_seen_call_are_removed(self, handler):
        text = "</｜DSML｜ parameter>\n</｜DSML｜ invoke>\n</｜DSML｜ calls>\nFinished."
        assert _shown(_feed(handler, _pieces(text, 3))).strip() == "Finished."

    def test_huge_call_token_by_token_is_not_quadratic(self, handler):
        big = _dsml(value="x = 1  # <tag> \n" * 25_000)
        started = time.monotonic()
        assert _shown(_feed(handler, _pieces(big, 4))) == ""
        assert time.monotonic() - started < 5


class TestOrdinaryTextIsUntouched:
    @pytest.mark.parametrize(
        "text",
        [
            "If a < b and b < c then a < c.",
            "Use <br> for a line break, and <b>bold</b> for emphasis.",
            "The ratio was <1% in Q3 and <2% in Q4.",
            "Generics look like List<String> in Java.",
            "Ends with a lone angle bracket <",
            "Ends with a backtick `",
        ],
    )
    @pytest.mark.parametrize("size", [1, 3, 1000])
    def test_angle_brackets_and_backticks_pass_through(self, handler, text, size):
        assert _shown(_feed(handler, _pieces(text, size))) == text

    @pytest.mark.parametrize("size", [1, 4, 1000])
    def test_markup_quoted_in_a_code_block_is_an_example(self, handler, size):
        text = (
            "DeepSeek writes a call like this:\n\n```xml\n"
            + _dsml(tool="read_file", param="file_path", value="a.txt")
            + "\n```\n\nThat is all."
        )
        assert _shown(_feed(handler, _pieces(text, size))) == text

    def test_code_block_state_resets_between_model_calls(self, handler):
        """An unclosed fence in one reply must not shield the next reply's call."""
        _feed(handler, ["```python\nprint(1)\n"])
        assert _shown(_feed(handler, _pieces(_dsml(), 5))) == ""

    def test_envelope_quoting_markup_is_dropped_whole(self, handler):
        envelope = (
            '{"thought": "t", "tool": "write_file", "tool_args": {"content": '
            '"DeepSeek emits <｜DSML｜invoke name=\\"x\\"> and </｜DSML｜invoke>."}}'
        )
        assert _shown(_feed(handler, _pieces(envelope, 5))) == ""


class TestEnvelopeShapesSeenLive:
    def test_narration_and_envelope_in_one_token(self, handler):
        """The token boundary fell between the prose and the envelope's first key."""
        chunks = [
            "I",
            "'ll start by exploring the directory",
            " structure to understand what we're",
            ' working with.\n\n{"',
            "thought",
            '": "Let',
            " me explore the working directory.",
            '", "goal": "Discover the files", "tool": "tree", "tool_args": ',
            '{"path": "C:\\\\work", "max_depth": 3}}',
        ]
        shown = _shown(_feed(handler, chunks))
        assert shown.strip() == (
            "I'll start by exploring the directory structure to understand what "
            "we're working with."
        )

    def test_envelope_with_an_unescaped_quote_is_held_whole(self, handler):
        """Invalid JSON still led by "thought" is protocol, not prose."""
        thought = (
            'Playbook topic 2: the playbook says "2-3x the general cap" here. ' * 300
        )
        # The model forgot to escape the quotes, so this is not valid JSON.
        envelope = (
            '{"thought": "' + thought + '", "tool": "run_python", "tool_args": '
            '{"code": "wb.save(path)\\nprint(\\"saved\\")\\n"}}'
        )
        events = _feed(handler, _pieces(envelope, 20))
        assert _shown(events) == ""
        assert _shown(_feed(handler, ["Saved the table."])) == "Saved the table."

    @pytest.mark.parametrize(
        "text",
        [
            '{"name": "a "quoted" word"}',
            '{"answer is": 42',
            'Config:\n```json\n{"thought": "x", "tool": "y", "tool_args": {}}\n```',
        ],
    )
    def test_broken_or_quoted_json_that_is_not_protocol_shows(self, handler, text):
        assert _shown(_feed(handler, _pieces(text, 4))) == text

    def test_plan_envelope_tail_never_shows(self, handler):
        envelope = (
            '{"thought": "Search results confirm the author.", '
            '"goal": "Verify facts from primary sources", "plan": ['
            '{"tool": "fetch_page", "tool_args": {"url": "https://example.org/a", '
            '"extract": "text", "max_length": 2000}}, '
            '{"tool": "fetch_page", "tool_args": {"url": "https://example.org/b", '
            '"extract": "text", "max_length": 2000}}], '
            '"tool": "fetch_page", "tool_args": {"url": "https://example.org/a", '
            '"extract": "text", "max_length": 2000}}'
        )
        for size in (1, 3, 18, len(envelope)):
            fresh = SSEOutputHandler()
            assert _shown(_feed(fresh, _pieces(envelope, size))) == "", size


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(sse_mod.time, "monotonic", fake)
    return fake


def _feed_ticking(handler, clock, chunks, step=0.5):
    events = []
    for chunk in chunks:
        clock.now += step
        handler.print_streaming_text(chunk)
        events += _drain(handler)
    handler.print_streaming_text("", end_of_stream=True)
    return events + _drain(handler)


class TestHeldCallsReportProgress:
    def test_long_markup_call_reports_its_kind_and_size(self, handler, clock):
        text = _dsml(value=_CODE * 20)
        events = _feed_ticking(handler, clock, _pieces(text, 40))
        progress = [e for e in _status(events) if e.get("phase") == "tool_call"]
        assert len(progress) >= 2
        assert all(e["message"].startswith("Writing Python code — ") for e in progress)
        assert progress[-1]["chars"] > progress[0]["chars"]
        for e in progress:
            _assert_clean(e["message"])
        assert _shown(events) == ""

    def test_progress_is_throttled_not_per_token(self, handler, clock):
        text = _dsml(value=_CODE * 20)
        events = _feed_ticking(handler, clock, _pieces(text, 4), step=0.01)
        # ~2,700 tokens over ~27 s: one report per 3 s, not one per token.
        assert 3 <= len(_status(events)) <= 12

    def test_short_call_reports_nothing(self, handler, clock):
        text = _dsml(tool="read_file", param="file_path", value="a.txt")
        assert _status(_feed_ticking(handler, clock, _pieces(text, 4))) == []

    def test_long_envelope_reports_reasoning_then_the_tool(self, handler, clock):
        thought = "Weighing clause 7 against the playbook position. " * 80
        content = "row,issue\\n" * 400
        envelope = (
            '{"thought": "' + thought + '", "tool": "write_file", "tool_args": '
            '{"file_path": "review.csv", "content": "' + content + '"}}'
        )
        events = _feed_ticking(handler, clock, _pieces(envelope, 40))
        messages = [e["message"] for e in _status(events)]
        assert any(m.startswith("Reasoning — ") for m in messages), messages
        assert any(m.startswith("Writing a file — ") for m in messages), messages
        assert not any("clause 7" in m or "row,issue" in m for m in messages)
        assert _shown(events) == ""


def test_opener_as_the_last_token_does_not_eat_the_next_reply(handler):
    handler.print_streaming_text("Reading.\n<｜DSML｜ invoke name=")
    handler.print_streaming_text("", end_of_stream=True)
    _drain(handler)
    assert _shown(_feed(handler, ["Here you go."])) == "Here you go."
