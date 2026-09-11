# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Resolving the Lemonade credential, including the embedded server's own."""

import json

import pytest

from gaia.llm import lemonade_client as lc


@pytest.fixture
def embedded_state(tmp_path, monkeypatch):
    """Point the resolver at a throwaway embedded-Lemonade state file."""

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
        embedded_state({"api_key": "from-state"})
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

    @pytest.mark.parametrize("port", [0, -1, "63207", None])
    def test_an_unusable_port_is_ignored(self, embedded_state, monkeypatch, port):
        embedded_state({"port": port, "api_key": "k"})
        monkeypatch.delenv("LEMONADE_BASE_URL", raising=False)
        _, _, base = lc._get_lemonade_config()
        assert base == lc.DEFAULT_LEMONADE_URL
