# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one way GAIA reads, writes and deletes OS keyring entries.

Each call first runs ``store.verify_keyring_backend``, so no secret is read
from or written to a plaintext or weak keyring backend, whichever feature asks.
``tests/unit/connectors/test_keyring_access_guard.py`` fails if code elsewhere
calls ``keyring.get_password`` / ``set_password`` / ``delete_password`` directly.

Keyring's own exceptions pass through unchanged (``keyring_access.errors``); a
refused backend raises ``ConnectorsError`` before keyring is touched.
"""

from __future__ import annotations

from typing import Optional

from gaia.connectors._keyring import keyring  # actionable error if missing (#1621)

#: ``keyring.errors``, so callers can catch keyring's exceptions without importing it.
errors = keyring.errors


def _verify() -> None:
    # Looked up at call time: store imports this module.
    from gaia.connectors import store

    store.verify_keyring_backend()


def read(service: str, username: str) -> Optional[str]:
    """``keyring.get_password`` after the backend check."""
    _verify()
    return keyring.get_password(service, username)


def write(service: str, username: str, value: str) -> None:
    """``keyring.set_password`` after the backend check."""
    _verify()
    keyring.set_password(service, username, value)


def delete(service: str, username: str) -> None:
    """``keyring.delete_password`` after the backend check.

    Raises ``keyring.errors.PasswordDeleteError`` when the entry is absent.
    """
    _verify()
    keyring.delete_password(service, username)
