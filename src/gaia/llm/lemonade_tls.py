# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Whether GAIA's own Lemonade can verify HTTPS on this Linux PC.

The Linux ``lemond`` GAIA bundles is built on Ubuntu, and its libcurl checks
every HTTPS site against exactly one file, ``/etc/ssl/certs/ca-certificates.crt``.
It reads no environment variable for another one (``SSL_CERT_FILE``,
``CURL_CA_BUNDLE`` and ``SSL_CERT_DIR`` were each tried and ignored), so where
that file is missing every cloud model and every download fails with
``CURL error: Problem with the SSL CA cert``. Fedora 44's original
``ca-certificates`` build ships no such file; its update restores it.

GAIA cannot add the file without root, so where it is missing it starts
lemond in a private mount namespace that shows the system bundle at that path
(:func:`plan_ca_overlay`, :func:`enter_ca_overlay`), as ``bwrap`` would. Only
lemond and its children see it; certificate verification stays on. Where the
kernel refuses that namespace it names the problem instead of letting it read
as a rejected key or a broken download.

stdlib-only: imported by the launcher, the client and the UI routers.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

#: The only CA file the bundled lemond's libcurl reads.
LEMOND_CA_FILE = "/etc/ssl/certs/ca-certificates.crt"

#: Where Linux distributions keep the system CA bundle, Debian's path first.
SYSTEM_CA_BUNDLES = (
    LEMOND_CA_FILE,
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/ca-bundle.pem",
    "/etc/ssl/cert.pem",
)

#: What lemond's libcurl says when it cannot read its CA file (CURLE 77).
_CURL_CA_ERROR = "Problem with the SSL CA cert"


def find_system_ca_bundle(
    exists: Callable[[str], bool] = os.path.isfile,
) -> Optional[str]:
    """The first system CA bundle present, or None."""
    return next((path for path in SYSTEM_CA_BUNDLES if exists(path)), None)


def ca_store_problem(
    *,
    platform: Optional[str] = None,
    exists: Callable[[str], bool] = os.path.isfile,
) -> Optional[str]:
    """Why GAIA's Lemonade cannot verify HTTPS here, or None when it can.

    Args:
        platform: ``sys.platform`` to judge; the running one by default.
        exists: File test, replaceable in tests.
    """
    if not (platform or sys.platform).startswith("linux"):
        return None
    if exists(LEMOND_CA_FILE):
        return None
    effect = (
        "Until that file exists, the model server cannot reach cloud models "
        "or download models."
    )
    bundle = find_system_ca_bundle(exists)
    if bundle:
        return (
            f"This PC's certificate store has no {LEMOND_CA_FILE}, the file "
            "the model server reads to trust HTTPS sites (the store is at "
            f"{bundle}). {effect} Install your system's latest updates (the "
            "updated ca-certificates package adds that file), then restart GAIA."
        )
    return (
        "This PC has no system certificate store, so the model server cannot "
        f"trust HTTPS sites (it reads {LEMOND_CA_FILE}). {effect} Install the "
        "ca-certificates package from your system's software manager, then "
        "restart GAIA."
    )


def explain_tls_failure(message: str, **probe) -> str:
    """*message* with the cause and remedy in front when it is a CA-store failure.

    Args:
        message: An error Lemonade returned.
        **probe: Passed to :func:`ca_store_problem`.
    """
    if _CURL_CA_ERROR not in (message or ""):
        return message
    problem = ca_store_problem(**probe)
    if not problem:
        return message
    return f"{problem} (Lemonade said: {message})"


# From <sched.h> and <sys/mount.h>.
_CLONE_NEWNS = 0x00020000
_CLONE_NEWUSER = 0x10000000
_MS_BIND = 0x1000
_MS_REC = 0x4000
_MS_PRIVATE = 0x40000


class CaOverlayError(OSError):
    """The private view of the CA file could not be prepared or entered."""


@dataclass(frozen=True)
class CaOverlay:
    """A directory GAIA mounts, in lemond's namespace only, over a system one.

    Attributes:
        mount_point: The existing directory it covers, symlinks resolved.
        mirror: GAIA's copy of that directory, plus the missing CA file.
        binds: ``(original, placeholder)`` pairs, each original entry mounted
            onto its placeholder in *mirror* before *mirror* covers them, so
            nothing that was in *mount_point* disappears.
        bundle: The system bundle the CA file was copied from.
    """

    mount_point: str
    mirror: str
    binds: Tuple[Tuple[str, str], ...]
    bundle: str


def plan_ca_overlay(
    root: Path,
    *,
    platform: Optional[str] = None,
    ca_file: str = LEMOND_CA_FILE,
    bundles: Sequence[str] = SYSTEM_CA_BUNDLES,
) -> Optional[CaOverlay]:
    """Build the directory that gives lemond *ca_file*, or None when not needed.

    None when this is not Linux, *ca_file* already exists, or no bundle exists
    to copy into it (:func:`ca_store_problem` names that remedy).

    Args:
        root: GAIA-owned directory for the mirror; replaced on every call.
        platform: ``sys.platform`` to judge; the running one by default.
        ca_file: The path lemond reads.
        bundles: Where to look for the system bundle, in order.

    Raises:
        CaOverlayError: The mirror could not be written, or neither the
            directory of *ca_file* nor its parent exists.
    """
    if not (platform or sys.platform).startswith("linux"):
        return None
    if os.path.isfile(ca_file):
        return None
    bundle = next((b for b in bundles if b != ca_file and os.path.isfile(b)), None)
    if bundle is None:
        return None

    covered = os.path.dirname(ca_file)
    missing = []
    while not os.path.isdir(covered):
        missing.insert(0, os.path.basename(covered))
        covered = os.path.dirname(covered)
    # Covering a wider tree (all of /etc) would pin files like resolv.conf.
    if len(missing) > 1:
        raise CaOverlayError(
            f"Neither {os.path.dirname(ca_file)} nor its parent exists, so GAIA "
            "will not show the model server a CA file there."
        )
    mount_point = os.path.realpath(covered)
    own_name = (missing or [os.path.basename(ca_file)])[0]
    mirror = Path(root) / "ca-overlay"
    binds = []
    try:
        if mirror.exists() or mirror.is_symlink():
            shutil.rmtree(mirror)
        mirror.mkdir(parents=True)
        for entry in os.scandir(mount_point):
            if entry.name == own_name:
                continue
            placeholder = mirror / entry.name
            if entry.is_symlink():
                os.symlink(os.readlink(entry.path), placeholder)
                continue
            if entry.is_dir():
                placeholder.mkdir()
            else:
                placeholder.touch()
            binds.append((entry.path, str(placeholder)))
        target = mirror.joinpath(*missing, os.path.basename(ca_file))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle, target)
    except OSError as e:
        raise CaOverlayError(
            f"GAIA could not prepare {mirror} to show the model server a CA "
            f"file at {ca_file}: {e}"
        ) from e
    return CaOverlay(mount_point, str(mirror), tuple(binds), bundle)


def enter_ca_overlay(overlay: CaOverlay) -> Callable[[], None]:
    """A ``preexec_fn`` that puts the child in a namespace showing *overlay*.

    The child gets its own user and mount namespace with the caller's uid and
    gid mapped to themselves, so it runs as the same user, with the same
    groups and no added privilege. Mounts there never reach the host.

    Everything that can fail before the fork is resolved here: the child only
    makes system calls. A failure is written to the child's stderr (lemond's
    log) and raised, which ``subprocess`` reports as ``SubprocessError``.

    Raises:
        CaOverlayError: The C library has no ``unshare`` or ``mount``.
    """
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        unshare, mount = libc.unshare, libc.mount
    except (OSError, AttributeError) as e:
        raise CaOverlayError(
            f"this system's C library has no unshare/mount to call: {e}"
        ) from e
    unshare.argtypes = [ctypes.c_int]
    mount.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
    ]
    uid, gid = os.getuid(), os.getgid()  # pylint: disable=no-member
    maps = (
        ("/proc/self/setgroups", b"deny"),
        ("/proc/self/uid_map", f"{uid} {uid} 1".encode()),
        ("/proc/self/gid_map", f"{gid} {gid} 1".encode()),
    )
    binds = [(src.encode(), dst.encode()) for src, dst in overlay.binds]
    binds.append((overlay.mirror.encode(), overlay.mount_point.encode()))

    def _check(rc: int, what: str) -> None:
        if rc != 0:
            err = ctypes.get_errno()
            raise CaOverlayError(err, f"{what}: {os.strerror(err)}")

    # Runs between fork and exec of a threaded parent: no imports, logging or locks.
    def enter() -> None:
        try:
            _check(unshare(_CLONE_NEWUSER | _CLONE_NEWNS), "unshare")
            for path, data in maps:
                fd = os.open(path, os.O_WRONLY)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
            _check(mount(None, b"/", None, _MS_REC | _MS_PRIVATE, None), "mount /")
            for src, dst in binds:
                _check(
                    mount(src, dst, None, _MS_BIND | _MS_REC, None),
                    f"mount {src.decode()} on {dst.decode()}",
                )
        except OSError as e:
            os.write(
                2,
                f"GAIA could not give the model server its own CA file: {e}\n".encode(),
            )
            raise

    return enter
