# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ffmpeg-backed media decoding for GAIA transcription.

Lemonade's transcription endpoint accepts WAV only, so any other container
(mp4, m4a, mp3, webm...) has to be decoded first. This module owns that step
plus the lazy ffmpeg bootstrap.

Nothing here touches the filesystem or the network at import time: probing and
installing happen only inside :func:`ensure_ffmpeg`, which callers invoke when
they actually need to decode something.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from gaia.logger import get_logger

log = get_logger(__name__)

DOCS_URL = "https://amd-gaia.ai/docs/reference/troubleshooting"

TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
TARGET_CODEC = "pcm_s16le"

_INSTALL_TIMEOUT_SECONDS = 900
_PROBE_TIMEOUT_SECONDS = 60

# (package manager executable, install argv). First manager present on PATH wins.
_INSTALL_COMMANDS: dict[str, Sequence[tuple[str, Sequence[str]]]] = {
    "Windows": (
        (
            "winget",
            (
                "winget",
                "install",
                "--id",
                "Gyan.FFmpeg",
                "--exact",
                "--source",
                "winget",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ),
        ),
    ),
    "Darwin": (("brew", ("brew", "install", "ffmpeg")),),
    # Linux is deliberately absent: every Linux package manager here needs
    # root, and this runs inside a tool the model calls on its own. An
    # unattended privileged install is not something an agent gets to decide —
    # on Linux we fail with the exact command and let the user run it.
}

# Shown to the user when we cannot (or must not) install automatically.
_MANUAL_COMMANDS: dict[str, Sequence[str]] = {
    "Windows": ("winget install Gyan.FFmpeg",),
    "Darwin": ("brew install ffmpeg",),
    "Linux": (
        "sudo apt-get install ffmpeg",
        "sudo dnf install ffmpeg",
        "sudo pacman -S ffmpeg",
    ),
}

_OUT_TIME_RE = re.compile(r"^out_time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)$")


class MediaError(RuntimeError):
    """Raised when media decoding or the ffmpeg bootstrap cannot complete."""


def _extra_search_dirs() -> List[str]:
    """Directories a package manager may install into but not export to this process.

    A ``winget install`` in a child process cannot mutate our already-inherited
    ``PATH``, so re-probing with ``shutil.which`` alone would miss the binary we
    just installed.
    """
    dirs: List[str] = []
    if platform.system() == "Windows":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            dirs.append(str(Path(local_appdata) / "Microsoft" / "WinGet" / "Links"))
    else:
        dirs.extend(["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin"])
    return [d for d in dirs if os.path.isdir(d)]


def _which(tool: str) -> Optional[str]:
    found = shutil.which(tool)
    if found:
        return found
    extra = _extra_search_dirs()
    if not extra:
        return None
    return shutil.which(tool, path=os.pathsep.join(extra))


def find_ffmpeg() -> Optional[str]:
    """Return the ffmpeg path if present, else ``None``. Never installs anything.

    Use this for status reporting; use :func:`ensure_ffmpeg` when you need it to
    actually work.
    """
    return _which("ffmpeg")


def _manual_install_hint() -> str:
    system = platform.system()
    commands = _MANUAL_COMMANDS.get(system)
    if not commands:
        return f"Install ffmpeg for {system} and ensure it is on PATH."
    return f"Install it with: {' or '.join(commands)}"


def _install_ffmpeg() -> None:
    """Attempt a package-manager install of ffmpeg. Raises MediaError on failure."""
    system = platform.system()
    candidates = _INSTALL_COMMANDS.get(system, ())
    for manager, argv in candidates:
        if not _which(manager):
            continue
        log.info("ffmpeg not found; installing with %s", manager)
        try:
            result = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=_INSTALL_TIMEOUT_SECONDS,
                check=False,
                # Never inherit our stdin: on the TUI's subprocess transport it
                # is the event wire, and a child that reads it deadlocks.
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as e:
            raise MediaError(
                f"ffmpeg install via {manager} timed out after "
                f"{_INSTALL_TIMEOUT_SECONDS}s. {_manual_install_hint()} "
                f"See {DOCS_URL}"
            ) from e
        except OSError as e:
            raise MediaError(
                f"Could not run '{manager}' to install ffmpeg: {e}. "
                f"{_manual_install_hint()} See {DOCS_URL}"
            ) from e
        if result.returncode != 0:
            raise MediaError(
                f"ffmpeg install via {manager} failed (exit {result.returncode}): "
                f"{(result.stderr or result.stdout or '').strip()[:500]}\n"
                f"{_manual_install_hint()} See {DOCS_URL}"
            )
        return

    raise MediaError(
        "ffmpeg is required to decode audio and video but is not installed. "
        f"{_manual_install_hint()} Then run this again. See {DOCS_URL}"
    )


def ensure_ffmpeg() -> str:
    """Return an ffmpeg path, installing it via the platform package manager if needed.

    Raises:
        MediaError: ffmpeg is absent and could not be installed. The message
            names the exact install command and the docs page.
    """
    found = find_ffmpeg()
    if found:
        return found

    _install_ffmpeg()

    found = find_ffmpeg()
    if not found:
        raise MediaError(
            "ffmpeg install reported success but the binary is still not on "
            "PATH. Open a new terminal so PATH is refreshed, then retry. "
            f"{_manual_install_hint()} See {DOCS_URL}"
        )
    log.info("ffmpeg ready at %s", found)
    return found


def _ensure_ffprobe() -> str:
    """Return an ffprobe path; ffprobe ships with every ffmpeg package we install."""
    found = _which("ffprobe")
    if found:
        return found
    ensure_ffmpeg()
    found = _which("ffprobe")
    if not found:
        raise MediaError(
            "ffprobe was not found alongside ffmpeg. "
            f"{_manual_install_hint()} See {DOCS_URL}"
        )
    return found


def _resolve_source(src: os.PathLike | str) -> Path:
    path = Path(src).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"Media file not found: {path}. Pass an absolute path to an "
            "existing audio or video file."
        )
    if path.is_dir():
        raise MediaError(
            f"Expected a media file but got a directory: {path}. "
            "Pass the path of a single audio or video file."
        )
    return path.resolve()


def probe_duration(src: os.PathLike | str) -> float:
    """Return the duration of ``src`` in seconds, via ffprobe."""
    path = _resolve_source(src)
    ffprobe = _ensure_ffprobe()
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        raise MediaError(
            f"ffprobe timed out after {_PROBE_TIMEOUT_SECONDS}s reading {path}. "
            "The file may be corrupt or on an unresponsive drive."
        ) from e
    if result.returncode != 0:
        raise MediaError(
            f"ffprobe could not read {path} (exit {result.returncode}): "
            f"{(result.stderr or '').strip()[:500]}"
        )
    raw = (result.stdout or "").strip()
    try:
        return float(raw)
    except ValueError as e:
        raise MediaError(
            f"ffprobe reported no usable duration for {path} (got {raw!r}). "
            "The file may contain no decodable audio or video stream."
        ) from e


def _default_dest(src: Path) -> Path:
    workdir = Path(tempfile.mkdtemp(prefix="gaia-media-"))
    return workdir / f"{src.stem}.16k.wav"


def _parse_progress_seconds(line: str) -> Optional[float]:
    match = _OUT_TIME_RE.match(line.strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def to_wav16k_mono(
    src: os.PathLike | str,
    dest: Optional[os.PathLike | str] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> Path:
    """Decode any ffmpeg-readable media to 16 kHz mono ``pcm_s16le`` WAV.

    That is the only shape Lemonade's transcription endpoint accepts.

    Args:
        src: Source media file (mp4, m4a, mp3, wav, ...).
        dest: Output WAV path. Defaults to a fresh temp directory the caller owns.
        progress_callback: Called with decode progress in ``0.0..1.0``. Supplying
            it costs one extra ffprobe call to learn the total duration.

    Returns:
        Path to the decoded WAV.

    Raises:
        FileNotFoundError: ``src`` does not exist.
        MediaError: ffmpeg is unavailable or the decode failed.
    """
    path = _resolve_source(src)
    ffmpeg = ensure_ffmpeg()

    out_path = Path(dest).expanduser() if dest is not None else _default_dest(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ar",
        str(TARGET_SAMPLE_RATE),
        "-ac",
        str(TARGET_CHANNELS),
        "-c:a",
        TARGET_CODEC,
        str(out_path),
    ]

    if progress_callback is None:
        _run_ffmpeg(cmd, path)
    else:
        total = probe_duration(path)
        _run_ffmpeg_with_progress(cmd, path, total, progress_callback)

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise MediaError(
            f"ffmpeg exited successfully but produced no audio at {out_path}. "
            f"{path} may have no audio stream — check it with "
            f"'ffprobe -hide_banner {path}'."
        )
    log.debug("decoded %s -> %s (%d bytes)", path, out_path, out_path.stat().st_size)
    return out_path


def _decode_failure(src: Path, returncode: int, stderr: str) -> MediaError:
    return MediaError(
        f"ffmpeg failed to decode {src} (exit {returncode}): "
        f"{stderr.strip()[:500]}\n"
        "Verify the file plays locally and contains an audio stream. "
        f"See {DOCS_URL}"
    )


def _run_ffmpeg(cmd: List[str], src: Path) -> None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except OSError as e:
        raise MediaError(f"Could not launch ffmpeg ({cmd[0]}): {e}") from e
    if result.returncode != 0:
        raise _decode_failure(src, result.returncode, result.stderr or "")


def _run_ffmpeg_with_progress(
    cmd: List[str],
    src: Path,
    total_seconds: float,
    progress_callback: Callable[[float], None],
) -> None:
    # -loglevel error keeps stderr small enough that reading it after stdout
    # cannot deadlock on a full pipe.
    progress_cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    try:
        proc = subprocess.Popen(
            progress_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except OSError as e:
        raise MediaError(f"Could not launch ffmpeg ({cmd[0]}): {e}") from e

    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            elapsed = _parse_progress_seconds(line)
            if elapsed is None or total_seconds <= 0:
                continue
            progress_callback(min(elapsed / total_seconds, 1.0))
        stderr = proc.stderr.read() if proc.stderr else ""
        returncode = proc.wait()
    except BaseException:
        # A callback that raises (cancellation) must not leave ffmpeg decoding
        # a file nobody will read, holding two pipes open.
        proc.kill()
        proc.wait()
        raise
    finally:
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()

    if returncode != 0:
        raise _decode_failure(src, returncode, stderr)
    progress_callback(1.0)
