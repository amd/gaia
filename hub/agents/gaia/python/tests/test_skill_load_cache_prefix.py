# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill loaded mid-turn must not rewrite the start of the prompt.

On a backend that caches by exact prefix (Claude), the session context in the
first user message is pinned for the turn and a newly loaded skill goes out
as a note appended after the history, so every call extends the one before
it. Every other backend keeps the old behaviour byte for byte.
"""

from __future__ import annotations

import json

import pytest
from test_prompt_prefix_stability import _turn

from gaia.agents.base.history import TurnMessages, with_session_context
from gaia.skills.manager import SkillManager

MARKERS = {"pinned-notes": "ZZ-PINNED-BODY-ZZ", "second-notes": "ZZ-SECOND-BODY-ZZ"}
NOTE_HEADER = "SKILLS LOADED THIS TURN"


def _skills(root) -> SkillManager:
    for name, marker in MARKERS.items():
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Keep {name}.\nlicense: MIT\n"
            f"---\n\n# {name}\n\n{marker}\n",
            encoding="utf-8",
        )
    return SkillManager(user_skills_root=root, include_claude_roots=False)


@pytest.fixture
def agent_for(make_flagship, monkeypatch, tmp_path):
    """``agent_for(cached)``: a flagship whose backend caches by prefix or not."""

    def build(cached: bool):
        agent, model, _doc = make_flagship()
        agent._skill_manager = _skills(tmp_path / "skills")
        monkeypatch.setattr(
            agent.chat.llm_client, "caches_prompt_prefix", cached, raising=False
        )
        return agent, model

    return build


def _call(tool, call_id, **args):
    return {"id": call_id, "name": tool, "args": args}


def _messages(request):
    messages, _tools = request
    return json.loads(json.dumps(messages))


def _assert_each_extends_the_last(model):
    sent = [_messages(r) for r in model.requests]
    for index, (before, after) in enumerate(zip(sent, sent[1:]), start=1):
        assert after[: len(before)] == before, f"call {index + 1} rewrote call {index}"


def _first_user(messages) -> str:
    return next(m["content"] for m in messages if m["role"] == "user")


def _text(messages) -> str:
    return json.dumps(messages, ensure_ascii=False)


def test_a_skill_loaded_mid_turn_is_appended_not_rewritten(agent_for):
    agent, model = agent_for(cached=True)

    _turn(
        agent,
        model,
        "keep my notes pinned",
        [
            _call("load_skill", "c1", name="pinned-notes"),
            _call("list_skills", "c2"),
            "Done.",
        ],
    )

    _assert_each_extends_the_last(model)
    last = _messages(model.requests[-1])
    assert MARKERS["pinned-notes"] not in _first_user(last)
    notes = [
        m for m in last if m["role"] == "user" and NOTE_HEADER in str(m["content"])
    ]
    assert len(notes) == 1 and MARKERS["pinned-notes"] in notes[0]["content"]
    assert _text(last).count(MARKERS["pinned-notes"]) == 1


def test_two_skills_each_arrive_once(agent_for):
    agent, model = agent_for(cached=True)

    _turn(
        agent,
        model,
        "keep my notes pinned",
        [
            _call("load_skill", "c1", name="pinned-notes"),
            _call("load_skill", "c2", name="second-notes"),
            "Done.",
        ],
    )

    _assert_each_extends_the_last(model)
    last = _text(_messages(model.requests[-1]))
    assert last.count(MARKERS["pinned-notes"]) == 1
    assert last.count(MARKERS["second-notes"]) == 1


def test_loading_the_same_skill_twice_appends_nothing_more(agent_for):
    agent, model = agent_for(cached=True)

    _turn(
        agent,
        model,
        "keep my notes pinned",
        [
            _call("load_skill", "c1", name="pinned-notes"),
            _call("load_skill", "c2", name="pinned-notes"),
            "Done.",
        ],
    )

    _assert_each_extends_the_last(model)
    assert _text(_messages(model.requests[-1])).count(MARKERS["pinned-notes"]) == 1


def test_an_unloaded_skill_is_withdrawn_by_a_note(agent_for):
    agent, model = agent_for(cached=True)

    _turn(
        agent,
        model,
        "keep my notes pinned",
        [
            _call("load_skill", "c1", name="pinned-notes"),
            _call("unload_skill", "c2", name="pinned-notes"),
            "Done.",
        ],
    )

    _assert_each_extends_the_last(model)
    last = _messages(model.requests[-1])
    assert any(
        "Skills no longer active: pinned-notes" in str(m["content"]) for m in last
    )


def test_the_next_turn_opens_with_the_skill_in_place(agent_for):
    agent, model = agent_for(cached=True)
    _turn(
        agent,
        model,
        "keep my notes pinned",
        [_call("load_skill", "c1", name="pinned-notes"), "Done."],
    )

    _turn(agent, model, "and again", ["Sure."])

    assert MARKERS["pinned-notes"] in _first_user(_messages(model.requests[-1]))


def test_a_one_off_request_carries_the_note_without_marking_it_sent(agent_for):
    agent, _model = agent_for(cached=True)
    turn = TurnMessages()
    turn.append({"role": "user", "content": "hello"})
    agent._turn_context_pin = None
    agent._with_session_context(turn)
    agent.load_skill("pinned-notes")

    one_off = agent._with_session_context([*turn, {"role": "user", "content": "x"}])
    sent = agent._with_session_context(turn)

    assert NOTE_HEADER in str(one_off[-1]["content"])
    assert NOTE_HEADER in str(sent[-1]["content"])
    assert NOTE_HEADER not in json.dumps(turn.recorded)


def test_uncached_backends_send_exactly_what_they_did_before(agent_for, monkeypatch):
    """DeepSeek and local models: every request is the live context rewritten
    in place, exactly as before append-only delivery existed."""
    agent, model = agent_for(cached=False)
    real = agent._with_session_context
    compared = []

    def spy(messages):
        sent = real(messages)
        legacy = with_session_context(messages, agent.session_context)
        assert json.dumps(sent) == json.dumps(legacy)
        compared.append(sent)
        return sent

    monkeypatch.setattr(agent, "_with_session_context", spy)
    _turn(
        agent,
        model,
        "keep my notes pinned",
        [
            _call("load_skill", "c1", name="pinned-notes"),
            _call("load_skill", "c2", name="second-notes"),
            _call("unload_skill", "c3", name="pinned-notes"),
            "Done.",
        ],
    )

    assert len(compared) >= 4
    assert NOTE_HEADER not in _text([_messages(r) for r in model.requests])
    assert MARKERS["second-notes"] in _first_user(_messages(model.requests[-1]))
