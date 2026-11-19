# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/host/v1/keychain/*`` — the daemon reads the OS keychain for the flagship.

See :mod:`gaia.connectors.daemon_keyring` for why. These are the only daemon
routes that return a stored secret, so they take their own credential, minted
only for specs with ``daemon_owns_keychain``. The daemon client token and the
custody secret do not open them. Values are never logged.
"""

from __future__ import annotations

# Module-level (mirrors custody/routes.py): imported lazily from create_app, so
# endpoint annotations must resolve from module globals under PEP 563.
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from gaia.connectors.errors import ConnectorsError
from gaia.daemon.custody.constants import HOST_API_PREFIX
from gaia.daemon.custody.routes import _resolve_agent

# Mirrors gaia.connectors.store.SERVICE_NAME; a test pins them. Importing the
# store needs ``keyring``, an optional extra the daemon must start without.
SERVICE_NAME = "gaia.connections"


def _keyring():
    try:
        from gaia.connectors._keyring import keyring
        from gaia.connectors.store import verify_keyring_backend

        # The sidecar's own check sees only DaemonKeyring, so refuse a
        # plaintext backend here, where the secrets are actually stored.
        verify_keyring_backend()
    except ConnectorsError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return keyring


class KeychainSlot(BaseModel):
    service: str
    username: str = Field(min_length=1)


class KeychainWrite(KeychainSlot):
    value: str


def _check_service(slot: KeychainSlot) -> None:
    if slot.service != SERVICE_NAME:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Keychain service {slot.service!r} is not available through the "
                f"daemon; only {SERVICE_NAME!r} is."
            ),
        )


def _unavailable(action: str, e: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=f"The OS keychain could not {action} the item: {e}",
    )


def build_keychain_router(auth) -> APIRouter:
    router = APIRouter()

    # Plain ``def``: a read can wait minutes on a macOS dialog, so it needs a
    # worker thread rather than the event loop.
    @router.post(f"{HOST_API_PREFIX}/keychain/get")
    def keychain_get(
        slot: KeychainSlot, authorization: "str | None" = Header(default=None)
    ) -> dict:
        _resolve_agent(auth, authorization)
        _check_service(slot)
        keyring = _keyring()
        try:
            return {"value": keyring.get_password(slot.service, slot.username)}
        except keyring.errors.KeyringError as e:
            raise _unavailable("read", e) from e

    @router.post(f"{HOST_API_PREFIX}/keychain/set")
    def keychain_set(
        write: KeychainWrite, authorization: "str | None" = Header(default=None)
    ) -> dict:
        _resolve_agent(auth, authorization)
        _check_service(write)
        keyring = _keyring()
        try:
            keyring.set_password(write.service, write.username, write.value)
        except keyring.errors.KeyringError as e:
            raise _unavailable("write", e) from e
        return {"ok": True}

    @router.post(f"{HOST_API_PREFIX}/keychain/delete")
    def keychain_delete(
        slot: KeychainSlot, authorization: "str | None" = Header(default=None)
    ) -> dict:
        _resolve_agent(auth, authorization)
        _check_service(slot)
        keyring = _keyring()
        try:
            keyring.delete_password(slot.service, slot.username)
        except keyring.errors.PasswordDeleteError as e:
            raise HTTPException(status_code=404, detail="Not stored.") from e
        except keyring.errors.KeyringError as e:
            raise _unavailable("delete", e) from e
        return {"ok": True}

    return router
