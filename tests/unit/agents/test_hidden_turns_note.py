# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The model is told when earlier turns were left out of what it reads.

On v0.26.0-rc18 the chat showed "Older messages are now out of the model's
view" and, right under it, the model said facts from those turns were "never
mentioned in this chat". The window knew; the model was not told.
"""

from copy import deepcopy
from unittest.mock import patch

import pytest

from gaia.agents.base.history import (
    HIDDEN_TURNS_NOTE,
    fit_pushed_history,
    fit_turns,
    select_history,
)


def _turn(index):
    return [
        {"role": "user", "content": f"question-{index}"},
        {"role": "assistant", "content": f"answer-{index}"},
    ]


def _texts(messages):
    return [m["content"] for m in messages if isinstance(m["content"], str)]


def _notes(messages):
    return sum(text.count(HIDDEN_TURNS_NOTE) for text in _texts(messages))


@pytest.fixture
def ten_tokens_a_turn():
    with patch("gaia.agents.base.history.count_tokens", return_value=10):
        yield


def test_the_note_is_one_sentence():
    assert HIDDEN_TURNS_NOTE.startswith("[") and HIDDEN_TURNS_NOTE.endswith(".]")
    assert HIDDEN_TURNS_NOTE.count(". ") == 0


def test_no_note_while_every_turn_is_in_view(ten_tokens_a_turn):
    turns = [_turn(i) for i in range(10)]

    assert select_history(turns, 100) == sum(turns, [])


def test_the_note_opens_the_oldest_turn_in_view_once_turns_are_dropped(
    ten_tokens_a_turn,
):
    turns = [_turn(i) for i in range(11)]

    view = select_history(turns, 100)

    assert view[0] == {
        "role": "user",
        "content": f"{HIDDEN_TURNS_NOTE}\n\nquestion-6",
    }
    assert view[1:] == sum(turns[6:], [])[1:]
    assert _notes(view) == 1


def test_the_stored_turns_are_not_changed(ten_tokens_a_turn):
    turns = [_turn(i) for i in range(11)]
    before = deepcopy(turns)

    kept = fit_turns(turns, 100)

    assert turns == before
    # Only the noted message is a copy; the rest are the stored dicts.
    assert kept[0][1] is turns[6][1]
    assert kept[1] is turns[7]


def test_a_second_fit_of_the_same_view_adds_no_second_note(ten_tokens_a_turn):
    """The Agent UI fits the turns, then the agent fits what it was sent."""
    sent = fit_turns([_turn(i) for i in range(11)], 100)

    again = select_history(sent, 100)

    assert _notes(again) == 1
    assert again[0]["content"].startswith(HIDDEN_TURNS_NOTE)


def test_a_second_fit_that_drops_more_moves_the_note(ten_tokens_a_turn):
    sent = fit_turns([_turn(i) for i in range(11)], 100)

    tighter = select_history(sent, 40)

    assert _notes(tighter) == 1
    assert tighter[0]["content"] == f"{HIDDEN_TURNS_NOTE}\n\nquestion-9"


def test_a_turn_dropped_for_its_size_is_noted_too():
    """Too big even shortened: left out while the turns around it stay."""
    huge = [
        {"role": "user", "content": "q " * 4000},
        {"role": "assistant", "content": "a " * 4000},
    ]
    turns = [_turn(0), huge, _turn(2)]
    small = {id(turns[0]), id(turns[2])}

    def fake_cost(turn):
        return 10 if id(turn) in small else 500

    with patch("gaia.agents.base.history._turn_cost", side_effect=fake_cost):
        view = select_history(turns, 100)

    assert [m["content"] for m in view[1:]] == ["answer-0", "question-2", "answer-2"]
    assert view[0]["content"] == f"{HIDDEN_TURNS_NOTE}\n\nquestion-0"


def test_the_note_survives_a_question_sent_in_parts(ten_tokens_a_turn):
    turns = [_turn(i) for i in range(11)]
    parts = [{"type": "text", "text": "what is in this picture?"}]
    turns[6][0]["content"] = list(parts)

    view = select_history(turns, 100)

    assert view[0]["content"] == [{"type": "text", "text": HIDDEN_TURNS_NOTE}, *parts]


def test_a_recorded_tool_result_is_left_as_it_is(ten_tokens_a_turn):
    """A text-tool model's results are user messages; their prefix is parsed."""
    turns = [_turn(i) for i in range(11)]
    result = '[Recorded tool result: read_file] "the file text"'
    turns[6][0]["content"] = result

    view = select_history(turns, 100)

    assert view[0]["content"] == result
    assert view[2]["content"] == f"{HIDDEN_TURNS_NOTE}\n\nquestion-7"
    assert _notes(view) == 1
    assert _notes(select_history([view[i : i + 2] for i in range(0, 10, 2)], 100)) == 1


def test_pushed_history_is_noted_when_the_agent_drops_turns(ten_tokens_a_turn):
    class _Agent:
        pass

    messages = sum((_turn(i) for i in range(11)), [])
    with patch("gaia.agents.base.history.history_budget", return_value=100):
        view = fit_pushed_history(_Agent(), messages, "next question")

    assert view[0]["content"] == f"{HIDDEN_TURNS_NOTE}\n\nquestion-6"
    assert _notes(view) == 1


def test_nothing_in_view_leaves_nothing_to_note(ten_tokens_a_turn):
    assert select_history([_turn(0), _turn(1)], 0) == []
