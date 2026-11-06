# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where the bot token lives, including a Linux box with no keyring."""

from __future__ import annotations

import pytest

from gaia.messaging.discord import credentials


class _NoBackend:
    """What keyring is on a headless Linux box: every call fails."""

    class errors:
        PasswordDeleteError = Exception

    @staticmethod
    def set_password(*_a):
        raise RuntimeError(
            "No recommended backend was available. Install a recommended 3rd "
            "party backend package"
        )

    get_password = set_password


@pytest.fixture
def no_keyring(monkeypatch):
    monkeypatch.setattr(credentials, "_keyring", lambda: _NoBackend)
    monkeypatch.delenv(credentials.BOT_TOKEN_ENV_VAR, raising=False)


def test_no_keyring_says_what_to_do_not_what_python_package_to_install(no_keyring):
    with pytest.raises(credentials.DiscordCredentialsError) as caught:
        credentials.save("token")
    message = str(caught.value)
    assert "no secure place to keep" in message
    assert credentials.BOT_TOKEN_ENV_VAR in message
    assert "keyrings.alt" not in message


def test_a_token_from_the_environment_needs_no_keyring(no_keyring, monkeypatch):
    monkeypatch.setenv(credentials.BOT_TOKEN_ENV_VAR, "token")
    credentials.save("token")  # nothing to store, nothing raised
    assert credentials.load() == "token"


def test_the_connector_check_does_not_crash_without_a_keyring(no_keyring):
    assert credentials.connector_token() is None
