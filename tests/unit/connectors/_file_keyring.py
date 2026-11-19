# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A test keyring kept in one JSON file, so two Python processes can share it.

The in-memory test backend lives in one process; a cross-process test needs
both processes reading and writing the same store, the way the OS credential
store behaves for the daemon, the Agent UI and the CLI.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import keyring.backend
import keyring.errors


def _retry(op, attempts: int = 100):
    # Windows refuses a replace or open while another handle has the file open.
    for _ in range(attempts - 1):
        try:
            return op()
        except PermissionError:
            time.sleep(0.02)
    return op()


class FileKeyring(keyring.backend.KeyringBackend):
    priority = 99

    def __init__(self, path: str | os.PathLike):
        super().__init__()
        self._path = Path(path)

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        return json.loads(_retry(lambda: self._path.read_text(encoding="utf-8")))

    def _write(self, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        _retry(lambda: os.replace(tmp, self._path))

    @staticmethod
    def _key(service: str, username: str) -> str:
        return f"{service}\x00{username}"

    def get_password(self, service, username):
        return self._read().get(self._key(service, username))

    def set_password(self, service, username, password):
        data = self._read()
        data[self._key(service, username)] = password
        self._write(data)

    def delete_password(self, service, username):
        data = self._read()
        if data.pop(self._key(service, username), None) is None:
            raise keyring.errors.PasswordDeleteError(
                f"No password for {service}:{username}"
            )
        self._write(data)
