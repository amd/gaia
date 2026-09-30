# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The eval's scripted user: declines named commands, answers nothing (#4447).

The eval transport auto-approves every prompt, so without this nothing in a
scenario can ever say no. These pin that the script declines what it names —
even with auto-approve on — leaves every other call alone, is refused unless
the backend opted in, and never outlives the scenario that set it.
"""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from gaia.eval import runner
from gaia.ui import scripted_user
from gaia.ui.server import create_app
from gaia.ui.sse_handler import SSEOutputHandler


@pytest.fixture(autouse=True)
def _clear_script():
    scripted_user.set_declined_commands([])
    yield
    scripted_user.set_declined_commands([])


def _events(handler):
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    return events


def _auto_approving_handler():
    handler = SSEOutputHandler()
    handler.auto_approve_gated_tools = True
    return handler


def test_a_scripted_decline_beats_auto_approve():
    scripted_user.set_declined_commands(["pytest"])
    handler = _auto_approving_handler()

    approved = handler.confirm_tool_execution(
        "run_shell_command", {"command": "python -m pytest -q"}
    )

    assert approved is False
    assert handler.confirmation_denied_reason("run_shell_command") == (
        "Tool 'run_shell_command' was denied by the user."
    )
    assert any(e["type"] == "tool_confirm_denied" for e in _events(handler))


def test_other_calls_are_untouched():
    scripted_user.set_declined_commands(["pytest"])
    handler = _auto_approving_handler()

    assert handler.confirm_tool_execution("run_shell_command", {"command": "ls"})
    # The reroute is not the scripted user's to stop; the agent loop's is.
    assert handler.confirm_tool_execution(
        "execute_python_file", {"file_path": "run_tests.py"}
    )


def test_it_insists_on_seeing_what_it_would_decline():
    """A skill grant runs ``pytest`` unasked; the scripted user must still see it."""
    scripted_user.set_declined_commands(["pytest"])
    handler = SSEOutputHandler()

    assert handler.insists_on_asking("run_shell_command", {"command": "pytest -q"})
    assert not handler.insists_on_asking("run_shell_command", {"command": "git status"})


def test_questions_go_unanswered_without_waiting():
    scripted_user.set_declined_commands(["pytest"])
    handler = SSEOutputHandler()

    answer = handler.request_user_input_blocking(
        "Allow it once?", choices=["Allow once"], timeout_seconds=300
    )

    assert answer == "__NO_RESPONSE__"
    assert handler._user_input_events == {}
    assert [e["type"] for e in _events(handler)] == ["user_input_request"]


def test_a_transport_that_cannot_answer_is_never_shown_a_question():
    handler = SSEOutputHandler()
    handler.answers_questions = False

    answer = handler.request_user_input_blocking(
        "Allow it once?", choices=["Allow once"], timeout_seconds=300
    )

    assert answer == "__NO_RESPONSE__"
    assert handler._user_input_events == {}
    assert _events(handler) == []


def test_a_command_that_names_nothing_is_rejected():
    with pytest.raises(ValueError, match="names no command"):
        scripted_user.set_declined_commands(["| tail"])
    assert not scripted_user.active()


_UI = {"X-Gaia-UI": "1"}


@pytest.fixture
def ui_client():
    """No lifespan: entering it installs a process-wide agent registry that
    outlives this test and changes what later suites see."""
    return TestClient(create_app(db_path=":memory:"))


class TestTheEndpoint:
    def test_refused_unless_the_backend_opted_in(self, ui_client, monkeypatch):
        monkeypatch.delenv(scripted_user.ENV_VAR, raising=False)
        resp = ui_client.post(
            "/api/eval/scripted-user",
            json={"decline_commands": ["pytest"]},
            headers=_UI,
        )
        assert resp.status_code == 403
        assert scripted_user.ENV_VAR in resp.json()["detail"]
        assert not scripted_user.active()

    def test_sets_and_clears(self, ui_client, monkeypatch):
        monkeypatch.setenv(scripted_user.ENV_VAR, "1")
        resp = ui_client.post(
            "/api/eval/scripted-user",
            json={"decline_commands": ["pytest"]},
            headers=_UI,
        )
        assert resp.status_code == 200
        assert scripted_user.active()

        resp = ui_client.post("/api/eval/scripted-user", json={}, headers=_UI)
        assert resp.status_code == 200
        assert not scripted_user.active()


class TestTheRunner:
    _SCENARIO = {
        "id": "s",
        "setup": {"index_documents": [], "decline_commands": ["pytest"]},
    }

    def test_the_script_is_cleared_after_the_scenario(self):
        calls = []
        with (
            patch.object(
                runner, "_set_scripted_user", side_effect=lambda url, c: calls.append(c)
            ),
            patch.object(
                runner, "run_scenario_subprocess", return_value={"status": "PASS"}
            ),
        ):
            result = runner.run_scripted_scenario(
                "p", self._SCENARIO, "d", "http://x", "m", "1", 10
            )
        assert result == {"status": "PASS"}
        assert calls == [["pytest"], []]

    def test_a_backend_that_refuses_errors_the_scenario_loudly(self):
        with (
            patch.object(
                runner,
                "_set_scripted_user",
                return_value="needs GAIA_EVAL_SCRIPTED_USER=1",
            ),
            patch.object(runner, "run_scenario_subprocess") as run,
        ):
            result = runner.run_scripted_scenario(
                "p", self._SCENARIO, "d", "http://x", "m", "1", 10
            )
        run.assert_not_called()
        assert result["status"] == "ERRORED"
        assert "GAIA_EVAL_SCRIPTED_USER" in result["error"]

    def test_scenarios_without_a_script_never_touch_the_backend(self):
        with (
            patch.object(runner, "_set_scripted_user") as setter,
            patch.object(
                runner, "run_scenario_subprocess", return_value={"status": "PASS"}
            ),
        ):
            runner.run_scripted_scenario(
                "p", {"id": "s", "setup": {}}, "d", "http://x", "m", "1", 10
            )
        setter.assert_not_called()

    def test_validation_rejects_a_malformed_script(self, tmp_path):
        data = {
            "id": "s",
            "category": "c",
            "persona": "power_user",
            "setup": {"index_documents": [], "decline_commands": "pytest"},
            "turns": [{"turn": 1, "objective": "o", "success_criteria": "x"}],
        }
        with pytest.raises(ValueError, match="decline_commands"):
            runner.validate_scenario(tmp_path / "s.yaml", data)
