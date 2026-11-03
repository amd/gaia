# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-skill preferences: saved locally, checked against the skill's schema,
not written from a private chat, and listable/resettable from the Agent UI."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.skills import context as skill_context
from gaia.skills.errors import SkillValidationError
from gaia.skills.preferences import SkillPreferenceStore, load_schema

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "theme": {"type": "string", "enum": ["light", "dark"]},
        "max_slides": {"type": "integer", "minimum": 1, "maximum": 40},
        "fonts": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def skill_dir(tmp_path):
    folder = tmp_path / "slide-decks"
    folder.mkdir()
    (folder / "preferences.schema.json").write_text(json.dumps(SCHEMA))
    return folder


def test_update_merges_and_reset_forgets(home):
    store = SkillPreferenceStore()
    store.update("slide-decks", {"theme": "dark"})
    store.update("slide-decks", {"max_slides": 10})

    assert store.get("slide-decks") == {"theme": "dark", "max_slides": 10}
    assert (home / "skill-preferences" / "slide-decks.json").is_file()
    assert store.list() == [
        {"skill": "slide-decks", "preferences": {"max_slides": 10, "theme": "dark"}}
    ]

    store.update("slide-decks", {"theme": None})
    assert store.get("slide-decks") == {"max_slides": 10}
    assert store.reset("slide-decks") is True
    assert store.get("slide-decks") == {}
    assert store.reset("slide-decks") is False


@pytest.mark.parametrize(
    "bad",
    [
        {"theme": "neon"},
        {"max_slides": 0},
        {"max_slides": True},
        {"fonts": ["a", "b", "c", "d"]},
        {"fonts": [1]},
        {"colour": "red"},
    ],
)
def test_the_schema_refuses_a_bad_write_and_keeps_the_old_one(home, skill_dir, bad):
    store = SkillPreferenceStore()
    schema = load_schema(skill_dir)
    store.update("slide-decks", {"theme": "light"}, schema=schema)

    with pytest.raises(SkillValidationError):
        store.update("slide-decks", bad, schema=schema)
    assert store.get("slide-decks") == {"theme": "light"}


def test_a_schema_keyword_that_is_not_enforced_is_refused(tmp_path):
    (tmp_path / "preferences.schema.json").write_text(
        json.dumps({"type": "object", "patternProperties": {}})
    )
    with pytest.raises(SkillValidationError, match="patternProperties"):
        load_schema(tmp_path)


def test_a_skill_name_cannot_point_outside_the_store(home):
    with pytest.raises(SkillValidationError):
        SkillPreferenceStore().get("../config")


# --------------------------------------------------------------------------
# From a skill tool: private chats and memory-off skip writes, reads work
# --------------------------------------------------------------------------


def _call(agent, skill_dir):
    skill = SimpleNamespace(
        name="slide-decks", directory=skill_dir, parsed_permissions=lambda: []
    )
    return skill_context.bind(agent, skill)


def test_a_skill_tool_reads_and_writes_its_preferences(home, skill_dir):
    with _call(SimpleNamespace(_incognito=False), skill_dir):
        prefs = skill_context.preferences()
        write = prefs.update({"theme": "dark"})
        assert write.saved and write.preferences == {"theme": "dark"}
        assert prefs.get() == {"theme": "dark"}
        with pytest.raises(SkillValidationError):
            prefs.update({"theme": "neon"})


@pytest.mark.parametrize("reason", ["private", "memory_off"])
def test_a_private_chat_reads_but_does_not_save(home, skill_dir, reason):
    SkillPreferenceStore().update("slide-decks", {"theme": "light"})
    agent = SimpleNamespace(_incognito=True, _incognito_reason=reason)

    with _call(agent, skill_dir):
        prefs = skill_context.preferences()
        assert prefs.get() == {"theme": "light"}
        write = prefs.update({"theme": "dark"})
        reset = prefs.reset()

    assert write.saved is False and reset.saved is False
    assert "not saved" in write.note
    assert SkillPreferenceStore().get("slide-decks") == {"theme": "light"}


# --------------------------------------------------------------------------
# Agent UI endpoints
# --------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, skill_dir):
    from gaia.ui.routers import skills as skills_router

    monkeypatch.setattr(
        skills_router, "_preference_schema", lambda name: load_schema(skill_dir)
    )
    app = FastAPI()
    app.include_router(skills_router.router)
    return TestClient(app)


def test_the_ui_lists_edits_and_resets_preferences(home, client):
    assert client.get("/api/skills/preferences").json() == {"skills": []}

    put = client.put("/api/skills/slide-decks/preferences", json={"theme": "dark"})
    assert put.status_code == 200, put.text
    assert client.get("/api/skills/slide-decks/preferences").json() == {
        "skill": "slide-decks",
        "preferences": {"theme": "dark"},
    }
    assert client.get("/api/skills/preferences").json()["skills"][0]["skill"] == (
        "slide-decks"
    )

    bad = client.put("/api/skills/slide-decks/preferences", json={"theme": "neon"})
    assert bad.status_code == 422

    assert client.delete("/api/skills/slide-decks/preferences").json()["reset"] is True
    assert client.get("/api/skills/slide-decks/preferences").json()["preferences"] == {}
    assert not Path(home / "skill-preferences" / "slide-decks.json").exists()
