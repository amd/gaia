# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the daemon thin-client query timeout contract."""

from types import SimpleNamespace

import pytest
import requests

from gaia.daemon import agent_query
from gaia.daemon.errors import DaemonError


class _Response:
    status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_run_query_passes_configured_read_timeout(monkeypatch):
    observed = {}

    monkeypatch.setattr(
        agent_query.client,
        "ensure_agent",
        lambda _agent_id: SimpleNamespace(
            base_url="http://127.0.0.1:12345", token="daemon-token"
        ),
    )

    def _post(*_args, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        return _Response()

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(agent_query, "_consume", lambda *_args: "final")
    monkeypatch.setenv("GAIA_AGENT_TOOL_TIMEOUT", "900")

    renderer = SimpleNamespace(
        final_answer=None,
        final_failed=False,
        final_usage=None,
        final_model_messages=None,
        final_time_limit_reached=False,
        final_withheld_tools=None,
        error_detail=None,
    )
    outcome = agent_query.run_query("email", "triage", renderer=renderer)

    assert outcome.exit_code == 0
    assert observed["timeout"] == (10.0, 960.0)


class _Version:
    def __init__(self, api_version):
        self._api_version = api_version

    def raise_for_status(self):
        pass

    def json(self):
        return {"apiVersion": self._api_version}


def _stub_daemon(monkeypatch, api_version):
    sent = []
    monkeypatch.setattr(
        agent_query.client,
        "ensure_agent",
        lambda _agent_id: SimpleNamespace(base_url="http://127.0.0.1:1", token="t"),
    )
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Version(api_version))

    def _post(*_args, **kwargs):
        sent.append(kwargs["json"])
        return _Response()

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(agent_query, "_consume", lambda *_args: "final")
    return sent


def test_run_scoped_flags_reach_the_request_on_a_new_enough_agent(monkeypatch):
    sent = _stub_daemon(monkeypatch, "2.32")
    agent_query.run_query(
        "gaia",
        "q",
        time_limit=5,
        disallowed_tools=["write_file"],
        no_web=True,
        no_skills=True,
        skip_permissions=True,
        full_access=True,
    )
    [payload] = sent
    assert {k: payload[k] for k in agent_query.RUN_SCOPED_FLAGS} == {
        "time_limit": 5,
        "disallowed_tools": ["write_file"],
        "no_web": True,
        "no_skills": True,
        "skip_permissions": True,
        "full_access": True,
    }


def test_an_agent_older_than_the_flags_is_refused_not_run_unrestricted(monkeypatch):
    sent = _stub_daemon(monkeypatch, "2.27")
    with pytest.raises(DaemonError, match="--disallowed-tools"):
        agent_query.run_query("gaia", "q", disallowed_tools=["write_file"])
    assert sent == []


@pytest.mark.parametrize("version", ["2.31", "2.3", "1.99", "garbage", ""])
def test_an_agent_one_contract_short_is_refused(monkeypatch, version):
    # 2.31 is session_created only; a 2.31 agent would ignore the flags.
    sent = _stub_daemon(monkeypatch, version)
    with pytest.raises(DaemonError, match="2.32"):
        agent_query.run_query("gaia", "q", full_access=True)
    assert sent == []


@pytest.mark.parametrize("version", ["2.32", "2.40", "3.0"])
def test_a_newer_agent_takes_the_flags(monkeypatch, version):
    sent = _stub_daemon(monkeypatch, version)
    agent_query.run_query("gaia", "q", time_limit=10)
    [payload] = sent
    assert payload["time_limit"] == 10


def test_no_run_scoped_flag_means_no_version_check(monkeypatch):
    sent = _stub_daemon(monkeypatch, "2.27")
    agent_query.run_query("gaia", "q")
    [payload] = sent
    assert not set(payload) & set(agent_query.RUN_SCOPED_FLAGS)


def test_run_scoped_flags_are_refused_with_a_session(monkeypatch):
    sent = _stub_daemon(monkeypatch, "2.32")
    with pytest.raises(ValueError, match="one run only"):
        agent_query.run_query("gaia", "q", session_id="chat-1", full_access=True)
    assert sent == []


def _stub_version_body(monkeypatch, body):
    sent = _stub_daemon(monkeypatch, "2.27")

    class _Body(_Version):
        def json(self):
            return body

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Body(""))
    return sent


def test_gaiacpp_listing_the_fields_gets_them(monkeypatch):
    # gaiacpp's /version: an older contract, the one-shot fields by capability.
    sent = _stub_version_body(
        monkeypatch,
        {
            "apiVersion": "2.27",
            "capabilities": {"one_shot_fields": list(agent_query.RUN_SCOPED_FLAGS)},
        },
    )
    agent_query.run_query(
        "gaia",
        "q",
        time_limit=60,
        skip_permissions=True,
        disallowed_tools=["bash_*"],
        no_web=True,
    )
    [payload] = sent
    assert payload["time_limit"] == 60
    assert payload["skip_permissions"] is True
    assert payload["disallowed_tools"] == ["bash_*"]
    assert payload["no_web"] is True


def test_gaiacpp_missing_a_field_is_refused_naming_only_that_flag(monkeypatch):
    sent = _stub_version_body(
        monkeypatch,
        {"apiVersion": "2.27", "capabilities": {"one_shot_fields": ["time_limit"]}},
    )
    with pytest.raises(DaemonError) as caught:
        agent_query.run_query("gaia", "q", time_limit=60, full_access=True)
    message = str(caught.value)
    assert "--full-access" in message
    assert "--time-limit" not in message
    # A switch that takes effect for the next run: the saved choice alone
    # leaves the running agent where it is.
    assert "gaia daemon stop-agent gaia" in message
    assert sent == []


@pytest.mark.parametrize(
    "capabilities", [None, [], "time_limit", {"one_shot_fields": "time_limit"}]
)
def test_a_malformed_capability_list_is_not_read_as_support(monkeypatch, capabilities):
    sent = _stub_version_body(
        monkeypatch, {"apiVersion": "2.27", "capabilities": capabilities}
    )
    with pytest.raises(DaemonError, match="--time-limit"):
        agent_query.run_query("gaia", "q", time_limit=60)
    assert sent == []
