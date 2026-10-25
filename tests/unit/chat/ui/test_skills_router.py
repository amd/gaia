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


class _FakeHandle:
    """What ``acquire_handle`` returns for a running Hub-binary flagship."""

    def __init__(self, api_version, payload=None, error=None):
        self.api_version = api_version
        self._payload = payload
        self._error = error

    def proxy(self):
        handle = self

        class _Proxy:
            def skills(self):
                if handle._error is not None:
                    raise handle._error
                return handle._payload

        return _Proxy()


def _no_agent_package(monkeypatch, handle=None, acquire_error=None):
    """The Hub-binary install: no ``gaia_agent`` package beside the UI."""
    from gaia.ui.email_sidecar import daemon_client

    monkeypatch.setitem(sys.modules, "gaia_agent.agent", None)
    calls = []

    def acquire(agent_id):
        calls.append(agent_id)
        if acquire_error is not None:
            raise acquire_error
        return handle

    monkeypatch.setattr(daemon_client, "acquire_handle", acquire)
    return calls


def test_a_hub_binary_agent_lists_its_skills_through_the_daemon(client, monkeypatch):
    """The bundled skills live inside the binary; the page used to say the agent
    was not installed and to run ``gaia init``, which changed nothing."""
    payload = {
        "skills": [
            {
                "name": "web-research",
                "description": "Research",
                "version": "1.0",
                "tier": "verified",
                "tools": [],
                "path": "",
            }
        ],
        "invalid": {},
    }
    calls = _no_agent_package(monkeypatch, handle=_FakeHandle("2.17", payload))

    resp = client.get("/api/skills")

    assert resp.status_code == 200, resp.text
    assert resp.json() == payload
    assert calls == ["gaia"]


def test_an_agent_too_old_to_list_skills_says_to_update_it(client, monkeypatch):
    _no_agent_package(monkeypatch, handle=_FakeHandle("2.16", {"skills": []}))

    resp = client.get("/api/skills")

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "Update GAIA" in detail
    assert "2.17" not in detail and "contract" not in detail
    assert "gaia init" not in detail


def test_an_agent_that_cannot_start_is_reported_not_hidden(client, monkeypatch):
    from gaia.ui.email_sidecar.errors import SidecarError

    _no_agent_package(
        monkeypatch, acquire_error=SidecarError("daemon not reachable at :59171")
    )

    resp = client.get("/api/skills")

    assert resp.status_code == 503
    assert "daemon not reachable at :59171" in resp.json()["detail"]


def test_an_agent_that_fails_to_list_is_a_502_with_its_reason(client, monkeypatch):
    from gaia.ui.email_sidecar.errors import SidecarError

    _no_agent_package(
        monkeypatch,
        handle=_FakeHandle("2.17", error=SidecarError("skills root unreadable")),
    )

    resp = client.get("/api/skills")

    assert resp.status_code == 502
    assert "skills root unreadable" in resp.json()["detail"]


def test_the_proxy_asks_the_flagships_skills_route():
    """Pin the outgoing call, not just that one was made."""
    from gaia.ui.email_sidecar.proxy import SidecarProxy

    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"skills": [], "invalid": {}}

    class _Session:
        headers: dict = {}

        def get(self, url, params=None, timeout=None):
            seen["url"] = url
            return _Resp()

    proxy = SidecarProxy(
        "http://127.0.0.1:51709", agent_id="gaia", session=_Session(), timeout=5
    )

    assert proxy.skills() == {"skills": [], "invalid": {}}
    assert seen["url"] == "http://127.0.0.1:51709/v1/gaia/skills"


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


# --- Adding skills from a pasted link ----------------------------------------


def test_preview_returns_the_skills_and_warning(client, monkeypatch):
    from gaia.skills import link

    preview = link.LinkPreview(
        link="acme/skills",
        source="acme/skills",
        skills=[link.LinkSkill(name="deslop", description="d", path="skills/deslop")],
    )
    monkeypatch.setattr(link, "preview_link", lambda url: preview)

    body = client.post("/api/skills/link/preview", json={"url": "acme/skills"}).json()

    assert body["skills"][0]["name"] == "deslop"
    assert body["warning"] == link.SECURITY_WARNING


def test_a_bad_link_is_a_400_naming_why(client, monkeypatch):
    from gaia.skills import link

    def refuse(url):
        raise link.SkillLinkError("only https links are accepted")

    monkeypatch.setattr(link, "preview_link", refuse)
    r = client.post("/api/skills/link/preview", json={"url": "http://x"})
    assert r.status_code == 400
    assert "https" in r.json()["detail"]


def test_add_passes_the_chosen_names_and_code_opt_in(client, monkeypatch):
    from gaia.skills import link

    seen = {}

    def fake_add(url, names, *, allow_code, force, pin):
        seen.update(url=url, names=names, allow_code=allow_code, force=force, pin=pin)
        return link.LinkAddResult(added=[{"name": "deslop"}], failed={"x": "why"})

    monkeypatch.setattr(link, "add_from_link", fake_add)
    body = client.post(
        "/api/skills/link/add",
        json={
            "url": "acme/skills",
            "names": ["deslop"],
            "allow_code": True,
            "pin": "commit:" + "a" * 40,
        },
    ).json()

    assert seen == {
        "url": "acme/skills",
        "names": ["deslop"],
        "allow_code": True,
        "force": False,
        "pin": "commit:" + "a" * 40,
    }
    assert body == {"added": [{"name": "deslop"}], "failed": {"x": "why"}}


def test_featured_libraries_include_pstack(client):
    body = client.get("/api/skills/featured").json()
    assert [lib["id"] for lib in body["libraries"]] == ["pstack"]
