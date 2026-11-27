# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The unit suite never uses the developer's real OS keyring (#8848)."""

from __future__ import annotations

import subprocess
import sys

import keyring
import keyring.backend
import pytest

from tests.conftest import _guard_keyring_backend, in_memory_keyring


class _FakePlatformKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def get_password(self, service, username):
        return "developer-secret"

    def set_password(self, service, username, password):
        return None

    def delete_password(self, service, username):
        return None


def test_every_test_starts_on_the_in_memory_keyring():
    backend = keyring.get_keyring()
    assert type(backend).__name__ == "_InMemoryKeyring"

    keyring.set_password("gaia.test.isolation", "round-trip", "value")
    assert keyring.get_password("gaia.test.isolation", "round-trip") == "value"
    keyring.delete_password("gaia.test.isolation", "round-trip")


@pytest.mark.parametrize(
    "operation", ("get_password", "set_password", "delete_password", "get_credential")
)
def test_a_reference_to_the_platform_backend_fails_loudly(operation):
    """The wrapper catches code that bypasses the active in-memory backend."""
    backend, restore = _guard_keyring_backend(_FakePlatformKeyring())
    try:
        with pytest.raises(pytest.fail.Exception, match="real OS keyring"):
            getattr(backend, operation)(
                "gaia.test.isolation", "developer-secret", "value"
            )
    finally:
        restore()


def test_the_guard_restores_the_platform_backend_after_the_session():
    backend, restore = _guard_keyring_backend(_FakePlatformKeyring())
    with pytest.raises(pytest.fail.Exception, match="real OS keyring"):
        backend.get_password("gaia.test.isolation", "developer-secret")
    restore()
    assert (
        backend.get_password("gaia.test.isolation", "developer-secret")
        == "developer-secret"
    )


def test_the_session_fixture_guards_and_restores_the_prior_backend(monkeypatch):
    """Drive the fixture directly so its teardown can be asserted."""
    prior = _FakePlatformKeyring()
    installed = []
    monkeypatch.setattr(keyring, "get_keyring", lambda: prior)
    monkeypatch.setattr(keyring, "set_keyring", installed.append)

    # The undecorated generator is the session fixture's body, so closing it
    # exercises the real teardown without ending the test session.
    fixture = in_memory_keyring.__wrapped__()
    try:
        backend = next(fixture)
        assert type(backend).__name__ == "_InMemoryKeyring"
        assert installed[-1] is backend
        with pytest.raises(pytest.fail.Exception, match="real OS keyring"):
            prior.get_password("gaia.test.isolation", "developer-secret")
    finally:
        fixture.close()

    assert installed[-1] is prior
    assert (
        prior.get_password("gaia.test.isolation", "developer-secret")
        == "developer-secret"
    )


def test_child_processes_resolve_the_null_keyring_backend():
    """``set_keyring`` is process-local, so children need the env backend."""
    code = "import keyring; print(type(keyring.get_keyring()).__module__)"
    output = subprocess.check_output([sys.executable, "-c", code], text=True)
    assert output.strip() == "keyring.backends.null"
