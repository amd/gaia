# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GET /v1/gaia/skills`` (contract 2.17): the skills the agent can load.

The Agent UI's Skills settings read this when the agent is the Hub binary: its
bundled skills live inside the executable, so the UI cannot discover them on
disk. Driven over the real app so the route, its auth and its payload are what
a caller actually gets.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent import caller_auth  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402

import gaia.skills  # noqa: E402
from gaia.skills.errors import SkillError  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"


@pytest.fixture
def client(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    try:
        yield TestClient(server_mod.build_app(), base_url=_BASE_URL)
    finally:
        caller_auth.reset()


def test_lists_the_bundled_skills_without_the_engineering_one(
    client, tmp_path, monkeypatch
):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / ".gaia"))
    monkeypatch.chdir(tmp_path)

    r = client.get("/v1/gaia/skills")

    assert r.status_code == 200, r.text
    body = r.json()
    names = {s["name"] for s in body["skills"]}
    assert names, "the agent's bundled skills were not discovered"
    assert "gaia-harness-engineering" not in names
    assert set(body["skills"][0]) == {
        "name",
        "description",
        "version",
        "tier",
        "tools",
        "path",
        "always_on",
    }
    assert next(s for s in body["skills"] if s["name"] == "gaia-voice")["always_on"] is True
    assert isinstance(body["invalid"], dict)


def test_an_unreadable_skill_root_is_a_500_naming_it(client, monkeypatch):
    class BrokenManager:
        discovery_errors: dict = {}

        def __init__(self, **_kwargs):
            pass

        def reload(self):
            raise SkillError("skills root C:/x unreadable")

    monkeypatch.setattr(gaia.skills, "SkillManager", BrokenManager)

    r = client.get("/v1/gaia/skills")

    assert r.status_code == 500
    assert r.json()["detail"] == "skills root C:/x unreadable"


def test_the_contract_version_advertises_the_route():
    assert tuple(int(p) for p in server_mod.API_VERSION.split(".")) >= (2, 17)
