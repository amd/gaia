# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A dismissed unlock prompt is not reopened by GAIA's polling (#7227).

On GNOME each keyring read with no unlocked collection opens a shell-modal
prompt; GAIA used to start a fresh read every poll, so Cancel brought it
straight back about once a second.
"""

from __future__ import annotations

import pytest

from gaia.connectors import credential_wait, store
from gaia.connectors.credential_wait import CredentialStoreLocked, read_within
from gaia.llm import cloud_keys


class FakeKeyring:
    """keyring.get_password: counts reads, and the user cancels every prompt."""

    def __init__(self, error):
        self.reads = 0
        self.error = error
        self.values = {}

    def get_password(self, service, username):
        self.reads += 1
        if self.error is not None:
            raise self.error
        return self.values.get(username)


@pytest.fixture(autouse=True)
def lifted():
    store.retry_keyring()
    credential_wait._in_flight.clear()
    yield
    store.retry_keyring()
    credential_wait._in_flight.clear()


@pytest.fixture(params=["create", "unlock"])
def dismissed(request, monkeypatch):
    errors = store.keyring.errors
    error = (
        errors.InitError("Failed to create the collection: Prompt dismissed..")
        if request.param == "create"
        else errors.KeyringLocked("Failed to unlock the collection!")
    )
    fake = FakeKeyring(error)
    monkeypatch.setattr(store.keyring, "get_password", fake.get_password)
    monkeypatch.setattr(store, "verify_keyring_backend", lambda: None)
    return fake


def test_after_a_dismissed_prompt_reads_stop_asking(dismissed):
    with pytest.raises(store.KeyringRefused):
        store.peek_secret("provider:fireworks")
    for _ in range(20):
        with pytest.raises(store.KeyringRefused) as e:
            store.peek_secret("provider:fireworks")
    assert dismissed.reads == 1
    assert "will not ask again" in str(e.value)


def test_retry_asks_the_store_once_more(dismissed):
    with pytest.raises(store.KeyringRefused):
        store.peek_secret("provider:fireworks")
    store.retry_keyring()
    dismissed.error = None
    dismissed.values["secret:provider:fireworks"] = "fw_saved_key_123456"
    assert store.keyring_refusal() is None
    store.peek_secret("provider:fireworks")
    assert dismissed.reads == 2


def test_saving_a_key_is_an_action_that_may_ask_again(dismissed, monkeypatch):
    with pytest.raises(store.KeyringRefused):
        store.peek_secret("provider:fireworks")
    monkeypatch.setattr(store.keyring, "set_password", lambda *a: None)
    dismissed.error = None
    store.save_secret("provider:fireworks", "fw_new_key_1234567")
    assert store.keyring_refusal() is None


def test_other_keyring_failures_do_not_hold_reads(monkeypatch):
    fake = FakeKeyring(store.keyring.errors.KeyringError("dbus went away"))
    monkeypatch.setattr(store.keyring, "get_password", fake.get_password)
    monkeypatch.setattr(store, "verify_keyring_backend", lambda: None)
    for _ in range(2):
        with pytest.raises(store.ConnectorsError):
            store.peek_secret("provider:fireworks")
    assert store.keyring_refusal() is None
    assert fake.reads == 2


def test_polling_the_saved_key_reports_locked_not_no_key(dismissed):
    def poll():
        return read_within(
            "cloud-key:fireworks",
            lambda: cloud_keys.recall_key("fireworks"),
            "your saved Fireworks AI key",
        )

    for _ in range(30):
        with pytest.raises(CredentialStoreLocked):
            poll()
    assert dismissed.reads == 1
