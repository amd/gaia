# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared "agent wheel not installed" messaging.

The ``gaia-agent-*`` wheels (gaia, chat, email) are built
and packaged (``hub/agents/<id>/python/``) but publishing them to PyPI is
still paused (see ``.github/workflows/publish_agents.yml``, tracked by
#1179 / #1513). Until that lands, ``pip install gaia-agent-<id>`` and
``pip install "amd-gaia[agents]"`` both fail on a clean environment (#2240)
-- so every call site that used to recommend them needs to point at the one
install path that actually resolves today: pip installing straight from the
package's subdirectory in this repo.

It also owns the choice of package installer for the running interpreter
(``resolve_pip_frontend``): the GAIA installer's venv has no pip, so every
install GAIA runs or recommends goes through that one decision.
"""

import importlib.metadata
import importlib.util
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlparse
from urllib.request import url2pathname

# hub/agents/<subdir>/python for each wheel this module has a hint for. Keep
# in sync with the directories under hub/agents/ (ls hub/agents/).
_AGENT_SOURCE_SUBDIRS = {
    "gaia-agent-chat": "chat",
    "gaia-agent-email": "email",
    "gaia-agent-gaia": "gaia",
}

# Map wheel name -> actual top-level Python package name.
# Needed because wheel names use hyphens but package directories use underscores,
# and the mapping isn't always a simple replace (e.g. gaia-agent-gaia -> gaia_agent).
_WHEEL_TOP_LEVEL_PACKAGE = {
    "gaia-agent-chat": "gaia_agent_chat",
    "gaia-agent-email": "gaia_agent_email",
    "gaia-agent-gaia": "gaia_agent",
}

# Required sibling packages for each primary wheel. If one is missing, suggest
# installing the primary wheel normally so dependency resolution installs it.
_WHEEL_REQUIRED_SIBLINGS = {
    "gaia-agent-gaia": ("gaia-agent-chat", "gaia_agent_chat"),
}

_REPO_URL = "https://github.com/amd/gaia.git"

# Agent ids the ``gaia-agent-chat`` wheel registers — its three prompt
# profiles. All three are ``hidden`` (resolvable, not selectable), so a stored
# session can still ask for one on a box that never installed the wheel; the
# answer there is this package's install command, not "pick another agent".
CHAT_WHEEL_AGENT_IDS = frozenset({"chat", "doc", "file"})


def _installed_version(package: str) -> Optional[str]:
    """Installed version string for ``package``, or None when absent."""
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


_FINAL_VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
_RC_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)rc([1-9]\d*)")


def _core_release_tag(version: Optional[str]) -> Optional[str]:
    """The git tag ``version`` was released from, or None for an untagged build.

    A release candidate installs as ``X.Y.ZrcN`` but is tagged ``vX.Y.Z-rcN``
    (util/release_tag.py). A dev build has no tag and tracks trunk.
    """
    if not version:
        return None
    if _FINAL_VERSION_RE.fullmatch(version):
        return f"v{version}"
    candidate = _RC_VERSION_RE.fullmatch(version)
    if candidate:
        return f"v{candidate.group(1)}-rc{candidate.group(2)}"
    return None


class PackageInstallerUnavailableError(RuntimeError):
    """Neither pip nor uv can install into the running interpreter."""


_UV_INSTALL_DOCS = "https://docs.astral.sh/uv/getting-started/installation/"

# Double-quote args with spaces or glob/bracket chars (e.g. ``gaia[rag]``).
# Not a full shell escaper: ``$`` and backticks still expand in bash, and a
# quoted executable path needs ``& "..."`` in PowerShell.
_NEEDS_QUOTES = re.compile(r"[\s\[\]@#&|<>;()*?'$`!{}]")


@dataclass(frozen=True)
class PipFrontend:
    """An ``install`` command that targets ``sys.executable``'s environment.

    ``argv`` is what to execute; ``display`` is the same command as a user
    would type it (a bare ``uv`` when PATH is what resolved it).
    """

    argv: Tuple[str, ...]
    display: Tuple[str, ...]


def _pip_available() -> bool:
    """Whether pip is importable in the running interpreter."""
    return importlib.util.find_spec("pip") is not None


def _find_uv() -> Optional[Tuple[str, str]]:
    """Return ``(path, display_name)`` for the uv binary, or None.

    Besides PATH, checks where uv's own installer puts it (``~/.local/bin``,
    older releases ``~/.cargo/bin``): the GAIA installer adds those to PATH
    only for its own session, so a later shell may not have them.
    """
    on_path = shutil.which("uv")
    if on_path:
        return on_path, "uv"
    exe = "uv.exe" if sys.platform == "win32" else "uv"
    for directory in (Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"):
        candidate = directory / exe
        if candidate.is_file():
            return str(candidate), str(candidate)
    return None


def format_command(argv: Sequence[str]) -> str:
    """Render ``argv`` as one pasteable line for the commands GAIA prints."""
    return " ".join(f'"{a}"' if _NEEDS_QUOTES.search(a) else a for a in argv)


def resolve_pip_frontend() -> PipFrontend:
    """Pick the installer that can actually write into this interpreter.

    ``python -m pip`` when pip is importable here; otherwise ``uv pip install
    --python <this interpreter>``. The GAIA installer builds its venv with
    ``uv venv`` (no pip) and never activates it, so a bare ``uv pip install``
    finds no environment and ``python -m pip`` does not exist.

    Raises:
        PackageInstallerUnavailableError: pip is missing and no uv binary exists.
    """
    if _pip_available():
        prefix = (sys.executable, "-m", "pip", "install")
        return PipFrontend(argv=prefix, display=prefix)
    uv = _find_uv()
    if uv is not None:
        path, name = uv
        tail = ("pip", "install", "--python", sys.executable)
        return PipFrontend(argv=(path, *tail), display=(name, *tail))
    raise PackageInstallerUnavailableError(
        f"Cannot install Python packages into {sys.executable}: pip is not "
        "installed in it and no `uv` binary was found on PATH, in ~/.local/bin "
        f"or in ~/.cargo/bin. Install uv ({_UV_INSTALL_DOCS}) or run "
        f"`{format_command([sys.executable, '-m', 'ensurepip'])}`, then retry."
    )


def editable_gaia_root() -> Optional[str]:
    """Return the source checkout amd-gaia is editable-installed from, if any.

    Reads the PEP 610 ``direct_url.json`` written at install time, rather than
    asking a pip frontend that may not exist in this environment.
    """
    try:
        dist = importlib.metadata.distribution("amd-gaia")
    except importlib.metadata.PackageNotFoundError:
        return None
    raw = dist.read_text("direct_url.json")
    if not raw:
        return None
    try:
        direct_url = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"amd-gaia's install record in {dist.locate_file('')} is corrupt "
            f"(direct_url.json is not JSON: {e}). Reinstall GAIA."
        ) from e
    url = direct_url.get("url", "")
    if not direct_url.get("dir_info", {}).get("editable") or not url.startswith(
        "file://"
    ):
        return None
    return url2pathname(urlparse(url).path)


def gaia_extras_install_args(extras: Sequence[str]) -> List[str]:
    """Return the install arguments that add ``extras`` to this GAIA install.

    An editable checkout reinstalls itself with the extras; a wheel install
    asks the index for ``amd-gaia[...]``.
    """
    joined = ",".join(extras)
    root = editable_gaia_root()
    if root is not None:
        return ["-e", f"{root}[{joined}]"]
    return [f"amd-gaia[{joined}]"]


_EXTRA_MARKER = re.compile(r"""^extra\s*==\s*["']([^"']+)["']$""")


def gaia_extra_requirements(extras: Sequence[str]) -> List[str]:
    """Return the requirements ``extras`` add to the installed GAIA.

    Installing these, rather than ``amd-gaia[...]``, never reinstalls GAIA
    itself: on Windows the running ``gaia.exe`` cannot be replaced, so an
    editable checkout's ``gaia init`` failed while adding its own extras.

    Raises:
        RuntimeError: GAIA's install record is missing, names no requirement
            for an extra, or marks one with a condition this cannot evaluate.
    """
    try:
        declared = importlib.metadata.requires("amd-gaia")
    except importlib.metadata.PackageNotFoundError as e:
        raise RuntimeError(
            "amd-gaia is not installed in this Python, so its extras cannot be "
            "added. Reinstall GAIA (https://amd-gaia.ai/docs/guides/install)."
        ) from e
    wanted = set(extras)
    found = set()
    requirements = []
    for line in declared or []:
        requirement, _, marker = line.partition(";")
        if "extra" not in marker:
            continue
        match = _EXTRA_MARKER.match(marker.strip())
        if match is None:
            raise RuntimeError(
                f"amd-gaia declares {line!r}, a condition GAIA cannot evaluate "
                f"while adding extras. Run: {pip_install_hint(*gaia_extras_install_args(extras))}"
            )
        if match.group(1) in wanted:
            found.add(match.group(1))
            requirements.append(requirement.strip())
    missing = wanted - found
    if missing:
        raise RuntimeError(
            f"amd-gaia declares no extra {sorted(missing)}. Reinstall GAIA "
            "(https://amd-gaia.ai/docs/guides/install)."
        )
    return list(dict.fromkeys(requirements))


def pip_install_hint(*args: str) -> str:
    """Return a command a user can paste to install ``args`` into this Python.

    With no installer available yet, the text says to install uv first and
    then gives the uv command -- never a command that cannot run as printed.
    """
    try:
        return format_command([*resolve_pip_frontend().display, *args])
    except PackageInstallerUnavailableError:
        command = format_command(
            ["uv", "pip", "install", "--python", sys.executable, *args]
        )
        return f"install uv ({_UV_INSTALL_DOCS}), then run: {command}"


def source_install_command(wheel: str, *, force_reinstall: bool = False) -> str:
    """Return the command that installs ``wheel`` straight from source.

    Raises ``KeyError`` if ``wheel`` isn't a known ``gaia-agent-*`` package --
    that's a bug at the call site (a typo'd wheel name), not a runtime
    condition to swallow. With ``force_reinstall=True`` (an installed but
    broken wheel needs the same file contents replaced), adds
    ``--force-reinstall --no-deps`` so pip replaces only this wheel rather
    than re-deploying its whole dependency tree.
    """
    subdir = _AGENT_SOURCE_SUBDIRS[wheel]
    # Pin the ref to the installed core: the wheels track this repo's trunk,
    # so pulling `main` against an older released core is exactly the version
    # skew that produced the misdiagnosed "not installed" ImportErrors.
    tag = _core_release_tag(_installed_version("amd-gaia"))
    ref = f"@{tag}" if tag else ""
    spec = f"{wheel} @ git+{_REPO_URL}{ref}#subdirectory=hub/agents/{subdir}/python"
    if force_reinstall:
        return pip_install_hint(spec, "--force-reinstall", "--no-deps")
    return pip_install_hint(spec)


def agent_not_installed_message(
    subject: str, wheel: str, *, next_step: str = ""
) -> str:
    """Build the standard "agent not installed" error text for ``wheel``.

    ``subject`` is the complete first sentence, without a trailing period,
    e.g. ``"The chat agent is not installed"`` or ``"The drafting eval needs
    the email agent"``. ``next_step`` is an optional trailing instruction,
    e.g. ``"Then re-run `gaia chat`."``.

    The ``gaia-agent-*`` wheels aren't on PyPI yet (#2240), so this
    deliberately does NOT recommend ``pip install gaia-agent-<id>`` or
    ``pip install "amd-gaia[agents]"`` -- both fail on a clean environment.
    It points at the verified working install instead: pip installing
    straight from this repo's subdirectory.
    """
    command = source_install_command(wheel)
    message = (
        f"{subject}. The `{wheel}` package isn't published yet "
        f"(see https://github.com/amd/gaia/issues/2240); install it from "
        f"source instead:\n`{command}`"
    )
    if next_step:
        message = f"{message} {next_step}"
    return message


def agent_wheel_failed_message(
    subject: str, wheel: str, error: ImportError, *, next_step: str = ""
) -> str:
    """Build the error text for an installed wheel that fails to import.

    A blanket "not installed" answer here misdiagnoses version skew (a wheel
    importing symbols a newer/older core does not have) as a missing package,
    and reinstalling from a mismatched ref reproduces the same failure. Name
    both package versions and surface the real import error instead.
    """
    installed = _installed_version(wheel) or "unknown"
    core = _installed_version("amd-gaia") or "unknown"
    if subject.endswith(" is not installed"):
        opening = (
            subject[: -len(" is not installed")]
            + " is installed, but it could not be imported"
        )
    else:
        opening = (
            f"{subject}. The `{wheel}` package is installed, but it could "
            "not be imported"
        )
    message = (
        f"{opening}.\n"
        f"Detected versions: {wheel} {installed}, amd-gaia {core}\n"
        f"Import error: {type(error).__name__}: {error}\n"
        "This is usually a version skew between the wheel and the installed "
        "core. Reinstall the wheel built from the matching core tag:\n"
        f"`{source_install_command(wheel, force_reinstall=True)}`"
    )
    if next_step:
        message = f"{message} {next_step}"
    return message


def agent_import_error_message(
    error: ImportError, subject: str, wheel: str, *, next_step: str = ""
) -> str:
    """Pick the right error message for a failed agent-wheel import.

    Only a ``ModuleNotFoundError`` that names the wheel's own top-level
    package means the package is genuinely absent. Anything else -- a
    missing transitive dependency, or an ImportError from a version-skewed
    wheel -- means the package is installed but broken.

    Also treats a missing *required sibling* wheel (e.g. gaia-agent-chat
    for gaia-agent-gaia) as "not installed" so the reinstall hint includes
    the sibling via normal dependency resolution (no --no-deps).
    """
    top_level = _WHEEL_TOP_LEVEL_PACKAGE.get(wheel, wheel.replace("-", "_"))

    # Primary wheel genuinely absent?
    if isinstance(error, ModuleNotFoundError) and (
        error.name == top_level or (error.name or "").startswith(top_level + ".")
    ):
        return agent_not_installed_message(subject, wheel, next_step=next_step)

    # Missing required sibling (e.g. gaia_agent_chat for gaia-agent-gaia)?
    siblings = _WHEEL_REQUIRED_SIBLINGS.get(wheel)
    if siblings:
        _, sib_pkg = siblings
        if isinstance(error, ModuleNotFoundError) and (
            error.name == sib_pkg or (error.name or "").startswith(sib_pkg + ".")
        ):
            # Reinstall the primary wheel with dependency resolution enabled;
            # installing only the sibling would leave any other missing
            # requirements undiscovered, while --no-deps would preserve this
            # broken environment.
            return agent_not_installed_message(subject, wheel, next_step=next_step)

    return agent_wheel_failed_message(subject, wheel, error, next_step=next_step)
