# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The external programs GAIA's skills use, and how each one gets onto a PC.

Git, Tesseract, ffmpeg and LibreOffice are separate programs under their own
licences. GAIA ships none of them. It looks for one already installed (on
``PATH``, in the folders its installer uses, then in GAIA's own folder), and
only when asked fetches one from its publisher: a pinned, checksummed archive
unpacked under ``~/.gaia/programs`` (Git on Windows), or the publisher's
package through the operating system's package manager (the route
``gaia.audio.media.ensure_ffmpeg`` already takes for ffmpeg).

Data, not code: adding a program is another :data:`PROGRAMS` entry.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from gaia.logger import get_logger

log = get_logger(__name__)

#: Setup asks about it and the default answer is yes.
PRESELECTED = "preselected"
#: Setup asks about it and the default answer is no.
OFFERED = "offered"
#: Setup never asks; installed only when named.
EXPLICIT = "explicit"

SETUP_COMMAND = "gaia init --programs"

INSTALL_TIMEOUT_S = 900
_VERSION_TIMEOUT_S = 20


class ProgramError(RuntimeError):
    """A program could not be installed; the message says what to do instead."""


@dataclass(frozen=True)
class Download:
    """A portable build GAIA unpacks itself, straight from the publisher."""

    url: str
    version: str
    sha256: str
    size_mb: int
    #: Folder inside the archive that holds the program.
    bin_dir: str


@dataclass(frozen=True)
class Program:
    """One program: where it lives, where it comes from, and its terms."""

    id: str
    command: str
    title: str
    purpose: str
    licence: str
    source: str
    setup: str
    size: str
    #: ``{platform.system(): folders}`` an installer uses without adding to PATH.
    known_dirs: Mapping[str, Sequence[str]] = field(default_factory=dict)
    #: ``{(sys.platform, machine): Download}``.
    downloads: Mapping[Tuple[str, str], Download] = field(default_factory=dict)
    #: ``{sys.platform: argv}`` that installs the publisher's package.
    package_commands: Mapping[str, Sequence[str]] = field(default_factory=dict)
    #: ``{sys.platform: command to type}`` where GAIA does not install it.
    manual: Mapping[str, str] = field(default_factory=dict)
    #: Argv after the program that prints its version; empty to not ask.
    version_argv: Sequence[str] = ("--version",)


@dataclass(frozen=True)
class Found:
    """An installed program and how GAIA came to know about it."""

    path: str
    #: ``path`` (on PATH), ``standard`` (its installer's folder) or ``gaia``.
    origin: str


def _winget(package: str) -> Tuple[str, ...]:
    # Non-interactive flags: a child of GAIA has no terminal to answer on.
    return (
        "winget",
        "install",
        "--id",
        package,
        "--exact",
        "--source",
        "winget",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    )


_GIT_RELEASE = (
    "https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1"
)

PROGRAMS: Dict[str, Program] = {
    "git": Program(
        id="git",
        command="git",
        title="Git",
        purpose="lets GAIA read and change repositories (the coding skills)",
        licence="GPL-2.0-only",
        source="https://github.com/git-for-windows/git/releases",
        setup=PRESELECTED,
        size="40 MB download",
        known_dirs={
            "Windows": (
                "{ProgramFiles}/Git/cmd",
                "{ProgramFiles(x86)}/Git/cmd",
                "{LOCALAPPDATA}/Programs/Git/cmd",
            ),
            "Darwin": ("/opt/homebrew/bin", "/usr/local/bin"),
        },
        downloads={
            # MinGit: Git for Windows' build for applications. No Git Bash and
            # no installer; `cmd/git.exe` is the whole interface.
            ("win32", "amd64"): Download(
                url=f"{_GIT_RELEASE}/MinGit-2.56.1-64-bit.zip",
                version="2.56.1",
                sha256=(
                    "4a92850b6e917da4fd010669b614b55dccf5b0ec333a534cc4be4700dfefdafd"
                ),
                size_mb=38,
                bin_dir="cmd",
            ),
            ("win32", "arm64"): Download(
                url=f"{_GIT_RELEASE}/MinGit-2.56.1-arm64.zip",
                version="2.56.1",
                sha256=(
                    "96571e442e90523a90e5fd8661b4c4e43bc192910f3c6db938816e6955b4cc8c"
                ),
                size_mb=36,
                bin_dir="cmd",
            ),
        },
        manual={
            "darwin": "xcode-select --install",
            "linux": "sudo apt install git",
        },
    ),
    "tesseract": Program(
        id="tesseract",
        command="tesseract",
        title="Tesseract OCR",
        purpose="reads text from scanned PDFs and images, with English data",
        licence="Apache-2.0",
        source="https://github.com/UB-Mannheim/tesseract/wiki",
        setup=PRESELECTED,
        size="about 50 MB",
        known_dirs={
            "Windows": (
                "{ProgramFiles}/Tesseract-OCR",
                "{LOCALAPPDATA}/Programs/Tesseract-OCR",
            ),
            "Darwin": ("/opt/homebrew/bin", "/usr/local/bin"),
        },
        package_commands={
            "win32": _winget("UB-Mannheim.TesseractOCR"),
            "darwin": ("brew", "install", "tesseract"),
        },
        manual={"linux": "sudo apt install tesseract-ocr"},
    ),
    "ffmpeg": Program(
        id="ffmpeg",
        command="ffmpeg",
        title="ffmpeg",
        purpose="decodes audio and video for transcription",
        licence="GPL-3.0-or-later (the winget Gyan.FFmpeg build)",
        source="https://www.gyan.dev/ffmpeg/builds/",
        setup=OFFERED,
        size="about 100 MB",
        known_dirs={
            "Windows": ("{LOCALAPPDATA}/Microsoft/WinGet/Links",),
            "Darwin": ("/opt/homebrew/bin", "/usr/local/bin"),
        },
        version_argv=("-version",),
        manual={"linux": "sudo apt install ffmpeg"},
    ),
    "libreoffice": Program(
        id="libreoffice",
        command="soffice",
        title="LibreOffice",
        purpose="recalculates and converts Office files for the document skills",
        licence="MPL-2.0",
        source="https://www.libreoffice.org/download/",
        setup=EXPLICIT,
        size="about 350 MB",
        known_dirs={
            "Windows": (
                "{ProgramFiles}/LibreOffice/program",
                "{ProgramFiles(x86)}/LibreOffice/program",
            ),
            "Darwin": ("/Applications/LibreOffice.app/Contents/MacOS",),
            "Linux": ("/usr/lib/libreoffice/program", "/opt/libreoffice/program"),
        },
        package_commands={
            "win32": _winget("TheDocumentFoundation.LibreOffice"),
            "darwin": ("brew", "install", "--cask", "libreoffice"),
        },
        manual={"linux": "sudo apt install libreoffice"},
        # Starting soffice just to read a version takes seconds and can open a window.
        version_argv=(),
    ),
}

#: Program id by the command a skill declares (``program:run:soffice``).
BY_COMMAND: Dict[str, str] = {p.command: p.id for p in PROGRAMS.values()}


def programs_dir() -> Path:
    """Where GAIA unpacks the programs it downloads itself."""
    return Path.home() / ".gaia" / "programs"


def _platform_key() -> str:
    return "linux" if sys.platform.startswith("linux") else sys.platform


def _exe(command: str) -> str:
    return f"{command}.exe" if sys.platform == "win32" else command


def _gaia_dirs(program: Program) -> List[Path]:
    """The folders of *program* GAIA unpacked itself, newest version first."""
    root = programs_dir() / program.id
    if not root.is_dir():
        return []
    dirs = []
    for version in sorted(root.iterdir(), reverse=True):
        for download in program.downloads.values():
            candidate = version / download.bin_dir
            if candidate.is_dir() and candidate not in dirs:
                dirs.append(candidate)
    return dirs


def _standard_dirs(program: Program) -> List[Path]:
    dirs = []
    for pattern in program.known_dirs.get(platform.system(), ()):
        try:
            dirs.append(Path(pattern.format_map(os.environ)))
        except KeyError:
            continue  # that variable is not set on this machine
    return dirs


def find(program_id: str) -> Optional[Found]:
    """Where *program_id* is installed, or None. Never installs anything."""
    program = PROGRAMS[program_id]
    on_path = shutil.which(program.command)
    if on_path:
        return Found(on_path, "path")
    for origin, dirs in (
        ("standard", _standard_dirs(program)),
        ("gaia", _gaia_dirs(program)),
    ):
        for folder in dirs:
            candidate = folder / _exe(program.command)
            if candidate.is_file():
                return Found(str(candidate), origin)
    return None


def find_command(command: str) -> Optional[str]:
    """Path of a registered program by the command it runs as, or None."""
    program_id = BY_COMMAND.get(command)
    found = find(program_id) if program_id else None
    return found.path if found else None


def known_path_entries() -> List[str]:
    """Folders holding an installed, registered program that PATH does not name.

    For GAIA's own process ``PATH`` only: nothing here edits the user's.
    """
    entries = []
    for program in PROGRAMS.values():
        if shutil.which(program.command):
            continue
        found = find(program.id)
        if found is not None:
            entries.append(os.path.dirname(found.path))
    return entries


def version(program_id: str) -> Optional[str]:
    """The first line *program_id* prints for its version, or None."""
    program = PROGRAMS[program_id]
    found = find(program_id)
    if found is None or not program.version_argv:
        return None
    try:
        result = subprocess.run(
            [found.path, *program.version_argv],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning(
            "Could not read %s's version from %s: %s", program_id, found.path, e
        )
        return None
    lines = (result.stdout or result.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else None


def download_for(program: Program) -> Optional[Download]:
    """The pinned archive for this PC, or None when the publisher offers none."""
    return program.downloads.get((sys.platform, platform.machine().lower()))


def how_to_install(program_id: str) -> str:
    """The command a user runs to get *program_id*: GAIA's, else the platform's."""
    program = PROGRAMS[program_id]
    if download_for(program) or _platform_key() in program.package_commands:
        return f"{SETUP_COMMAND} --with-program {program_id}"
    if program_id == "ffmpeg" and _platform_key() != "linux":
        return f"{SETUP_COMMAND} --with-program {program_id}"
    manual = program.manual.get(_platform_key())
    return manual or f"install it from {program.source}"


def can_install(program_id: str) -> bool:
    """Whether GAIA has a way to install *program_id* on this PC."""
    return how_to_install(program_id).startswith(SETUP_COMMAND)


def describe(program_id: str) -> str:
    """What installing it means, for a prompt: purpose, size, licence, source."""
    program = PROGRAMS[program_id]
    return (
        f"{program.title} {program.purpose} ({program.size}; licence "
        f"{program.licence}; from {program.source})."
    )


def _fetch(url: str, target: Path) -> None:
    import requests  # pylint: disable=import-outside-toplevel

    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with open(target, "wb") as out:
            for chunk in response.iter_content(chunk_size=1 << 20):
                out.write(chunk)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unpack(program: Program, download: Download, fetch) -> None:
    """Download, verify and unpack *download* under GAIA's programs folder."""
    target = programs_dir() / program.id / download.version
    retry = f"Run `{SETUP_COMMAND} --with-program {program.id}` to try again."
    with tempfile.TemporaryDirectory(prefix="gaia-program-") as scratch:
        archive = Path(scratch) / "download.zip"
        try:
            fetch(download.url, archive)
        except Exception as e:  # noqa: BLE001 - any transport failure, reported
            raise ProgramError(
                f"Could not download {program.title} from {download.url}: {e}. "
                f"Check your network connection. {retry}"
            ) from e
        actual = _sha256(archive)
        if actual != download.sha256:
            raise ProgramError(
                f"The {program.title} download from {download.url} does not match "
                f"its pinned checksum (expected {download.sha256}, got {actual}), "
                f"so it was not unpacked. {retry} If it happens again, install "
                f"it yourself from {program.source}."
            )
        staging = Path(scratch) / "unpacked"
        try:
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(staging)
        except (zipfile.BadZipFile, OSError) as e:
            raise ProgramError(
                f"Could not unpack the {program.title} download: {e}. {retry}"
            ) from e
        if not (staging / download.bin_dir / _exe(program.command)).is_file():
            raise ProgramError(
                f"The {program.title} download holds no "
                f"{download.bin_dir}/{_exe(program.command)}, so it was not "
                f"installed. Install it yourself from {program.source}."
            )
        try:
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staging), str(target))
        except OSError as e:
            raise ProgramError(
                f"Could not put {program.title} in {target}: {e}. {retry}"
            ) from e


def _run_package_manager(program: Program, argv: Sequence[str]) -> None:
    manager = shutil.which(argv[0])
    by_hand = " ".join(argv)
    if manager is None:
        raise ProgramError(
            f"{program.title} is installed with `{argv[0]}`, which this PC does "
            f"not have. Install it yourself from {program.source}."
        )
    try:
        result = subprocess.run(  # noqa: S603 - a fixed argv from PROGRAMS
            [manager, *argv[1:]],
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        raise ProgramError(
            f"Installing {program.title} with `{argv[0]}` timed out after "
            f"{INSTALL_TIMEOUT_S // 60} minutes. Run it yourself: {by_hand}"
        ) from e
    except OSError as e:
        raise ProgramError(
            f"Could not run `{argv[0]}` to install {program.title}: {e}. "
            f"Run it yourself: {by_hand}"
        ) from e
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-500:] or "(no output)"
        raise ProgramError(
            f"Installing {program.title} with `{argv[0]}` failed (exit "
            f"{result.returncode}): {tail}\nRun it yourself: {by_hand}"
        )


def install(program_id: str, fetch: Callable[[str, Path], None] = _fetch) -> Found:
    """Install *program_id* from its publisher and return where it is.

    Already installed: returns it and installs nothing. Success is the program
    being found afterwards, never the installer's exit code alone.

    Raises:
        ProgramError: it is still not installed; the message says why and what
            to run by hand.
    """
    program = PROGRAMS[program_id]
    found = find(program_id)
    if found is not None:
        return found
    download = download_for(program)
    package = program.package_commands.get(_platform_key())
    if download is not None:
        log.info(
            "Downloading %s %s from %s", program.title, download.version, download.url
        )
        _unpack(program, download, fetch)
    elif program_id == "ffmpeg":
        # One ffmpeg bootstrap for setup and for transcription.
        from gaia.audio.media import MediaError, ensure_ffmpeg

        try:
            ensure_ffmpeg()
        except MediaError as e:
            raise ProgramError(str(e)) from e
    elif package is not None:
        log.info("Installing %s with %s", program.title, package[0])
        _run_package_manager(program, package)
    else:
        raise ProgramError(
            f"GAIA has no install of {program.title} for this operating system. "
            f"Install it with: {how_to_install(program_id)}"
        )
    from gaia import program_lookup

    program_lookup.refresh_path(after_install=True)
    found = find(program_id)
    if found is None:
        raise ProgramError(
            f"The {program.title} install reported success, but "
            f"`{program.command}` is still not found. Open a new terminal and "
            f"run `{SETUP_COMMAND} --check`, or install it from {program.source}."
        )
    log.info("%s ready at %s", program.title, found.path)
    return found


def status() -> List[dict]:
    """Every registered program as found (path, version, origin) or missing."""
    rows = []
    for program in PROGRAMS.values():
        found = find(program.id)
        rows.append(
            {
                "id": program.id,
                "title": program.title,
                "found": found is not None,
                "path": found.path if found else None,
                "origin": found.origin if found else None,
                "version": version(program.id) if found else None,
                "setup": program.setup,
                "size": program.size,
                "licence": program.licence,
                "source": program.source,
                "install_command": None if found else how_to_install(program.id),
            }
        )
    return rows


def status_line(row: dict) -> str:
    """One readiness line for a :func:`status` row."""
    if row["found"]:
        how = "installed by GAIA" if row["origin"] == "gaia" else "found"
        detail = f", {row['version']}" if row["version"] else ""
        return f"[ok] {row['title']}: {how} at {row['path']}{detail}"
    kind = "optional, large" if row["setup"] == EXPLICIT else "optional"
    return (
        f"[  ] {row['title']}: missing ({kind}; {row['size']}). "
        f"To install: {row['install_command']}"
    )
