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
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from gaia.env import child_env
from gaia.logger import get_logger
from gaia.tool_cancellation import ToolCancelled, current_cancel_event

log = get_logger(__name__)

DOCS_URL = "https://amd-gaia.ai/docs/reference/troubleshooting"

TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
TARGET_CODEC = "pcm_s16le"

_INSTALL_TIMEOUT_SECONDS = 900
_PROBE_TIMEOUT_SECONDS = 60
# ffmpeg reports progress about twice a second once decoding; this much
# silence means stuck. Generous so a slow share can finish probing the input.
_DECODE_STALL_SECONDS = 300.0
# How much of ffmpeg's stderr is kept for the error message.
_STDERR_KEEP_CHARS = 16384
# How long to wait for a killed ffmpeg, and its pipes, to go away.
_KILL_WAIT_SECONDS = 10.0
# How often a running decode checks for a stall or a cancelled tool call.
_POLL_SECONDS = 0.25

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
                env=child_env(),
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
            env=child_env(),
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

    total = probe_duration(path) if progress_callback is not None else 0.0
    _run_ffmpeg(cmd, path, total, progress_callback)

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


def _pump_lines(
    stream, sink: "queue.Queue[Optional[str]]", keep_chars: Optional[int] = None
) -> None:
    """Copy ``stream``'s lines into ``sink``, then None at end of stream.

    With ``keep_chars``, only that much is queued; the rest is still read so
    the child never blocks on a full pipe.
    """
    kept = 0
    try:
        # readline, not iteration: the iterator's read-ahead holds lines back.
        for line in iter(stream.readline, ""):
            if keep_chars is None or kept < keep_chars:
                sink.put(line)
                kept += len(line)
    except (OSError, ValueError) as e:
        log.debug("ffmpeg output closed: %s", e)
    finally:
        sink.put(None)


def _run_ffmpeg(
    cmd: List[str],
    src: Path,
    total_seconds: float = 0.0,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> None:
    """Run a decode, stopping it if it stalls or the tool call is cancelled.

    A decode's length scales with the file, so there is no fixed total limit.
    ffmpeg writes a progress block about twice a second while it works; one
    that goes silent for ``_DECODE_STALL_SECONDS`` is stuck (a drive or share
    that stopped answering, a stream that stopped sending) and is killed. The
    calling thread's tool cancellation is polled the whole time, not only when
    progress arrives, so Stop works during a stall too.

    Raises:
        ToolCancelled: the agent stopped waiting for this tool.
        MediaError: the decode failed, stalled, or ffmpeg could not be stopped.
    """
    progress_cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    try:
        proc = subprocess.Popen(
            progress_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # A file's metadata can put non-UTF-8 bytes in ffmpeg's messages; a
            # strict decode would stop the stderr drain and look like a stall.
            errors="replace",
            stdin=subprocess.DEVNULL,
            env=child_env(),
        )
    except OSError as e:
        raise MediaError(f"Could not launch ffmpeg ({cmd[0]}): {e}") from e

    stall_seconds = _DECODE_STALL_SECONDS
    cancel_event = current_cancel_event()

    # Drained on its own thread: a damaged file logs an error per frame, and a
    # full stderr pipe would block ffmpeg and read as a stall.
    stderr_lines: "queue.Queue[Optional[str]]" = queue.Queue()
    stdout_lines: "queue.Queue[Optional[str]]" = queue.Queue()
    readers = [
        threading.Thread(
            target=_pump_lines, args=(stream, sink, keep), name=name, daemon=True
        )
        for stream, sink, keep, name in (
            (proc.stdout, stdout_lines, None, "ffmpeg-stdout"),
            (proc.stderr, stderr_lines, _STDERR_KEEP_CHARS, "ffmpeg-stderr"),
        )
    ]
    for reader in readers:
        reader.start()

    kill_attempted = False

    def stop() -> None:
        nonlocal kill_attempted
        kill_attempted = True
        proc.kill()
        deadline = time.monotonic() + _KILL_WAIT_SECONDS
        try:
            proc.wait(timeout=_KILL_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            raise MediaError(
                f"ffmpeg (pid {proc.pid}) could not be stopped while decoding "
                f"{src}. End it in Task Manager (or with `kill -9 {proc.pid}`), "
                "then retry."
            ) from None
        for reader in readers:
            reader.join(max(0.0, deadline - time.monotonic()))

    stopped_for: Optional[str] = None
    last_output = time.monotonic()
    try:
        while True:
            try:
                line = stdout_lines.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                line = ""
            if line is None:
                break
            if line:
                last_output = time.monotonic()
                if progress_callback is not None and total_seconds > 0:
                    elapsed = _parse_progress_seconds(line)
                    if elapsed is not None:
                        progress_callback(min(elapsed / total_seconds, 1.0))
            if cancel_event is not None and cancel_event.is_set():
                stopped_for = "cancelled"
            elif time.monotonic() - last_output >= stall_seconds:
                stopped_for = "stalled"
            if stopped_for:
                stop()
                break
        if stopped_for is None:
            try:
                returncode = proc.wait(timeout=_KILL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                # It closed its output but will not exit: treat as stuck.
                stop()
                stopped_for = "stalled"
        for reader in readers:
            reader.join(_KILL_WAIT_SECONDS)
    except BaseException:
        # A callback that raises (cancellation) must not leave ffmpeg decoding
        # a file nobody will read, holding two pipes open.
        # Not twice: if stop() itself failed, its error already says so, and a
        # second try would double the wait.
        if not kill_attempted and proc.poll() is None:
            stop()
        raise
    finally:
        # Closed only once nothing is reading them: closing a pipe another
        # thread is blocked reading can itself block.
        if not any(reader.is_alive() for reader in readers):
            proc.stdout.close()
            proc.stderr.close()

    if stopped_for == "cancelled":
        raise ToolCancelled()
    if stopped_for == "stalled":
        raise MediaError(
            f"ffmpeg made no progress decoding {src} for {stall_seconds:.0f}s, so "
            "it was stopped. The file may be on a drive or network share that "
            "stopped responding. Copy it to a local disk and retry. "
            f"See {DOCS_URL}"
        )
    stderr = _drain(stderr_lines, _STDERR_KEEP_CHARS)
    if returncode != 0:
        raise _decode_failure(src, returncode, stderr)
    if progress_callback is not None:
        progress_callback(1.0)


def _drain(lines: "queue.Queue[Optional[str]]", keep: int) -> str:
    """The first ``keep`` characters of what a reader collected."""
    kept: List[str] = []
    size = 0
    while True:
        try:
            line = lines.get_nowait()
        except queue.Empty:
            break
        if line is None:
            break
        if size < keep:
            kept.append(line)
            size += len(line)
    return "".join(kept)
