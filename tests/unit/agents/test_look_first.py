# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An answer about named workspace files that never looked is sent back once.

From Qwen3 30B on the everyday benchmark: asked to review ``toybox/dates.py``
it answered "the file is missing" with no tool call, and asked what the file
does it guessed. Both are one look away from right.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.look_first import _PATH_TOKEN, named_workspace_paths
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "toybox").mkdir()
    (tmp_path / "toybox" / "dates.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# toybox\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "request_text,expected",
    [
        ("What does toybox/dates.py do?", ["toybox/dates.py"]),
        ("Document it in README.md and keep the suite green.", ["README.md"]),
        # Not there, but its folder is: the agent should go and find it.
        ("Review toybox/parsers.py", ["toybox/parsers.py"]),
        ("What is 17 times 23?", []),
        ("How does Node.js handle this?", []),
        ("Write notes.md with a summary.", []),
    ],
)
def test_named_workspace_paths(workspace, request_text, expected):
    assert named_workspace_paths(request_text, str(workspace)) == expected


def test_a_path_inside_text_to_translate_is_not_something_to_look_at(workspace):
    """The path is part of the sentence the user wants translated (#7439)."""
    request_text = (
        "Translate this Spanish sentence to English and reply with only the "
        "translation, nothing else: Guardé una copia de seguridad en "
        f"{workspace}/toybox/dates.py.bak."
    )
    assert named_workspace_paths(request_text, str(workspace)) == []


def test_a_file_the_translation_request_itself_names_is_still_found(workspace):
    request_text = "Translate README.md into Spanish: keep the headings."
    assert named_workspace_paths(request_text, str(workspace)) == ["README.md"]


def test_the_working_folder_alone_is_not_something_to_look_at(workspace):
    request_text = f"You are working in {workspace}. What is 2 + 2?"
    assert named_workspace_paths(request_text, str(workspace)) == []


@pytest.mark.parametrize(
    "request_text,expected_tokens",
    [
        ("What does /etc/hosts contain?", ["/etc/hosts"]),
        ("Look at /home/me/proj/x.py please.", ["/home/me/proj/x.py"]),
    ],
)
def test_unix_absolute_paths_keep_their_leading_slash(request_text, expected_tokens):
    # The first alternative required a word char right after the drive-letter
    # group, so a bare leading "/" (no drive letter) was dropped, not matched.
    assert _PATH_TOKEN.findall(request_text) == expected_tokens


@pytest.mark.skipif(
    os.name == "nt", reason="/etc/hosts only exists as an absolute path on POSIX"
)
def test_a_unix_absolute_path_outside_the_workspace_is_still_found(workspace):
    assert named_workspace_paths("What does /etc/hosts contain?", str(workspace)) == [
        "/etc/hosts"
    ]


class _LookingAgent(Agent):
    def _get_system_prompt(self) -> str:
        return "test"

    def _register_tools(self) -> None:
        @tool
        def look_first_test_reader(file_path: str) -> dict:
            """Read a file."""
            return {"status": "success", "file_path": file_path, "content": "x = 1"}

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with (
        patch("gaia.agents.base.agent.AgentSDK"),
        patch("gaia.agents.base.agent._LOOK_TOOLS", ("look_first_test_reader",)),
    ):
        a = _LookingAgent(silent_mode=True, skip_lemonade=True)
        a.streaming = False
        yield a
    # The registry is process-wide; a leftover test tool breaks the flagship's label check.
    _TOOL_REGISTRY.pop("look_first_test_reader", None)


def _script(agent_, *responses):
    queue = list(responses)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        resp = MagicMock()
        resp.text = queue.pop(0)
        resp.stats = {}
        return resp

    agent_.chat = MagicMock()
    agent_.chat.send_messages = MagicMock(side_effect=_send)
    return sent


def _answer(text):
    return json.dumps({"thought": "", "answer": text})


def _read(path):
    return json.dumps(
        {
            "thought": "",
            "tool": "look_first_test_reader",
            "tool_args": {"file_path": path},
        }
    )


GUESS = "The file is missing, so I can't review it."
REVIEW = "It assigns x = 1 and nothing else."


def test_an_unlooked_answer_is_sent_back_to_look(agent, workspace):
    sent = _script(agent, _answer(GUESS), _read("toybox/dates.py"), _answer(REVIEW))

    result = agent.process_query("Review toybox/dates.py", max_steps=10)

    assert "`toybox/dates.py`" in sent[1][-1]["content"]
    assert "without opening anything" in sent[1][-1]["content"]
    assert REVIEW in result["result"]


def test_it_asks_only_once(agent, workspace):
    sent = _script(agent, _answer(GUESS), _answer(GUESS))

    result = agent.process_query("Review toybox/dates.py", max_steps=10)

    assert len(sent) == 2
    assert GUESS in result["result"]


def test_look_first_and_the_grounding_look_check_correct_once_between_them(
    agent, workspace
):
    sent = _script(agent, _answer(GUESS), _answer(GUESS), _answer(GUESS))

    agent.process_query("Review toybox/dates.py", max_steps=10)

    corrections = [
        m["content"]
        for m in sent[-1]
        if m.get("role") == "user" and m["content"] != "Review toybox/dates.py"
    ]
    assert len(sent) == 2
    assert len(corrections) == 1
    assert "Look with your tools first" in corrections[0]
    assert not any("[check:grounding]" in c for c in corrections)


def test_an_answer_that_looked_is_left_alone(agent, workspace):
    sent = _script(agent, _read("toybox/dates.py"), _answer(REVIEW))

    agent.process_query("Review toybox/dates.py", max_steps=10)

    assert len(sent) == 2


PLAN = (
    "1. I will read toybox/dates.py to see what it exports.\n"
    "2. I will add a test for each date helper.\n"
    "3. I will run the new tests."
)


def test_a_request_not_to_act_is_not_sent_to_look(agent, workspace):
    """rc18, DeepSeek: the nudge read as the user's newest instruction."""
    sent = _script(agent, _answer(PLAN), _answer(PLAN))

    result = agent.process_query(
        "For toybox/dates.py, do NOT run anything in this turn. Answer only "
        "with a three-step plan for adding tests.",
        max_steps=10,
    )

    assert len(sent) == 1
    assert PLAN in result["result"]


def test_a_touch_ban_on_an_investigation_still_sends_the_answer_to_look(
    agent, workspace
):
    sent = _script(agent, _answer(GUESS), _read("toybox/dates.py"), _answer(REVIEW))

    result = agent.process_query(
        "Find out why toybox/dates.py fails. Don't touch anything.", max_steps=10
    )

    assert "`toybox/dates.py`" in sent[1][-1]["content"]
    assert "without opening anything" in sent[1][-1]["content"]
    assert REVIEW in result["result"]


ANNOUNCED_EDIT = "The bug is on line 1. Let me check toybox/dates.py and patch it."


def test_a_touch_ban_keeps_an_announced_edit_from_being_pushed(agent, workspace):
    sent = _script(agent, _read("toybox/dates.py"), _answer(ANNOUNCED_EDIT))

    result = agent.process_query(
        "Why does toybox/dates.py fail? Don't touch anything.", max_steps=10
    )

    assert len(sent) == 2
    assert ANNOUNCED_EDIT in result["result"]


def test_small_talk_before_a_touch_ban_is_not_sent_to_look(agent, workspace):
    sent = _script(agent, _answer(GUESS))

    agent.process_query(
        "Hi, how are you? Don't touch anything, toybox/dates.py stays as is.",
        max_steps=10,
    )

    assert len(sent) == 1


READ_NOTHING_PLAN = (
    "1. I will load the module.\n2. I will add a test for each date helper.\n"
    "3. I will run the new tests."
)
PLAN_ONLY_REQUEST = (
    "For toybox/dates.py I want tests. Use no tools in this turn. Answer only "
    "with a three-step plan."
)


def test_a_plan_the_user_asked_for_gets_no_note_about_not_looking(agent, workspace):
    """rc18 round 2: "I haven't confirmed this works - I didn't read `x` this turn"."""
    _script(agent, _answer(READ_NOTHING_PLAN))

    result = agent.process_query(PLAN_ONLY_REQUEST, max_steps=10)

    assert result["result"].strip() == READ_NOTHING_PLAN


@pytest.mark.parametrize(
    "answer, reason",
    [
        (
            "I read toybox/dates.py and it exports three helpers.",
            "I didn't read `toybox/dates.py` this turn",
        ),
        (
            "toybox/dates.py doesn't exist in this workspace.",
            "I said something doesn't exist without searching for it this turn",
        ),
    ],
)
def test_a_plan_only_turn_still_notes_a_false_claim_about_the_file(
    agent, workspace, answer, reason
):
    # Another check may ask once for a rewrite; the model repeats itself.
    _script(agent, _answer(answer), _answer(answer))

    result = agent.process_query(PLAN_ONLY_REQUEST, max_steps=10)

    assert reason in result["result"]


@pytest.mark.parametrize(
    "request_text",
    [
        "Without using any tools, what does toybox/dates.py set `x` to?",
        "Don't use any tools. What does toybox/dates.py set x to?",
        "Use no tools: what does toybox/dates.py set x to?",
    ],
)
def test_a_no_tools_answer_stating_a_file_unread_keeps_its_note(
    agent, workspace, request_text
):
    """Not sent to look, since the user said not to, but the guess is still marked."""
    answer = "toybox/dates.py sets `x` to 2."
    sent = _script(agent, _answer(answer), _answer(answer))

    result = agent.process_query(request_text, max_steps=10)

    assert not [m for m in sent[-1] if "Look with your tools first" in m["content"]]
    assert "I didn't read `toybox/dates.py` this turn" in result["result"]


@pytest.mark.parametrize(
    "request_text",
    [
        # rc18 round 3, as the tester typed it.
        "Do NOT run anything in this turn and use no tools. I am testing a "
        "display: answer with exactly this one sentence: I read {path} and it "
        "has 4 data rows.",
        "For {path} I want totals. Use no tools in this turn. Answer only with "
        "a three-step plan.",
    ],
)
@pytest.mark.parametrize(
    "path", ["C:/no-such-drive-folder/t/orders.csv", "C:\\no-such\\t\\orders.csv"]
)
def test_a_no_tools_answer_that_says_it_read_a_full_path_keeps_its_note(
    agent, workspace, request_text, path
):
    """The drive colon hid the claim: "I read C:/.../orders.csv", no note."""
    answer = f"I read {path} and it has 4 data rows."
    _script(agent, _answer(answer), _answer(answer))

    result = agent.process_query(request_text.format(path=path), max_steps=10)

    assert f"I didn't read `{path}` this turn" in result["result"]


def test_a_plan_naming_a_full_path_it_has_yet_to_read_gets_no_note(agent, workspace):
    path = "C:/no-such-drive-folder/t/orders.csv"
    plan = f"1. I will read {path}.\n2. I will total each region.\n3. I will report."
    _script(agent, _answer(plan))

    result = agent.process_query(
        f"For {path} I want totals. Use no tools in this turn. Answer only with "
        "a three-step plan.",
        max_steps=10,
    )

    assert result["result"].strip() == plan


def test_the_verifiers_version_question_keeps_its_note(agent, workspace):
    (workspace / "src" / "gaia").mkdir(parents=True)
    (workspace / "src" / "gaia" / "version.py").write_text(
        '__version__ = "0.26.0"\n', encoding="utf-8"
    )
    answer = "src/gaia/version.py sets `__version__` to 0.9.1."
    _script(agent, _answer(answer), _answer(answer))

    result = agent.process_query(
        "Without using any tools, what does src/gaia/version.py set "
        "`__version__` to?",
        max_steps=10,
    )

    assert "I didn't read `src/gaia/version.py` this turn" in result["result"]


def test_an_unread_file_is_still_noted_when_the_user_did_not_forbid_looking(
    agent, workspace
):
    _script(agent, _answer(GUESS), _answer(GUESS))

    result = agent.process_query("What does toybox/dates.py do?", max_steps=10)

    assert "I didn't read `toybox/dates.py` this turn" in result["result"]


def test_forbidding_extra_work_still_sends_the_answer_to_look(agent, workspace):
    sent = _script(agent, _answer(GUESS), _answer(GUESS))

    agent.process_query(
        "Review toybox/dates.py and don't do anything else.", max_steps=10
    )

    assert len(sent) == 2
    assert "Look with your tools first" in sent[1][-1]["content"]


def test_a_question_about_no_file_is_answered_directly(agent, workspace):
    sent = _script(agent, _answer("391"))

    result = agent.process_query("What is 17 times 23?", max_steps=10)

    assert len(sent) == 1
    assert "391" in result["result"]
