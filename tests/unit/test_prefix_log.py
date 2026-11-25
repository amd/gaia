# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The log says when a new chat cannot reuse the prompt the model already read.

A warm-up whose prompt differed from the Agent UI's by two lines made a small
GPU read 10K tokens twice for "Hi" (#9041), and nothing in any log said so.
"""

from __future__ import annotations

import logging

import pytest

from gaia.agents.base import prefix_log
from gaia.agents.base.agent import Agent

MODEL = "Gemma-4-E4B-it-GGUF"


def _tool(name: str, description: str = "does a thing") -> dict:
    return {"type": "function", "function": {"name": name, "description": description}}


@pytest.fixture(autouse=True)
def _fresh_record():
    prefix_log.forget()
    yield
    prefix_log.forget()


@pytest.fixture
def log(caplog):
    caplog.set_level(logging.INFO, logger=prefix_log.logger.name)
    return caplog


def test_the_same_prefix_is_reported_as_reused(log):
    prefix = prefix_log.prefix_of("PROMPT", [_tool("read_file")])

    assert prefix_log.note_sent(MODEL, "the warm-up", prefix) is True
    assert prefix_log.note_sent(MODEL, "chat a", prefix) is True

    assert [r.levelno for r in log.records] == [logging.INFO, logging.INFO]
    assert "the same as the warm-up sent" in log.records[-1].getMessage()


def test_a_changed_system_prompt_is_a_warning_that_shows_where(log):
    rules = "**RULES:** Be brief.\n**UNSUPPORTED:** "
    warm = prefix_log.prefix_of(rules + "Scheduling, C:\\Users\\sam\\taxes.pdf", [])
    turn = prefix_log.prefix_of(rules + "C:\\Users\\sam\\taxes.pdf", [])
    prefix_log.note_sent(MODEL, "the warm-up", warm)

    assert prefix_log.note_sent(MODEL, "chat a", turn) is False

    [warning] = [r for r in log.records if r.levelno == logging.WARNING]
    message = warning.getMessage()
    assert "chat a" in message and "the warm-up" in message
    assert f"differs from character {len(rules)} of" in message
    assert "under **UNSUPPORTED:** (-12 characters)" in message
    assert warm.fingerprint in message and turn.fingerprint in message
    # The prompt can name the user's files; the log must not.
    assert "taxes" not in message and "sam" not in message


def test_a_difference_before_any_heading_is_placed_at_the_opening():
    before = prefix_log.prefix_of("You are GAIA.", [])
    now = prefix_log.prefix_of("You are Gaia.", [])
    assert "under its opening" in prefix_log.describe_difference(before, now)


def test_a_warm_up_that_fails_is_not_recorded_as_read(log):
    class _Down(_Agent):
        def _send_warm_up_request(self, cancel, **request):
            raise ConnectionError("Lemonade not reachable")

    with pytest.raises(ConnectionError):
        _Down("WARM PROMPT").warm_up()

    turn = _Agent("WARM PROMPT")
    turn._log_prefix_sent()  # pylint: disable=protected-access
    assert "nothing was sent to it before" in log.records[-1].getMessage()


@pytest.mark.parametrize(
    "tools, expected",
    [
        ([_tool("read_file"), _tool("send_email")], "added ['send_email']"),
        ([], "removed ['read_file']"),
        ([_tool("read_file", "reads")], "description or parameters changed"),
    ],
)
def test_a_changed_tool_list_says_what_changed(log, tools, expected):
    prefix_log.note_sent(
        MODEL, "the warm-up", prefix_log.prefix_of("P", [_tool("read_file")])
    )

    assert (
        prefix_log.note_sent(MODEL, "chat a", prefix_log.prefix_of("P", tools)) is False
    )

    assert expected in log.records[-1].getMessage()


def test_reordered_tools_are_named_as_reordered():
    before = prefix_log.prefix_of("P", [_tool("a"), _tool("b")])
    now = prefix_log.prefix_of("P", [_tool("b"), _tool("a")])
    assert prefix_log.describe_difference(before, now) == (
        "the tool list is in a different order"
    )


def test_each_chat_is_compared_with_the_one_before_it(log):
    first = prefix_log.prefix_of("P1", [])
    second = prefix_log.prefix_of("P2", [])
    prefix_log.note_sent(MODEL, "the warm-up", first)
    prefix_log.note_sent(MODEL, "chat a", second)

    assert prefix_log.note_sent(MODEL, "chat b", second) is True
    assert "the same as chat a sent" in log.records[-1].getMessage()


def test_models_are_tracked_apart(log):
    prefix_log.note_sent("model-a", "the warm-up", prefix_log.prefix_of("P1", []))

    assert prefix_log.note_sent("model-b", "chat a", prefix_log.prefix_of("P2", []))
    assert "nothing was sent to it before" in log.records[-1].getMessage()


def test_the_full_tool_list_on_its_own_slot_is_not_a_changed_prefix(log):
    core = prefix_log.prefix_of("P", [_tool("read_file")])
    full = prefix_log.prefix_of("P", [_tool("read_file"), _tool("send_email")])
    prefix_log.note_sent(MODEL, "the warm-up", core)
    prefix_log.note_sent(MODEL, "the warm-up", full, slot=2)

    assert prefix_log.note_sent(MODEL, "chat a", full, slot=2) is True
    assert prefix_log.note_sent(MODEL, "chat b", core) is True
    assert not [r for r in log.records if r.levelno == logging.WARNING]


def test_the_read_line_reports_the_servers_counts(log):
    prefix_log.note_read(MODEL, "chat a", {"prompt_tokens": 10178, "cached_tokens": 0})
    assert "10178 prompt tokens, 0 of them reused" in log.records[-1].getMessage()


def test_a_server_that_reports_no_cache_count_is_not_given_a_zero(log):
    prefix_log.note_read(MODEL, "chat a", {"prompt_tokens": 10178})
    assert "an unreported number" in log.records[-1].getMessage()

    prefix_log.note_read(MODEL, "chat a", None)
    assert "reported no token counts" in log.records[-1].getMessage()


class _Client:
    def get_last_usage(self):
        return {"prompt_tokens": 9000, "cached_tokens": 8990}


class _Chat:
    llm_client = _Client()

    def send_messages(self, *_args, **_kwargs):
        pass


class _Agent(Agent):
    """The pieces the log reads, without building a real agent."""

    def __init__(self, prompt: str):  # pylint: disable=super-init-not-called
        self.chat = _Chat()
        self.model_id = MODEL
        self._prompt = prompt
        self._active_tool_filter = None

    system_prompt = property(lambda self: self._prompt)
    _openai_tools = property(lambda self: [_tool("read_file")])

    def _refresh_active_skill_filter(self, user_input):
        pass

    def _get_system_prompt(self):
        return ""

    def _register_tools(self):
        pass


def test_a_first_request_unlike_the_warm_up_is_logged_once(log):
    _Agent("WARM PROMPT").warm_up()
    turn = _Agent("TURN PROMPT")

    for _step in range(2):
        turn._log_prefix_sent()  # pylint: disable=protected-access
        turn._log_prefix_read()  # pylint: disable=protected-access

    messages = [r.getMessage() for r in log.records]
    warnings = [r for r in log.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "but the warm-up sent" in warnings[0].getMessage()
    assert sum("first request" in m for m in messages) == 1
    assert "9000 prompt tokens, 8990 of them reused" in messages[-1]
