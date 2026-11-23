# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which program the daemon's GAIA agent runs, and what an ensure may ask of it.

A client that talks to the Python agent must never be handed gaiacpp (or the
reverse) just because another client started the shared sidecar first.
"""

from __future__ import annotations

import pytest

from gaia.daemon.sidecars import fetch
from gaia.daemon.sidecars.spec import builtin_specs


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "host"))


@pytest.fixture()
def saved(monkeypatch):
    """The saved ``flagship_agent``; gaiacpp unless a test sets it."""
    choice = {"value": fetch.NATIVE_FLAGSHIP}
    monkeypatch.setattr(fetch, "saved_flagship", lambda: choice["value"])
    return choice


class _Manager:
    """Starts on ``requested_program`` or the saved choice, like the real one."""

    def __init__(self, spec, mode=None, **kwargs):
        self.spec = spec
        self.mode = "user"
        self.resolved_mode = None
        self.mode_agnostic = True
        self.requested_program = None
        self.program = None
        self.port = self.base_url = self.pid = self.started_at = None
        self.api_version = "1.0"
        self.agent_version = "0.1.0"
        self.auth_token = "tok"
        self.uses_ignored_lemonade = False
        self.exited_unexpectedly = False
        self._running = False
        self.starts = 0
        self.stops = 0

    @property
    def is_running(self):
        return self._running

    def start(self):
        self.starts += 1
        self.program = self.requested_program or fetch.saved_flagship()
        self.resolved_mode = "user"
        self.pid = 4000 + self.starts
        self.port = 50000 + self.starts
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._running = True
        return self.base_url

    def shutdown(self):
        self.stops += 1
        self._running = False


def _registry():
    from gaia.daemon.sidecars.registry import SidecarRegistry

    reg = SidecarRegistry({"gaia": builtin_specs()["gaia"]})
    reg._manager_factory = _Manager
    return reg


def _manager(reg):
    return reg._managers["gaia"][0]


def test_entry_names_the_program_that_runs(saved):
    reg = _registry()
    assert reg.ensure("gaia")["program"] == fetch.NATIVE_FLAGSHIP
    listed = {a["agent_id"]: a for a in reg.list_agents()}
    assert listed["gaia"]["program"] == fetch.NATIVE_FLAGSHIP


def test_stopped_entry_names_no_program(saved):
    listed = {a["agent_id"]: a for a in _registry().list_agents()}
    assert listed["gaia"]["program"] is None


def test_expecting_another_program_than_the_running_one_is_refused(saved):
    from gaia.daemon.sidecars.errors import KIND_PROGRAM_CONFLICT, ProgramConflictError

    reg = _registry()
    reg.ensure("gaia")
    with pytest.raises(ProgramConflictError) as e:
        reg.ensure("gaia", program=fetch.PYTHON_FLAGSHIP)
    assert e.value.kind == KIND_PROGRAM_CONFLICT
    assert "running on gaiacpp, not gaiapy" in str(e.value)
    assert _manager(reg).stops == 0
    assert _manager(reg).program == fetch.NATIVE_FLAGSHIP


def test_expecting_a_program_that_is_not_saved_does_not_start_it(saved):
    from gaia.daemon.sidecars.errors import ProgramConflictError

    reg = _registry()
    with pytest.raises(ProgramConflictError, match="set to start on gaiacpp"):
        reg.ensure("gaia", program=fetch.PYTHON_FLAGSHIP)
    assert _manager(reg).starts == 0


def test_expecting_the_running_program_attaches(saved):
    reg = _registry()
    reg.ensure("gaia")
    entry = reg.ensure("gaia", program=fetch.NATIVE_FLAGSHIP)
    assert entry["program"] == fetch.NATIVE_FLAGSHIP
    assert _manager(reg).starts == 1


def test_replace_restarts_the_agent_on_the_program_asked_for(saved):
    reg = _registry()
    reg.ensure("gaia")
    entry = reg.ensure("gaia", program=fetch.PYTHON_FLAGSHIP, replace_program=True)
    manager = _manager(reg)
    assert entry["program"] == fetch.PYTHON_FLAGSHIP
    assert (manager.stops, manager.starts) == (1, 2)
    # Another client that expects it now attaches rather than conflicting.
    assert reg.ensure("gaia", program=fetch.PYTHON_FLAGSHIP)["program"] == "gaiapy"
    assert manager.starts == 2


def test_replace_starts_a_stopped_agent_on_the_program_asked_for(saved):
    reg = _registry()
    entry = reg.ensure("gaia", program=fetch.PYTHON_FLAGSHIP, replace_program=True)
    assert entry["program"] == fetch.PYTHON_FLAGSHIP


def test_an_ensure_without_a_program_starts_the_saved_choice_again(saved):
    reg = _registry()
    reg.ensure("gaia", program=fetch.PYTHON_FLAGSHIP, replace_program=True)
    reg.stop("gaia")
    assert reg.ensure("gaia")["program"] == fetch.NATIVE_FLAGSHIP


@pytest.mark.parametrize(
    "agent_id, program",
    [("gaia", "gaia-agent"), ("other", fetch.PYTHON_FLAGSHIP)],
)
def test_check_program_rejects_what_is_not_a_flagship_program(agent_id, program):
    from gaia.daemon.sidecars.registry import check_program

    with pytest.raises(ValueError):
        check_program(agent_id, program)


def _client(registry):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gaia.daemon.sidecars.routes import build_agents_router

    app = FastAPI()
    app.include_router(build_agents_router("tok", registry))
    return TestClient(app, raise_server_exceptions=False)


_AUTH = {"Authorization": "Bearer tok"}


def test_route_maps_a_program_conflict_to_409_with_its_kind(saved):
    from gaia.daemon.sidecars.errors import ERROR_KIND_HEADER, KIND_PROGRAM_CONFLICT

    reg = _registry()
    reg.ensure("gaia")
    r = _client(reg).post(
        "/daemon/v1/agents/gaia/ensure", json={"program": "gaiapy"}, headers=_AUTH
    )
    assert r.status_code == 409
    assert r.headers[ERROR_KIND_HEADER] == KIND_PROGRAM_CONFLICT


def test_route_passes_replace_through(saved):
    reg = _registry()
    reg.ensure("gaia")
    r = _client(reg).post(
        "/daemon/v1/agents/gaia/ensure",
        json={"program": "gaiapy", "replace_program": True},
        headers=_AUTH,
    )
    assert r.status_code == 200
    assert r.json()["program"] == "gaiapy"


@pytest.mark.parametrize(
    "body",
    [
        {"program": "nope"},
        {"program": 3},
        {"program": "gaiapy", "replace_program": "yes"},
    ],
)
def test_route_refuses_a_malformed_program_with_400(saved, body):
    r = _client(_registry()).post(
        "/daemon/v1/agents/gaia/ensure", json=body, headers=_AUTH
    )
    assert r.status_code == 400
