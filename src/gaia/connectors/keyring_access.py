# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one way GAIA reads, writes and deletes OS keyring entries.

Each call first runs ``store.verify_keyring_backend``, so no secret is read
from or written to a plaintext or weak keyring backend, whichever feature asks.
Deletes are the exception: removing a secret only reduces exposure, so on a
weak backend they go ahead and log a warning telling the user to switch.
``tests/unit/connectors/test_keyring_access_guard.py`` fails if code elsewhere
calls ``keyring.get_password`` / ``set_password`` / ``delete_password`` directly.

Keyring's own exceptions pass through unchanged (``keyring_access.errors``); a
refused backend raises ``ConnectorsError`` before keyring is touched.
"""

from __future__ import annotations

import logging
from typing import Optional

from gaia.connectors._keyring import keyring  # actionable error if missing (#1621)

logger = logging.getLogger(__name__)

#: ``keyring.errors``, so callers can catch keyring's exceptions without importing it.
errors = keyring.errors

DOCS_URL = "https://amd-gaia.ai/docs/security/connections"

# One warning per distinct refusal per process, not one per deleted slot.
_warned: set = set()


def _verify() -> None:
    # Looked up at call time: store imports this module.
    from gaia.connectors import store

    store.verify_keyring_backend()


def check_delete() -> None:
    """Run the backend check for a delete: a weak backend warns, never refuses.

    Any other failure (no usable keyring at all) still raises.
    """
    from gaia.connectors import store

    try:
        store.verify_keyring_backend()
    except store.WeakKeyringError as e:
        message = str(e)
        if message in _warned:
            return
        _warned.add(message)
        logger.warning(
            "Deleting from an insecure keyring backend anyway, since removing "
            "a secret only reduces exposure. GAIA will not read or save "
            "secrets there. Switch to a secure system credential store: %s "
            "(%s)",
            DOCS_URL,
            message,
        )


def read(service: str, username: str) -> Optional[str]:
    """``keyring.get_password`` after the backend check."""
    _verify()
    return keyring.get_password(service, username)


def read_for_delete(service: str, username: str) -> Optional[str]:
    """Read only to find what a delete must also remove, such as chunk slots.

    Checked like a delete, so it works on a weak backend. The value must never
    leave the delete path.
    """
    check_delete()
    return keyring.get_password(service, username)


def write(service: str, username: str, value: str) -> None:
    """``keyring.set_password`` after the backend check."""
    _verify()
    keyring.set_password(service, username, value)


def delete(service: str, username: str) -> None:
    """``keyring.delete_password`` after :func:`check_delete`.

    Raises ``keyring.errors.PasswordDeleteError`` when the entry is absent.
    """
    check_delete()
    keyring.delete_password(service, username)
