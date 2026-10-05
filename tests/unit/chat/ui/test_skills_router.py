# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``GET /api/skills`` lists what the GAIA agent can load, never the dev-only skill."""

import sys
import types
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import gaia.skills
from gaia.skills.errors import SkillError
from gaia.ui.routers import skills as skills_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(skills_router.router)
    return TestClient(app)


def _fake_agent_module(monkeypatch, skill_dirs):
    mod = types.ModuleType("gaia_agent.agent")
    mod.ENGINEERING_SKILL = "gaia-harness-engineering"
    mod.GaiaAgent = type("GaiaAgent", (), {"SKILL_DIRS": skill_dirs})
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", mod)


def _skill(name, **kw):
    return SimpleNamespace(
        name=name,
        description=kw.get("description", ""),
        version=kw.get("version"),
        security_tier=kw.get("tier", "experimental"),
        tool_names=kw.get("tools", ()),
        root=kw.get("root"),
    )


def test_lists_with_the_agents_roots_and_excludes_engineering(client, monkeypatch):
    _fake_agent_module(monkeypatch, ["/bundled/a", "/bundled/b"])
    seen = {}

    class FakeManager:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.discovery_errors = {"/bad/SKILL.md": "missing name"}

        def reload(self):
            return {
                "zeta": _skill("zeta", root="/bundled/a/zeta", tools=("t1",)),
                "alpha": _skill(
                    "alpha", description="A", version="1.0", tier="verified"
                ),
            }

    monkeypatch.setattr(gaia.skills, "SkillManager", FakeManager)
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    assert seen == {
        "agent_skill_dirs": ["/bundled/a", "/bundled/b"],
        "excluded_names": ("gaia-harness-engineering",),
    }
    body = resp.json()
    assert body["invalid"] == {"/bad/SKILL.md": "missing name"}
    assert body["skills"] == [
        {
            "name": "alpha",
            "description": "A",
            "version": "1.0",
            "tier": "verified",
            "tools": [],
            "path": "",
        },
        {
            "name": "zeta",
            "description": "",
            "version": None,
            "tier": "experimental",
            "tools": ["t1"],
            "path": "/bundled/a/zeta",
        },
    ]


def test_missing_agent_package_is_503(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "gaia_agent.agent", None)
    resp = client.get("/api/skills")
    assert resp.status_code == 503
    assert "gaia init" in resp.json()["detail"]


def test_discovery_failure_is_500(client, monkeypatch):
    _fake_agent_module(monkeypatch, [])

    class BrokenManager:
        discovery_errors = {}

        def __init__(self, **_kwargs):
            pass

        def reload(self):
            raise SkillError("skills root unreadable")

    monkeypatch.setattr(gaia.skills, "SkillManager", BrokenManager)
    resp = client.get("/api/skills")
    assert resp.status_code == 500
    assert resp.json()["detail"] == "skills root unreadable"


def test_real_discovery_hides_the_engineering_skill(client, mock_home, monkeypatch):
    """Against the real GaiaAgent roots: bundled skills listed, dev skill not."""
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(mock_home / ".gaia"))
    monkeypatch.chdir(mock_home)
    pytest.importorskip("gaia_agent.agent")
    resp = client.get("/api/skills")
    assert resp.status_code == 200, resp.text
    names = {s["name"] for s in resp.json()["skills"]}
    assert names, "the flagship's bundled skills were not discovered"
    assert "gaia-harness-engineering" not in names
