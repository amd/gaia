# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Show the images and audio GAIA generated inline in the Agent UI chat (#540).

The image and music/sound-effect tools save into fixed folders under the GAIA
config dir and report the saved path. This module turns such a tool result into
an ``image`` or ``audio`` render card, and resolves the ``/api/media/<kind>/<name>``
URL the card points at back to the file — for a file GAIA wrote into one of
those folders and nothing else: no subfolders, no symlinks, no other paths.
"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import quote

import gaia.config as _config

logger = logging.getLogger(__name__)

#: URL prefix the media endpoint serves under; cards carry ``<prefix><kind>/<name>``.
MEDIA_URL_PREFIX = "/api/media/"

#: Served suffix -> media type, per kind. No SVG: it can carry script.
MEDIA_TYPES: Dict[str, Dict[str, str]] = {
    "image": {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    },
    "audio": {".wav": "audio/wav"},
}

#: The tool-result key that names the saved file, per kind.
_PATH_KEYS = {"image": "image_path", "audio": "audio_path"}


class MediaNotServable(ValueError):
    """The requested name is not a file GAIA generated into its media folder."""


def media_dir(kind: str) -> Path:
    """Where the generation tools save *kind* — the same folders they write to."""
    base = Path(_config.GAIA_CONFIG_DIR)
    if kind == "image":
        return base / "cache" / "sd" / "images"
    if kind == "audio":
        return base / "cache" / "audio" / "generations"
    raise MediaNotServable(f"unknown media kind {kind!r}")


def resolve_generated_media(kind: str, name: str) -> Path:
    """Return the real path of generated file *name*, or raise MediaNotServable.

    *name* must be one plain file name with an allowed suffix that sits directly
    in ``media_dir(kind)`` as a regular file, not a symlink.
    """
    suffixes = MEDIA_TYPES.get(kind)
    if suffixes is None:
        raise MediaNotServable(f"unknown media kind {kind!r}")
    if (
        not name
        or name in (".", "..")
        or any(c in name for c in ("/", "\\", ":", "\x00"))
        or name != name.strip()
    ):
        raise MediaNotServable(f"{name!r} is not a plain file name")
    if Path(name).suffix.lower() not in suffixes:
        raise MediaNotServable(f"{name!r} is not a served {kind} type")
    root_dir = media_dir(kind)
    if not root_dir.is_dir():
        raise MediaNotServable(f"no {kind} has been generated yet")
    root = root_dir.resolve()
    candidate = root / name
    if candidate.is_symlink():
        raise MediaNotServable(f"{name!r} is a symlink")
    try:
        real = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MediaNotServable(f"{name!r} does not exist") from exc
    if real.parent != root or not real.is_file():
        raise MediaNotServable(f"{name!r} is not a file GAIA generated")
    return real


def media_type(kind: str, path: Path) -> str:
    return MEDIA_TYPES[kind][path.suffix.lower()]


def wav_duration_seconds(path: Path) -> Optional[float]:
    """Length of a RIFF WAV from its header, or None when it can't be read.

    Reads ``fmt``'s byte rate and the ``data`` chunk size rather than using
    :mod:`wave`, which rejects the float WAVs music models write.
    """
    try:
        with path.open("rb") as fh:
            header = fh.read(12)
            if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return None
            byte_rate = None
            while True:
                chunk = fh.read(8)
                if len(chunk) < 8:
                    return None
                chunk_id, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
                if chunk_id == b"fmt ":
                    fmt = fh.read(size)
                    if len(fmt) < 12:
                        return None
                    byte_rate = struct.unpack("<I", fmt[8:12])[0]
                    if size % 2:
                        fh.seek(1, os.SEEK_CUR)
                elif chunk_id == b"data":
                    if not byte_rate:
                        return None
                    return round(size / byte_rate, 2)
                else:
                    fh.seek(size + (size % 2), os.SEEK_CUR)
    except OSError as exc:
        logger.warning("Could not read the WAV header of %s: %s", path, exc)
        return None


def _result_path(event: Mapping[str, Any], key: str) -> Optional[str]:
    """The saved-file path on a tool_result, in-process or relayed."""
    value = event.get(key)
    if isinstance(value, str):
        return value
    data = event.get("data")
    if isinstance(data, Mapping) and isinstance(data.get(key), str):
        return data[key]
    return None


def media_card(
    event: Mapping[str, Any], args: Optional[Mapping[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """The ``{render, data}`` card for a tool_result that saved generated media.

    Returns None for every other result — a failed call, no saved path, or a
    path outside GAIA's media folders.
    """
    if event.get("type") != "tool_result" or event.get("success") is False:
        return None
    for kind, key in _PATH_KEYS.items():
        raw = _result_path(event, key)
        if not raw:
            continue
        name = Path(raw).name
        try:
            real = resolve_generated_media(kind, name)
            same = os.path.normcase(real) == os.path.normcase(
                Path(raw).expanduser().resolve()
            )
        except (MediaNotServable, OSError) as exc:
            logger.info("No inline %s for %s: %s", kind, raw, exc)
            return None
        if not same:
            logger.info("No inline %s for %s: not in %s", kind, raw, media_dir(kind))
            return None
        prompt = (args or {}).get("prompt")
        caption = prompt.strip() if isinstance(prompt, str) and prompt.strip() else None
        data: Dict[str, Any] = {
            "src": f"{MEDIA_URL_PREFIX}{kind}/{quote(name, safe='')}",
            "path": str(real),
        }
        if caption:
            data["caption"] = caption
        if kind == "image":
            data["alt"] = caption or "Generated image"
        else:
            duration = wav_duration_seconds(real)
            if duration is not None:
                data["duration_seconds"] = duration
        return {"render": kind, "data": data}
    return None
