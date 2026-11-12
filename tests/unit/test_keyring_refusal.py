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
    assert "won't ask again for 5 minutes" in str(e.value)


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


def test_the_locked_message_says_where_to_try_again(dismissed):
    with pytest.raises(store.KeyringRefused) as e:
        store.peek_connection("google")
    assert "Try again under Settings → Model" in str(e.value)


def test_a_read_that_returned_a_key_is_not_reported_locked():
    def read_while_another_slot_is_refused():
        store._refuse(RuntimeError("Prompt dismissed"))
        return "fw_saved_key_123456"

    assert (
        read_within("cloud-key:fireworks", read_while_another_slot_is_refused, "key")
        == "fw_saved_key_123456"
    )


def test_concurrent_pollers_across_slots_open_the_prompt_once(dismissed):
    import threading

    gate = threading.Barrier(16)
    outcomes = []

    def poll(slot):
        gate.wait()
        for _ in range(10):
            try:
                read_within(
                    f"cloud-key:{slot}",
                    lambda: cloud_keys.recall_key(slot),
                    "a saved key",
                )
            except CredentialStoreLocked:
                outcomes.append("locked")
            except Exception as e:  # a crashed poller must fail the test
                outcomes.append(repr(e))
            else:
                outcomes.append("read")

    slots = ["fireworks", "amd"] * 8
    threads = [threading.Thread(target=poll, args=(s,)) for s in slots]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Racing first reads may each reach the keyring once; no poll after that does.
    assert dismissed.reads <= len(set(slots))
    assert outcomes == ["locked"] * 160
    reads_after_race = dismissed.reads
    for slot in set(slots):
        with pytest.raises(CredentialStoreLocked):
            read_within(f"cloud-key:{slot}", lambda: cloud_keys.recall_key(slot), "k")
    assert dismissed.reads == reads_after_race


def _expire_hold():
    with store._refusal_lock:
        store._refused_at = store.time.monotonic() - store.HOLD_SECONDS - 1


def test_the_hold_expires(dismissed):
    with pytest.raises(store.KeyringRefused):
        store.peek_secret("provider:fireworks")
    assert store.keyring_refusal() is not None
    _expire_hold()
    assert store.keyring_refusal() is None


def test_a_read_after_the_hold_expires_asks_the_store_again(dismissed):
    """The daemon's token refresh never calls retry: expiry is how it recovers."""
    with pytest.raises(store.KeyringRefused):
        store.peek_connection("google")
    _expire_hold()
    with pytest.raises(store.KeyringRefused):
        store.peek_connection("google")
    assert dismissed.reads == 2
    # Still dismissed: the new hold covers the polls that follow.
    for _ in range(10):
        with pytest.raises(store.KeyringRefused):
            store.peek_connection("google")
    assert dismissed.reads == 2


def test_an_unlock_elsewhere_lets_reads_through_after_expiry(dismissed):
    with pytest.raises(store.KeyringRefused):
        store.peek_secret("provider:fireworks")
    dismissed.error = None
    dismissed.values["secret:provider:fireworks"] = "fw_saved_key_123456"
    with pytest.raises(store.KeyringRefused):
        store.peek_secret("provider:fireworks")
    _expire_hold()
    assert store.peek_secret("provider:fireworks") is not None
    assert store.keyring_refusal() is None
    store.peek_secret("provider:fireworks")
    assert dismissed.reads == 3


def test_concurrent_polls_inside_the_window_never_reach_the_store(dismissed):
    import threading

    with pytest.raises(store.KeyringRefused):
        store.peek_secret("provider:fireworks")
    gate = threading.Barrier(16)
    errors = []

    def poll(i):
        gate.wait()
        for _ in range(10):
            try:
                if i % 2:
                    store.peek_connection("google")
                else:
                    read_within(
                        "cloud-key:fireworks",
                        lambda: cloud_keys.recall_key("fireworks"),
                        "k",
                    )
            except (store.KeyringRefused, CredentialStoreLocked):
                continue
            errors.append("read went through")

    threads = [threading.Thread(target=poll, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    assert dismissed.reads == 1


def test_concurrent_polls_at_expiry_send_one_read_to_the_store(dismissed, monkeypatch):
    """Only one poller probes an expired hold; the rest stay held until it ends."""
    import threading

    with pytest.raises(store.KeyringRefused):
        store.peek_secret("provider:fireworks")
    _expire_hold()

    probing = threading.Event()
    release = threading.Event()
    real_get = dismissed.get_password

    def slow_prompt(service, username):
        probing.set()
        release.wait(5)
        return real_get(service, username)

    monkeypatch.setattr(store.keyring, "get_password", slow_prompt)
    outcomes = []

    def poll(slot):
        try:
            store.peek_secret(slot)
        except store.KeyringRefused:
            outcomes.append("held")

    first = threading.Thread(target=poll, args=("provider:fireworks",))
    first.start()
    assert probing.wait(5)
    others = [
        threading.Thread(target=poll, args=(f"provider:p{i}",)) for i in range(12)
    ]
    for t in others:
        t.start()
    for t in others:
        t.join(timeout=5)
    release.set()
    first.join(timeout=5)
    assert dismissed.reads == 2
    assert outcomes.count("held") == 13
    assert store.keyring_refusal() is not None


def test_a_probe_that_fails_another_way_does_not_wedge_the_hold(dismissed):
    with pytest.raises(store.KeyringRefused):
        store.peek_secret("provider:fireworks")
    _expire_hold()
    dismissed.error = RuntimeError("backend crashed")
    with pytest.raises(RuntimeError):
        store.peek_secret("provider:fireworks")
    assert store._probing is False
    dismissed.error = None
    store.peek_secret("provider:fireworks")
    assert store.keyring_refusal() is None
    assert dismissed.reads == 3


def test_another_slots_successful_read_does_not_lift_a_fresh_hold(monkeypatch):
    """A read already in flight when a prompt is dismissed must not clear the hold."""
    import threading

    started = threading.Event()
    release = threading.Event()
    errors = store.keyring.errors

    def get_password(service, username):
        if username.endswith("slow"):
            started.set()
            release.wait(5)
            return "value"
        raise errors.KeyringLocked("Failed to unlock the collection!")

    monkeypatch.setattr(store.keyring, "get_password", get_password)
    monkeypatch.setattr(store, "verify_keyring_backend", lambda: None)
    slow = threading.Thread(target=store.peek_secret, args=("slow",))
    slow.start()
    assert started.wait(5)
    with pytest.raises(store.KeyringRefused):
        store.peek_secret("fast")
    release.set()
    slow.join(timeout=5)
    assert store.keyring_refusal() is not None
