# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The mood tag a model opens a reply with: taken out of the text, sent to the console."""

import json

import pytest

from gaia.agents.base.mood import (
    MOOD_PROMPT,
    MOODS,
    MoodFilter,
    strip_mood,
    without_mood,
)
from gaia.chat.sdk import AgentResponse


def _stream(text, sizes):
    """*text* fed through a filter in pieces of *sizes* characters, cycling."""
    heard = []
    mood_filter = MoodFilter(heard.append)
    shown, i, n = "", 0, 0
    while i < len(text):
        size = sizes[n % len(sizes)]
        shown += mood_filter.feed(text[i : i + size])
        i, n = i + size, n + 1
    return shown + mood_filter.flush(), heard


@pytest.mark.parametrize("sizes", [[1], [2], [3, 1], [5], [7, 2], [1000]])
def test_a_leading_tag_leaves_the_text_however_the_stream_is_cut(sizes):
    shown, heard = _stream("[mood:happy] Glad that worked!", sizes)
    assert shown == "Glad that worked!"
    assert heard == ["happy"]


@pytest.mark.parametrize("sizes", [[1], [4], [1000]])
def test_a_tag_on_its_own_line_takes_the_line_break_with_it(sizes):
    shown, heard = _stream("[mood:sad]\n\nI'm sorry to hear that.", sizes)
    assert shown == "I'm sorry to hear that."
    assert heard == ["sad"]


@pytest.mark.parametrize("sizes", [[1], [3], [1000]])
def test_a_tag_in_the_middle_keeps_the_line_breaks_around_it(sizes):
    shown, heard = _stream("Done.\n[mood:proud] All 12 tests pass.", sizes)
    assert shown == "Done.\nAll 12 tests pass."
    assert heard == ["proud"]


@pytest.mark.parametrize(
    "text",
    [
        "See [the docs](https://example.com) and [1].",
        "A list: [a, b] and [mood] and [mood:] and [mood:two words].",
        "items[0] = moods[mood:index]... [",
        "[m",
        "[mood:happy",
    ],
)
@pytest.mark.parametrize("sizes", [[1], [2], [1000]])
def test_text_that_is_not_a_tag_passes_through_unchanged(text, sizes):
    # "[mood:index]" is a tag by shape, so that one case loses it.
    expected = text.replace("[mood:index]", "")
    shown, heard = _stream(text, sizes)
    assert shown == expected
    assert heard == []


def test_case_and_spacing_inside_the_tag_do_not_matter():
    assert strip_mood("[Mood: Curious ] Tell me more.") == ("Tell me more.", "curious")


def test_a_word_outside_the_list_is_removed_but_not_reported():
    shown, heard = _stream("[mood:bored] Sure.", [3])
    assert shown == "Sure."
    assert heard == []


def test_a_reply_that_is_only_the_tag_is_empty():
    assert strip_mood("[mood:determined]") == ("", "determined")


def test_text_without_brackets_is_returned_as_is():
    text = "Nothing to see."
    assert strip_mood(text) == (text, None)


def test_a_tool_call_envelope_keeps_its_shape():
    calls = [{"id": "1", "function": {"name": "read_file", "arguments": "{}"}}]
    envelope = json.dumps(
        {
            "__tool_calls__": calls,
            "finish_reason": "tool_calls",
            "content": "[mood:curious] Let me look at that file.",
        }
    )
    cleaned, mood = strip_mood(envelope)
    assert mood == "curious"
    assert json.loads(cleaned) == {
        "__tool_calls__": calls,
        "finish_reason": "tool_calls",
        "content": "Let me look at that file.",
    }


def test_an_envelope_with_no_tag_is_the_same_string():
    envelope = json.dumps(
        {
            "__tool_calls__": [{"id": "1"}],
            "finish_reason": "tool_calls",
            "content": None,
        }
    )
    assert strip_mood(envelope) == (envelope, None)


def _chunks(*texts, final=""):
    for text in texts:
        yield AgentResponse(text=text, is_complete=False)
    yield AgentResponse(text=final, is_complete=True, stats={"tokens": 3})


def test_a_streamed_reply_loses_its_tag_and_keeps_its_final_chunk():
    heard = []
    out = list(without_mood(_chunks("[mo", "od:hap", "py] Hi", " there"), heard.append))
    assert "".join(c.text for c in out if not c.is_complete) == "Hi there"
    assert heard == ["happy"]
    assert out[-1].is_complete and out[-1].stats == {"tokens": 3}
    assert [c.is_complete for c in out].count(True) == 1


def test_a_held_tail_is_shown_before_the_final_chunk():
    out = list(without_mood(_chunks("an open [mood")))
    assert "".join(c.text for c in out if not c.is_complete) == "an open [mood"
    assert out[-1].is_complete and out[-1].text == ""


def test_an_envelope_after_streamed_text_reports_the_mood_once():
    heard = []
    envelope = json.dumps(
        {
            "__tool_calls__": [{"id": "1"}],
            "finish_reason": "tool_calls",
            "content": "[mood:curious] Checking.",
        }
    )
    out = list(
        without_mood(
            _chunks("[mood:curious] ", "Checking.", final=envelope), heard.append
        )
    )
    assert heard == ["curious"]
    assert json.loads(out[-1].text)["content"] == "Checking."


def test_a_reply_that_arrives_whole_still_reports_its_mood():
    heard = []
    out = list(without_mood(_chunks(final="[mood:sleepy] Good night."), heard.append))
    assert heard == ["sleepy"]
    assert out[-1].text == "Good night."


def test_closing_the_filtered_stream_closes_the_model_stream():
    closed = []

    def source():
        try:
            yield AgentResponse(text="[mood:happy] one", is_complete=False)
            yield AgentResponse(text=" two", is_complete=False)
        finally:
            closed.append(True)

    filtered = without_mood(source())
    next(filtered)
    filtered.close()
    assert closed == [True]


@pytest.mark.parametrize("sizes", [[1], [3], [6, 1], [1000]])
def test_tags_the_model_weighs_while_reasoning_are_not_its_mood(sizes):
    text = (
        "<think>Maybe [mood:playful]? No, [mood:worried] fits.</think>"
        "[mood:worried] What went wrong?"
    )
    shown, heard = _stream(text, sizes)
    assert shown == (
        "<think>Maybe [mood:playful]? No, [mood:worried] fits.</think>"
        "What went wrong?"
    )
    assert heard == ["worried"]


@pytest.mark.parametrize("sizes", [[1], [4], [1000]])
def test_a_tag_the_user_asked_to_see_in_code_stays(sizes):
    text = "[mood:happy] Here it is:\n```\n[mood:happy]\n```\nand inline `[mood:sad]`."
    shown, heard = _stream(text, sizes)
    assert shown == "Here it is:\n```\n[mood:happy]\n```\nand inline `[mood:sad]`."
    assert heard == ["happy"]


@pytest.mark.parametrize("sizes", [[1], [5], [1000]])
def test_reasoning_with_no_opening_marker_does_not_hide_the_replys_mood(sizes):
    # The tag weighed there is gone before the closing marker shows what it was.
    text = "Should I say [mood:sad]? No.</think>\n[mood:happy] Hi!"
    shown, heard = _stream(text, sizes)
    assert shown == "Should I say ? No.</think>\nHi!"
    assert heard == ["sad", "happy"]


@pytest.mark.parametrize("sizes", [[1], [4], [1000]])
def test_a_reasoning_marker_quoted_in_code_does_not_switch_the_filter_off(sizes):
    text = "Models wrap thoughts in `<think>` tags. [mood:curious] Want an example?"
    shown, heard = _stream(text, sizes)
    assert shown == "Models wrap thoughts in `<think>` tags. Want an example?"
    assert heard == ["curious"]


@pytest.mark.parametrize("sizes", [[1], [3], [1000]])
def test_a_stray_backtick_hides_a_tag_no_further_than_its_line(sizes):
    text = "It's the 5`th try.\n[mood:determined] One more.\n```\n[mood:sad]\n\n```"
    shown, heard = _stream(text, sizes)
    assert shown == "It's the 5`th try.\nOne more.\n```\n[mood:sad]\n\n```"
    assert heard == ["determined"]


@pytest.mark.parametrize("sizes", [[1], [3], [1000]])
@pytest.mark.parametrize(
    "text, shown, mood",
    [
        ("[curious] When a star dies...", "When a star dies...", "curious"),
        ("\n[ Worried ]\nThat's real.", "\nThat's real.", "worried"),
        # Short forms are only a mood at the start of the reply, and only mood words.
        ("Say [happy] to me.", "Say [happy] to me.", None),
        ("[docs](https://x.io) say so", "[docs](https://x.io) say so", None),
        ("[1] First point", "[1] First point", None),
        ("[bored] Sure.", "[bored] Sure.", None),
    ],
)
def test_the_short_form_at_the_start_of_a_reply_is_a_mood_too(text, shown, mood, sizes):
    out, heard = _stream(text, sizes)
    assert out == shown
    assert heard == ([mood] if mood else [])


def test_only_the_first_mood_of_a_reply_is_reported():
    shown, heard = _stream("[mood:happy] Yes! [mood:excited] Really.", [5])
    assert shown == "Yes! Really."
    assert heard == ["happy"]


@pytest.mark.parametrize("sizes", [[1], [2], [1000]])
def test_angle_brackets_that_are_not_reasoning_pass_through(sizes):
    text = "a < b, <b>bold</b>, </thin, <thinking> and a trailing <"
    shown, heard = _stream(text, sizes)
    assert shown == text
    assert heard == []


def test_the_prompt_names_every_mood_and_the_tag_shape():
    assert "[mood:WORD]" in MOOD_PROMPT
    for mood in MOODS:
        assert mood in MOOD_PROMPT
    assert len(set(MOODS)) == len(MOODS)


# -- the agent loop ------------------------------------------------------------


def _mood_agent(replies, expresses_mood=True, streaming=False):
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from gaia.agents.base.agent import Agent
    from gaia.agents.base.console import AgentConsole
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    class MoodAgent(Agent):
        def _get_system_prompt(self) -> str:
            return "test"

        def _register_tools(self) -> None:
            pass

        def _create_console(self):
            return AgentConsole()

    MoodAgent.expresses_mood = expresses_mood
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = MoodAgent(
            silent_mode=True, skip_lemonade=True, model_id=DEFAULT_MODEL_NAME
        )
    agent.streaming = streaming
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(
        side_effect=lambda *_, **__: SimpleNamespace(text=replies.pop(0), stats={})
    )

    def stream(*_, **__):
        text = replies.pop(0)
        for i in range(0, len(text), 4):
            yield AgentResponse(text=text[i : i + 4], is_complete=False)
        yield AgentResponse(text="", is_complete=True, stats={})

    chat.send_messages_stream = MagicMock(side_effect=stream)
    agent.chat = chat
    heard = []
    agent.console.print_mood = heard.append
    return agent, heard


@pytest.mark.parametrize("streaming", [False, True])
def test_the_loop_takes_the_tag_out_of_the_answer_and_tells_the_console(streaming):
    agent, heard = _mood_agent(["[mood:happy] You're welcome!"], streaming=streaming)
    shown = []
    agent.console.print_streaming_text = lambda text, end_of_stream=False: shown.append(
        text
    )

    result = agent.process_query("thanks!")

    assert result["result"] == "You're welcome!"
    assert heard == ["happy"]
    assert "[mood" not in "".join(shown)
    assert "[mood" not in json.dumps(result.get("model_messages"))


def test_an_agent_that_does_not_ask_for_a_mood_leaves_the_text_alone():
    agent, heard = _mood_agent(["[mood:happy] literal"], expresses_mood=False)
    result = agent.process_query("say it")
    assert result["result"] == "[mood:happy] literal"
    assert heard == []


def test_each_request_reminds_the_model_but_history_never_keeps_it():
    from gaia.agents.base.mood import MOOD_REMINDER

    agent, _ = _mood_agent(["[mood:happy] Hi!"])
    result = agent.process_query("hello")

    sent = agent.chat.send_messages.call_args.kwargs["messages"]
    assert any(m.get("content") == MOOD_REMINDER for m in sent)
    assert MOOD_REMINDER not in json.dumps(result.get("model_messages"))


def test_an_agent_that_does_not_ask_sends_no_reminder():
    from gaia.agents.base.mood import MOOD_REMINDER

    agent, _ = _mood_agent(["Hi!"], expresses_mood=False)
    agent.process_query("hello")

    assert MOOD_REMINDER not in json.dumps(agent.chat.send_messages.call_args.kwargs)
