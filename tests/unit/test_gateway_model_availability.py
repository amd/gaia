# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A gateway model that answers 404 is recorded and named, never swapped out.

Found testing v0.25.0-rc12: the saved active model stopped being served, every
surface that fell back to it failed with a generic "cloud model unavailable",
and `gaia gateway test` could not say which model it had tried.

The HTTP layer is mocked, but each test asserts the request that went out (the
model id in the body) so a passing test means the right model was asked for.
"""

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.gateway import (
    GatewayModel,
    GatewayState,
    GatewayStatus,
    clear_model_unavailable,
    mark_model_unavailable,
)
from gaia.llm.lemonade_client import (
    CloudModelUnavailableError,
    LemonadeAuthError,
    LemonadeClient,
    record_cloud_models,
)

BROKEN = "amd.GLM-5.2-FP8"
WORKING = "amd.DeepSeek-V4.1-Flash"

CATALOG = {
    "data": [
        {"id": BROKEN, "recipe": "cloud", "labels": ["tool-calling"]},
        {"id": WORKING, "recipe": "cloud", "labels": ["tool-calling"]},
    ]
}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    path = tmp_path / "gateway.json"
    monkeypatch.setattr("gaia.llm.gateway.GATEWAY_STATE_FILE", path)
    record_cloud_models(CATALOG)
    yield path
    record_cloud_models({"data": []})


def _http(status, body=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body or {}
    resp.text = json.dumps(body or {})
    return resp


@pytest.fixture
def client():
    c = LemonadeClient(verbose=False)
    c.list_models = MagicMock(return_value=CATALOG)
    return c


def _complete(client, model, response):
    with patch("gaia.llm.lemonade_client.requests.post") as post:
        post.return_value = response
        try:
            return (
                client.chat_completions(
                    model=model,
                    messages=[{"role": "user", "content": "hi"}],
                    auto_download=False,
                ),
                post,
            )
        except Exception as e:  # noqa: BLE001 - returned for the assertion
            return e, post


class TestClientRecordsAvailability:
    def test_a_404_names_the_model_and_is_recorded(self, client):
        error, post = _complete(client, BROKEN, _http(404))

        assert isinstance(error, CloudModelUnavailableError)
        assert error.model_id == BROKEN and error.status == 404
        assert post.call_args.kwargs["json"]["model"] == BROKEN
        entry = GatewayState.load().unavailable(BROKEN)
        assert entry["status"] == 404 and entry["at"].endswith("Z")

    def test_a_401_is_an_auth_problem_not_an_unavailable_model(self, client):
        error, _ = _complete(client, BROKEN, _http(401))

        assert isinstance(error, LemonadeAuthError)
        assert not isinstance(error, CloudModelUnavailableError)
        assert GatewayState.load().unavailable(BROKEN) is None

    def test_a_success_clears_an_earlier_404(self, client):
        mark_model_unavailable(BROKEN)
        ok = _http(200, {"choices": [{"message": {"content": "hello"}}]})

        result, post = _complete(client, BROKEN, ok)

        assert result["choices"][0]["message"]["content"] == "hello"
        assert post.call_args.kwargs["json"]["model"] == BROKEN
        assert GatewayState.load().unavailable(BROKEN) is None

    def test_clearing_an_unrecorded_model_writes_nothing(self, isolated_state):
        assert clear_model_unavailable(WORKING) is False
        assert not isolated_state.exists()


def _manager(chat_effect):
    manager = MagicMock()
    manager.status.return_value = GatewayStatus(
        installed=True, runtime_key_set=True, models_discovered=2
    )
    manager.list_models.return_value = [
        GatewayModel(id=BROKEN),
        GatewayModel(id=WORKING),
    ]
    manager.client.chat_completions.side_effect = chat_effect
    return manager


def _run(args, manager):
    from gaia.cli import handle_gateway_command

    with patch("gaia.llm.gateway.GatewayManager", return_value=manager):
        handle_gateway_command(args)


def _test_args(model=None):
    return Namespace(
        gateway_action="test",
        prompt="ping",
        model=model,
        token=None,
        token_stdin=False,
    )


class TestGatewayTestCommand:
    def test_model_flag_routes_that_model(self, capsys):
        reply = {"choices": [{"message": {"content": "pong"}}]}
        manager = _manager(lambda **kw: reply)
        GatewayState(active_model=BROKEN, enabled_models=[BROKEN]).save()

        _run(_test_args(model=WORKING), manager)

        assert manager.client.chat_completions.call_args.kwargs["model"] == WORKING
        assert f"Testing {WORKING} (from --model)" in capsys.readouterr().out

    def test_without_the_flag_it_says_it_is_testing_the_active_model(self, capsys):
        reply = {"choices": [{"message": {"content": "pong"}}]}
        manager = _manager(lambda **kw: reply)
        GatewayState(active_model=WORKING, enabled_models=[WORKING]).save()

        _run(_test_args(), manager)

        assert manager.client.chat_completions.call_args.kwargs["model"] == WORKING
        assert f"Testing {WORKING} (the active gateway model)" in (
            capsys.readouterr().out
        )

    def test_a_404_names_the_model_the_status_and_deployed_alternatives(self, capsys):
        def fail(**kw):
            mark_model_unavailable(kw["model"])
            raise CloudModelUnavailableError("unavailable", model_id=kw["model"])

        GatewayState(active_model=BROKEN, enabled_models=[BROKEN]).save()

        with pytest.raises(SystemExit) as exit_info:
            _run(_test_args(), _manager(fail))

        assert exit_info.value.code == 1
        err = capsys.readouterr().err
        assert f"{BROKEN} is unavailable on the gateway (HTTP 404)" in err
        assert "gaia gateway use <id>" in err
        # Suggests the deployed one, never the model that just failed.
        assert f"For example: {WORKING}" in err

    def test_a_401_is_reported_as_a_failure_without_the_unavailable_advice(
        self, capsys
    ):
        def fail(**kw):
            raise LemonadeAuthError("Cloud authentication or access was denied")

        GatewayState(active_model=WORKING, enabled_models=[WORKING]).save()

        with pytest.raises(SystemExit):
            _run(_test_args(), _manager(fail))

        err = capsys.readouterr().err
        assert f"Testing {WORKING} failed" in err
        assert "unavailable on the gateway" not in err
        assert GatewayState.load().unavailable(WORKING) is None


class TestStatusAndSelection:
    def test_status_shows_the_active_model_s_recorded_404(self, capsys):
        GatewayState(active_model=BROKEN, enabled_models=[BROKEN]).save()
        mark_model_unavailable(BROKEN)
        manager = _manager(None)
        manager.status.return_value = GatewayStatus(installed=False)

        _run(Namespace(gateway_action="status"), manager)

        out = capsys.readouterr().out
        assert "Unavailable:" in out and f"{BROKEN} answered HTTP 404" in out
        assert "gaia gateway use <id>" in out

    def test_status_says_when_no_failure_is_recorded(self, capsys):
        GatewayState(active_model=WORKING, enabled_models=[WORKING]).save()
        manager = _manager(None)
        manager.status.return_value = GatewayStatus(installed=False)

        _run(Namespace(gateway_action="status"), manager)

        assert "no failed call recorded" in capsys.readouterr().out

    def test_the_saved_default_warns_without_being_replaced(self, capsys):
        from gaia.cli import _warn_if_default_model_unavailable

        mark_model_unavailable(BROKEN)
        _warn_if_default_model_unavailable(BROKEN)
        _warn_if_default_model_unavailable(WORKING)

        err = capsys.readouterr().err
        assert err.count("Your default model") == 1
        assert f"Your default model {BROKEN} answered HTTP 404" in err

    def test_models_listing_marks_the_unavailable_model(self, capsys):
        from gaia.cli import _print_gateway_models

        mark_model_unavailable(BROKEN)
        state = GatewayState.load()
        _print_gateway_models(
            [GatewayModel(id=BROKEN), GatewayModel(id=WORKING)], state
        )

        out = capsys.readouterr().out
        broken_line = next(line for line in out.splitlines() if BROKEN in line)
        working_line = next(line for line in out.splitlines() if WORKING in line)
        assert "unavailable: HTTP 404" in broken_line
        assert "unavailable" not in working_line
