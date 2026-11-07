# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fetch the audio of a video or podcast link so it can be transcribed.

yt-dlp does the fetching: it handles YouTube and many other sites, as well as
direct links to media files, and it changes too often to pin into core. It is
installed on first use, never at import or startup, like ffmpeg and the
speaker-identification engine.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

from gaia.audio.media import MEDIA_EXTENSIONS
from gaia.env import child_env
from gaia.logger import get_logger
from gaia.tool_cancellation import ToolCancelled, current_cancel_event, run_cancellable

log = get_logger(__name__)

DOCS_URL = "https://amd-gaia.ai/docs/guides/transcription"

# Silence this long means the connection died; it is not a slow download.
_STALL_SECONDS = 300.0
_POLL_SECONDS = 0.25
_KILL_WAIT_SECONDS = 10.0
_INSTALL_TIMEOUT_SECONDS = 900

# Keeps a mistaken link from filling the disk. About 150 hours of podcast audio.
MAX_DOWNLOAD_BYTES = 8 * 1024**3

# A direct link to a big file can legitimately take this long.
_DIRECT_DOWNLOAD_DEADLINE = 6 * 3600.0
_STOPPED_BY_FILTER = 101  # yt-dlp's exit code once --break-match-filters fires
_BARE_LINK_RE = re.compile(r"(?:[\w-]+\.)?(?:youtube\.com|youtu\.be)/", re.I)

# yt-dlp does not check a size it is not told, so measure the folder too.
_SIZE_CHECK_SECONDS = 2.0

# Written only after a download finishes, so a partial file is never reused.
_FINISHED_MARKER = "download.json"

# Enough of yt-dlp's output to explain a failure.
_ERROR_KEEP_LINES = 40

_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


class MediaUrlError(RuntimeError):
    """A link could not be fetched. The message says what to do about it."""


@dataclass(frozen=True)
class DownloadedMedia:
    """A fetched recording on disk."""

    path: Path
    url: str
    reused: bool


def _with_scheme(text: str) -> str:
    """*text* with ``https://`` added to a video link pasted without it."""
    text = text.strip()
    return f"https://{text}" if _BARE_LINK_RE.match(text) else text


def is_url(text: str) -> bool:
    """Whether *text* is a link rather than a path on this machine."""
    return bool(_URL_RE.match(_with_scheme(text)))


def _validated(url: str) -> str:
    """Refuse a link that could reach this machine or its private network."""
    from gaia.web.client import WebClient

    try:
        return WebClient().validate_url(_with_scheme(url))
    except ValueError as e:
        raise MediaUrlError(
            f"Cannot fetch {url}: {e} Give a public http or https link to the "
            f"video, or a path to a file on this machine. See {DOCS_URL}"
        ) from e


def _package_installed() -> bool:
    return importlib.util.find_spec("yt_dlp") is not None


def ensure_downloader(say: Callable[[str], None]) -> None:
    """Install yt-dlp if it is not here yet."""
    if _package_installed():
        return
    if getattr(sys, "frozen", False):
        raise MediaUrlError(
            "Fetching videos from links is missing from this build of GAIA. It "
            "has to be bundled when the binary is built. Download the video "
            f"yourself and give GAIA the file instead. See {DOCS_URL}"
        )

    from gaia.agents.install_hints import (
        PackageInstallerUnavailableError,
        format_command,
        resolve_pip_frontend,
    )

    try:
        frontend = resolve_pip_frontend()
    except PackageInstallerUnavailableError as e:
        raise MediaUrlError(f"{e} See {DOCS_URL}") from e
    manual = format_command([*frontend.display, "yt-dlp"])

    say("Installing the video downloader (about 10 MB, once)...")
    try:
        subprocess.run(
            [*frontend.argv, "--quiet", "yt-dlp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
            env=child_env(),
        )
    except subprocess.CalledProcessError as e:
        raise MediaUrlError(
            f"Could not install yt-dlp: {(e.stderr or e.stdout or '').strip()[:400]}\n"
            f"Install it manually with: {manual}\nSee {DOCS_URL}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise MediaUrlError(
            f"Installing yt-dlp timed out. Install it manually with: {manual}\n"
            f"See {DOCS_URL}"
        ) from e

    importlib.invalidate_caches()
    if not _package_installed():
        raise MediaUrlError(
            "yt-dlp installed but cannot be imported. Install it into the "
            f"environment GAIA runs from with: {manual}\nSee {DOCS_URL}"
        )


def download_dir_for(url: str, root: Path) -> Path:
    """Where this link's recording is kept, so a second request reuses it."""
    return root / hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


def _finished_file(directory: Path) -> Optional[Path]:
    """The completed download in *directory*, or None if there is not one yet."""
    marker = directory / _FINISHED_MARKER
    if not marker.is_file():
        return None
    try:
        path = directory / json.loads(marker.read_text(encoding="utf-8"))["file"]
    except (OSError, ValueError, KeyError) as e:
        log.warning("Ignoring unreadable download record %s: %s", marker, e)
        return None
    return path if path.is_file() else None


def _mark_finished(directory: Path, path: Path) -> None:
    (directory / _FINISHED_MARKER).write_text(
        json.dumps({"file": path.name}), encoding="utf-8"
    )


def _is_direct_media(url: str) -> bool:
    return Path(urlparse(url).path).suffix.lower() in MEDIA_EXTENSIONS


def _folder_size(directory: Path) -> int:
    total = 0
    for path in directory.iterdir():
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            # The downloader renames and deletes its temporary files as it goes.
            continue
    return total


def _fetch_direct(url: str, directory: Path) -> Path:
    """Fetch a plain media file with GAIA's client, which checks every redirect."""
    from gaia.web.client import WebClient

    client = WebClient(download_deadline=_DIRECT_DOWNLOAD_DEADLINE)
    try:
        saved = run_cancellable(
            lambda: client.download(url, str(directory), max_size=MAX_DOWNLOAD_BYTES)
        )
    except (ValueError, OSError, requests.RequestException) as e:
        raise MediaUrlError(
            f"Could not download {url}: {e} Check that the link opens in a "
            f"browser, or download the file yourself and give GAIA the file. "
            f"See {DOCS_URL}"
        ) from e
    return Path(saved["path"])


def _command(url: str, directory: Path) -> list:
    return [
        sys.executable,
        "-m",
        "yt_dlp",
        # A user's yt-dlp config can run commands and rewrite the output path.
        "--ignore-config",
        "--no-playlist",
        # The catch-all extractor follows links GAIA has not checked. Sites with
        # their own extractor stay on their own hosts; direct files use WebClient.
        "--ies",
        "default,-generic",
        "--newline",
        "--no-warnings",
        "--match-filter",
        "!is_live",
        "--max-filesize",
        str(MAX_DOWNLOAD_BYTES),
        # Audio alone is enough, and far smaller than the video.
        "-f",
        "bestaudio/best",
        "--windows-filenames",
        # --no-playlist only covers a video link; a playlist link has no one video.
        "--break-match-filters",
        "!playlist_id",
        "-o",
        str(directory / "%(title).80B [%(id)s].%(ext)s"),
        "--",
        url,
    ]


def _failure(url: str, code: int, output: list) -> MediaUrlError:
    if code == _STOPPED_BY_FILTER:
        return MediaUrlError(
            f"{url} is a playlist, channel or search page, not one video, so nothing "
            f"was downloaded. Use the link to the video you want. See {DOCS_URL}"
        )
    errors = [line.strip() for line in output if line.startswith("ERROR")]
    reason = " ".join(errors) or " ".join(line.strip() for line in output[-3:])
    return MediaUrlError(
        f"Could not download {url} (exit {code}): {reason[:500]}\nIf the video "
        "is private, age-restricted or needs a login, download it yourself and "
        f"give GAIA the file. If it plays fine in a browser, the downloader may "
        f"need updating. See {DOCS_URL}"
    )


def _run_download(url: str, directory: Path, progress: Callable[[str], None]) -> None:
    try:
        process = subprocess.Popen(
            _command(url, directory),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            env=child_env(),
        )
    except OSError as e:
        raise MediaUrlError(f"Could not start the downloader: {e}") from e

    lines: "queue.Queue[Optional[str]]" = queue.Queue()

    def pump() -> None:
        for line in iter(process.stdout.readline, ""):
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=pump, name="yt-dlp-output", daemon=True)
    reader.start()

    cancel = current_cancel_event()
    output: list = []
    shown = -10.0
    last_output = time.monotonic()
    last_size_check = last_output
    stopped_for = None
    try:
        while True:
            try:
                line = lines.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                line = ""
            if line is None:
                break
            if line:
                last_output = time.monotonic()
                output = (output + [line])[-_ERROR_KEEP_LINES:]
                match = _PROGRESS_RE.search(line)
                if match and float(match.group(1)) - shown >= 10:
                    shown = float(match.group(1))
                    progress(f"Downloading — {shown:.0f}%")
            if cancel is not None and cancel.is_set():
                stopped_for = "cancelled"
            elif time.monotonic() - last_output >= _STALL_SECONDS:
                stopped_for = "stalled"
            elif time.monotonic() - last_size_check >= _SIZE_CHECK_SECONDS:
                last_size_check = time.monotonic()
                if _folder_size(directory) > MAX_DOWNLOAD_BYTES:
                    stopped_for = "too large"
            if stopped_for:
                process.kill()
                break
    except BaseException:
        # Never leave the downloader running behind an error.
        process.kill()
        raise

    try:
        code = process.wait(timeout=_KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired as e:
        process.kill()
        raise MediaUrlError(
            f"The downloader (pid {process.pid}) did not exit. End it in Task "
            "Manager, then retry."
        ) from e
    reader.join(_KILL_WAIT_SECONDS)

    if stopped_for == "cancelled":
        raise ToolCancelled()
    if stopped_for == "stalled":
        raise MediaUrlError(
            f"The download of {url} made no progress for {_STALL_SECONDS:.0f}s, "
            "so it was stopped. Check the connection and retry."
        )
    if stopped_for == "too large":
        shutil.rmtree(directory, ignore_errors=True)
        raise MediaUrlError(
            f"The download of {url} passed the "
            f"{MAX_DOWNLOAD_BYTES // 1024**3} GB limit, so it was stopped and "
            "removed. Download it yourself and give GAIA the file."
        )
    if code != 0:
        raise _failure(url, code, output)


def fetch_media(
    url: str, root: os.PathLike | str, progress: Callable[[str], None]
) -> DownloadedMedia:
    """Download a link's audio under *root*, or reuse an earlier download.

    Raises:
        MediaUrlError: The link is unsafe or unusable, or yt-dlp could not
            fetch it. The message says what to do next.
        ToolCancelled: The agent stopped waiting.
    """
    url = _validated(url)
    directory = download_dir_for(url, Path(root))
    finished = _finished_file(directory)
    if finished is not None:
        return DownloadedMedia(path=finished, url=url, reused=True)

    directory.mkdir(parents=True, exist_ok=True)
    progress(f"Downloading {url}")
    if _is_direct_media(url):
        path = _fetch_direct(url, directory)
    else:
        ensure_downloader(progress)
        _run_download(url, directory, progress)
        files = [
            p
            for p in directory.iterdir()
            if p.is_file() and p.suffix not in (".part", ".ytdl")
        ]
        if not files:
            raise MediaUrlError(
                f"The download of {url} produced no file. A playlist, a channel, a "
                "live stream, or a page with no video on it does this: use the link "
                f"to one video. See {DOCS_URL}"
            )
        path = max(files, key=lambda p: p.stat().st_size)
    _mark_finished(directory, path)
    return DownloadedMedia(path=path, url=url, reused=False)
