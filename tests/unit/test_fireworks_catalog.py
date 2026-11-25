# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA's Fireworks key resolution: argument, environment, then keyring."""

from __future__ import annotations

import pytest

from gaia.connectors.errors import ConnectorsError
from gaia.llm.providers.fireworks import (
    API_KEY_ENV_VARS,
    FireworksError,
    resolve_fireworks_api_key,
)


@pytest.fixture
def no_env(monkeypatch):
    """No key anywhere: not in the environment, not in the OS keyring.

    The keyring half matters — without it these tests pass or fail depending
    on whether the developer running them happens to have a real key stored,
    which is exactly the hidden-state trap the repo's testing rules warn about.
    """
    for name in API_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("gaia.connectors.store.peek_secret", lambda _name: None)


class TestKeyResolution:
    def test_argument_wins(self, monkeypatch):
        monkeypatch.setenv(API_KEY_ENV_VARS[0], "from-env")
        assert resolve_fireworks_api_key("explicit") == "explicit"

    def test_falls_back_to_lemonades_own_variable(self, monkeypatch, no_env):
        monkeypatch.setenv(API_KEY_ENV_VARS[1], "lemonade-key")
        assert resolve_fireworks_api_key() == "lemonade-key"

    def test_blank_counts_as_unset(self, monkeypatch, no_env):
        monkeypatch.setenv(API_KEY_ENV_VARS[0], "   ")
        assert resolve_fireworks_api_key() is None

    def test_a_stored_key_is_used_when_nothing_is_set(self, monkeypatch):
        """The keyring is the whole point: no shell profile, no file."""
        for name in API_KEY_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(
            "gaia.connectors.store.peek_secret", lambda _name: "from-keyring"
        )
        assert resolve_fireworks_api_key() == "from-keyring"

    def test_environment_beats_the_stored_key(self, monkeypatch):
        """A key set for this run must not be overridden by an old stored one."""
        monkeypatch.setenv(API_KEY_ENV_VARS[0], "from-env")
        monkeypatch.setattr(
            "gaia.connectors.store.peek_secret", lambda _name: "from-keyring"
        )
        assert resolve_fireworks_api_key() == "from-env"

    def test_an_unreadable_keyring_still_names_the_variable_to_set(
        self, monkeypatch, no_env
    ):
        """No usable credential store must not hide the simple fix."""

        def no_backend(_name):
            raise ConnectorsError("Keyring get_password failed: no backend.")

        monkeypatch.setattr("gaia.connectors.store.peek_secret", no_backend)
        with pytest.raises(FireworksError) as e:
            resolve_fireworks_api_key()
        message = str(e.value)
        assert API_KEY_ENV_VARS[0] in message
        assert "no backend" in message, "the keyring problem must stay visible"
        assert isinstance(e.value.__cause__, ConnectorsError)
