# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Small JSON state files that survive a crash and say so when they don't."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from gaia.logger import get_logger

log = get_logger(__name__)


class StateFileError(RuntimeError):
    """A state file exists but cannot be read."""


class CorruptStateFile(StateFileError):
    """A state file exists but its bytes are not the JSON object it should hold."""


class DamagedStateFile(StateFileError):
    """A corrupt state file was moved aside; *moved_to* keeps its bytes."""

    def __init__(self, message: str, moved_to: Path) -> None:
        super().__init__(message)
        self.moved_to = moved_to


def write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write atomically so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            # Without it a crash can keep the rename but not the bytes: a file of zeros.
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path, remedy: str) -> Optional[Dict[str, Any]]:
    """Return the object at *path*, ``None`` when absent; raise when unusable.

    *remedy* finishes the error message: what the user does to recover.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise StateFileError(f"Cannot read {path}: {e}. Check its permissions.") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CorruptStateFile(f"{path} is not valid JSON: {e}. {remedy}") from e
    if not isinstance(data, dict):
        raise CorruptStateFile(
            f"{path} must hold a JSON object, got {type(data).__name__}. {remedy}"
        )
    return data


def read_json_or_set_aside(path: Path, what: str) -> Optional[Dict[str, Any]]:
    """Like :func:`read_json`, but move a corrupt file aside and say so.

    For files GAIA can start over without: raises :class:`DamagedStateFile`
    once, naming where the bytes went; the next read finds no file.
    """
    try:
        return read_json(path, f"Delete it to start over with no {what}.")
    except CorruptStateFile as e:
        moved = path.with_name(f"{path.name}.damaged-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            os.replace(path, moved)
        except FileNotFoundError:
            # Another reader moved it first; it reports the damage.
            return read_json(path, "")
        except OSError as move_error:
            raise StateFileError(
                f"{path} is damaged and could not be moved aside: {move_error}. "
                f"Delete it to start over."
            ) from e
        log.error("Moved damaged %s to %s: %s", path, moved, e)
        raise DamagedStateFile(
            f"Your saved {what} could not be read — the file was damaged, most "
            f"likely because the PC shut down while saving it. GAIA kept a copy "
            f"at {moved} and starts over with none.",
            moved,
        ) from e
