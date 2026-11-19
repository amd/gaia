# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Python libraries GAIA's scripts and bundled skills use, at pinned versions.

A source install runs the model's ``run_python`` scripts and skill tools in
GAIA's own interpreter, which has only what was installed into it. Setup offers
one optional, recommended download: the ``data-libraries`` extra in
``setup.py`` -- the only place the pins live, read here from GAIA's installed
metadata -- plus whatever the bundled skills declare. Everything comes from
PyPI at install time; GAIA ships none of it.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from gaia.agents.install_hints import (
    PackageInstallerUnavailableError,
    format_command,
    gaia_extra_requirements,
    resolve_pip_frontend,
)
from gaia.frozen import is_frozen
from gaia.logger import get_logger

log = get_logger(__name__)

#: The ``setup.py`` extra that holds the pins.
EXTRA = "data-libraries"

#: The command that installs the libraries and nothing else.
SETUP_COMMAND = "gaia init --data-libraries"

#: ``~/.gaia/config.json`` key; ``false`` once the user declines the download.
CONFIG_KEY = "data_libraries"

#: Measured on Windows, Python 3.12: wheels downloaded, and unpacked on disk.
DOWNLOAD_MB = 90
INSTALLED_MB = 315

INSTALL_TIMEOUT_S = 900

_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_CLAUSE = r"(?:==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9.*+!_-]+"
_PIN = re.compile(rf"^({_NAME})==([A-Za-z0-9.!+_-]+)$")
#: A package name with version specifiers and nothing else: no URL, path, VCS
#: reference, extra, marker or installer option reaches the command line.
_PLAIN_REQUIREMENT = re.compile(rf"^({_NAME})\s*((?:{_CLAUSE})(?:\s*,\s*{_CLAUSE})*)?$")


class DataLibrariesError(RuntimeError):
    """The libraries are not all installed; the message says which and what to run."""


@dataclass(frozen=True)
class LibraryStatus:
    """One library setup installs and the version this Python has, if any."""

    name: str
    #: What the installer is asked for: ``pandas==2.3.3`` or a skill's ``lxml>=5.0``.
    requirement: str
    #: The exact version GAIA pins, or None for a skill's own requirement.
    pinned: Optional[str]
    installed: Optional[str]
    #: Bundled skills that declare it.
    skills: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        if self.pinned is None:
            return self.installed is not None
        return self.installed == self.pinned

    def describe(self) -> str:
        if self.ok:
            return f"{self.name} {self.installed}"
        wanted = self.pinned or self.requirement
        if self.installed is None:
            return f"{self.name}: not installed (optional, recommended; {wanted})"
        return f"{self.name} {self.installed} (setup installs {wanted})"


def _key(name: str) -> str:
    """A distribution name as PyPI compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def plain_requirement(spec: str) -> Tuple[str, str]:
    """Split ``spec`` into ``(name, specifiers)``, refusing anything else.

    Raises:
        ValueError: *spec* is not a bare package name with version specifiers.
    """
    match = _PLAIN_REQUIREMENT.match(spec.strip()) if isinstance(spec, str) else None
    if match is None:
        raise ValueError(
            f"{spec!r} is not a plain package requirement. Only a package name "
            "with version specifiers (e.g. `openpyxl>=3.1,<4`) is installed: no "
            "URL, path, git reference, extra or installer option."
        )
    return match.group(1), (match.group(2) or "").replace(" ", "")


def pinned_libraries() -> Dict[str, str]:
    """Install name -> pinned version, from GAIA's ``data-libraries`` extra.

    Raises:
        RuntimeError: GAIA's install record has no such extra (an install made
            before it existed), or an entry is not an exact ``name==version``.
    """
    try:
        requirements = gaia_extra_requirements([EXTRA])
    except RuntimeError as e:
        raise RuntimeError(
            f"Could not read the pinned data libraries: {e} An editable "
            "checkout picks up a new extra once it is reinstalled "
            "(`pip install -e .`)."
        ) from e
    pins: Dict[str, str] = {}
    for requirement in requirements:
        match = _PIN.match(requirement.replace(" ", ""))
        if match is None:
            raise RuntimeError(
                f"amd-gaia[{EXTRA}] declares {requirement!r}, which is not an "
                "exact `name==version` pin. Fix the extra in setup.py."
            )
        pins[match.group(1)] = match.group(2)
    return pins


def bundled_skill_roots() -> List[str]:
    """Folders holding the skills that ship with the GAIA agent, if it is importable.

    A source install has the agent package; an install that runs the agent
    binary has none, and that binary carries its own libraries.
    """
    if importlib.util.find_spec("gaia_agent") is None:
        return []
    from gaia_agent.agent import _bundled_skill_roots

    return _bundled_skill_roots()


def skill_dependencies(roots: Optional[List[str]] = None) -> Dict[str, List[str]]:
    """Bundled skill name -> the Python requirements its ``SKILL.md`` declares.

    Raises:
        ValueError: a skill declares something other than a plain requirement.
    """
    from gaia.skills.format import parse_skill_file

    declared: Dict[str, List[str]] = {}
    for root in bundled_skill_roots() if roots is None else roots:
        for skill_file in sorted(Path(root).glob("*/SKILL.md")):
            skill = parse_skill_file(skill_file)
            if skill.name in declared:
                continue
            dependencies = list(skill.gaia.requirements.dependencies)
            for spec in dependencies:
                try:
                    plain_requirement(spec)
                except ValueError as e:
                    raise ValueError(f"Skill '{skill.name}' ({skill_file}): {e}") from e
            if dependencies:
                declared[skill.name] = dependencies
    return declared


def _installed_version(name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def requirement_status(
    declared: Dict[str, List[str]], pins: Dict[str, str], include_pins: bool = True
) -> List[LibraryStatus]:
    """Status of GAIA's *pins* (unless left out) and of what *declared* adds.

    *declared* maps a skill name to its requirements. A package GAIA also pins
    is checked, and installed, at the pin.
    """
    # A package installed since the last look is invisible until caches reset.
    importlib.invalidate_caches()
    users: Dict[str, List[str]] = {}
    extra: Dict[str, Tuple[str, str]] = {}
    pinned_keys = {_key(name) for name in pins}
    for skill, specs in declared.items():
        for spec in specs:
            name, specifiers = plain_requirement(spec)
            users.setdefault(_key(name), []).append(skill)
            if _key(name) not in pinned_keys:
                extra.setdefault(_key(name), (name, f"{name}{specifiers}"))
    status = [
        LibraryStatus(
            name,
            f"{name}=={version}",
            version,
            _installed_version(name),
            tuple(users.get(_key(name), ())),
        )
        for name, version in pins.items()
        if include_pins or _key(name) in users
    ]
    status += [
        LibraryStatus(name, spec, None, _installed_version(name), tuple(users[key]))
        for key, (name, spec) in extra.items()
    ]
    return status


def library_status(
    skills: Optional[Dict[str, List[str]]] = None,
) -> List[LibraryStatus]:
    """Every library setup installs: GAIA's pins, then the bundled skills' own."""
    declared = skill_dependencies() if skills is None else skills
    return requirement_status(declared, pinned_libraries())


def skill_status(status: List[LibraryStatus]) -> Dict[str, List[str]]:
    """Bundled skill name -> the libraries it declares that are not installed."""
    skills: Dict[str, List[str]] = {}
    for library in status:
        for skill in library.skills:
            missing = skills.setdefault(skill, [])
            if not library.ok:
                missing.append(library.name)
    return skills


def offer_text(status: List[LibraryStatus]) -> str:
    """What the download is, for the setup prompt: purpose, source, size, licences."""
    names = [s.name for s in status]
    shown = ", ".join(names[:4])
    more = f" and {len(names) - 4} more" if len(names) > 4 else ""
    return (
        f"Recommended: {shown}{more} let GAIA read spreadsheets, draw charts "
        "and write Excel, Word, PowerPoint and PDF files. Downloaded from "
        f"PyPI, about {DOWNLOAD_MB} MB ({INSTALLED_MB} MB installed), under "
        "BSD, MIT, Apache-2.0 and similar permissive licences."
    )


def missing_summary(status: List[LibraryStatus]) -> Optional[str]:
    """One sentence naming what is missing and the command that adds it, or None."""
    wanted = [s.describe() for s in status if not s.ok]
    if not wanted:
        return None
    return (
        "Optional Python libraries for scripts and skills are not installed: "
        f"{'; '.join(wanted)}. Run: {SETUP_COMMAND}"
    )


def declined() -> bool:
    """Whether the user turned the download down in an earlier setup."""
    from gaia.config import GaiaConfig

    return not GaiaConfig.load().data_libraries


def remember_choice(wanted: bool) -> None:
    """Save whether setup should install the libraries."""
    from gaia.config import GaiaConfig

    def mutate(doc: dict) -> None:
        doc[CONFIG_KEY] = wanted

    GaiaConfig.edit_doc(mutate)


def install(
    say: Callable[[str], None] = lambda _message: None,
    status_of: Callable[[], List[LibraryStatus]] = library_status,
) -> List[LibraryStatus]:
    """Install every library this Python lacks; return the final status.

    *status_of* names the libraries; by default everything setup installs.

    Installs nothing when all are present. Success is what the interpreter
    holds afterwards, never the installer's exit code alone.

    Raises:
        DataLibrariesError: something is still missing; the message names it
            and how to retry.
    """
    before = status_of()
    wanted = [s for s in before if not s.ok]
    if not wanted:
        return before
    names = ", ".join(s.name for s in wanted)
    requirements = [s.requirement for s in wanted]
    try:
        frontend = resolve_pip_frontend()
    except PackageInstallerUnavailableError as e:
        raise DataLibrariesError(f"Could not install {names}: {e}") from e

    argv = [*frontend.argv, *requirements]
    by_hand = format_command([*frontend.display, *requirements])
    retry = f"Then run `{SETUP_COMMAND}` again, or install them yourself: {by_hand}"
    say(f"Installing {names}...")
    log.info("Installing data libraries: %s", argv)
    try:
        result = subprocess.run(  # noqa: S603 - argv is constructed, not shell
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=INSTALL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise DataLibrariesError(
            f"Installing {names} timed out after {INSTALL_TIMEOUT_S // 60} "
            f"minutes. Check your network connection. {retry}"
        ) from e
    except OSError as e:
        raise DataLibrariesError(
            f"Could not run the package installer ({argv[0]}) to install "
            f"{names}: {e}. {retry}"
        ) from e
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        tail = "\n".join(output.splitlines()[-20:]) or "(no output)"
        raise DataLibrariesError(
            f"Installing {names} failed (exit {result.returncode}); they are "
            f"still missing.\n{tail}\n"
            f"Check your network connection and the error above. {retry}"
        )

    status = status_of()
    still = [s.describe() for s in status if not s.ok]
    if still:
        raise DataLibrariesError(
            "The package installer reported success, but this Python "
            f"({sys.executable}) still lacks: {'; '.join(still)}. {retry}"
        )
    return status


def _report(status: List[LibraryStatus]) -> dict:
    return {
        "ready": all(s.ok for s in status),
        "bundled": False,
        "libraries": [
            {
                "name": s.name,
                "requirement": s.requirement,
                "installed": s.installed,
                "ok": s.ok,
            }
            for s in status
        ],
        "skills": [
            {"name": name, "ready": not missing, "missing": missing}
            for name, missing in skill_status(status).items()
        ],
    }


def run_command(check: bool = False, as_json: bool = False) -> int:
    """Entry point for ``gaia init --data-libraries`` (``--check``, ``--json``).

    Prints one line per library, installed with its version or not installed,
    and one per bundled skill that declares libraries. Returns 0 when every
    library is installed, else 1.
    """
    if is_frozen():
        if as_json:
            print(json.dumps({"ready": True, "bundled": True}))
        else:
            print(
                "This GAIA build bundles its Python libraries; there is "
                "nothing to install."
            )
        return 0
    problem = None
    try:
        if check:
            status = library_status()
        else:
            # Asking for them by name is a yes, whatever an earlier setup saved.
            remember_choice(True)
            try:
                status = install(print)
            except DataLibrariesError as e:
                problem = str(e)
                status = library_status()
    except (RuntimeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    report = _report(status)
    if as_json:
        print(json.dumps(report))
    else:
        for s in status:
            print(f"  {'[ok]' if s.ok else '[  ]'} {s.describe()}")
        for skill in report["skills"]:
            if skill["ready"]:
                print(f"  [ok] skill {skill['name']}: libraries installed")
            else:
                print(
                    f"  [  ] skill {skill['name']}: needs "
                    f"{', '.join(skill['missing'])}"
                )
    if problem:
        print(f"Error: {problem}", file=sys.stderr)
    elif not report["ready"]:
        print(f"To install them, run: {SETUP_COMMAND}", file=sys.stderr)
    elif not as_json:
        print("READY: every data library is installed.")
    return 0 if report["ready"] else 1
