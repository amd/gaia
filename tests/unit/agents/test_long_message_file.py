# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A huge message is also saved to a file, so the model can use tools on it."""

import os
import queue
from types import SimpleNamespace

from gaia.agents.base.agent import LONG_MESSAGE_CHARS, Agent


class _Bare(Agent):
    def _register_tools(self):
        pass


def _agent(scratch):
    agent = _Bare.__new__(_Bare)
    agent._read_validator = lambda: SimpleNamespace(scratch_dir=scratch)
    return agent


def _paste():
    return (
        "Please count how many times the word ZEBRA appears. "
        + "alpha beta gamma delta " * 3000
        + "ZEBRA "
        + "omega " * 2000
        + "ZEBRA ZEBRA"
    )


def test_a_short_message_is_sent_unchanged(tmp_path):
    assert _agent(str(tmp_path))._with_long_message_file("hello") == "hello"
    assert list(tmp_path.iterdir()) == []


def test_a_long_message_is_saved_and_the_model_is_told_where(tmp_path):
    paste = _paste()
    assert len(paste) >= LONG_MESSAGE_CHARS

    sent = _agent(str(tmp_path))._with_long_message_file(paste)

    saved = list(tmp_path.glob("message-*.txt"))
    assert len(saved) == 1
    assert saved[0].read_text(encoding="utf-8") == paste
    assert sent.endswith(paste)
    note = sent[: -len(paste)]
    assert str(saved[0]) in note
    assert "never copy the message into a tool call" in note


def test_the_note_says_the_file_holds_the_request_itself(tmp_path):
    """rc18, DeepSeek: a count over the file included the question's ZEBRA."""
    paste = _paste()

    sent = _agent(str(tmp_path))._with_long_message_file(paste)

    note = sent[: -len(paste)]
    assert "the request itself" in note


def test_the_file_holds_the_users_words_not_the_framed_message(tmp_path):
    paste = _paste()
    agent = _agent(str(tmp_path))
    agent._original_user_input = paste
    framed = "[GAIA Memory Context]\nCurrent time: now\n\n" + paste

    sent = agent._with_long_message_file(framed)

    (saved,) = tmp_path.glob("message-*.txt")
    assert saved.read_text(encoding="utf-8") == paste
    assert sent.endswith(framed)


def test_a_deleted_scratch_folder_is_recreated_private(tmp_path):
    scratch = tmp_path / "scratch"
    sent = _agent(str(scratch))._with_long_message_file(_paste())
    assert scratch.is_dir()
    assert str(scratch) in sent
    if os.name != "nt":
        assert scratch.stat().st_mode & 0o077 == 0


def test_a_long_follow_up_mid_turn_is_saved_too(tmp_path):
    paste = _paste()
    agent = _agent(str(tmp_path))
    agent._original_user_input = "short first question"
    agent._followup_queue = queue.Queue()
    agent._followup_queue.put(paste)
    agent.console = SimpleNamespace(print_info=lambda *_: None)
    messages, conversation = [], []

    assert agent._drain_followups(messages, conversation) == 1

    saved = list(tmp_path.glob("message-*.txt"))
    assert [p.read_text(encoding="utf-8") for p in saved] == [paste]
    assert str(saved[0]) in messages[0]["content"]
    assert str(saved[0]) not in conversation[0]["content"]


def test_no_scratch_folder_sends_the_message_unchanged(caplog):
    paste = _paste()
    assert _agent(None)._with_long_message_file(paste) == paste
    assert "no scratch folder" in caplog.text


def test_a_failed_save_sends_the_message_unchanged(tmp_path, caplog):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    paste = _paste()
    assert _agent(str(blocker))._with_long_message_file(paste) == paste
    assert "Could not save a long message" in caplog.text
