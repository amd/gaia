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


def test_a_link_with_nothing_behind_it_names_no_terminal_command(monkeypatch):
    """The terminal app's user may have no `gaia` on PATH either (#7973)."""

    def missing(url, manager=None):
        raise link.SkillLinkNotFoundError("Nothing found at https://x/y.")

    monkeypatch.setattr(link, "preview_link", missing)
    detail = _events(_Agent(), "/skills add acme/none")[-1]["detail"]
    assert "gaia skill" not in detail
    assert "skills folder inside .gaia" in detail


@pytest.mark.parametrize("query", ["/skills add --allow-code", "/skills add confirm"])
def test_add_without_a_link_explains_usage(query):
    events = _events(_Agent(), query)
    assert events[-1]["type"] == "error"
    assert "/skills add <link>" in events[-1]["detail"]


def test_unknown_subcommand_explains_usage():
    events = _events(_Agent(), "/skills remove x")
    assert events[-1]["type"] == "error"
    assert "/skills add <link>" in events[-1]["detail"]


_PIN = "commit:" + "b" * 40


def _preview_with_grants():
    return link.LinkPreview(
        link="acme/skills",
        source="acme/skills",
        pin=_PIN,
        skills=[
            link.LinkSkill(
                name="fetcher",
                description="reads docs",
                path="s/fetcher",
                permissions=[
                    {
                        "grant": "network:read:*.example.com",
                        "text": "Read pages and data from sites matching "
                        "*.example.com",
                    }
                ],
                findings=["[REVIEW] fetches remote content"],
                verdict="REVIEW",
            ),
            link.LinkSkill(name="plain", description="notes", path="s/plain"),
            link.LinkSkill(
                name="garbled",
                description="",
                path="s/garbled",
                permissions_unreadable=True,
                error="unknown permission 'teleport:everywhere'",
            ),
            link.LinkSkill(
                name="shelly",
                description="",
                path="s/shelly",
                permissions=[
                    {"grant": "shell:execute", "text": "Run any command on this PC"}
                ],
                error="asks for the whole shell",
            ),
        ],
    )


def test_preview_says_what_each_skill_may_do(monkeypatch):
    monkeypatch.setattr(
        link, "preview_link", lambda url, manager=None: _preview_with_grants()
    )

    answer = _events(_Agent(), "/skills add acme/skills")[-1]["answer"]

    assert (
        "Read pages and data from sites matching *.example.com "
        "(`network:read:*.example.com`)" in answer
    )
    assert "Audit: [REVIEW] fetches remote content" in answer
    assert answer.count("no permissions declared") == 1
    garbled = answer[answer.index("`garbled`") :].split("\n- ", 1)[0]
    assert "permissions could not be read" in garbled
    assert "no permissions declared" not in garbled
    # A refused skill still shows what it asked for, so the refusal makes sense.
    assert "Run any command on this PC (`shell:execute`)" in answer


def test_confirm_without_a_preview_previews_instead_of_adding(monkeypatch):
    monkeypatch.setattr(
        link, "preview_link", lambda url, manager=None: _preview_with_grants()
    )
    monkeypatch.setattr(
        link,
        "add_from_link",
        lambda *a, **k: pytest.fail("added a link nobody previewed"),
    )
    agent = _Agent()

    answer = _events(agent, "/skills add acme/skills confirm")[-1]["answer"]

    assert answer.startswith("**Nothing was added yet.**")
    assert link.SECURITY_WARNING in answer
    assert "Read pages and data from sites matching" in answer
    assert agent._skill_link_pins == {"acme/skills": _PIN}


def test_confirm_for_a_different_link_does_not_reuse_another_pin(monkeypatch):
    monkeypatch.setattr(
        link, "preview_link", lambda url, manager=None: _preview_with_grants()
    )
    monkeypatch.setattr(
        link, "add_from_link", lambda *a, **k: pytest.fail("used another link's pin")
    )
    agent = _Agent()
    agent._skill_link_pins = {"acme/skills": _PIN}

    answer = _events(agent, "/skills add evil/skills confirm")[-1]["answer"]

    assert answer.startswith("**Nothing was added yet.**")


def test_preview_then_confirm_adds_the_previewed_pin_once(monkeypatch):
    monkeypatch.setattr(
        link, "preview_link", lambda url, manager=None: _preview_with_grants()
    )
    pins = []

    def fake_add(url, names=None, *, allow_code, pin, manager):
        pins.append(pin)
        return link.LinkAddResult(added=[{"name": "fetcher"}])

    monkeypatch.setattr(link, "add_from_link", fake_add)
    agent = _Agent()

    _events(agent, "/skills add acme/skills")
    _events(agent, "/skills add acme/skills confirm")
    # The pin is spent: confirming again re-previews rather than re-adding.
    again = _events(agent, "/skills add acme/skills confirm")[-1]["answer"]

    assert pins == [_PIN]
    assert again.startswith("**Nothing was added yet.**")


def test_a_changed_link_is_refused_and_must_be_previewed_again(monkeypatch):
    def changed(*a, **k):
        raise link.SkillLinkChangedError("acme/skills changed since you previewed it")

    monkeypatch.setattr(link, "add_from_link", changed)
    agent = _Agent()
    agent._skill_link_pins = {"acme/skills": _PIN}

    events = _events(agent, "/skills add acme/skills confirm")

    assert events[-1]["type"] == "error"
    assert "changed since you previewed it" in events[-1]["detail"]
    assert "acme/skills" not in agent._skill_link_pins
