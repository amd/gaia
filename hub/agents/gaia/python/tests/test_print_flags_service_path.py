# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia -p`` on an installed GAIA: its flags reach the agent's service (#8495).

There the agent is its own program, so ``gaia -p`` relays the turn through
GAIA's background service. These run ``gaia -p`` end to end against the real
sidecar app, the HTTP hop stubbed into a test client, and check what each flag
did to the run. The outgoing request is checked against the sidecar's own
request model, so a field the agent would refuse can't pass here.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("gaiapy")

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from gaiapy import caller_auth  # noqa: E402
from gaiapy import server as server_mod  # noqa: E402
from gaiapy import session_registry as sr  # noqa: E402

from gaia import print_mode  # noqa: E402
from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.daemon import agent_query  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"
_TOKEN = "test-caller-token"

_TOOLS = ("read_file", "write_file", "run_shell_command", "search_web", "load_skill")


class _Agent:
    """GaiaAgent's tool gate, with a turn that writes a file."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._tools_registry = {name: {} for name in _TOOLS}
        self._denied_tool_patterns = ()
        self._learned_skills_enabled = True
        self.time_limit_s = None
        self.offered = None
        self.approved = None

    deny_tools = Agent.deny_tools

    def get_tools_info(self):
        return dict(self._tools_registry)

    def rebuild_system_prompt(self):
        pass

    def process_query(self, query, max_steps=None):
        self.offered = sorted(
            n for n in self._tools_registry if Agent._tool_permitted(self, n)
        )
        if self.time_limit_s:
            self._cancel_event.wait(timeout=10)
            return {
                "status": "incomplete",
                "result": self._cancel_event.message,
                "time_limit_reached": True,
            }
        self.approved = self.console.confirm_tool_execution(
            "write_file", {"file_path": "/tmp/greeting.txt", "content": "hi"}
        )
        return {"answer": f"approved={self.approved}"}

    def close(self):
        pass


class _Streamed:
    """A test-client response, shaped as the ``requests`` stream run_query reads."""

    def __init__(self, response):
        self._response = response
        self.status_code = response.status_code

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def iter_lines(self, decode_unicode=True):
        yield from self._response.text.split("\n")

    def json(self):
        return self._response.json()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture
def service(monkeypatch):
    """The sidecar app behind a stubbed daemon relay; records each request."""
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.setenv(caller_auth.TOKEN_ENV_VAR, _TOKEN)
    monkeypatch.delenv("GAIA_AUTO_APPROVE_TOOLS", raising=False)
    agents: list = []

    def build(**kw):
        agents.append(_Agent(**kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    app = TestClient(
        server_mod.build_app(),
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    sent: list = []
    state = SimpleNamespace(agents=agents, sent=sent, app=app, api_version=None)

    def post(url, headers=None, json=None, stream=False, timeout=None):
        sent.append(json)
        return _Streamed(app.post(url[len(_BASE_URL) :], json=json))

    def get(url, headers=None, timeout=None):
        response = app.get(url[len(_BASE_URL) :])
        if state.api_version is not None:
            response._content = b'{"apiVersion": "' + state.api_version.encode() + b'"}'
        return _Streamed(response)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(
        agent_query.client,
        "ensure_agent",
        lambda _agent_id: SimpleNamespace(base_url=_BASE_URL, token="daemon"),
    )
    # The installed layout: the agent package is not importable in this process.
    monkeypatch.setitem(sys.modules, "gaiapy.agent", None)
    monkeypatch.setattr(
        print_mode, "_route", lambda text, model, *a, **k: (model, None, None)
    )
    monkeypatch.setattr(print_mode, "_model_on_disk", lambda model, *a, **k: model)
    monkeypatch.setattr("gaia.logger.configure", lambda *a, **k: MagicMock())
    try:
        yield state
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _run(capsys, **flags):
    code = print_mode.run("write a greeting", model="m", output_format="json", **flags)
    return code, json.loads(capsys.readouterr().out)


def test_every_request_gaia_p_sends_is_one_the_agent_accepts(service, capsys):
    _run(
        capsys,
        time_limit=1,
        disallowed_tools=("run_shell_command",),
        no_web=True,
        no_skills=True,
        full_access=True,
    )
    [payload] = service.sent
    request = server_mod.QueryRequest.model_validate(payload)
    assert request.session_id is None
    assert request.surface == "cli" and request.can_answer_questions is False
    for field in server_mod.RUN_SCOPED_FIELDS - {"skip_permissions"}:
        assert getattr(request, field), field


def test_unset_flags_are_left_out_of_the_request(service, capsys):
    _run(capsys)
    [payload] = service.sent
    assert not set(payload) & server_mod.RUN_SCOPED_FIELDS


def test_without_a_flag_the_gated_write_stops_and_names_the_flag(service, capsys):
    code, record = _run(capsys)
    assert code == 1
    assert service.agents[-1].approved is False
    assert "--dangerously-skip-permissions" in record["error"]
    assert "surface that supports confirmation" not in record["error"]


@pytest.mark.parametrize("flag", ["skip_permissions", "full_access"])
def test_a_permission_flag_lets_the_gated_write_run(service, capsys, flag):
    code, record = _run(capsys, **{flag: True})
    assert (code, record["result"]) == (0, "approved=True")
    assert service.agents[-1].approved is True


def test_the_override_does_not_reach_a_chat_on_the_same_service(service, capsys):
    assert _run(capsys, full_access=True)[0] == 0
    # An Agent UI chat on the same sidecar, right after.
    body = {
        "query": "write a greeting",
        "run_id": "5b0c4b8e-1f0e-4b8a-9f57-2b6c9f4d8a11",
        "context": [],
        "session_id": "chat-1",
        "can_answer_questions": False,
    }
    events = [
        json.loads(line[6:])
        for line in service.app.post("/v1/gaia/query", json=body).text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1]["failed"] is True
    assert service.agents[-1].approved is False
    assert sr.registry.get("chat-1").permissions.full_access is False


def test_disallowed_tools_and_no_web_and_no_skills_take_the_tools_away(service, capsys):
    code, record = _run(
        capsys,
        skip_permissions=True,
        disallowed_tools=("run_shell_*",),
        no_web=True,
        no_skills=True,
    )
    assert code == 0
    assert service.agents[-1].offered == ["read_file", "write_file"]
    assert set(record["withheld_tools"]) == {
        "run_shell_command",
        "search_web",
        "load_skill",
    }


def test_the_time_limit_stops_the_turn(service, capsys):
    code, record = _run(capsys, time_limit=1)
    assert code == 1
    assert record["time_limit_reached"] is True
    assert record["status"] == "incomplete"
    assert "1-second time limit" in record["result"]


def test_an_agent_too_old_for_the_flags_is_refused_before_the_turn(service, capsys):
    # The native harness, or a flagship from before the flags reached it.
    service.api_version = "2.27"
    code = print_mode.run("q", model="m", time_limit=30)
    err = capsys.readouterr().err
    assert code == 1
    assert "--time-limit" in err and "2.32" in err and "2.27" in err
    assert service.sent == []
