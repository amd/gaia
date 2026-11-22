# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/query``'s run-scoped fields (contract 2.32): the ``gaia -p`` flags.

On an installed GAIA, ``gaia -p`` runs its turn on this sidecar. These drive
the real app with a scripted agent and check each flag's effect on the run, not
just that the request was accepted: the time limit stops the turn, a withheld
tool is not offered, and a permission override approves a gated call for that
run alone. Agent UI chats share this sidecar, so the override must never reach
a session.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

pytest.importorskip("gaiapy")

from fastapi.testclient import TestClient  # noqa: E402
from gaiapy import caller_auth  # noqa: E402
from gaiapy import server as server_mod  # noqa: E402
from gaiapy import session_registry as sr  # noqa: E402

from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.print_mode import NO_WEB_NOTE  # noqa: E402

_BASE_URL = "http://127.0.0.1:8141"
_TOKEN = "test-caller-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}

_TOOLS = (
    "read_file",
    "write_file",
    "run_shell_command",
    "search_web",
    "fetch_page",
    "list_skills",
    "load_skill",
)


class _ToolAgent:
    """GaiaAgent's tool gate and loop seams, with no model behind them."""

    #: (tool, args) the turn asks approval for before answering.
    gated_call = None
    #: Spin until the turn's cancel fires, as the loop does between steps.
    wait_for_stop = False
    #: Park the turn here, deaf to the stop, like a model call in flight.
    hang = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.console = None
        self.conversation_history = []
        self._cancel_event = None
        self._tools_registry = {name: {} for name in _TOOLS}
        self._denied_tool_patterns = ()
        self._learned_skills_enabled = True
        self.time_limit_s = None
        self.queries: list = []
        self.offered = None
        self.approved = None
        self.closed = 0

    deny_tools = Agent.deny_tools

    def get_tools_info(self):
        return dict(self._tools_registry)

    def rebuild_system_prompt(self):
        pass

    def time_limit_abandoned_result(self, message):
        return {
            "status": "incomplete",
            "result": message,
            "time_limit_reached": True,
            "steps_taken": 1,
        }

    def process_query(self, query, max_steps=None):
        self.queries.append(query)
        self.offered = sorted(
            n for n in self._tools_registry if Agent._tool_permitted(self, n)
        )
        if self.hang is not None:
            self.hang.wait(timeout=20)
        if self.wait_for_stop:
            deadline = time.monotonic() + 10
            while not self._cancel_event.is_set() and time.monotonic() < deadline:
                time.sleep(0.02)
            return {
                "status": "incomplete",
                "result": self._cancel_event.message or "no stop",
                "time_limit_reached": True,
            }
        if self.gated_call is not None:
            tool, args = self.gated_call
            self.approved = self.console.confirm_tool_execution(tool, args)
            return {"answer": f"approved={self.approved}"}
        return {"answer": "done"}

    def close(self):
        self.closed += 1


@pytest.fixture
def built(monkeypatch):
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.setenv(caller_auth.TOKEN_ENV_VAR, _TOKEN)
    monkeypatch.delenv("GAIA_AUTO_APPROVE_TOOLS", raising=False)
    agents: list = []

    def build(**kw):
        agents.append(_ToolAgent(**kw))
        return agents[-1]

    monkeypatch.setattr(server_mod, "build_query_agent", build)
    monkeypatch.setattr(sr, "build_session_agent", build)
    sr.registry.clear()
    server_mod._registry = server_mod._RunRegistry()
    try:
        yield TestClient(
            server_mod.build_app(), base_url=_BASE_URL, headers=_AUTH
        ), agents
    finally:
        sr.registry.clear()
        server_mod._registry = server_mod._RunRegistry()
        caller_auth.reset()


def _query(client, **fields):
    body = {"query": "make the file", "run_id": str(uuid.uuid4()), "context": []}
    body.update(fields)
    return client.post("/v1/gaia/query", json=body)


def _events(response):
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _final(response):
    assert response.status_code == 200, response.text
    events = _events(response)
    assert events[-1]["type"] == "final", events
    return events[-1]


_WRITE = ("write_file", {"file_path": "/tmp/greeting.txt", "content": "hi"})


def test_the_contract_advertises_the_run_scoped_fields():
    major, minor = (int(x) for x in server_mod.API_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 32)
    for field in server_mod.RUN_SCOPED_FIELDS:
        assert field in server_mod.QueryRequest.model_fields


# --- permissions ----------------------------------------------------------


def test_without_an_override_a_headless_gated_call_is_refused_naming_the_flag(
    built, monkeypatch
):
    client, agents = built
    monkeypatch.setattr(_ToolAgent, "gated_call", _WRITE)
    final = _final(_query(client, surface="cli", can_answer_questions=False))
    assert final["failed"] is True
    # The CLI's remedy, not "a surface that supports confirmation".
    assert "--dangerously-skip-permissions" in final["answer"]
    assert "surface that supports confirmation" not in final["answer"]
    assert agents[-1].approved is False


def test_a_path_prompt_names_full_access_not_the_pre_approval_flag(built, monkeypatch):
    client, _ = built
    monkeypatch.setattr(
        _ToolAgent, "gated_call", ("allow_path_access", {"path": "/elsewhere"})
    )
    final = _final(
        _query(client, surface="cli", can_answer_questions=False, skip_permissions=True)
    )
    assert "--full-access" in final["answer"]
    assert "--dangerously-skip-permissions" not in final["answer"]


@pytest.mark.parametrize(
    "flag, unguarded", [("skip_permissions", False), ("full_access", True)]
)
def test_a_permission_override_runs_the_gated_call_without_asking(
    built, monkeypatch, flag, unguarded
):
    client, agents = built
    monkeypatch.setattr(_ToolAgent, "gated_call", _WRITE)
    final = _final(_query(client, can_answer_questions=False, **{flag: True}))
    assert final["answer"] == "approved=True"
    assert not final.get("failed")
    handler = agents[-1].console
    assert handler.auto_approve_gated_tools is True
    # Only --full-access lifts the shell guardrails, as in-process.
    assert handler.full_access is unguarded


@pytest.mark.parametrize("flag", ["skip_permissions", "full_access"])
def test_an_override_needs_an_authenticated_caller(monkeypatch, flag):
    """As /sessions/{id}/bypass: a sidecar with no token can't tell callers apart."""
    caller_auth.reset()
    monkeypatch.delenv(caller_auth.TOKEN_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(caller_auth.TOKEN_ENV_VAR, raising=False)
    monkeypatch.setenv(caller_auth.ALLOW_NO_TOKEN_ENV_VAR, "1")
    agents: list = []
    monkeypatch.setattr(
        server_mod, "build_query_agent", lambda **kw: agents.append(kw) or _ToolAgent()
    )
    try:
        client = TestClient(server_mod.build_app(), base_url=_BASE_URL)
        response = _query(client, **{flag: True})
    finally:
        caller_auth.reset()
    assert response.status_code == 403
    assert "caller authentication" in response.text
    assert agents == []


def test_skip_permissions_alone_never_widens_the_file_scope(built, monkeypatch):
    client, agents = built
    monkeypatch.setattr(
        _ToolAgent, "gated_call", ("allow_path_access", {"path": "/elsewhere"})
    )
    final = _final(_query(client, can_answer_questions=False, skip_permissions=True))
    assert final["failed"] is True
    assert agents[-1].approved is False


@pytest.mark.parametrize("flag", sorted(server_mod.RUN_SCOPED_FIELDS))
def test_a_run_scoped_field_is_refused_on_a_session(built, flag):
    client, agents = built
    value = {
        "time_limit": 30,
        "disallowed_tools": ["write_file"],
    }.get(flag, True)
    response = _query(client, session_id="chat-1", **{flag: value})
    assert response.status_code == 422
    assert "one-shot" in response.text
    assert agents == []


def test_an_override_never_reaches_a_session_that_runs_after_it(built, monkeypatch):
    client, agents = built
    monkeypatch.setattr(_ToolAgent, "gated_call", _WRITE)
    assert _final(_query(client, full_access=True))["answer"] == "approved=True"

    chat = _final(_query(client, session_id="chat-1", can_answer_questions=False))
    assert chat["failed"] is True
    assert agents[-1].approved is False
    session = sr.registry.get("chat-1")
    assert session.permissions.full_access is False
    assert session.agent.console.auto_approve_gated_tools is False


def test_an_override_never_reaches_a_session_running_alongside_it(built, monkeypatch):
    client, agents = built
    monkeypatch.setattr(_ToolAgent, "gated_call", _WRITE)
    hold = threading.Event()
    monkeypatch.setattr(_ToolAgent, "hang", hold)
    one_shot: dict = {}

    def run_one_shot():
        one_shot["final"] = _final(_query(client, full_access=True))

    worker = threading.Thread(target=run_one_shot)
    worker.start()
    deadline = time.monotonic() + 5
    while not agents and time.monotonic() < deadline:
        time.sleep(0.01)
    # The chat's turn needs no hold; only the one-shot parks.
    monkeypatch.setattr(_ToolAgent, "hang", None)
    try:
        chat = _final(_query(client, session_id="chat-1", can_answer_questions=False))
    finally:
        hold.set()
        worker.join(timeout=10)
    assert chat["failed"] is True
    assert agents[-1].approved is False
    assert one_shot["final"]["answer"] == "approved=True"


# --- tool restrictions ----------------------------------------------------


def test_disallowed_tools_are_not_offered_and_are_reported(built):
    client, agents = built
    final = _final(_query(client, disallowed_tools=["write_file", "run_shell_*"]))
    assert "write_file" not in agents[-1].offered
    assert "run_shell_command" not in agents[-1].offered
    assert "read_file" in agents[-1].offered
    assert final["withheld_tools"] == ["run_shell_command", "write_file"]


def test_a_disallowed_pattern_matching_no_tool_is_a_loud_400(built):
    client, agents = built
    response = _query(client, disallowed_tools=["no_such_tool"])
    assert response.status_code == 400
    assert "No tool matches no_such_tool" in response.text
    # The one-shot agent built for it is torn down, not leaked.
    assert agents[-1].closed == 1


def test_no_web_withholds_the_web_tools_and_tells_the_model(built):
    client, agents = built
    final = _final(_query(client, no_web=True))
    agent = agents[-1]
    assert "search_web" not in agent.offered and "fetch_page" not in agent.offered
    assert "write_file" in agent.offered
    assert agent.queries == ["make the file" + NO_WEB_NOTE]
    assert set(final["withheld_tools"]) == {"search_web", "fetch_page"}


def test_no_skills_turns_skill_discovery_off_and_withholds_the_skill_tools(built):
    client, agents = built
    _final(_query(client, no_skills=True))
    agent = agents[-1]
    assert agent.kwargs["skill_discovery"] is False
    assert agent.kwargs["skill_suggestions"] is False
    assert agent._learned_skills_enabled is False
    assert "load_skill" not in agent.offered and "list_skills" not in agent.offered


def test_an_unrestricted_run_offers_every_tool(built):
    client, agents = built
    final = _final(_query(client))
    assert agents[-1].offered == sorted(_TOOLS)
    assert "withheld_tools" not in final
    assert "skill_discovery" not in agents[-1].kwargs


# --- time limit -----------------------------------------------------------


def test_the_time_limit_stops_the_turn_and_final_says_so(built, monkeypatch):
    client, agents = built
    monkeypatch.setattr(_ToolAgent, "wait_for_stop", True)
    started = time.monotonic()
    final = _final(_query(client, time_limit=1))
    assert time.monotonic() - started < 5
    assert agents[-1].time_limit_s == 1
    assert final["time_limit_reached"] is True
    assert "1-second time limit" in final["answer"]


def test_a_turn_deaf_to_the_stop_is_abandoned_past_the_grace(built, monkeypatch):
    client, _ = built
    hold = threading.Event()
    monkeypatch.setattr(_ToolAgent, "hang", hold)
    monkeypatch.setattr(server_mod, "_HARD_STOP_GRACE_S", 0.3)
    started = time.monotonic()
    try:
        final = _final(_query(client, time_limit=1))
    finally:
        hold.set()
    assert time.monotonic() - started < 5
    assert final["time_limit_reached"] is True
    assert "time limit" in final["answer"]


def test_a_run_with_no_time_limit_has_none(built):
    client, agents = built
    final = _final(_query(client))
    assert agents[-1].time_limit_s is None
    assert "time_limit_reached" not in final


def test_a_time_limit_must_be_positive(built):
    client, _ = built
    assert _query(client, time_limit=0).status_code == 422
