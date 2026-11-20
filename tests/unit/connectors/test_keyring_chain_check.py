# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The weak-backend check covers every backend a chained keyring can write to.

Every fake here sets ``viable = False``: defining a ``KeyringBackend`` subclass
registers it globally, and a viable one would be discovered by any real
``ChainerBackend`` built later on this worker. ``keyring.get_keyring`` is
monkeypatched, so the real OS keyring is never read or written.
"""

from __future__ import annotations

import keyring
import keyring.backend
import pytest
from keyring.backends.chainer import ChainerBackend

from gaia.connectors import store
from gaia.connectors.errors import ConnectorsError

WEAK_NAMES = sorted(store._REFUSED_BACKEND_CLASS_NAMES)


def _never(*_args, **_kwargs):
    raise AssertionError("a fake backend must never be read or written")


def _backend_class(name: str, base: type = keyring.backend.KeyringBackend) -> type:
    return type(
        name,
        (base,),
        {
            "viable": False,
            "priority": 1,
            "get_password": _never,
            "set_password": _never,
            "delete_password": _never,
        },
    )


StrongA = _backend_class("WinVaultKeyring")
StrongB = _backend_class("SecretServiceKeyring")
WEAK_CLASSES = {name: _backend_class(name) for name in WEAK_NAMES}


class _FakeChainer(ChainerBackend):
    viable = False

    def __init__(self, *members):
        self._members = list(members)

    @property
    def backends(self):
        return self._members


class _RaisingChainer(ChainerBackend):
    viable = False

    @property
    def backends(self):
        raise RuntimeError("backend discovery failed")


class _DuckChain:
    """Not a ChainerBackend, but exposes a ``backends`` list like one."""

    def __init__(self, *members):
        self.backends_list = list(members)

    @property
    def backends(self):
        return self.backends_list


@pytest.fixture
def active(monkeypatch):
    def _install(backend):
        monkeypatch.setattr(keyring, "get_keyring", lambda: backend)

    return _install


def test_fakes_are_never_discoverable():
    fakes = [StrongA, StrongB, _FakeChainer, _RaisingChainer, *WEAK_CLASSES.values()]
    assert all(cls.viable is False for cls in fakes)


def test_chain_of_strong_backends_is_accepted(active, in_memory_keyring):
    active(_FakeChainer(StrongA(), StrongB(), in_memory_keyring))
    store.verify_keyring_backend()


def test_empty_chain_is_accepted(active):
    active(_FakeChainer())
    store.verify_keyring_backend()


def test_nested_chain_of_strong_backends_is_accepted(active):
    active(_FakeChainer(StrongA(), _FakeChainer(StrongB(), _FakeChainer(StrongA()))))
    store.verify_keyring_backend()


@pytest.mark.parametrize("weak_name", WEAK_NAMES)
@pytest.mark.parametrize("position", ["first", "last"])
def test_chain_with_any_weak_backend_is_refused(active, weak_name, position):
    weak = WEAK_CLASSES[weak_name]()
    members = [weak, StrongA()] if position == "first" else [StrongA(), weak]
    active(_FakeChainer(*members))
    with pytest.raises(ConnectorsError) as exc:
        store.verify_keyring_backend()
    msg = str(exc.value)
    assert "Insecure" in msg and weak_name in msg
    assert "See docs" in msg


@pytest.mark.parametrize("weak_name", WEAK_NAMES)
def test_weak_backend_in_nested_chain_is_refused(active, weak_name):
    inner = _FakeChainer(StrongB(), WEAK_CLASSES[weak_name]())
    active(_FakeChainer(StrongA(), _FakeChainer(StrongA(), inner)))
    with pytest.raises(ConnectorsError, match=weak_name):
        store.verify_keyring_backend()


@pytest.mark.parametrize("weak_name", WEAK_NAMES)
def test_strong_name_over_weak_base_is_refused_alone(active, weak_name):
    mimic = _backend_class("WinVaultKeyring", base=WEAK_CLASSES[weak_name])
    active(mimic())
    with pytest.raises(ConnectorsError, match=weak_name):
        store.verify_keyring_backend()


@pytest.mark.parametrize("weak_name", WEAK_NAMES)
def test_strong_name_over_weak_base_is_refused_in_chain(active, weak_name):
    mimic = _backend_class("macOSKeyring", base=WEAK_CLASSES[weak_name])
    active(_FakeChainer(StrongA(), mimic()))
    with pytest.raises(ConnectorsError, match=weak_name):
        store.verify_keyring_backend()


def test_chainer_named_like_a_strong_backend_is_still_expanded(active):
    disguised = type("WinVaultKeyring", (_FakeChainer,), {"viable": False})
    active(disguised(StrongA(), WEAK_CLASSES["PlaintextKeyring"]()))
    with pytest.raises(ConnectorsError, match="PlaintextKeyring"):
        store.verify_keyring_backend()


def test_duck_typed_chain_is_expanded(active):
    active(_DuckChain(StrongA(), WEAK_CLASSES["EncryptedKeyring"]()))
    with pytest.raises(ConnectorsError, match="EncryptedKeyring"):
        store.verify_keyring_backend()


def test_chain_that_cannot_be_inspected_is_refused(active):
    active(_FakeChainer(StrongA(), _RaisingChainer()))
    with pytest.raises(ConnectorsError) as exc:
        store.verify_keyring_backend()
    assert "Could not inspect" in str(exc.value)
    assert "See docs" in str(exc.value)
    assert isinstance(exc.value.__cause__, RuntimeError)


def test_chain_with_non_iterable_members_is_refused(active):
    chain = _FakeChainer()
    chain._members = 42
    active(chain)
    with pytest.raises(ConnectorsError, match="Could not inspect"):
        store.verify_keyring_backend()


def test_self_referencing_chain_is_refused(active):
    chain = _FakeChainer(StrongA())
    chain._members.append(chain)
    active(chain)
    with pytest.raises(ConnectorsError, match="nested more than"):
        store.verify_keyring_backend()


def test_chain_nested_past_the_limit_is_refused(active):
    chain = StrongA()
    for _ in range(store._MAX_CHAIN_DEPTH + 1):
        chain = _FakeChainer(chain)
    active(chain)
    with pytest.raises(ConnectorsError, match="nested more than"):
        store.verify_keyring_backend()


def test_chain_nested_to_the_limit_is_accepted(active):
    chain = StrongA()
    for _ in range(store._MAX_CHAIN_DEPTH):
        chain = _FakeChainer(chain)
    active(chain)
    store.verify_keyring_backend()


def test_save_through_weak_chain_never_writes(active):
    active(_FakeChainer(StrongA(), WEAK_CLASSES["Win32CryptoKeyring"]()))
    with pytest.raises(ConnectorsError, match="Win32CryptoKeyring"):
        store.save_connection(
            provider="google",
            account_email="a@example.com",
            refresh_token="x",
            scopes=["s"],
            client_id_hash="h",
        )


class _InstanceChain(keyring.backend.KeyringBackend):
    """Keeps its stores in an instance attribute, not a class-level property."""

    viable = False
    get_password = set_password = delete_password = _never

    def __init__(self, *members):
        super().__init__()
        self.backends = list(members)


@pytest.mark.parametrize("weak_name", WEAK_NAMES)
def test_chain_holding_stores_in_an_instance_attribute_is_expanded(active, weak_name):
    active(_InstanceChain(StrongA(), WEAK_CLASSES[weak_name]()))
    with pytest.raises(ConnectorsError, match=weak_name):
        store.verify_keyring_backend()


def test_instance_attribute_chain_of_strong_backends_is_accepted(active):
    active(_InstanceChain(StrongA(), _InstanceChain(StrongB())))
    store.verify_keyring_backend()


@pytest.mark.parametrize(
    "member",
    [object(), "PlaintextKeyring", None, 42],
    ids=["object", "class-name-string", "none", "int"],
)
def test_chain_member_that_is_not_a_keyring_backend_is_refused(active, member):
    active(_FakeChainer(StrongA(), member))
    with pytest.raises(ConnectorsError) as exc:
        store.verify_keyring_backend()
    msg = str(exc.value)
    assert "not a keyring backend" in msg and "See docs" in msg


def test_duck_chain_nested_inside_a_chain_is_refused(active):
    """A member must be a real backend, even one that looks like a chain."""
    active(_FakeChainer(StrongA(), _DuckChain(StrongB())))
    with pytest.raises(ConnectorsError, match="not a keyring backend"):
        store.verify_keyring_backend()


def _repeated_chain(width: int, depth: int, leaf) -> object:
    chain = leaf
    for _ in range(depth):
        chain = _FakeChainer(*([chain] * width))
    return chain


def test_repeated_chain_is_walked_once_per_chain(active):
    import time

    active(_repeated_chain(10, store._MAX_CHAIN_DEPTH, StrongA()))
    started = time.monotonic()
    store.verify_keyring_backend()
    assert time.monotonic() - started < 2.0


def test_weak_store_under_a_repeated_chain_is_still_refused(active):
    import time

    active(
        _repeated_chain(10, store._MAX_CHAIN_DEPTH, WEAK_CLASSES["PlaintextKeyring"]())
    )
    started = time.monotonic()
    with pytest.raises(ConnectorsError, match="PlaintextKeyring"):
        store.verify_keyring_backend()
    assert time.monotonic() - started < 2.0


def test_repeated_chain_past_the_depth_limit_is_still_refused(active):
    active(_repeated_chain(10, store._MAX_CHAIN_DEPTH + 1, StrongA()))
    with pytest.raises(ConnectorsError, match="nested more than"):
        store.verify_keyring_backend()


def test_shared_chain_reached_at_two_depths_is_checked(active):
    weak_inner = _FakeChainer(WEAK_CLASSES["EncryptedKeyring"]())
    active(_FakeChainer(_FakeChainer(weak_inner), weak_inner))
    with pytest.raises(ConnectorsError, match="EncryptedKeyring"):
        store.verify_keyring_backend()
