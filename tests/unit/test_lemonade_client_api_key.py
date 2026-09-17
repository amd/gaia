# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Resolving the Lemonade credential, including the embedded server's own."""

import json

import pytest
import responses

from gaia.llm import lemonade_client as lc


@pytest.fixture
def embedded_state(tmp_path, monkeypatch):
    """Point the resolver at a throwaway embedded-Lemonade state file."""
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)

    def _write(payload):
        path = tmp_path / "state.json"
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )
        monkeypatch.setattr(lc, "EMBEDDED_LEMONADE_STATE", path)
        return path

    monkeypatch.setattr(lc, "EMBEDDED_LEMONADE_STATE", tmp_path / "state.json")
    return _write


class TestResolveLemonadeApiKey:
    def test_explicit_argument_wins(self, embedded_state, monkeypatch):
        embedded_state({"api_key": "from-state"})
        monkeypatch.setenv("LEMONADE_API_KEY", "from-env")
        assert lc.resolve_lemonade_api_key("explicit") == "explicit"

    def test_env_beats_the_state_file(self, embedded_state, monkeypatch):
        """A configured credential must not be overridden by a local file."""
        embedded_state({"api_key": "from-state"})
        monkeypatch.setenv("LEMONADE_API_KEY", "from-env")
        assert lc.resolve_lemonade_api_key() == "from-env"

    def test_falls_back_to_the_embedded_servers_key(self, embedded_state, monkeypatch):
        """Regression: every client got 401 from a healthy embedded server.

        `gaia lemonade embedded` mints a key and writes it to its state file.
        Nothing exports it, so resolving from the environment alone produced
        401s that the readiness screen reported as "Lemonade not running".
        """
        embedded_state({"pid": 1, "port": 13305, "api_key": "from-state"})
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        assert lc.resolve_lemonade_api_key() == "from-state"

    def test_blank_env_does_not_mask_the_state_file(self, embedded_state, monkeypatch):
        """An empty env var is unset, not an instruction to send no key."""
        embedded_state({"port": 13305, "api_key": "from-state"})
        monkeypatch.setenv("LEMONADE_API_KEY", "   ")
        assert lc.resolve_lemonade_api_key() == "from-state"

    def test_no_state_and_no_env_is_unauthenticated(self, embedded_state, monkeypatch):
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        assert lc.resolve_lemonade_api_key() is None

    @pytest.mark.parametrize(
        "payload", ["{not json", {}, {"api_key": ""}, {"api_key": None}]
    )
    def test_unusable_state_file_is_not_fatal(
        self, embedded_state, monkeypatch, payload
    ):
        embedded_state(payload)
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        assert lc.resolve_lemonade_api_key() is None


class TestEmbeddedBaseURL:
    """The embedded server binds a port chosen at start time."""

    def test_discovers_the_embedded_port(self, embedded_state, monkeypatch):
        """Regression: clients looked at the default port and saw nothing.

        `gaia lemonade embedded` came up on 63207 holding every model, while
        the readiness screen reported the language model as not downloaded.
        """
        embedded_state({"pid": 1, "port": 63207, "api_key": "k"})
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        _, port, base = lc._get_lemonade_config()
        assert port == 63207
        assert base == "http://localhost:63207/api/v1"

    def test_an_explicit_base_url_wins(self, embedded_state, monkeypatch):
        embedded_state({"port": 63207, "api_key": "k"})
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://example.test:9000")
        _, port, base = lc._get_lemonade_config()
        assert port == 9000
        assert base == "http://example.test:9000/api/v1"

    def test_falls_back_to_the_packaged_default(self, embedded_state, monkeypatch):
        """No embedded server is the normal case for a tray install."""
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        _, _, base = lc._get_lemonade_config()
        assert base == lc.DEFAULT_LEMONADE_URL

    @pytest.mark.parametrize("port", [0, -1, 65536, True, "63207", None])
    def test_an_unusable_port_is_ignored(self, embedded_state, monkeypatch, port):
        embedded_state({"port": port, "api_key": "k"})
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        _, _, base = lc._get_lemonade_config()
        assert base == lc.DEFAULT_LEMONADE_URL


class TestResolveLemonadeBaseURL:
    """The public resolver every caller should use to locate Lemonade."""

    def test_explicit_argument_wins(self, embedded_state, monkeypatch):
        embedded_state({"port": 63207, "api_key": "k"})
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://env.test:9000")
        assert (
            lc.resolve_lemonade_base_url("http://explicit.test:1234/api/v1")
            == "http://explicit.test:1234/api/v1"
        )

    def test_a_bare_origin_gains_the_api_path(self, embedded_state, monkeypatch):
        """Users configure the origin; callers append endpoints to the result.

        Returning it unchanged put the burden of adding /api/v1 back on each
        caller, which is exactly the split that produced a doubled
        ``/api/v1/api/v1/models`` in the agent's readiness probe.
        """
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        assert (
            lc.resolve_lemonade_base_url("http://explicit.test:1234")
            == "http://explicit.test:1234/api/v1"
        )

    def test_a_trailing_slash_does_not_double_the_path(self, monkeypatch):
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        assert (
            lc.resolve_lemonade_base_url("http://x.test:1/api/v1/")
            == "http://x.test:1/api/v1"
        )

    def test_finds_the_embedded_server(self, embedded_state, monkeypatch):
        embedded_state({"port": 63207, "api_key": "k"})
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        assert lc.resolve_lemonade_base_url() == "http://localhost:63207/api/v1"

    def test_falls_back_to_the_packaged_default(self, embedded_state, monkeypatch):
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        assert lc.resolve_lemonade_base_url() == lc.DEFAULT_LEMONADE_URL

    def test_always_carries_the_api_version(self, embedded_state, monkeypatch):
        """The embedded state file records a port, not a URL.

        Returning a bare host:port here would leave callers to append
        ``/api/v1`` themselves — which is how the inline defaults this
        function replaces drifted apart in the first place.
        """
        embedded_state({"port": 63207, "api_key": "k"})
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        assert lc.resolve_lemonade_base_url().endswith("/api/v1")


class TestEmbeddedCredentialScope:
    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
    def test_key_matches_embedded_local_endpoint(
        self, embedded_state, monkeypatch, host
    ):
        embedded_state({"port": 63207, "api_key": "embedded-only"})
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        base = f"http://{host}:63207/api/v1"
        assert lc.resolve_lemonade_api_key(base_url=base) == "embedded-only"
        assert lc.LemonadeClient(base_url=base).api_key == "embedded-only"

    @pytest.mark.parametrize(
        "base",
        [
            "https://api.fireworks.ai/inference/v1",
            "https://gateway.example.test:63207/v1",
            "http://other-host:63207/api/v1",
            "http://127.0.0.2:63207/api/v1",
            "http://localhost:63208/api/v1",
            "https://localhost:63207/api/v1",
            "http://user@localhost:63207/api/v1",
            "http://localhost:63207/api/v1?redirect=elsewhere",
            "http://localhost:63207/api/v1#fragment",
            "http://localhost:invalid/api/v1",
        ],
    )
    def test_unrelated_endpoint_never_gets_embedded_key(
        self, embedded_state, monkeypatch, base
    ):
        embedded_state({"port": 63207, "api_key": "embedded-only"})
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        assert lc.resolve_lemonade_api_key(base_url=base) is None

    def test_explicit_and_environment_keys_still_override(
        self, embedded_state, monkeypatch
    ):
        embedded_state({"port": 63207, "api_key": "embedded-only"})
        base = "https://gateway.example.test/v1"
        monkeypatch.setenv("LEMONADE_API_KEY", "configured-key")
        assert lc.resolve_lemonade_api_key(base_url=base) == "configured-key"
        assert (
            lc.resolve_lemonade_api_key("explicit-key", base_url=base) == "explicit-key"
        )

    def test_no_argument_resolver_scopes_key_to_configured_url(
        self, embedded_state, monkeypatch
    ):
        embedded_state({"port": 63207, "api_key": "embedded-only"})
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        monkeypatch.setenv("LEMONADE_BASE_URL", "https://gateway.example.test/v1")
        assert lc.resolve_lemonade_api_key() is None

    @pytest.mark.parametrize("client_type", ["chat", "transcription"])
    def test_client_explicit_endpoint_does_not_inherit_local_key(
        self, embedded_state, monkeypatch, client_type
    ):
        from gaia.audio.lemonade_asr import LemonadeASRClient

        embedded_state({"port": 63207, "api_key": "embedded-only"})
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        cls = lc.LemonadeClient if client_type == "chat" else LemonadeASRClient
        client = cls(base_url="https://gateway.example.test/v1")
        assert client.api_key is None

    def test_gaia_home_isolated_from_default_runtime(
        self, embedded_state, monkeypatch, tmp_path
    ):
        embedded_state({"port": 63207, "api_key": "default-key"})
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        isolated = tmp_path / "isolated"
        monkeypatch.setenv("GAIA_HOME", str(isolated))
        assert lc.resolve_lemonade_base_url() == lc.DEFAULT_LEMONADE_URL
        assert lc.resolve_lemonade_api_key() is None

        (isolated / "lemonade").mkdir(parents=True)
        state = isolated / "lemonade" / "state.json"
        state.write_text(json.dumps({"port": 63208, "api_key": "isolated-key"}))
        assert lc.resolve_lemonade_base_url() == "http://localhost:63208/api/v1"
        assert lc.resolve_lemonade_api_key() == "isolated-key"
        assert (
            lc.resolve_lemonade_api_key(base_url="http://localhost:63207/api/v1")
            is None
        )

        state.write_text("invalid json")
        assert lc.resolve_lemonade_base_url() == lc.DEFAULT_LEMONADE_URL
        assert lc.resolve_lemonade_api_key() is None

    def test_readiness_explicit_endpoint_does_not_receive_embedded_key(
        self, embedded_state, monkeypatch
    ):
        from unittest.mock import MagicMock

        from gaia.agents.base.readiness import probe_model_present, pull_model

        embedded_state({"port": 63207, "api_key": "embedded-only"})
        monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
        response = MagicMock()
        response.json.return_value = {"data": []}
        get = MagicMock(return_value=response)
        post = MagicMock(return_value=response)
        monkeypatch.setattr("requests.get", get)
        monkeypatch.setattr("requests.post", post)
        base = "https://gateway.example.test/api/v1"

        assert probe_model_present(base, "test-model") is False
        pull_model(base, "test-model")

        assert get.call_args.kwargs["headers"] == {}
        assert post.call_args.kwargs["headers"] == {}


@pytest.mark.parametrize("source", ["environment", "explicit"])
@pytest.mark.parametrize(
    "configured,expected",
    [
        ("http://proxy.test:13305", "http://proxy.test:13305/api/v1"),
        ("http://proxy.test:13305/", "http://proxy.test:13305/api/v1"),
        ("http://proxy.test:13305/v1", "http://proxy.test:13305/v1"),
        ("http://proxy.test:13305/v1/", "http://proxy.test:13305/v1"),
        ("https://proxy.test/llm/custom/v1", "https://proxy.test/llm/custom/v1"),
        ("https://proxy.test/api/v1/", "https://proxy.test/api/v1"),
    ],
)
@responses.activate
def test_configured_api_path_reaches_exact_endpoint(
    embedded_state, monkeypatch, source, configured, expected
):
    from gaia.audio.lemonade_asr import LemonadeASRClient

    monkeypatch.delenv("LEMONADE_API_KEY", raising=False)
    kwargs = {}
    if source == "environment":
        monkeypatch.setenv("LEMONADE_BASE_URL", configured)
    else:
        kwargs["base_url"] = configured
    assert lc.resolve_lemonade_base_url(kwargs.get("base_url")) == expected
    client = lc.LemonadeClient(**kwargs)
    assert client.base_url == expected
    assert LemonadeASRClient(**kwargs).base_url == expected
    responses.get(f"{expected}/health", json={"status": "ok"})
    assert client.health_check() == {"status": "ok"}
    assert len(responses.calls) == 1
