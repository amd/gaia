# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Download ``gaiacpp``, the native GAIA agent, from this GAIA release.

Each release attaches one bare ``gaiacpp`` per platform and
``gaiacpp-SHA256SUMS.txt``. The binary is streamed with byte progress, checked
against that list, asked for its version, and only then moved into GAIA's bin
dir, where :func:`gaia.daemon.sidecars.fetch.native_flagship` finds it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

from gaia.daemon.sidecars import fetch
from gaia.daemon.sidecars import platform as plat
from gaia.daemon.sidecars.errors import (
    KIND_NO_RELEASE,
    IntegrityError,
    PlatformError,
)
from gaia.logger import get_logger

logger = get_logger(__name__)

RELEASES_DOWNLOAD_URL = "https://github.com/amd/gaia/releases/download"
#: Overrides ``RELEASES_DOWNLOAD_URL/<tag>``: the directory holding the assets.
ASSETS_URL_ENV = "GAIA_GAIACPP_ASSETS_URL"
SUMS_NAME = "gaiacpp-SHA256SUMS.txt"
INSTALL_RECORD_NAME = fetch.NATIVE_INSTALL_RECORD_NAME

#: Platform key -> the bare binary a release attaches for it.
ASSETS = {
    "win32-x64": "gaiacpp-windows-x64.exe",
    "linux-x64": "gaiacpp-linux-x64",
    "darwin-arm64": "gaiacpp-darwin-arm64",
}

_FINAL_RE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)$")
_RC_RE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)rc(?P<rc>\d+)$")
_SUM_LINE_RE = re.compile(r"^(?P<sha>[0-9a-f]{64})\s+\*?(?P<name>\S+)$")
_TIMEOUT = (10, 60)
_CHUNK = 1 << 16

_PART_PREFIX = "gaiacpp-part-"

Progress = Callable[[int, Optional[int]], None]


@dataclass(frozen=True)
class NativeDownload:
    """One platform's ``gaiacpp`` in a release."""

    tag: str
    url: str
    sha256: str
    size: Optional[int]
    version: str


def install_target() -> Path:
    """Where a download is installed: GAIA's bin dir."""
    from gaia.home import gaia_home

    return gaia_home() / "bin" / fetch.native_flagship_executable_name()


def update_available(binary: Path) -> bool:
    """True when *binary* was downloaded for another GAIA release than this one."""
    try:
        record = fetch.native_install_record(binary)
        return bool(record) and record.get("version") != stamped_version(release_tag())
    except (PlatformError, IntegrityError):
        return False


def release_tag(core_version: Optional[str] = None) -> str:
    """``0.26.1`` -> ``v0.26.1``; ``0.26.0rc3`` -> ``v0.26.0-rc3``."""
    from gaia.version import __version__

    version = core_version or __version__
    m = _RC_RE.match(version)
    if m:
        return f"v{m.group('base')}-rc{m.group('rc')}"
    if _FINAL_RE.match(version):
        return f"v{version}"
    raise PlatformError(
        f"GAIA {version} is a development build, so there is no release to "
        f"download gaiacpp from. Build it from cpp/ and put it on PATH, or set "
        f"{ASSETS_URL_ENV} to a folder holding a release's gaiacpp files."
    )


def stamped_version(tag: str) -> str:
    """The version a release's gaiacpp prints: ``v0.26.0-rc3`` -> ``0.26.0-rc.3``."""
    return re.sub(r"-rc(\d+)$", r"-rc.\1", tag[1:])


def asset_name(platform_key: Optional[str] = None) -> str:
    key = platform_key or plat.current_platform_key()
    if key not in ASSETS:
        raise PlatformError(
            f"gaiacpp is not built for this kind of PC ({key}); it is built for "
            f"{', '.join(sorted(ASSETS))}. Use the Python agent here."
        )
    return ASSETS[key]


def assets_base_url(tag: str) -> str:
    override = os.environ.get(ASSETS_URL_ENV, "").strip().rstrip("/")
    if not override:
        return f"{RELEASES_DOWNLOAD_URL}/{tag}"
    if not override.startswith(("https://", "file://")):
        raise PlatformError(
            f"{ASSETS_URL_ENV}={override!r} must be an https:// or file:// URL; "
            "over plain http the binary and its checksums could both be swapped "
            f"in transit. Fix or unset {ASSETS_URL_ENV}."
        )
    return override


def _local_path(url: str) -> Path:
    parts = urlsplit(url)
    path = unquote(parts.path)
    # file:///C:/x arrives as /C:/x; file://C:/x carries the drive as the host.
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    elif re.match(r"^[A-Za-z]:$", parts.netloc):
        path = parts.netloc + path
    elif parts.netloc:
        path = f"//{parts.netloc}{path}"
    return Path(path)


def _no_gaiacpp(url: str) -> PlatformError:
    # The address stays in the log: this message is shown in Settings.
    logger.warning("gaiacpp: %s is not there", url)
    return PlatformError(
        "this GAIA release has no gaiacpp to download. Use the Python "
        "agent, or update GAIA.",
        kind=KIND_NO_RELEASE,
    )


def _read_text(url: str, session) -> str:
    if url.startswith("file://"):
        try:
            return _local_path(url).read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise _no_gaiacpp(url) from e
        except OSError as e:
            raise PlatformError(f"could not read {url}: {e}") from e
    import requests

    try:
        resp = (session or requests).get(url, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"could not reach {url}: {e}") from e
    if resp.status_code == 404:
        raise _no_gaiacpp(url)
    if resp.status_code != 200:
        raise RuntimeError(f"{url} answered HTTP {resp.status_code}")
    return resp.text


def _size(url: str, session) -> Optional[int]:
    """The asset's size in bytes, or ``None`` when the server does not say."""
    if url.startswith("file://"):
        try:
            return _local_path(url).stat().st_size
        except OSError:
            return None
    import requests

    try:
        resp = (session or requests).head(url, timeout=_TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        logger.warning("gaiacpp: could not read the size of %s: %s", url, e)
        return None
    length = resp.headers.get("Content-Length") if resp.status_code == 200 else None
    return int(length) if length and length.isdigit() else None


def checksum_for(sums_text: str, name: str, source: str) -> str:
    """The one SHA-256 the list gives for *name*."""
    found = []
    for line in sums_text.splitlines():
        m = _SUM_LINE_RE.match(line.strip())
        if m and m.group("name") == name:
            found.append(m.group("sha"))
    if len(found) != 1:
        raise IntegrityError(
            f"{source} lists {len(found)} checksums for {name}; exactly one is "
            "needed to verify the download. Refusing to install it."
        )
    return found[0]


def download_info(
    session=None, *, core_version: Optional[str] = None
) -> NativeDownload:
    """What this release offers for this PC: URL, checksum and size."""
    tag = release_tag(core_version)
    name = asset_name()
    base = assets_base_url(tag)
    sums_url = f"{base}/{SUMS_NAME}"
    sha = checksum_for(_read_text(sums_url, session), name, sums_url)
    url = f"{base}/{name}"
    return NativeDownload(tag, url, sha, _size(url, session), stamped_version(tag))


def _stream(url: str, session):
    """``(total or None, iterator of byte chunks)`` for *url*."""
    if url.startswith("file://"):
        path = _local_path(url)
        try:
            total = path.stat().st_size
            handle = open(path, "rb")
        except OSError as e:
            raise RuntimeError(f"could not read {url}: {e}") from e

        def chunks():
            with handle:
                yield from iter(lambda: handle.read(_CHUNK), b"")

        return total, chunks()
    import requests

    try:
        resp = (session or requests).get(url, timeout=_TIMEOUT, stream=True)
    except requests.RequestException as e:
        raise RuntimeError(f"could not reach {url}: {e}") from e
    if resp.status_code != 200:
        raise RuntimeError(f"{url} answered HTTP {resp.status_code}")
    length = resp.headers.get("Content-Length")
    total = int(length) if length and length.isdigit() else None

    def body():
        try:
            yield from resp.iter_content(_CHUNK)
        finally:
            resp.close()

    return total, body()


def download(
    progress: Optional[Progress] = None,
    *,
    session=None,
    core_version: Optional[str] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Path:
    """Download, verify and install this release's ``gaiacpp``; return its path.

    Raises:
        PlatformError: no release, no build for this PC, or the binary does not
            report this release's version.
        IntegrityError: the checksum does not match.
        RuntimeError: the download failed or was cancelled.
    """
    import requests

    info = download_info(session, core_version=core_version)
    target = install_target()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Left by a download the app was closed in the middle of.
    for stale in target.parent.glob(f"{_PART_PREFIX}*"):
        stale.unlink(missing_ok=True)
    digest = hashlib.sha256()
    done = 0
    # Ends in the real suffix: Windows only runs it for the version check as an .exe.
    fd, tmp_name = tempfile.mkstemp(
        prefix=_PART_PREFIX, suffix=target.suffix, dir=target.parent
    )
    tmp = Path(tmp_name)
    try:
        try:
            total, chunks = _stream(info.url, session)
        except BaseException:
            os.close(fd)
            raise
        total = total or info.size
        if progress:
            progress(0, total)
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in chunks:
                    if cancelled and cancelled():
                        raise RuntimeError("the gaiacpp download was cancelled")
                    if not chunk:
                        continue
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        except requests.RequestException as e:
            raise RuntimeError(
                f"the gaiacpp download stopped after {done} bytes: {e}"
            ) from e
        actual = digest.hexdigest()
        if actual != info.sha256:
            raise IntegrityError(
                f"SHA-256 mismatch for {info.url}:\n"
                f"  expected {info.sha256}\n  actual   {actual}\n"
                "Refusing to install it. The download may be corrupt or tampered "
                "with; try again."
            )
        if os.name != "nt":
            tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        reported = fetch.native_flagship_stamp(tmp)
        if reported != info.version:
            raise PlatformError(
                f"the gaiacpp on {info.tag} reports version {reported}, not "
                f"{info.version}. Refusing to install it; report this at "
                "https://github.com/amd/gaia/issues."
            )
        record = {"version": info.version, "sha256": info.sha256, "tag": info.tag}
        record_path = target.parent / INSTALL_RECORD_NAME
        record_tmp = target.parent / f"{_PART_PREFIX}record.json"
        record_tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        try:
            # The program first: if this fails, the copy in use keeps its own record.
            os.replace(tmp, target)
        except OSError as e:
            record_tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"could not put gaiacpp at {target}: {e}. If GAIA is using it, "
                "stop the answer in progress and try again."
            ) from e
        try:
            os.replace(record_tmp, record_path)
        except OSError as e:
            # Left as it is, the new program fails its check against the old record.
            raise RuntimeError(
                f"gaiacpp was installed at {target} but its record could not be "
                f"written to {record_path}: {e}. Download it again."
            ) from e
    finally:
        tmp.unlink(missing_ok=True)
    logger.info("gaiacpp %s installed at %s", info.version, target)
    return target
