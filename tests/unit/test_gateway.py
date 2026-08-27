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

    def test_defaults_to_the_apim_header_the_gateway_checks(self, manager):
        """Verified live: the APIM subscription header alone returns 200, while
        `Authorization: Bearer` alone returns 401 "missing subscription key".

        Lemonade carries exactly one auth header and stores a key without
        validating it, so getting this wrong surfaces only much later as an
        empty model list.
        """
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.install("https://llm-api.amd.com/Unified/v1")

        payload = request.call_args.kwargs["json"]
        assert payload["auth_header_name"] == "Ocp-Apim-Subscription-Key"
        assert payload["auth_header_prefix"] == ""

    def test_an_explicit_auth_header_overrides_the_default(self, manager):
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.install(
                "https://gw.example.com/v1",
                auth_header_name="X-Api-Key",
                auth_header_prefix="Token ",
            )

        payload = request.call_args.kwargs["json"]
        assert payload["auth_header_name"] == "X-Api-Key"
        assert payload["auth_header_prefix"] == "Token "

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

    def test_insecure_http_opt_in_is_forwarded(self, manager):
        """Lemonade refuses to hold a token for an http:// endpoint without
        this, so an on-prem gateway without TLS cannot be registered at all."""
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.install("http://gw.internal:8080/v1", allow_insecure_http=True)

        assert request.call_args.kwargs["json"]["allow_insecure_http"] is True

    def test_insecure_flag_is_omitted_for_https(self, manager):
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.install("https://gw.example.com/v1")

        assert "allow_insecure_http" not in request.call_args.kwargs["json"]

    def test_auth_carries_the_insecure_opt_in_for_an_http_provider(self, manager):
        """Caught end to end: install succeeded with the opt-in, then auth
        failed 400 without it, leaving a registered provider that could never
        be given a token."""
        GatewayState(base_url="http://gw.internal:8080/v1").save()
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.set_token("tok")

        assert request.call_args.kwargs["json"]["allow_insecure_http"] is True

    def test_auth_omits_the_insecure_opt_in_for_https(self, manager):
        GatewayState(base_url="https://llm.amd.com/v1").save()
        with patch("gaia.llm.gateway.requests.request") as request:
            request.return_value = _response(body={})
            manager.set_token("tok")

        assert "allow_insecure_http" not in request.call_args.kwargs["json"]

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
        # An exact set, not a subset: a new field has to be added here
        # deliberately, so nobody slips a token-shaped one in unnoticed.
        assert set(persisted) == {
            "base_url",
            "enabled_models",
            "active_model",
            "non_streaming_models",
        }

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

    @pytest.mark.parametrize("status", [401, 403, 301, 302, 303, 307, 308])
    def test_probe_treats_auth_required_as_reachable(self, manager, status):
        """The chicken-and-egg case, caught against the live gateway.

        A token cannot be set until the provider is registered, so an
        unauthenticated probe must not block registration. AMD's gateway
        answers `302 -> /login`; 401/403 mean the same thing.
        """
        with patch(
            "gaia.llm.gateway.requests.get",
            return_value=_response(status_code=status, body={}),
        ):
            assert manager.check_reachable("https://gw.example.com/v1") is None

    def test_probe_does_not_follow_redirects(self, manager):
        """Following the redirect lands on an Okta HTML page — a 200 that
        parses as neither JSON nor a model list, which would report a correct
        URL as 'not an OpenAI-compatible endpoint'."""
        with patch(
            "gaia.llm.gateway.requests.get",
            return_value=_response(status_code=302, body={}),
        ) as get:
            manager.check_reachable("https://gw.example.com/v1")

        assert get.call_args.kwargs["allow_redirects"] is False

    def test_probe_counts_models_when_the_gateway_answers(self, manager):
        with patch(
            "gaia.llm.gateway.requests.get",
            return_value=_response(body={"data": [{"id": "a"}, {"id": "b"}]}),
        ):
            assert manager.check_reachable("https://gw.example.com/v1") == 2

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

    def test_recommendation_surfaces_the_flagship_and_on_prem_models(self):
        """Ids below are the gateway's real ones, taken from its live catalog.

        The gateway lists seven Opus variants; floating all of them would bury
        the two models this feature exists to reach, so the hints deliberately
        match only the current flagship and the on-prem model.
        """
        for model_id in ("amd.Claude-Opus-5", "amd.Claude-Sonnet-5", "amd.Gemma-4-31B"):
            assert GatewayModel(id=model_id).recommended, model_id

        for model_id in (
            "amd.claude-opus-4.8",  # superseded — still selectable, just not surfaced
            "amd.claude-haiku-4.5",
            "amd.gpt-oss-20b",
        ):
            assert not GatewayModel(id=model_id).recommended, model_id

    def test_gemma_is_the_default_because_it_is_the_only_one_that_streams(
        self, manager
    ):
        """Alphabetical order put Claude-Opus-5 first, which cannot stream.

        GAIA's agent path streams by default, so that default handed a new
        user an agent that produced nothing.
        """
        manager.client.list_models.return_value = self.CATALOG
        assert manager.list_models()[0].id == "amd.gemma-4-31b-it"
        assert manager.default_model() == "amd.gemma-4-31b-it"

    def test_ensure_active_model_does_not_override_a_choice(self, manager):
        manager.client.list_models.return_value = self.CATALOG
        manager.set_active("amd.Claude-Opus-5")
        assert manager.ensure_active_model() == "amd.Claude-Opus-5"

    def test_ensure_active_model_picks_the_preferred_one_when_unset(self, manager):
        manager.client.list_models.return_value = self.CATALOG
        assert manager.ensure_active_model() == "amd.gemma-4-31b-it"

    def test_recommendation_is_case_insensitive(self):
        """The gateway mixes casing across ids (`Claude-Opus-5` vs
        `claude-opus-4.8`), so matching cannot depend on it."""
        assert GatewayModel(id="amd.CLAUDE-OPUS-5").recommended
        assert GatewayModel(id="amd.gemma-4-31b").recommended


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

    def test_uninstall_clears_the_global_default_model(self, manager, monkeypatch):
        """`use` writes the choice to two places; removing it cleaned only one,
        leaving `gaia chat` resolving a gateway id that no longer exists."""
        cleared = {}

        class FakeCfg:
            def get(self, k):
                return "amd.Claude-Opus-5"

            def set(self, k, v):
                cleared[k] = v

            def save(self):
                cleared["saved"] = True

        monkeypatch.setattr(
            "gaia.config.GaiaConfig.load", classmethod(lambda cls, *a, **k: FakeCfg())
        )
        manager.set_active("amd.Claude-Opus-5")
        with patch(
            "gaia.llm.gateway.requests.request", return_value=_response(body={})
        ):
            manager.uninstall()

        assert cleared.get("default_model") == ""
        assert cleared.get("saved")

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


class TestRememberedToken:
    """The token is the one thing GAIA persists, so pin down where and how."""

    def test_round_trips_through_the_os_credential_store(self, monkeypatch):
        vault = {}
        monkeypatch.setattr(
            "gaia.connectors.store.save_secret", lambda n, v: vault.__setitem__(n, v)
        )
        monkeypatch.setattr("gaia.connectors.store.peek_secret", lambda n: vault.get(n))
        monkeypatch.setattr(
            "gaia.connectors.store.delete_secret", lambda n: vault.pop(n, None)
        )
        from gaia.llm import gateway as gw

        gw.remember_token("tok-abc")
        assert gw.recall_token() == "tok-abc"
        assert gw.forget_token() is True
        assert gw.recall_token() is None

    def test_never_lands_in_the_state_file(self, manager, isolated_state, monkeypatch):
        """The credential store is the ONLY place it is kept."""
        monkeypatch.setattr("gaia.connectors.store.save_secret", lambda n, v: None)
        with patch(
            "gaia.llm.gateway.requests.request", return_value=_response(body={})
        ):
            manager.set_token("tok-secret-xyz")
        manager.set_active("amd.Gemma-4-31B")
        assert "tok-secret-xyz" not in isolated_state.read_text()

    def test_an_unreadable_keyring_means_prompt_me_not_crash(self, monkeypatch):
        """A locked or missing keyring must degrade to asking, never fail hard."""

        def boom(_name):
            raise RuntimeError("keyring locked")

        monkeypatch.setattr("gaia.connectors.store.peek_secret", boom)
        from gaia.llm import gateway as gw

        assert gw.recall_token() is None

    def test_ensure_authenticated_restores_after_a_lemonade_restart(
        self, manager, monkeypatch
    ):
        """Lemonade forgets its token on restart; without this the user
        re-enters it every time."""
        monkeypatch.setattr(
            "gaia.connectors.store.peek_secret", lambda n: "remembered-tok"
        )
        sent = {}

        def fake_request(method, url, **kwargs):
            if url.endswith("/system-info"):
                installed = {
                    "name": GATEWAY_PROVIDER,
                    "base_url": "https://gw/v1",
                    "env_var_set": False,
                    "runtime_key_set": bool(sent),
                    "models_discovered": 76 if sent else 0,
                }
                return _response(body={"cloud": {"providers": [installed]}})
            sent["key"] = kwargs["json"]["api_key"]
            return _response(body={"models_discovered": 76})

        with patch("gaia.llm.gateway.requests.request", side_effect=fake_request):
            assert manager.ensure_authenticated() is True

        assert sent["key"] == "remembered-tok"

    def test_a_no_op_keyring_fails_loudly_instead_of_pretending(self, monkeypatch):
        """keyring's null backend accepts a write and stores nothing.

        A headless Linux box (no gnome-keyring/kwallet) selects it, as does
        PYTHON_KEYRING_BACKEND=null. Without a read-back the user is told the
        token was remembered and is then asked for it again next launch.
        """
        monkeypatch.setattr("gaia.connectors.store.save_secret", lambda n, v: None)
        monkeypatch.setattr("gaia.connectors.store.peek_secret", lambda n: None)
        from gaia.llm.gateway import GatewayError, remember_token

        with pytest.raises(GatewayError) as excinfo:
            remember_token("tok")
        message = str(excinfo.value)
        # The remedy is platform-specific now; what must always be offered is
        # the environment variable and the opt-out.
        assert "LEMONADE_AMD_API_KEY" in message
        assert "--no-remember" in message

    @pytest.mark.parametrize(
        "platform,expected",
        [
            ("linux", "headless Linux"),
            ("darwin", "Keychain"),
            ("win32", "Windows Credential Manager"),
        ],
    )
    def test_the_remedy_matches_the_platform(self, platform, expected, monkeypatch):
        """Telling someone on a headless server to unlock gnome-keyring sends
        them to fix something they cannot fix. The env var is the answer
        there, so it leads."""
        monkeypatch.setattr("gaia.llm.gateway.sys.platform", platform)
        from gaia.llm.gateway import GATEWAY_API_KEY_ENV, _no_credential_store_message

        message = _no_credential_store_message()
        assert expected in message
        assert GATEWAY_API_KEY_ENV in message  # always offered

    def test_the_shell_syntax_matches_the_platform(self, monkeypatch):
        """A bash export line pasted into PowerShell just fails."""
        from gaia.llm.gateway import _no_credential_store_message

        monkeypatch.setattr("gaia.llm.gateway.sys.platform", "win32")
        assert "$env:LEMONADE_AMD_API_KEY = " in _no_credential_store_message()
        monkeypatch.setattr("gaia.llm.gateway.sys.platform", "linux")
        assert "export LEMONADE_AMD_API_KEY=" in _no_credential_store_message()
