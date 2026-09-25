# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unpack zip and tar archives without letting a member land outside *dest*.

:func:`safe_extract` is the one extraction policy for hub, skill, model and
embedded-Lemonade archives:

* member names that are absolute, carry a drive, or contain a ``..`` segment
  are refused, and every target is resolve-checked to stay under *dest*;
* device nodes, FIFOs and other special members are refused;
* symlinks and hard links are refused unless the caller passes
  ``allow_links=True``, and even then each link must point inside *dest*;
* every member is validated before anything is written, then written one at a
  time -- ``extractall`` is never used;
* links are created after every regular file, so a link can never become a
  path component a later write follows;
* file permission bits are masked to ``0o777`` (no setuid, setgid or sticky);
* optional per-member and total size caps are checked against the declared
  sizes before anything is written, and again against the bytes actually
  written, so a member whose header understates its size is still cut off.
"""

import os
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Optional, Union

from gaia.logger import get_logger

log = get_logger(__name__)

ArchiveKind = Literal["zip", "tar"]

_CHUNK = 1024 * 1024

_TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


class ArchiveError(Exception):
    """An archive member would be unsafe to unpack, or the archive is unusable."""


class ArchiveSizeError(ArchiveError):
    """A member, or the archive as a whole, is larger than the caller allows.

    Attributes:
        scope: ``"member"`` for the per-member cap, ``"total"`` for the
            whole-archive cap.
        member: The member being checked or written when the cap was hit.
        size: The declared size (or declared running total) when
            *declared* is True; otherwise the bytes written so far.
        limit: The cap that was exceeded.
        declared: True when the archive's own size headers exceeded the cap
            (nothing was written); False when the bytes actually written did.
    """

    def __init__(
        self,
        *,
        scope: Literal["member", "total"],
        member: str,
        size: int,
        limit: int,
        declared: bool,
    ):
        self.scope = scope
        self.member = member
        self.size = size
        self.limit = limit
        self.declared = declared
        what = f"entry {member!r}" if scope == "member" else "the archive"
        how = "declares" if declared else "unpacks to more than"
        super().__init__(
            f"{what} {how} {size} bytes, over the {scope} limit of {limit} bytes"
        )


@dataclass
class _Budget:
    max_member: Optional[int]
    max_total: Optional[int]
    total: int = 0

    def charge(self, name: str, member_bytes: int, added: int, declared: bool):
        """Add *added* bytes to the running total and enforce both caps."""
        self.total += added
        if self.max_member is not None and member_bytes > self.max_member:
            raise ArchiveSizeError(
                scope="member",
                member=name,
                size=member_bytes,
                limit=self.max_member,
                declared=declared,
            )
        if self.max_total is not None and self.total > self.max_total:
            raise ArchiveSizeError(
                scope="total",
                member=name,
                size=self.total,
                limit=self.max_total,
                declared=declared,
            )


@dataclass
class _Entry:
    name: str
    target: Path
    kind: Literal["dir", "file", "symlink", "hardlink"]
    member: object
    linkname: str = ""
    mode: Optional[int] = None
    size: int = 0


def _kind_for(archive: Path) -> ArchiveKind:
    lower = archive.name.lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith(_TAR_SUFFIXES):
        return "tar"
    raise ArchiveError(
        f"cannot tell the format of '{archive.name}': expected a .zip or a tar "
        f"archive ({', '.join(_TAR_SUFFIXES)})"
    )


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _target_for(name: str, dest: Path) -> Path:
    """Return where member *name* may be written, or raise if it escapes."""
    if not name:
        raise ArchiveError("an entry has an empty name")
    parts = name.replace("\\", "/").split("/")
    if (
        PurePosixPath(name).is_absolute()
        or PureWindowsPath(name).is_absolute()
        or PureWindowsPath(name).drive
        or name.startswith(("/", "\\"))
    ):
        raise ArchiveError(
            f"entry {name!r} escapes the destination directory {dest} "
            "(absolute path)"
        )
    if ".." in parts:
        raise ArchiveError(
            f"entry {name!r} escapes the destination directory {dest} "
            "('..' segments are not allowed)"
        )
    target = (dest / name).resolve()
    if not _is_within(target, dest):
        raise ArchiveError(f"entry {name!r} escapes the destination directory {dest}")
    return target


def _link_pointee(entry: _Entry, dest: Path) -> Path:
    """Resolve what *entry* links to, refusing anything outside *dest*."""
    if (
        PurePosixPath(entry.linkname).is_absolute()
        or PureWindowsPath(entry.linkname).drive
    ):
        raise ArchiveError(
            f"entry {entry.name!r} links to {entry.linkname!r}, an absolute "
            f"path outside {dest}"
        )
    # A symlink's target is read relative to its own directory; a hard link
    # names another member, relative to the archive root.
    anchor = entry.target.parent if entry.kind == "symlink" else dest
    pointee = (anchor / entry.linkname).resolve()
    if not _is_within(pointee, dest):
        raise ArchiveError(
            f"entry {entry.name!r} links to {entry.linkname!r}, outside {dest}"
        )
    return pointee


def _refuse_link(name: str) -> ArchiveError:
    return ArchiveError(f"entry {name!r} is a link, and links are not allowed here")


def _refuse_special(name: str) -> ArchiveError:
    return ArchiveError(f"entry {name!r} is a device or special file")


def _tar_entries(handle: tarfile.TarFile, dest: Path, allow_links: bool) -> list:
    entries = []
    for member in handle.getmembers():
        target = _target_for(member.name, dest)
        if member.isdir():
            entries.append(_Entry(member.name, target, "dir", member))
        elif member.isfile():
            entries.append(
                _Entry(
                    member.name,
                    target,
                    "file",
                    member,
                    mode=member.mode,
                    size=member.size,
                )
            )
        elif member.issym() or member.islnk():
            if not allow_links:
                raise _refuse_link(member.name)
            kind = "symlink" if member.issym() else "hardlink"
            entries.append(_Entry(member.name, target, kind, member, member.linkname))
        else:
            raise _refuse_special(member.name)
    return entries


def _zip_entries(handle: zipfile.ZipFile, dest: Path, allow_links: bool) -> list:
    entries = []
    for info in handle.infolist():
        target = _target_for(info.filename, dest)
        file_type = stat.S_IFMT(info.external_attr >> 16)
        if info.is_dir() or file_type == stat.S_IFDIR:
            entries.append(_Entry(info.filename, target, "dir", info))
        elif file_type == stat.S_IFLNK:
            if not allow_links:
                raise _refuse_link(info.filename)
            linkname = handle.read(info).decode("utf-8")
            entries.append(_Entry(info.filename, target, "symlink", info, linkname))
        elif file_type in (0, stat.S_IFREG):
            entries.append(
                _Entry(info.filename, target, "file", info, size=info.file_size)
            )
        else:
            raise _refuse_special(info.filename)
    return entries


def _check_entries(entries: list, dest: Path, budget: _Budget) -> None:
    declared = _Budget(budget.max_member, budget.max_total)
    for entry in entries:
        if entry.kind in ("symlink", "hardlink"):
            _link_pointee(entry, dest)
        elif entry.kind == "file":
            declared.charge(entry.name, entry.size, entry.size, declared=True)


def _write_file(source, entry: _Entry, budget: _Budget) -> None:
    entry.target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with source, open(entry.target, "wb") as sink:
            while chunk := source.read(_CHUNK):
                written += len(chunk)
                budget.charge(entry.name, written, len(chunk), declared=False)
                sink.write(chunk)
    except ArchiveSizeError:
        entry.target.unlink(missing_ok=True)
        raise
    if entry.mode is not None:
        entry.target.chmod(entry.mode & 0o777)


def _write_link(entry: _Entry, dest: Path) -> None:
    # Re-check at creation time: earlier links now exist and resolve() follows them.
    if not _is_within(entry.target.parent.resolve(), dest):
        raise ArchiveError(
            f"entry {entry.name!r} escapes the destination directory {dest}"
        )
    pointee = _link_pointee(entry, dest)
    entry.target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if entry.kind == "symlink":
            os.symlink(entry.linkname, entry.target)
        else:
            os.link(pointee, entry.target)
    except OSError as e:
        raise ArchiveError(
            f"could not create link {entry.name!r} -> {entry.linkname!r}: {e}"
        ) from e


def safe_extract(
    archive_path: Union[str, Path],
    dest: Union[str, Path],
    *,
    allow_links: bool = False,
    kind: Optional[ArchiveKind] = None,
    max_member_bytes: Optional[int] = None,
    max_total_bytes: Optional[int] = None,
) -> None:
    """Unpack *archive_path* into *dest*, refusing any member that is unsafe.

    Args:
        archive_path: The ``.zip`` or tar archive (any compression ``tarfile``
            reads).
        dest: Directory to unpack into; created if missing.
        allow_links: Recreate symlinks and hard links that point inside
            *dest*. When False (the default) any link member is refused.
        kind: ``"zip"`` or ``"tar"``. Inferred from the file suffix when
            omitted.
        max_member_bytes: Largest uncompressed size allowed for any one
            member. ``None`` means no cap.
        max_total_bytes: Largest uncompressed size allowed for all members
            together. ``None`` means no cap.

    Raises:
        ArchiveError: A member escapes *dest*, is a disallowed link or a
            special file, or the format cannot be determined. Nothing is
            written when a member fails validation.
        ArchiveSizeError: A size cap was exceeded. Declared sizes are checked
            before anything is written; a member that unpacks to more than
            its header declared is removed when it crosses a cap, but members
            written before it stay, so unpack into a staging directory.
        ValueError: A size cap is negative.
        zipfile.BadZipFile: The zip archive is corrupt.
        tarfile.TarError: The tar archive is corrupt.
        OSError: Writing to *dest* failed.
    """
    for cap_name, cap in (
        ("max_member_bytes", max_member_bytes),
        ("max_total_bytes", max_total_bytes),
    ):
        if cap is not None and cap < 0:
            raise ValueError(f"{cap_name} must be >= 0, got {cap}")
    budget = _Budget(max_member_bytes, max_total_bytes)
    archive_path = Path(archive_path)
    kind = kind or _kind_for(archive_path)
    dest_dir = Path(dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = dest_dir.resolve()

    if kind == "zip":
        with zipfile.ZipFile(archive_path) as handle:
            entries = _zip_entries(handle, root, allow_links)
            _check_entries(entries, root, budget)
            for entry in entries:
                if entry.kind == "dir":
                    entry.target.mkdir(parents=True, exist_ok=True)
                elif entry.kind == "file":
                    _write_file(handle.open(entry.member), entry, budget)
    elif kind == "tar":
        with tarfile.open(archive_path, "r:*") as handle:
            entries = _tar_entries(handle, root, allow_links)
            _check_entries(entries, root, budget)
            for entry in entries:
                if entry.kind == "dir":
                    entry.target.mkdir(parents=True, exist_ok=True)
                elif entry.kind == "file":
                    source = handle.extractfile(entry.member)
                    if source is None:
                        raise ArchiveError(f"entry {entry.name!r} is unreadable")
                    _write_file(source, entry, budget)
    else:
        raise ArchiveError(f"unknown archive kind {kind!r}; expected 'zip' or 'tar'")

    for entry in entries:
        if entry.kind in ("symlink", "hardlink"):
            _write_link(entry, root)
    log.debug("Extracted %d entries from %s into %s", len(entries), archive_path, root)
