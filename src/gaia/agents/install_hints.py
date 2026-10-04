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

_REPO_URL = "https://github.com/amd/gaia.git"

# Agent ids the ``gaia-agent-chat`` wheel registers — its three prompt
# profiles. All three are ``hidden`` (resolvable, not selectable), so a stored
# session can still ask for one on a box that never installed the wheel; the
# answer there is this package's install command, not "pick another agent".
CHAT_WHEEL_AGENT_IDS = frozenset({"chat", "doc", "file"})


class PackageInstallerUnavailableError(RuntimeError):
    """Neither pip nor uv can install into the running interpreter."""


_UV_INSTALL_DOCS = "https://docs.astral.sh/uv/getting-started/installation/"

# Characters that force an argument into double quotes -- the one quoting
# style bash, zsh, PowerShell and cmd.exe all accept.
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
    """Render ``argv`` as one line a user can paste into any common shell."""
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


def source_install_command(wheel: str) -> str:
    """Return the command that installs ``wheel`` straight from source.

    Raises ``KeyError`` if ``wheel`` isn't a known ``gaia-agent-*`` package --
    that's a bug at the call site (a typo'd wheel name), not a runtime
    condition to swallow.
    """
    subdir = _AGENT_SOURCE_SUBDIRS[wheel]
    return pip_install_hint(
        f"{wheel} @ git+{_REPO_URL}#subdirectory=hub/agents/{subdir}/python"
    )


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
