# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/skills`` over stdio: the TUI's way to add skills from a pasted link."""

import io
import json

import pytest
from gaia_agent import stdio

from gaia.skills import link


class _Manager:
    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1
        return {"deslop": object(), "bro": object()}


class _Agent:
    def __init__(self):
        self.skill_manager = _Manager()
        self.rebuilt = 0

    def rebuild_system_prompt(self):
        self.rebuilt += 1


def _events(agent, query):
    out = io.StringIO()
    stdio.run_skills_command(agent, query, out)
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


@pytest.mark.parametrize("query", ["/skills", "/skills add acme/skills", " /skills "])
def test_skills_lines_are_commands_not_questions(query):
    assert stdio.is_skills_command(query)


def test_skillsy_questions_still_reach_the_model():
    assert not stdio.is_skills_command("/skillset please")
    assert not stdio.is_skills_command("what skills do you have?")


def test_bare_skills_lists_what_is_installed():
    events = _events(_Agent(), "/skills")
    assert events[-1]["type"] == "final"
    assert "`bro`, `deslop`" in events[-1]["answer"]


def test_add_without_confirm_previews_and_warns_but_adds_nothing(monkeypatch):
    preview = link.LinkPreview(
        link="acme/skills",
        source="acme/skills",
        skills=[
            link.LinkSkill(name="deslop", description="tidy", path="s/deslop"),
            link.LinkSkill(
                name="runner", description="runs", path="s/runner", has_code=True
            ),
        ],
    )
    monkeypatch.setattr(link, "preview_link", lambda url, manager=None: preview)
    monkeypatch.setattr(
        link, "add_from_link", lambda *a, **k: pytest.fail("added without confirm")
    )

    answer = _events(_Agent(), "/skills add acme/skills")[-1]["answer"]

    assert link.SECURITY_WARNING in answer
    assert "`runner` (ships code, skipped)" in answer
    assert "/skills add acme/skills confirm" in answer


def test_confirm_adds_and_refreshes_the_prompt(monkeypatch):
    seen = {}

    def fake_add(url, names=None, *, allow_code, pin, manager):
        seen.update(url=url, allow_code=allow_code, pin=pin)
        return link.LinkAddResult(
            added=[{"name": "deslop"}], failed={"runner": "ships code"}
        )

    monkeypatch.setattr(link, "add_from_link", fake_add)
    agent = _Agent()
    agent._skill_link_pins = {"acme/skills": "commit:" + "a" * 40}

    answer = _events(agent, "/skills add acme/skills --allow-code confirm")[-1][
        "answer"
    ]

    assert seen == {
        "url": "acme/skills",
        "allow_code": True,
        "pin": "commit:" + "a" * 40,
    }
    assert "Added 1 skill(s)" in answer and "`runner` — ships code" in answer
    assert agent.rebuilt == 1


def test_a_bad_link_ends_in_one_error_event(monkeypatch):
    def refuse(url, manager=None):
        raise link.SkillLinkError("only https links are accepted")

    monkeypatch.setattr(link, "preview_link", refuse)
    events = _events(_Agent(), "/skills add http://x")
    assert events[-1] == {"type": "error", "detail": "only https links are accepted"}
    assert sum(e["type"] in ("final", "error") for e in events) == 1


@pytest.mark.parametrize("query", ["/skills add --allow-code", "/skills add confirm"])
def test_add_without_a_link_explains_usage(query):
    events = _events(_Agent(), query)
    assert events[-1]["type"] == "error"
    assert "/skills add <link>" in events[-1]["detail"]


def test_unknown_subcommand_explains_usage():
    events = _events(_Agent(), "/skills remove x")
    assert events[-1]["type"] == "error"
    assert "/skills add <link>" in events[-1]["detail"]
