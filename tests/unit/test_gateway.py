# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for the AMD LLM gateway manager.

These assert the *shape* of the calls GAIA makes to Lemonade, not merely that
it made them: a stub returning ``{"status": "success"}`` proves nothing about
whether Lemonade would accept the request. The matching contract test lives in
``tests/integration/test_gateway_lemonade.py``.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.gateway import (
    DEFAULT_GATEWAY_BASE_URL,
    GATEWAY_API_KEY_ENV,
    GATEWAY_PROVIDER,
    GatewayError,
    GatewayManager,
    GatewayModel,
    GatewayState,
)


def _response(status_code=200, body=None):
    """A requests.Response stand-in with a JSON body."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = json.dumps(body or {}).encode()
    resp.json.return_value = body or {}
    resp.text = json.dumps(body or {})
    return resp


@pytest.fixture
def manager():
    client = MagicMock()
    client.base_url = "http://localhost:13305/api/v1"
    return GatewayManager(client=client)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep every test off the developer's real ~/.gaia/gateway.json."""
    monkeypatch.setenv("GAIA_GATEWAY_FILE", str(tmp_path / "gateway.json"))
    monkeypatch.setattr(
        "gaia.llm.gateway.GATEWAY_STATE_FILE", tmp_path / "gateway.json"
    )
    return tmp_path / "gateway.json"


# --------------------------------------------------------------------------
# install — the call must be one Lemonade will accept
# --------------------------------------------------------------------------


class TestInstall:
    def test_sends_the_cloud_backend_shape_lemonade_requires(self, manager):
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={"models_discovered": 3})
            manager.install("https://gw.example.com/api/v1")

        _, kwargs = request.call_args
        payload = kwargs["json"]
        # Lemonade routes on backend == "cloud"; without it this registers
        # nothing and silently 400s on the missing model fields.
        assert payload["backend"] == "cloud"
        assert payload["provider"] == GATEWAY_PROVIDER
        assert payload["base_url"] == "https://gw.example.com/api/v1"
        # GAIA's agents speak OpenAI chat completions end to end; the anthropic
        # wire format serves only /v1/messages and would break every agent.
        assert payload["wire_format"] == "openai"

    def test_posts_to_the_install_endpoint(self, manager):
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.install("https://gw.example.com/api/v1")

        args, _ = request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://localhost:13305/api/v1/install"

    def test_trailing_slash_is_normalized(self, manager):
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.install("https://gw.example.com/api/v1/")

        assert request.call_args.kwargs["json"]["base_url"] == (
            "https://gw.example.com/api/v1"
        )

    def test_optional_auth_header_fields_are_omitted_when_unset(self, manager):
        """Omitted fields keep Lemonade's stored value on a re-install."""
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.install("https://gw.example.com/api/v1")

        payload = request.call_args.kwargs["json"]
        assert "auth_header_name" not in payload
        assert "auth_header_prefix" not in payload

    def test_empty_auth_header_prefix_is_sent_not_dropped(self, manager):
        """A gateway using `X-Api-Key: <raw>` needs an explicitly empty prefix."""
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.install(
                "https://gw.example.com/api/v1",
                auth_header_name="X-Api-Key",
                auth_header_prefix="",
            )

        payload = request.call_args.kwargs["json"]
        assert payload["auth_header_name"] == "X-Api-Key"
        assert payload["auth_header_prefix"] == ""

    def test_remembers_the_base_url(self, manager):
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.install("https://gw.example.com/api/v1")

        assert GatewayState.load().base_url == "https://gw.example.com/api/v1"


# --------------------------------------------------------------------------
# auth — the token must never reach disk, and 409 must be actionable
# --------------------------------------------------------------------------


class TestAuth:
    def test_posts_the_token_to_the_runtime_auth_endpoint(self, manager):
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={"models_discovered": 7})
            manager.set_token("sk-secret-token")

        args, kwargs = request.call_args
        assert args[0] == "POST"
        assert args[1].endswith("/cloud/auth")
        assert kwargs["json"] == {
            "provider": GATEWAY_PROVIDER,
            "api_key": "sk-secret-token",
        }

    def test_token_is_never_written_to_the_state_file(self, manager, isolated_state):
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.set_token("sk-secret-token")

        # The whole design rests on this: GAIA holds no copy of the token.
        if isolated_state.exists():
            assert "sk-secret-token" not in isolated_state.read_text()

    def test_state_schema_has_no_field_that_could_hold_a_secret(self, isolated_state):
        GatewayState(base_url="https://x", enabled_models=["amd.a"]).save()
        persisted = json.loads(isolated_state.read_text())
        assert set(persisted) == {"base_url", "enabled_models", "active_model"}

    def test_empty_token_is_refused_before_any_request(self, manager):
        with patch("gaia.llm.gateway.requests.request") as request:
            with pytest.raises(GatewayError, match=GATEWAY_API_KEY_ENV):
                manager.set_token("   ")
        request.assert_not_called()

    def test_409_names_the_env_var_and_the_fix(self, manager):
        conflict = _response(
            status_code=409,
            body={
                "error": {
                    "type": "auth_conflict",
                    "message": "env var is set",
                    "env_var": GATEWAY_API_KEY_ENV,
                }
            },
        )
        with patch("gaia.llm.gateway.requests.request", return_value=conflict):
            with pytest.raises(GatewayError) as excinfo:
                manager.set_token("sk-other")

        message = str(excinfo.value)
        assert GATEWAY_API_KEY_ENV in message  # what
        assert "unset" in message.lower()  # what to do

    def test_clear_token_deletes_the_provider_key(self, manager):
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.clear_token()

        args, _ = request.call_args
        assert args[0] == "DELETE"
        assert args[1].endswith(f"/cloud/auth/{GATEWAY_PROVIDER}")


# --------------------------------------------------------------------------
# errors must say what failed, what to do, and where to look
# --------------------------------------------------------------------------


class TestErrors:
    def test_unreachable_lemonade_names_the_url_and_the_fix(self, manager):
        import requests as requests_module

        with patch(
            "gaia.llm.gateway.requests.request",
            side_effect=requests_module.exceptions.ConnectionError("refused"),
        ):
            with pytest.raises(GatewayError) as excinfo:
                manager.status()

        message = str(excinfo.value)
        assert "http://localhost:13305/api/v1" in message
        assert "lemonade-server serve" in message

    def test_404_on_a_cloud_route_points_at_the_version_requirement(self, manager):
        with patch(
            "gaia.llm.gateway.requests.request",
            return_value=_response(status_code=404, body={}),
        ):
            with pytest.raises(GatewayError, match="11.8.0"):
                manager.clear_token()

    def test_probe_401_tells_the_user_to_supply_a_token(self, manager):
        with patch(
            "gaia.llm.gateway.requests.get",
            return_value=_response(status_code=401, body={}),
        ):
            with pytest.raises(GatewayError) as excinfo:
                manager.check_reachable("https://gw.example.com/api/v1")

        assert "gaia gateway auth" in str(excinfo.value)

    def test_probe_non_json_body_says_it_is_not_an_openai_endpoint(self, manager):
        html = MagicMock()
        html.status_code = 200
        html.json.side_effect = ValueError("not json")
        with patch("gaia.llm.gateway.requests.get", return_value=html):
            with pytest.raises(GatewayError, match="OpenAI-compatible"):
                manager.check_reachable("https://gw.example.com")


# --------------------------------------------------------------------------
# model discovery
# --------------------------------------------------------------------------


class TestListModels:
    CATALOG = {
        "data": [
            {"id": "Gemma-4-E4B-it-GGUF", "recipe": "llamacpp", "labels": ["hot"]},
            {
                "id": "amd.Claude-Opus-5",
                "recipe": "cloud",
                "labels": ["tool-calling", "vision"],
                "context_length": 1000000,
            },
            {
                "id": "amd.zephyr-small",
                "recipe": "cloud",
                "labels": [],
                "context_length": 8192,
            },
            {
                "id": "amd.gemma-4-31b-it",
                "recipe": "cloud",
                "labels": ["tool-calling"],
                "context_length": 131072,
            },
            # Another provider's cloud model must not leak into the AMD list.
            {"id": "fireworks.kimi", "recipe": "cloud", "labels": []},
        ]
    }

    def test_returns_only_this_provider_s_cloud_models(self, manager):
        manager.client.list_models.return_value = self.CATALOG
        ids = [m.id for m in manager.list_models()]
        assert set(ids) == {
            "amd.Claude-Opus-5",
            "amd.zephyr-small",
            "amd.gemma-4-31b-it",
        }

    def test_recommended_models_sort_first(self, manager):
        manager.client.list_models.return_value = self.CATALOG
        ids = [m.id for m in manager.list_models()]
        # Claude-Opus-5 and gemma-4-31b are recommended; zephyr is not.
        assert ids[-1] == "amd.zephyr-small"

    def test_capabilities_come_from_the_gateway(self, manager):
        manager.client.list_models.return_value = self.CATALOG
        by_id = {m.id: m for m in manager.list_models()}
        assert by_id["amd.Claude-Opus-5"].tool_calling is True
        assert by_id["amd.zephyr-small"].tool_calling is False
        assert by_id["amd.Claude-Opus-5"].ctx_size == 1000000

    def test_upstream_id_strips_the_gaia_namespace(self):
        assert GatewayModel(id="amd.Claude-Opus-5").upstream_id == "Claude-Opus-5"

    def test_recommendation_is_a_substring_hint_not_a_hardcoded_id(self):
        # The gateway names models its own way, so matching must be loose.
        assert GatewayModel(id="amd.Claude-Opus-5").recommended
        assert GatewayModel(id="amd.claude-opus-4-8").recommended
        assert GatewayModel(id="amd.Gemma-4-31B-Instruct").recommended
        assert not GatewayModel(id="amd.some-other-model").recommended


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


class TestStatus:
    def test_reads_the_provider_entry_from_system_info(self, manager):
        body = {
            "cloud": {
                "providers": [
                    {"name": "fireworks", "models_discovered": 99},
                    {
                        "name": GATEWAY_PROVIDER,
                        "base_url": "https://gw.example.com/api/v1",
                        "env_var_set": False,
                        "runtime_key_set": True,
                        "models_discovered": 12,
                    },
                ]
            }
        }
        with patch(
            "gaia.llm.gateway.requests.request", return_value=_response(body=body)
        ):
            status = manager.status()

        assert status.installed
        assert status.base_url == "https://gw.example.com/api/v1"
        assert status.runtime_key_set
        assert status.models_discovered == 12
        assert status.authenticated

    def test_not_installed_when_the_provider_is_absent(self, manager):
        with patch(
            "gaia.llm.gateway.requests.request",
            return_value=_response(body={"cloud": {"providers": []}}),
        ):
            status = manager.status()

        assert not status.installed
        assert not status.authenticated


# --------------------------------------------------------------------------
# enabled-model selection
# --------------------------------------------------------------------------


class TestSelection:
    def test_first_enabled_model_becomes_active(self, manager):
        state = manager.enable("amd.gemma-4-31b-it")
        assert state.active_model == "amd.gemma-4-31b-it"

    def test_enabling_a_second_model_does_not_steal_active(self, manager):
        manager.enable("amd.gemma-4-31b-it")
        state = manager.enable("amd.Claude-Opus-5")
        assert state.active_model == "amd.gemma-4-31b-it"
        assert len(state.enabled_models) == 2

    def test_disabling_the_active_model_hands_off_to_the_next(self, manager):
        manager.enable("amd.gemma-4-31b-it")
        manager.enable("amd.Claude-Opus-5")
        state = manager.disable("amd.gemma-4-31b-it")
        assert state.active_model == "amd.Claude-Opus-5"

    def test_disabling_the_last_model_leaves_no_active(self, manager):
        manager.enable("amd.gemma-4-31b-it")
        state = manager.disable("amd.gemma-4-31b-it")
        assert state.active_model is None

    def test_set_active_enables_an_unlisted_model(self, manager):
        state = manager.set_active("amd.Claude-Opus-5")
        assert state.active_model == "amd.Claude-Opus-5"
        assert "amd.Claude-Opus-5" in state.enabled_models

    def test_selection_survives_a_reload(self, manager):
        manager.set_active("amd.Claude-Opus-5")
        assert GatewayState.load().active_model == "amd.Claude-Opus-5"

    def test_uninstall_forgets_the_selection(self, manager):
        manager.set_active("amd.Claude-Opus-5")
        with patch(
            "gaia.llm.gateway.requests.request", return_value=_response(body={})
        ):
            manager.uninstall()

        state = GatewayState.load()
        assert state.enabled_models == []
        assert state.active_model is None


class TestState:
    def test_missing_file_yields_defaults_not_an_error(self):
        state = GatewayState.load()
        assert state.base_url == DEFAULT_GATEWAY_BASE_URL
        assert state.enabled_models == []

    def test_corrupt_file_fails_loudly_with_the_path(self, isolated_state):
        isolated_state.parent.mkdir(parents=True, exist_ok=True)
        isolated_state.write_text("{ not json")
        with pytest.raises(GatewayError) as excinfo:
            GatewayState.load()
        assert str(isolated_state) in str(excinfo.value)
