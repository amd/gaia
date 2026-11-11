# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A ``keyring`` backend that sends the flagship sidecar's reads to the daemon.

macOS ties keychain approval to the exact build of the program asking. The
frozen agent changes on every update and the daemon's interpreter does not, so
the daemon holds the keychain and the agent asks it (#6481).

The credential arrives in a file this module deletes on read. It is not in the
environment, where ``ps eww`` would show it to any command the agent runs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from gaia.connectors._keyring import _KEYRING_BACKEND_ENV_VAR, keyring
from gaia.connectors.errors import ConnectorsError
from gaia.daemon.custody.constants import (
    CUSTODY_URL_ENV_VAR,
    HOST_API_PREFIX,
    KEYCHAIN_SECRET_FILE_ENV_VAR,
)

# Long enough for a person to answer the macOS dialog the daemon may be behind.
_READ_TIMEOUT_SECONDS = 300.0
_CONNECT_TIMEOUT_SECONDS = 5.0


class DaemonKeyring(keyring.backend.KeyringBackend):
    """Keychain access through ``/host/v1/keychain``. Installed explicitly."""

    priority = 0

    def __init__(self, base_url: str, secret: str):
        super().__init__()
        self._base = base_url.rstrip("/")
        self._secret = secret

    def _post(self, action: str, body: dict):
        import httpx

        url = f"{self._base}{HOST_API_PREFIX}/keychain/{action}"
        try:
            return httpx.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {self._secret}"},
                # These bodies hold secrets; a proxy setting must not reroute them.
                trust_env=False,
                timeout=httpx.Timeout(
                    _READ_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS
                ),
            )
        except httpx.HTTPError as e:
            raise keyring.errors.KeyringError(
                f"the GAIA daemon at {self._base} did not answer a keychain "
                f"{action} ({e.__class__.__name__}). If macOS is showing a "
                "keychain dialog, answer it. Otherwise run `gaia daemon status`, "
                "then restart the agent."
            ) from e

    def _raise_for_status(self, action: str, resp) -> None:
        if resp.status_code < 400:
            return
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise keyring.errors.KeyringError(
            f"the GAIA daemon refused a keychain {action} "
            f"(HTTP {resp.status_code}): {detail}"
        )

    def get_password(self, service: str, username: str) -> Optional[str]:
        resp = self._post("get", {"service": service, "username": username})
        self._raise_for_status("read", resp)
        return resp.json()["value"]

    def set_password(self, service: str, username: str, password: str) -> None:
        resp = self._post(
            "set", {"service": service, "username": username, "value": password}
        )
        self._raise_for_status("write", resp)

    def delete_password(self, service: str, username: str) -> None:
        resp = self._post("delete", {"service": service, "username": username})
        if resp.status_code == 404:
            raise keyring.errors.PasswordDeleteError(f"{username!r} is not stored")
        self._raise_for_status("delete", resp)


def install_daemon_keyring_from_env() -> bool:
    """Use the daemon's keychain when it offered one; return whether it did.

    A sidecar run any other way has no offer and keeps the OS keychain. An
    explicit ``PYTHON_KEYRING_BACKEND`` wins, so test suites stay on their own.
    """
    path = os.environ.pop(KEYCHAIN_SECRET_FILE_ENV_VAR, None)
    if not path:
        return False
    secret_file = Path(path)
    try:
        secret = secret_file.read_text(encoding="utf-8")
        secret_file.unlink()
    except OSError as e:
        raise ConnectorsError(
            f"Could not read the keychain credential the GAIA daemon left at "
            f"{path}: {e}. Restart the agent with `gaia daemon restart`."
        ) from e
    if os.environ.get(_KEYRING_BACKEND_ENV_VAR):
        return False
    base_url = os.environ.get(CUSTODY_URL_ENV_VAR)
    if not base_url:
        raise ConnectorsError(
            f"The GAIA daemon set {KEYCHAIN_SECRET_FILE_ENV_VAR} but not "
            f"{CUSTODY_URL_ENV_VAR}, so the agent cannot reach the keychain. "
            "Restart it with `gaia daemon restart`."
        )
    keyring.set_keyring(DaemonKeyring(base_url, secret))
    return True
