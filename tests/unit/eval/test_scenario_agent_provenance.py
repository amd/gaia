# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Guard: a scenario result records the agent that ran, not the one requested.

The runner asks for an agent in prompt prose, so a driver that drops the
``agent_type`` kwarg silently runs the backend default while the result still
carries the requested id (#4069). Provenance is only trustworthy when the
requested and observed ids are recorded separately and a disagreement fails the
scenario.
"""

import pytest

from gaia.eval.runner import (
    _canonical_agent_type,
    _read_session_agent_type,
    _stamp_agent_provenance,
    build_scenario_prompt,
)


def _passing_result(session_id="sess-1"):
    return {
        "scenario_id": "smart_discovery",
        "status": "PASS",
        "overall_score": 8.0,
        "turns": [{"turn": 1}],
        "session_id": session_id,
    }


def _stamp(result, scenario_data, cli_agent_type, observed, backend_url="http://x"):
    """Run the provenance stamp with the HTTP read-back stubbed to *observed*.

    *observed* may be an exception instance to simulate an unreachable backend.
    """

    def fake_read(_backend_url, _session_id):
        if isinstance(observed, Exception):
            raise observed
        return observed

    _stamp_agent_provenance(
        result,
        scenario_data,
        cli_agent_type,
        backend_url,
        read_session_agent_type=fake_read,
    )
    return result


# ---------------------------------------------------------------------------
# The happy path and the bug this issue is about
# ---------------------------------------------------------------------------


def test_agreement_records_both_ids_and_preserves_status():
    result = _stamp(_passing_result(), {"id": "s"}, "gaia", observed="gaia")
    assert result["agent_type_requested"] == "gaia"
    assert result["agent_type_observed"] == "gaia"
    assert result["status"] == "PASS"


def test_dropped_kwarg_is_caught_and_fails_the_scenario():
    # The driver was told to create a `gaia` session and didn't; the backend
    # default answered instead. Scoring that as a `gaia` PASS is the bug.
    result = _stamp(_passing_result(), {"id": "s"}, "gaia", observed="chat")
    assert result["status"] == "INFRA_ERROR"
    assert result["agent_type_requested"] == "gaia"
    assert result["agent_type_observed"] == "chat"
    assert "gaia" in result["error"] and "chat" in result["error"]


def test_a_stray_scenario_agent_type_does_not_override_the_run():
    # validate_scenario rejects the key, so this dict cannot come off disk --
    # pinned anyway because honouring it is what made a run score one agent
    # while its scorecard recorded another.
    result = _stamp(
        _passing_result(), {"id": "s", "agent_type": "doc"}, "gaia", observed="gaia"
    )
    assert result["agent_type_requested"] == "gaia"
    assert result["status"] == "PASS"


def test_legacy_alias_is_not_a_mismatch():
    # `doc-lite` sessions are stored and read back as `doc`.
    result = _stamp(_passing_result(), {"id": "s"}, "doc-lite", observed="doc")
    assert result["status"] == "PASS"
    assert result["agent_type_observed"] == "doc"


# ---------------------------------------------------------------------------
# Failure modes that must not degrade to a guess
# ---------------------------------------------------------------------------


def test_missing_session_id_discards_a_score():
    result = _passing_result()
    del result["session_id"]
    _stamp(result, {"id": "s"}, "gaia", observed="gaia")
    assert result["status"] == "INFRA_ERROR"
    assert result["agent_type_observed"] is None
    assert "session_id" in result["error"]


def test_unreachable_backend_discards_a_score_and_names_the_url():
    result = _stamp(
        _passing_result(),
        {"id": "s"},
        "gaia",
        observed=RuntimeError("connection refused"),
        backend_url="http://127.0.0.1:4200",
    )
    assert result["status"] == "INFRA_ERROR"
    assert "127.0.0.1:4200" in result["error"]


def test_unverifiable_does_not_overwrite_a_more_specific_status():
    # BLOCKED_BY_ARCHITECTURE says something INFRA_ERROR does not. Being unable
    # to verify only discards a score, and this status carries none.
    result = {
        "scenario_id": "s",
        "status": "BLOCKED_BY_ARCHITECTURE",
        "turns": [{"turn": 1}],
    }
    _stamp(result, {"id": "s"}, "gaia", observed="gaia")
    assert result["status"] == "BLOCKED_BY_ARCHITECTURE"
    assert "session_id" in result["provenance_warning"][0]
    assert "error" not in result


def test_a_mismatch_overrides_even_a_more_specific_status():
    # "the architecture blocks gaia" is a false claim if chat answered.
    result = {
        "scenario_id": "s",
        "status": "BLOCKED_BY_ARCHITECTURE",
        "session_id": "sess-1",
        "turns": [{"turn": 1}],
    }
    _stamp(result, {"id": "s"}, "gaia", observed="chat")
    assert result["status"] == "INFRA_ERROR"


@pytest.mark.parametrize(
    "status",
    ["TIMEOUT", "SETUP_ERROR", "ERRORED", "BUDGET_EXCEEDED", "INFRA_ERROR"],
)
def test_a_scenario_that_never_measured_keeps_its_own_status(status):
    # These never got far enough to create a session; relabelling them
    # INFRA_ERROR would erase the real failure.
    result = {"scenario_id": "s", "status": status, "turns": []}
    _stamp(result, {"id": "s"}, "gaia", observed="chat")
    assert result["status"] == status
    assert result["agent_type_requested"] == "gaia"
    assert "error" not in result


def test_no_agent_requested_checks_nothing():
    # The run named no agent, so the backend default is right by definition —
    # and a missing session_id costs nothing.
    result = _passing_result()
    del result["session_id"]
    _stamp(result, {"id": "s"}, None, observed="chat")
    assert result["agent_type_requested"] is None
    assert result["status"] == "PASS"
    assert "error" not in result


# ---------------------------------------------------------------------------
# The two layers that launder a dropped kwarg into the string "chat".
# Pinning them is what stops a naive `observed is None` test being written —
# over HTTP the dropped kwarg is never None.
# ---------------------------------------------------------------------------


def test_database_stores_a_dropped_agent_type_as_chat(tmp_path):
    from gaia.ui.database import ChatDatabase

    db = ChatDatabase(db_path=str(tmp_path / "chat.db"))
    session = db.create_session(title="t", agent_type=None)
    assert db.get_session(session["id"])["agent_type"] == "chat"


def test_session_response_serialises_a_missing_agent_type_as_chat():
    from gaia.ui.models import SessionResponse

    response = SessionResponse(
        id="s",
        title="t",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        model="m",
    )
    assert response.agent_type == "chat"


def test_a_chat_scenario_cannot_distinguish_a_drop_from_a_request():
    # Known limitation, pinned so it is read rather than rediscovered: over
    # HTTP a scenario that legitimately asks for `chat` looks identical to a
    # dropped kwarg. Harmless for every non-`chat` scenario, which is the case
    # the guard exists for.
    result = _stamp(_passing_result(), {"id": "s"}, "chat", observed="chat")
    assert result["status"] == "PASS"


# ---------------------------------------------------------------------------
# Plumbing: the driver has to hand back a session_id for any of this to work
# ---------------------------------------------------------------------------


def test_prompt_tells_the_driver_to_return_the_session_id(monkeypatch):
    monkeypatch.setattr("gaia.eval.runner._load_simulator_content", lambda: "sim")
    monkeypatch.setattr("gaia.eval.runner._load_judge_turn_content", lambda: "turn")
    monkeypatch.setattr("gaia.eval.runner._load_judge_scenario_content", lambda: "scen")

    prompt = build_scenario_prompt(
        {"id": "s", "turns": []}, {}, "http://x", agent_type="gaia"
    )
    assert '"session_id"' in prompt


def test_read_back_parses_the_agent_type_from_the_session_endpoint(monkeypatch):
    calls = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "sess-1", "agent_type": "gaia"}

    def fake_get(url, timeout=None):
        calls["url"] = url
        return _Resp()

    monkeypatch.setattr("requests.get", fake_get)
    assert _read_session_agent_type("http://x/", "sess-1") == "gaia"
    assert calls["url"] == "http://x/api/sessions/sess-1"


def test_read_back_raises_on_an_http_error(monkeypatch):
    import requests

    class _Resp:
        def raise_for_status(self):
            raise requests.HTTPError("404 Not Found")

    monkeypatch.setattr("requests.get", lambda url, timeout=None: _Resp())
    with pytest.raises(requests.HTTPError):
        _read_session_agent_type("http://x", "missing")


def test_canonical_agent_type_resolves_aliases_without_discovery():
    assert _canonical_agent_type("doc-lite") == "doc"
    assert _canonical_agent_type("gaia") == "gaia"
    assert _canonical_agent_type(None) is None
