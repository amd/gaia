# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The runner wipes long-term memory before each scenario that uses it.

Without the reset, facts one memory scenario stored answered for the next one
in the same lane. The reset goes through the real admin endpoint here, against
a throwaway store, so the request shape is checked, not just that a call happened.
"""

import io
import json
import urllib.error
from pathlib import Path

import pytest
import yaml

from gaia.eval import runner

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = REPO_ROOT / "eval" / "scenarios"


@pytest.mark.parametrize(
    "scenario,clears",
    [
        ({"category": "memory"}, True),
        ({"category": "gaia_memory"}, True),
        ({"category": "gaia_skills_tasks"}, True),
        ({"category": "gaia_core", "setup": {"index_documents": []}}, True),
        ({"category": "rag_quality"}, False),
        ({"category": "rag_quality", "setup": {"memory_seed": []}}, True),
        ({"category": "rag_quality", "setup": {"memory_clear": True}}, True),
        ({"category": "memory", "setup": {"memory_clear": False}}, False),
        ({"category": "gaia_memory", "setup": {"memory_clear": False}}, False),
        # Only a real false opts out; a falsy non-bool is a validation error.
        ({"category": "memory", "setup": {"memory_clear": 0}}, True),
        ({"category": "memory", "setup": {"memory_clear": None}}, True),
        ({"category": "memory", "setup": None}, True),
        ({"category": None}, False),
        ({}, False),
        (None, False),
    ],
)
def test_which_scenarios_start_from_an_empty_store(scenario, clears):
    assert runner._scenario_clears_memory(scenario) is clears


def _scenario_files(category):
    return sorted((SCENARIOS / category).glob("*.yaml"))


@pytest.mark.parametrize("category", ["memory", "gaia_memory"])
def test_every_memory_scenario_starts_clean(category):
    """None of them reads another scenario's memory; each stores its own facts."""
    files = _scenario_files(category)
    assert files, f"no scenarios found under {SCENARIOS / category}"
    kept = [
        f.name
        for f in files
        if not runner._scenario_clears_memory(
            yaml.safe_load(f.read_text(encoding="utf-8"))
        )
    ]
    assert kept == []


def test_no_scenario_in_the_tree_sets_a_non_bool_memory_clear():
    bad = []
    for path in SCENARIOS.rglob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        value = (data.get("setup") or {}).get("memory_clear")
        if value is not None and not isinstance(value, bool):
            bad.append(path.name)
    assert bad == []


@pytest.mark.parametrize("value", ["all", 1, 0, "false", []])
def test_validation_rejects_a_non_bool_memory_clear(value):
    data = {
        "id": "s",
        "category": "memory",
        "persona": "casual_user",
        "setup": {"index_documents": [], "memory_clear": value},
        "turns": [{"turn": 1, "objective": "x", "success_criteria": "y"}],
    }
    with pytest.raises(ValueError, match="setup.memory_clear must be true or false"):
        runner.validate_scenario(Path("s.yaml"), data)


@pytest.mark.parametrize("value", [True, False])
def test_validation_accepts_a_bool_memory_clear(value):
    data = {
        "id": "s",
        "category": "memory",
        "persona": "casual_user",
        "setup": {"index_documents": [], "memory_clear": value},
        "turns": [{"turn": 1, "objective": "x", "success_criteria": "y"}],
    }
    runner.validate_scenario(Path("s.yaml"), data)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def backend(monkeypatch, tmp_path):
    """The real memory router over a throwaway store, reached through urlopen."""
    from fastapi.testclient import TestClient

    from gaia.agents.base.memory_store import MemoryStore
    from gaia.ui.routers import memory as memory_router
    from gaia.ui.server import create_app

    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home" / ".gaia"))
    store = MemoryStore(db_path=tmp_path / "memory.db")
    monkeypatch.setattr(memory_router, "_store", store)
    client = TestClient(create_app(db_path=":memory:"))
    sent = []

    def urlopen(req, timeout=None):
        sent.append(req)
        resp = client.request(
            req.get_method(),
            req.full_url.replace("http://b", ""),
            content=req.data,
            headers=dict(req.header_items()),
        )
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                req.full_url, resp.status_code, resp.text, {}, None
            )
        return _Resp(resp.content)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    yield client, store, sent
    store.close()


def _seed(client):
    resp = client.post(
        "/api/memory/admin/seed",
        json={"items": [{"content": "User's name is Riley", "category": "fact"}]},
        headers={"X-Gaia-UI": "1"},
    )
    assert resp.status_code == 200, resp.text


def test_reset_wipes_what_an_earlier_scenario_stored(monkeypatch, backend):
    client, store, sent = backend
    monkeypatch.setenv("GAIA_MEMORY_ADMIN", "1")
    _seed(client)
    assert store.get_stats()["knowledge"]["total"] == 1

    assert runner._reset_memory("http://b", {"category": "memory"}) is None

    assert store.get_stats()["knowledge"]["total"] == 0
    assert len(sent) == 1
    assert sent[0].full_url == "http://b/api/memory/admin/clear"
    assert sent[0].get_method() == "POST"
    assert json.loads(sent[0].data) == {"scope": "all"}


def test_reset_leaves_memory_alone_for_an_opted_out_scenario(monkeypatch, backend):
    client, store, sent = backend
    monkeypatch.setenv("GAIA_MEMORY_ADMIN", "1")
    _seed(client)

    scenario = {"category": "memory", "setup": {"memory_clear": False}}
    assert runner._reset_memory("http://b", scenario) is None

    assert sent == []
    assert store.get_stats()["knowledge"]["total"] == 1


def test_reset_never_touches_a_scenario_without_memory(monkeypatch, backend):
    _client, _store, sent = backend
    monkeypatch.setenv("GAIA_MEMORY_ADMIN", "1")
    assert runner._reset_memory("http://b", {"category": "rag_quality"}) is None
    assert sent == []


def test_reset_without_admin_is_a_named_error(monkeypatch, backend):
    client, store, _sent = backend
    monkeypatch.setenv("GAIA_MEMORY_ADMIN", "1")
    _seed(client)
    monkeypatch.delenv("GAIA_MEMORY_ADMIN")

    error = runner._reset_memory("http://b", {"category": "gaia_memory"})

    assert "403" in error and "GAIA_MEMORY_ADMIN=1" in error
    assert "memory_clear: false" in error
    assert store.get_stats()["knowledge"]["total"] == 1


@pytest.mark.parametrize(
    "body",
    [
        b"<html>proxy</html>",
        b"[]",
        b'{"scope": "knowledge", "cleared": {}}',
        b'{"scope": "all"}',
        b'{"scope": "all", "cleared": null}',
    ],
)
def test_a_reset_the_backend_does_not_confirm_is_an_error(monkeypatch, body):
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Resp(body))
    error = runner._reset_memory("http://b", {"category": "memory"})
    assert error and "http://b/api/memory/admin/clear" in error


def test_unreachable_backend_is_named(monkeypatch):
    def refuse(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    error = runner._reset_memory("http://127.0.0.1:1", {"category": "memory"})
    assert "connection refused" in error and "backend running" in error


def test_failed_reset_stops_the_scenario_before_the_driver_runs(monkeypatch):
    monkeypatch.setattr(runner, "_apply_mailbox_fixture", lambda *_a: None)
    monkeypatch.setattr(runner, "_reset_memory", lambda *_a: "admin is off")

    def no_driver(*_a, **_k):
        raise AssertionError("the driver must not run after a failed reset")

    monkeypatch.setattr(runner, "_run_driver", no_driver)
    monkeypatch.setattr(runner, "_load_merged_manifest", no_driver)

    result = runner.run_scenario_subprocess(
        None, {"id": "s", "category": "memory"}, None, "http://b", "m", "1", 10
    )

    assert result["status"] == "SETUP_ERROR"
    assert result["error"] == "admin is off"


def test_mailbox_error_wins_and_skips_the_reset(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "_apply_mailbox_fixture", lambda *_a: "no mailbox")
    monkeypatch.setattr(
        runner, "_reset_memory", lambda *_a: calls.append("reset") or None
    )
    result = runner.run_scenario_subprocess(
        None, {"id": "s", "category": "gaia_email"}, None, "http://b", "m", "1", 10
    )
    assert result["error"] == "no mailbox" and calls == []


def test_preflight_requires_admin_for_any_scenario_the_runner_clears(monkeypatch):
    probed = []
    monkeypatch.setattr(
        runner, "_probe_memory_admin", lambda url: probed.append(url) or "no admin"
    )
    monkeypatch.setattr(runner, "_probe_memory_enabled", lambda url: None)
    monkeypatch.setattr(runner, "_probe_eval_mailbox", lambda url: None)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)

    errors = runner.preflight_check(
        "http://b", scenarios=[(None, {"category": "gaia_skills_tasks"})]
    )
    assert "no admin" in errors

    probed.clear()
    errors = runner.preflight_check(
        "http://b",
        scenarios=[
            (None, {"category": "rag_quality"}),
            (None, {"category": "memory", "setup": {"memory_clear": False}}),
        ],
    )
    assert "no admin" not in errors and probed == []
