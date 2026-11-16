# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The owner-only folder for paired devices, and the listener's certificate.

The folder holds the listener's private key, its certificate and the device
table. It is created 0700 (an owner-only, inherited ACL on Windows), each file
in it is written 0600 through a temp file and a rename, and the listener
refuses to start once any of them can be reached by someone else: the key may
have been copied, so the fix is Reset pairing, which makes a new one.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import ssl
import stat
from dataclasses import dataclass
from pathlib import Path

from gaia.config import UnsafeGaiaHomeError
from gaia.utils.file_lock import write_bytes_atomic
from gaia.utils.owner_only import (
    OwnerOnlyError,
    lock_down_windows_acl,
    windows_acl_problem,
)

from . import CERTIFICATE_DAYS

KEY_NAME = "listener.key"
CERTIFICATE_NAME = "listener.crt"
TABLE_NAME = "paired.db"


class UnsafeDeviceFolderError(UnsafeGaiaHomeError):
    """The device folder, or a file in it, can be reached by another user."""


class DeviceCertificateError(RuntimeError):
    """The listener's key or certificate is missing, unreadable or mismatched."""


@dataclass(frozen=True)
class ListenerIdentity:
    """Where the listener's key and certificate are, and the certificate's pin."""

    key_path: Path
    certificate_path: Path
    pin: str


def _unsafe(path: Path, why: str) -> UnsafeDeviceFolderError:
    return UnsafeDeviceFolderError(
        f"GAIA won't let devices in: {path} must be yours alone, but {why}. "
        "Use Reset pairing on the PC: it makes a new key, so a copy taken "
        "meanwhile is useless, and unpairs every device."
    )


def check_private(path: Path, *, folder: bool) -> None:
    """Raise :class:`UnsafeDeviceFolderError` unless only this user can reach *path*."""
    info = os.lstat(path)
    plain = stat.S_ISDIR(info.st_mode) if folder else stat.S_ISREG(info.st_mode)
    if stat.S_ISLNK(info.st_mode) or not plain:
        raise _unsafe(path, f"it isn't a plain {'folder' if folder else 'file'}")
    if os.name == "nt":
        try:
            problem = windows_acl_problem(path)
        except OwnerOnlyError as e:
            raise _unsafe(path, str(e)) from e
        if problem:
            raise _unsafe(path, problem)
        return
    # getuid is POSIX-only; the os.name check above keeps Windows off it.
    if info.st_uid != os.getuid():  # pylint: disable=no-member
        raise _unsafe(path, "it belongs to another user")
    if info.st_mode & 0o077:
        raise _unsafe(
            path, f"other users can open it (mode {stat.S_IMODE(info.st_mode):o})"
        )


def ensure_private_folder(folder: Path) -> Path:
    """Create *folder* owner-only the first time, then check it still is."""
    if not os.path.lexists(folder):
        folder.parent.mkdir(parents=True, exist_ok=True)
        folder.mkdir(mode=0o700, exist_ok=True)
        if os.name == "nt":
            # Inherited, so every file made inside is owner-only too.
            lock_down_windows_acl(folder, inherit=True)
        else:
            os.chmod(folder, 0o700)  # mkdir's mode is narrowed by the umask only
    check_private(folder, folder=True)
    return folder


def write_private(path: Path, data: bytes) -> None:
    """Create *path* at 0600, atomically: it is complete or absent, never partial."""
    write_bytes_atomic(path, data, mode=0o600)
    check_private(path, folder=False)


def pin_of(certificate_pem: bytes) -> str:
    """The pin a device checks: the SHA-256 of the certificate's DER, in hex."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    certificate = x509.load_pem_x509_certificate(certificate_pem)
    der = certificate.public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def _new_key_and_certificate() -> tuple:
    """An EC P-256 key and a self-signed certificate for it, as PEM."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "GAIA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # A device checks the pin only; a day back covers a slow clock anyway.
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=CERTIFICATE_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return key_pem, certificate.public_bytes(serialization.Encoding.PEM)


def load_identity(folder: Path) -> ListenerIdentity:
    """The listener's key and certificate, made the first time; refused if unsafe.

    Raises:
        UnsafeDeviceFolderError: Someone else can reach the folder or a file.
        DeviceCertificateError: Only half of the pair is there, or it can't be read.
    """
    ensure_private_folder(folder)
    key_path, certificate_path = folder / KEY_NAME, folder / CERTIFICATE_NAME
    have_key, have_certificate = (
        os.path.lexists(key_path),
        os.path.lexists(certificate_path),
    )
    if not have_key and not have_certificate:
        key_pem, certificate_pem = _new_key_and_certificate()
        write_private(key_path, key_pem)
        write_private(certificate_path, certificate_pem)
    elif not (have_key and have_certificate):
        raise DeviceCertificateError(
            f"{folder} holds half of the listener's key and certificate. Use Reset "
            "pairing on the PC to make both again; every device pairs again after."
        )
    check_private(key_path, folder=False)
    check_private(certificate_path, folder=False)
    try:
        pin = pin_of(certificate_path.read_bytes())
    except (OSError, ValueError) as e:
        raise DeviceCertificateError(
            f"The listener's certificate at {certificate_path} can't be read ({e}). "
            "Use Reset pairing on the PC to make a new one."
        ) from e
    return ListenerIdentity(key_path, certificate_path, pin)


def server_context(identity: ListenerIdentity) -> ssl.SSLContext:
    """The listener's TLS: this certificate, TLS 1.2 or later, no client certificate."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(identity.certificate_path, identity.key_path)
    except (ssl.SSLError, OSError) as e:
        raise DeviceCertificateError(
            f"The listener's key and certificate in {identity.key_path.parent} "
            f"don't load together ({e}). Use Reset pairing on the PC to make "
            "new ones."
        ) from e
    return context


def reset_folder(folder: Path) -> None:
    """Delete the key, certificate and table, and make *folder* yours alone again.

    The remedy for a folder someone else could reach: whatever they copied
    stops being useful. A folder that isn't plain, or isn't this user's, is
    refused, since deleting through it could reach somewhere else.
    """
    if os.path.lexists(folder):
        info = os.lstat(folder)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise _unsafe(folder, "it isn't a plain folder")
        # getuid is POSIX-only; the os.name check keeps Windows off it.
        if os.name != "nt" and info.st_uid != os.getuid():  # pylint: disable=no-member
            raise _unsafe(folder, "it belongs to another user")
        for name in (KEY_NAME, CERTIFICATE_NAME, TABLE_NAME):
            for suffix in ("", "-journal", "-wal", "-shm"):
                (folder / (name + suffix)).unlink(missing_ok=True)
        if os.name == "nt":
            lock_down_windows_acl(folder, inherit=True)
        else:
            os.chmod(folder, 0o700)
    ensure_private_folder(folder)
