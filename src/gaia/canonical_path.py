# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
r"""One spelling for every path GAIA judges.

Windows has many names for one file: ``\\?\C:\x``, ``\\localhost\C$\x``,
``C:\x.``, ``C:\PROGRA~1\x``. A guard that compares strings judges whichever
spelling it was handed, so every allowed-folder, protected-folder and
sensitive-name check reduces a path with :func:`canonical_path` first, and the
tools open the path it returns rather than the one they were given.

A spelling that cannot be reduced to a file on this PC (another computer's
share, a device or volume path) raises :class:`UncheckablePathError`; callers
refuse it outright rather than ask the user about it.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from typing import Any

#: Prefixes that hand the rest of a path to Windows unparsed.
_DEVICE_PREFIXES = ("\\\\?\\", "\\\\.\\", "\\??\\")

_DRIVE_RE = re.compile(r"^[A-Za-z]:(\\|$)")
_UNC_RE = re.compile(r"^\\\\([^\\]+)\\([^\\]+)(\\.*)?$")
_ADMIN_SHARE_RE = re.compile(r"^([A-Za-z])\$$")
_IPV6_LITERAL_SUFFIX = ".ipv6-literal.net"


class UncheckablePathError(ValueError):
    """A path GAIA cannot reduce to a file on this PC, so it will not open it."""


def _refusal(path: str, why: str, remedy: str) -> UncheckablePathError:
    return UncheckablePathError(
        f"'{path}' {why}, so GAIA cannot check it against the protected folders "
        f"and will not open it. No approval can grant it. {remedy}"
    )


def _is_this_pc(host: str) -> bool:
    r"""Whether *host* in ``\\host\share`` names this computer.

    Only names that can mean nothing else count; every other host, this PC's
    LAN address included, is treated as another computer and refused.
    """
    name = host.strip("[]").rstrip(".").lower()
    if name.endswith(_IPV6_LITERAL_SUFFIX):
        name = name[: -len(_IPV6_LITERAL_SUFFIX)].replace("-", ":").split("s", 1)[0]
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        pass
    own = {"localhost", socket.gethostname().lower()}
    computer = os.environ.get("COMPUTERNAME", "")
    if computer:
        own.add(computer.lower())
    return name in own


def _share_as_drive(path: str, original: str) -> str:
    r"""``\\<this PC>\C$\x`` as ``C:\x``; any other share is refused."""
    match = _UNC_RE.match(path)
    if not match:
        raise _refusal(
            original,
            "names a network location rather than a file",
            "Use the file's path on this PC, such as C:\\Users\\<you>\\Documents\\file.txt.",
        )
    host, share, rest = match.group(1), match.group(2), match.group(3)
    if not _is_this_pc(host):
        raise _refusal(
            original,
            f"is on a network share of another computer ('{host}')",
            "Copy the file to a folder on this PC first.",
        )
    drive = _ADMIN_SHARE_RE.match(share)
    if not drive:
        raise _refusal(
            original,
            f"goes through the shared folder '{share}'",
            "Use the folder's own path on this PC instead.",
        )
    return f"{drive.group(1).upper()}:{rest or chr(92)}"


def _component(part: str, for_comparison: bool, original: str) -> str:
    """One path segment as Win32 opens it: no stream suffix, no trailing dot or space."""
    if part in ("", ".", ".."):
        return part
    name, colon, stream = part.partition(":")
    if colon and stream.lower() != ":$data" and not for_comparison:
        raise _refusal(
            original,
            f"names the hidden data stream '{stream}' of '{name}'",
            "Use the file's own path, without the ':' part.",
        )
    trimmed = name.rstrip(" .")
    if not trimmed and for_comparison:
        return "."
    if not trimmed:
        raise _refusal(
            original,
            f"has the segment '{part}', which Windows reads in more than one way",
            "Use the file's plain path.",
        )
    return trimmed


def _windows_local_spelling(path: str, original: str, for_comparison: bool) -> str:
    """*path* with device prefixes, this PC's shares and Win32 trimming undone."""
    path = path.replace("/", "\\")
    lowered = path.lower()
    for prefix in _DEVICE_PREFIXES:
        if not lowered.startswith(prefix):
            continue
        rest = path[len(prefix) :]
        if rest[:4].lower() == "unc\\":
            path = "\\\\" + rest[4:]
        elif _DRIVE_RE.match(rest):
            path = rest
        else:
            raise _refusal(
                original,
                "is a device or volume path, not a file on a drive",
                "Use an ordinary path such as C:\\Users\\<you>\\Documents\\file.txt.",
            )
        break
    if path.startswith("\\\\"):
        path = _share_as_drive(path, original)
    drive, rest = os.path.splitdrive(path)
    return drive + "\\".join(
        _component(part, for_comparison, original) for part in rest.split("\\")
    )


def _windows_canonical(expanded: str, original: str, for_comparison: bool) -> str:
    local = _windows_local_spelling(expanded, original, for_comparison)
    real = os.path.realpath(local)
    # realpath can answer a link or a long path in \\?\ or UNC form again.
    try:
        again = _windows_local_spelling(real, original, True)
    except UncheckablePathError:
        # A mapped network drive: name it by the drive letter it was given.
        return os.path.abspath(local)
    if again != real:
        return os.path.realpath(again)
    return real


def canonical_path(path: Any, *, for_comparison: bool = False) -> str:
    r"""The one absolute spelling GAIA judges *path* by, and opens it by.

    Expands ``~``; resolves ``..``, symlinks, junctions and 8.3 short names.
    On Windows it also strips ``\\?\``, ``\\.\`` and ``\??\``, maps
    ``\\?\UNC\host\share`` to ``\\host\share``, maps this PC's administrative
    shares (``\\localhost\C$``, ``\\127.0.0.1\C$``, ``\\[::1]\C$``,
    ``\\<this PC>\C$``) to the drive, and drops trailing dots and spaces from
    each segment, as Win32 does when it opens the file.

    Args:
        path: Any path-like value.
        for_comparison: For callers that only judge a path and never open
            the result, such as words read off a shell command: a named NTFS
            stream (``file:name``) counts as its file and a segment of only
            dots or spaces as ``.``, instead of being refused.

    Returns:
        The canonical absolute path.

    Raises:
        UncheckablePathError: *path* is another computer's share, a local
            named share, a device or volume path, or contains a NUL
            character; without *for_comparison*, also a named stream or an
            ambiguous segment.
    """
    raw = os.fsdecode(os.fspath(path))
    if "\x00" in raw:
        raise _refusal(
            raw.replace("\x00", "\\0"),
            "contains a NUL character",
            "Use the file's plain path.",
        )
    expanded = os.path.expanduser(raw)
    if os.name == "nt":
        return _windows_canonical(expanded, raw, for_comparison)
    real = os.path.realpath(expanded)
    # POSIX lets '//x' differ from '/x'; Linux and macOS do not.
    if real.startswith("//"):
        real = "/" + real.lstrip("/")
    return real


def join_under(base: Any, path: Any) -> str:
    r"""*path* relative to *base*, unless it already names a place on its own.

    ``Path(base) / r"\??\C:\x"`` keeps *base*'s drive and gives ``C:\??\C:\x``;
    Windows opens the original as ``C:\x``, so it must not be joined.
    """
    text = os.fsdecode(os.fspath(path))
    if os.name == "nt" and text.replace("/", "\\").startswith(
        (*_DEVICE_PREFIXES, "\\\\")
    ):
        return text
    return os.path.join(os.fsdecode(os.fspath(base)), text)
