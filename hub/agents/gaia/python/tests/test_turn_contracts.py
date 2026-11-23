# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What a whole turn owes the user, on the flagship's real turn loop.

Each test scripts the model and the person answering approval cards
(``open_chat`` in conftest.py) and lets everything between them run for real:
the loop, the approval gate, the answer checks, and the shell, file, memory
and skill tools against a temp folder. Chats opened in one test share one
process, the way the sidecar hosts sessions.

Guards #8806 and #8850 (a declined call), #8798 (a declined detail), #8818
(a research follow-up), #8819 (another chat's skill tools) and #8803 (a
ruled-out step, not fixed yet).
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from test_skill_autoload_turn import _bundled

from gaia.agents.base.verification import EXECUTED_KEY

# ── A call the user declined ─────────────────────────────────────────────

#: #8806's command: the hidden character keeps it off the read-only list, and
#: it changes nothing, so no recorded effect stood in the way of a retry.
ECHO = "echo hi\u200bthere"
DECLINED_ANSWER = (
    "I didn't run that, because you declined it. Ask me again if you want."
)

#: kind -> (request, model steps before the gated call, the gated call).
GATED_CALLS = {
    "shell command that changes nothing": lambda work: (
        f"Run `{ECHO}` for me.",
        [],
        ("run_shell_command", {"command": ECHO}),
    ),
    "shell command that creates a file": lambda work: (
        "Run `touch marker.txt` for me.",
        [],
        ("run_shell_command", {"command": "touch marker.txt"}),
    ),
    "file write": lambda work: (
        "Save a note saying hello as note.txt.",
        [],
        ("write_file", {"file_path": str(work / "note.txt"), "content": "hello\n"}),
    ),
    "file edit": lambda work: (
        "In handbook.md, change goggles to gloves.",
        [("read_file", {"file_path": str(work / "handbook.md")})],
        (
            "edit_file",
            {
                "file_path": str(work / "handbook.md"),
                "old_content": "goggles",
                "new_content": "gloves",
            },
        ),
    ),
}


def _folder(work) -> dict:
    return {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}


@pytest.mark.parametrize("kind", GATED_CALLS)
def test_denied_call_is_not_asked_again_in_the_turn(open_chat, kind):
    chat = open_chat()
    request, lead_in, call = GATED_CALLS[kind](chat.work)
    before = _folder(chat.work)

    answer = chat.say(
        request,
        [*lead_in, call, call, DECLINED_ANSWER],
        # A second card would be allowed, and the call would then run.
        approvals=["deny", "allow"],
    )

    assert [tool for tool, _args in chat.approver.asked] == [call[0]]
    first, repeat = chat.results[-2:]
    assert first["status"] == "denied"
    assert repeat["status"] == "denied"
    assert repeat["already_declined"] is True
    assert repeat[EXECUTED_KEY] is False
    assert "declined" in repeat["error"]
    assert _folder(chat.work) == before
    # The honest answer is the one the user gets, with nothing sent back.
    assert answer == DECLINED_ANSWER
    assert chat.unused_steps == []
    assert len(chat.requests) == len(lead_in) + 3


def test_a_plain_follow_up_keeps_the_no_and_an_ordinary_reask_gets_the_card(open_chat):
    """#8850: "try it once more" was refused instead of put to the user again."""
    chat = open_chat()
    call = ("run_shell_command", {"command": ECHO})
    chat.say(f"Run `{ECHO}` for me.", [call, DECLINED_ANSWER], approvals=["deny"])

    chat.say("What does that print?", [call, DECLINED_ANSWER], approvals=["allow"])
    assert chat.approver.asked == []
    assert chat.results[-1]["already_declined"] is True

    chat.say(
        "try it once more", [call, "It printed hi\u200bthere."], approvals=["allow"]
    )
    assert chat.approver.asked == [("run_shell_command", {"command": ECHO})]
    assert chat.results[-1]["status"] == "success"
    assert chat.results[-1]["stdout"] == "hi\u200bthere\n"


# ── An answer about a call that did not run ──────────────────────────────

RAN_CLAIM = "I ran `touch marker.txt` and it completed successfully."
TOUCH = ("run_shell_command", {"command": "touch marker.txt"})


def test_a_claim_that_a_declined_command_ran_is_sent_back(open_chat):
    chat = open_chat()

    answer = chat.say(
        "Run `touch marker.txt` for me.",
        [TOUCH, RAN_CLAIM, DECLINED_ANSWER],
        approvals=["deny"],
    )

    assert answer == DECLINED_ANSWER
    assert not (chat.work / "marker.txt").exists()
    correction = chat.requests[-1][0][-1]
    assert correction["role"] == "user"
    assert "no command or script ran this turn" in correction["content"]


def test_a_claim_the_model_repeats_reaches_the_user_marked_unconfirmed(open_chat):
    chat = open_chat()

    answer = chat.say(
        "Run `touch marker.txt` for me.",
        [TOUCH, RAN_CLAIM, RAN_CLAIM],
        approvals=["deny"],
    )

    assert answer.startswith(RAN_CLAIM)
    assert "I haven't confirmed this works" in answer
    assert "no command or script ran this turn" in answer
    assert chat.unused_steps == []


# ── A detail the user said to leave out of memory ────────────────────────

DOG = "My dog is called Biscuit"
PHONE = "My phone number is 555-1234"
KEPT_ONE = "I saved your dog's name and left your phone number out."

LEAVE_ONE_OUT = [
    "Remember my dog is called Biscuit and my phone is 555-1234 leave it out",
    "Remember my dog is called Biscuit. My phone is 555-1234, but don't keep it.",
    "Remember my dog is called Biscuit. My phone is 555-1234 don't save that",
    "Remember my dog is called Biscuit. Don't remember my phone number, it's 555-1234",
    "remember my dog is called Biscuit\nmy phone is 555-1234 please do not store it",
]


def _memories(chat) -> list:
    rows = chat.agent._memory_store.get_all_knowledge(limit=100)["items"]
    return [row["content"] for row in rows]


@pytest.mark.parametrize("message", LEAVE_ONE_OUT)
def test_declined_detail_is_not_saved(open_chat, message):
    chat = open_chat()

    answer = chat.say(
        message,
        [("remember", {"fact": DOG}), ("remember", {"fact": PHONE}), KEPT_ONE],
    )

    kept, refused = chat.results
    assert kept["status"] == "stored"
    assert refused["status"] == "skipped"
    assert refused["message"].startswith("Not stored: the user said to leave this out")
    assert _memories(chat) == [DOG]
    assert "555-1234" not in json.dumps(_memories(chat))
    # The honest answer stands: nothing nudges the model to save the rest.
    assert answer == KEPT_ONE
    assert chat.unused_steps == []


def test_a_claim_that_the_declined_detail_was_saved_is_sent_back(open_chat):
    chat = open_chat()
    left_out = "I left your phone number out of memory."

    answer = chat.say(
        "Remember my phone is 555-1234 leave it out",
        [("remember", {"fact": PHONE}), "Stored. Won't repeat it back.", left_out],
    )

    assert _memories(chat) == []
    assert answer == left_out


@pytest.mark.xfail(
    strict=True,
    reason=(
        "On main the memory answer check stops as soon as one fact was saved "
        "this turn, so 'Stored both.' reaches the user although remember "
        "refused the declined one (follow-up to #8798)."
    ),
)
def test_a_claim_that_both_were_saved_is_sent_back(open_chat):
    chat = open_chat()

    answer = chat.say(
        LEAVE_ONE_OUT[0],
        [
            ("remember", {"fact": DOG}),
            ("remember", {"fact": PHONE}),
            "Stored both.",
            KEPT_ONE,
        ],
    )

    assert _memories(chat) == [DOG]
    assert answer == KEPT_ONE


# ── A follow-up in a research chat ───────────────────────────────────────

PAGE = "https://example.org/eu-ai-act"
REPORT = (
    "## EU AI Act timeline\n\nIt applies in stages from 2025 "
    f"[source]({PAGE}).\n\n## Sources\n- {PAGE}"
)
MEMO_TEXT = "The team offsite moves to March 14."
MEMO_ANSWER = f"Saved memo.txt. It says: {MEMO_TEXT}"


def _serve_page(chat, text: str) -> list:
    """Answer the chat's page fetches with *text*; returns the URLs asked for."""
    fetched = []

    def get(url, **_kwargs):
        fetched.append(url)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            headers={"Content-Type": "text/plain"},
            text=text,
        )

    chat.agent._web_client.get = get
    return fetched


def test_followup_after_research_answers_with_its_own_text(open_chat):
    """#8818: the memo turn answered with the earlier report, sent again."""
    chat = open_chat()
    fetched = _serve_page(chat, "The EU AI Act applies in stages from 2025.")

    report = chat.say(
        "/research the EU AI Act timeline", [("fetch_page", {"url": PAGE}), REPORT]
    )
    assert report == REPORT
    assert fetched == [PAGE]

    memo = chat.work / "memo.txt"
    # The apps keep research on, so the follow-up goes out as /research too.
    answer = chat.say(
        f"/research Save a memo as memo.txt. It should say: {MEMO_TEXT}",
        [
            ("write_file", {"file_path": str(memo), "content": MEMO_TEXT + "\n"}),
            MEMO_ANSWER,
            # What the model sent when asked to cite the pages turn 1 read.
            REPORT,
        ],
        approvals=["allow"],
    )

    assert memo.read_text(encoding="utf-8") == MEMO_TEXT + "\n"
    assert answer == MEMO_ANSWER
    assert chat.unused_steps == [REPORT]
    assert fetched == [PAGE]


def test_a_research_turn_that_cites_nothing_it_read_is_still_sent_back(open_chat):
    chat = open_chat()
    _serve_page(chat, "The EU AI Act applies in stages from 2025.")

    answer = chat.say(
        "/research the EU AI Act timeline",
        [("fetch_page", {"url": PAGE}), "It applies in stages.", REPORT],
    )

    assert answer == REPORT


# ── A skill's tools and the chat that loaded it ──────────────────────────

TALLY = "ledger-notes/tally"


def _skill_tools(names) -> set:
    return {name for name in names if "/" in name}


def test_skill_tools_stay_in_the_chat_that_loaded_them(open_chat, tmp_path):
    """#8819: a chat opened after another loaded a skill got that skill's tools."""

    def open_with_skills():
        chat = open_chat()
        chat.agent._skill_manager = _bundled(tmp_path / "skills")
        chat.agent.rebuild_system_prompt()
        return chat

    first = open_with_skills()
    first.say(
        "Load the ledger notes skill and tally 1 2.",
        [("load_skill", {"name": "ledger-notes"}), (TALLY, {"text": "1 2"}), "1 2"],
    )
    assert first.results[-1] == "1 2"
    assert _skill_tools(first.offered) == {TALLY}

    second = open_with_skills()
    relayed = "I can't tally here: that tool isn't available in this chat."
    answer = second.say("Tally 3 4.", [(TALLY, {"text": "3 4"}), relayed])

    for messages, tools in second.requests:
        assert _skill_tools(tool["function"]["name"] for tool in tools) == set()
        assert TALLY not in messages[0]["content"]
    assert _skill_tools(second.agent._tools_registry) == set()
    (refused,) = second.results
    assert refused["status"] == "error"
    assert refused[EXECUTED_KEY] is False
    assert "SkillContextError" not in json.dumps(second.requests)
    assert answer == relayed

    first.say("Tally 5 6.", [(TALLY, {"text": "5 6"}), "5 6"])
    assert first.results == ["5 6"]
    assert _skill_tools(first.offered) == {TALLY}


# ── A step the user ruled out ────────────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#8803: an answer that hides a step the user ruled out is not sent "
        "back yet. Remove this mark when #8810 merges."
    ),
)
def test_ruled_out_step_that_ran_is_named(open_chat):
    chat = open_chat()
    script = chat.work / "run.sh"
    script.write_text("echo hi\n", encoding="utf-8")
    named = "I ran `chmod +x run.sh`, which changed its permissions against your ask."

    answer = chat.say(
        "Make run.sh ready to use. Do not change permissions and do not use the shell.",
        [("run_shell_command", {"command": "chmod +x run.sh"}), "Done.", named],
        approvals=["allow"],
    )

    assert script.stat().st_mode & 0o100, "the approved chmod is what ran"
    assert "chmod" in answer
    assert "permission" in answer


# ── A turn the user stopped ──────────────────────────────────────────────


def test_a_turn_stopped_during_a_tool_makes_no_further_tool_calls(open_chat):
    chat = open_chat()
    # The Agent UI and the TUI set this on the turn's console when Stop is hit.
    stop = chat.agent.console.cancelled = threading.Event()
    write = chat.agent._tools_registry["write_file"]
    write_file = write["function"]

    def write_then_stop(*args, **kwargs):
        result = write_file(*args, **kwargs)
        stop.set()
        return result

    write["function"] = write_then_stop
    first, second = chat.work / "a.txt", chat.work / "b.txt"

    answer = chat.say(
        "Save a.txt and b.txt.",
        [
            ("write_file", {"file_path": str(first), "content": "a\n"}),
            ("write_file", {"file_path": str(second), "content": "b\n"}),
            "Saved both files.",
        ],
        approvals=["allow", "allow"],
    )

    assert first.read_text(encoding="utf-8") == "a\n"
    assert not second.exists()
    assert [args["file_path"] for _tool, args in chat.approver.asked] == [str(first)]
    assert answer == ""
