# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Recognize a shell command that changes packages outside any project.

``python -m pip install openpyxl`` run from a chat with no virtual environment
installs into the user's system Python, which every other program on the
machine shares. Full access lifts the per-binary shell policy, and ``python``
is in its developer set, so before this check nothing stood between the agent
and that change. An agent that hits a broken tool must not repair it by
mutating the user's machine.

:func:`system_install_target` names what such a command would change, or
returns ``None`` for an install that stays inside a virtual environment
(GAIA's own included), an explicit ``--target``/``--prefix`` directory, or a
project's ``node_modules``. The shell tool turns a hit into a one-time approval,
and refuses it outright under full access, where no prompt is shown.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

#: Subcommands that add, change or remove packages.
_MUTATING = frozenset(
    {"install", "uninstall", "remove", "rm", "un", "update", "upgrade", "up", "add"}
)

#: pip global options that take a value, so the subcommand is found after them.
_PIP_GLOBAL_VALUE_FLAGS = frozenset(
    {
        "--python",
        "--log",
        "--log-file",
        "--cache-dir",
        "--proxy",
        "--retries",
        "--timeout",
        "--exists-action",
        "--trusted-host",
        "--cert",
        "--client-cert",
        "--use-feature",
        "--use-deprecated",
    }
)

#: pip install flags that put the packages in a named folder, not site-packages.
_PIP_CONTAINED_FLAGS = ("--target", "-t", "--prefix", "--root")

#: OS-level package managers: every install they make is machine-wide.
_SYSTEM_MANAGERS = frozenset(
    {
        "winget",
        "choco",
        "scoop",
        "brew",
        "apt",
        "apt-get",
        "dnf",
        "yum",
        "pacman",
        "zypper",
        "port",
    }
)

#: Programs that run the rest of their argv as another command.
_PREFIX_WRAPPERS = frozenset({"sudo", "env", "nohup", "timeout", "time", "nice"})

#: Shells whose script argument is another command line.
_SHELLS = frozenset({"cmd", "powershell", "pwsh", "bash", "sh", "zsh"})
_SHELL_SCRIPT_FLAGS = frozenset({"-c", "/c", "/k", "-command", "-commandwithargs"})

_PYTHON_RE = re.compile(r"python(\d+(\.\d+)*)?w?")
_PIP_RE = re.compile(r"pip(\d+(\.\d+)*)?")
_PIECE_SPLIT_RE = re.compile(r"\$\(|[;&|`()\r\n]|&&|\|\|")


def _name(token: str) -> str:
    """The program name *token* runs: no folder, no Windows extension, lowercase."""
    base = re.split(r"[\\/]", token.strip().strip("'\""))[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".com", ".ps1"):
        if base.endswith(ext):
            return base[: -len(ext)]
    return base


def _locate(program: str, cwd: str) -> Optional[Path]:
    """Where *program* runs from, without following a venv's symlinked python."""
    program = program.strip().strip("'\"")
    if os.sep in program or "/" in program or "\\" in program:
        path = Path(program)
        if not path.is_absolute():
            path = Path(cwd) / path
        found = shutil.which(str(path)) or (str(path) if path.exists() else None)
    else:
        from gaia.env import child_env

        found = shutil.which(program, path=child_env().get("PATH"))
    return Path(os.path.abspath(found)) if found else None


def _environment_root(executable: Path) -> Path:
    folder = executable.parent
    if folder.name.lower() in ("scripts", "bin"):
        return folder.parent
    return folder


def _python_target(program: str, cwd: str) -> Optional[str]:
    """What installing through *program* changes, or None for a virtual env."""
    executable = _locate(program, cwd)
    if executable is None:
        # Not on PATH: it cannot run, so there is nothing to change.
        return None
    if _name(program) == "py":
        # The Windows launcher picks an installed Python, never a project's venv.
        return "an installed Python the py launcher selects"
    root = _environment_root(executable)
    if (root / "pyvenv.cfg").is_file():
        return None
    if sys.prefix != sys.base_prefix and os.path.normcase(
        str(root)
    ) == os.path.normcase(os.path.abspath(sys.prefix)):
        return None  # GAIA's own virtual environment
    return f"the Python installation at {root}, outside any virtual environment"


def _first_word(args: Sequence[str], value_flags: Iterable[str] = ()) -> tuple:
    """``(index, word)`` of the first non-option in *args*, skipping flag values."""
    value_flags = frozenset(value_flags)
    index = 0
    while index < len(args):
        token = args[index]
        if token in value_flags:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return index, token.lower()
    return len(args), ""


def _flag_value(args: Sequence[str], *flags: str) -> Optional[str]:
    for index, token in enumerate(args):
        for flag in flags:
            if token == flag and index + 1 < len(args):
                return args[index + 1]
            if token.startswith(flag + "="):
                return token[len(flag) + 1 :]
    return None


def _has_flag(args: Sequence[str], *flags: str) -> bool:
    return any(
        token == flag
        or token.startswith(flag + "=")
        or (len(flag) == 2 and not flag.startswith("--") and token.startswith(flag))
        for token in args
        for flag in flags
    )


def _pip_target(interpreter: str, args: Sequence[str], cwd: str) -> Optional[str]:
    index, sub = _first_word(args, _PIP_GLOBAL_VALUE_FLAGS)
    if sub not in ("install", "uninstall"):
        return None
    rest = list(args[index + 1 :])
    if {"--dry-run", "--help", "-h"} & set(rest):
        return None
    if _has_flag(rest, *_PIP_CONTAINED_FLAGS):
        return None
    chosen = _flag_value(list(args[:index]), "--python") or interpreter
    if "--user" in rest:
        return f"the user-wide site-packages of {chosen}"
    return _python_target(chosen, cwd)


def _python_module_target(argv: Sequence[str], cwd: str) -> Optional[str]:
    """``python -m pip install ...`` and friends."""
    index = 1
    module = ""
    while index < len(argv):
        token = argv[index]
        if token == "-m" and index + 1 < len(argv):
            module = argv[index + 1].lower()
            index += 2
            break
        if token.startswith("-m") and len(token) > 2:
            module = token[2:].lower()
            index += 1
            break
        if token in ("-c", "-"):
            return None
        if token in ("-X", "-W"):
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return None  # a script
    rest = list(argv[index:])
    if module == "pip":
        return _pip_target(argv[0], rest, cwd)
    if module in ("uv", "pipx", "conda"):
        return system_install_target([module, *rest], cwd)
    return None


def _uv_target(args: Sequence[str], cwd: str) -> Optional[str]:
    index, sub = _first_word(args, ("--directory", "--project", "--cache-dir"))
    rest = list(args[index + 1 :])
    _, action = _first_word(rest, ("--python", "-p"))
    if sub == "tool" and action in ("install", "upgrade"):
        return "your user-wide command-line tools (uv tool)"
    if sub != "pip" or action not in ("install", "uninstall"):
        return None  # uv add / sync / run stay in the project's .venv
    if _has_flag(rest, "--target", "--prefix"):
        return None
    if "--system" in rest or "--break-system-packages" in rest:
        return "the system Python (uv pip --system)"
    chosen = _flag_value(rest, "--python", "-p")
    if chosen and ("/" in chosen or "\\" in chosen or _PYTHON_RE.fullmatch(chosen)):
        return _python_target(chosen, cwd)
    return None  # uv refuses to install outside a virtual environment


def _node_target(name: str, args: Sequence[str]) -> Optional[str]:
    if name == "yarn":
        if args[:1] == ["global"] and len(args) > 1 and args[1] in _MUTATING:
            return "the machine's global Node packages (yarn global)"
        return None
    _, sub = _first_word(args)
    if sub not in _MUTATING and sub not in ("i", "link"):
        return None
    if _has_flag(args, "-g", "--global") or "--location=global" in args:
        return f"the machine's global Node packages ({name} -g)"
    return None


def _shell_pieces(shell: str, args: Sequence[str]) -> List[List[str]]:
    """The commands inside a ``cmd /c`` / ``powershell -Command`` / ``sh -c`` body."""
    for index, token in enumerate(args):
        if token.lower() in _SHELL_SCRIPT_FLAGS:
            body = " ".join(args[index + 1 :])
            break
    else:
        if shell not in ("powershell", "pwsh"):
            return []
        # PowerShell runs its positional words as a command.
        body = " ".join(a for a in args if not a.startswith("-"))
    pieces = []
    for piece in _PIECE_SPLIT_RE.split(body):
        piece = piece.strip()
        if not piece:
            continue
        try:
            words = shlex.split(piece, posix=os.name != "nt")
        except ValueError:
            words = piece.split()
        words = [w.strip("'\"") for w in words]
        if words:
            pieces.append(words)
    return pieces


def system_install_target(argv: Sequence[str], cwd: str) -> Optional[str]:
    """What *argv* would install into outside any project, or ``None``.

    Args:
        argv: One command's words, program first.
        cwd: The folder it runs in; a relative interpreter path resolves there.

    Returns:
        A short description of the environment it changes ("the Python
        installation at C:\\Python312, outside any virtual environment"), or
        ``None`` when it changes no package outside a virtual environment or a
        folder it names.
    """
    argv = [str(word) for word in argv if str(word)]
    if not argv:
        return None
    name = _name(argv[0])
    args = argv[1:]
    if name in _PREFIX_WRAPPERS:
        index = 0
        while index < len(args) and (
            args[index].startswith("-")
            or "=" in args[index]
            or args[index][:1].isdigit()
        ):
            index += 1
        return system_install_target(args[index:], cwd)
    if name in _SHELLS:
        for piece in _shell_pieces(name, args):
            found = system_install_target(piece, cwd)
            if found:
                return found
        return None
    if _PYTHON_RE.fullmatch(name) or name == "py":
        return _python_module_target(argv, cwd)
    if _PIP_RE.fullmatch(name):
        return _pip_target(argv[0], args, cwd)
    if name == "uv":
        return _uv_target(args, cwd)
    if name == "pipx":
        _, sub = _first_word(args)
        if sub in ("install", "inject", "upgrade", "upgrade-all", "reinstall-all"):
            return "your user-wide command-line tools (pipx)"
        return None
    if name in ("conda", "mamba", "micromamba"):
        _, sub = _first_word(args, ("-n", "--name", "-c", "--channel"))
        if sub not in _MUTATING:
            return None
        if _has_flag(args, "-p", "--prefix"):
            return None
        env = _flag_value(args, "-n", "--name")
        return (
            f"the conda environment '{env}'" if env else "the active conda environment"
        )
    if name in ("npm", "pnpm", "yarn"):
        return _node_target(name, args)
    if name == "gem":
        _, sub = _first_word(args)
        if sub in ("install", "uninstall", "update") and not _has_flag(
            args, "--install-dir", "-i"
        ):
            return "the machine's Ruby gems"
        return None
    if name == "cargo":
        _, sub = _first_word(args)
        if sub in ("install", "uninstall") and not _has_flag(args, "--root"):
            return "your user-wide Rust tools (cargo install)"
        return None
    if name in _SYSTEM_MANAGERS:
        _, sub = _first_word(args)
        if sub in _MUTATING:
            return f"this computer's installed software ({name})"
        return None
    return None


def install_refusal_hint(target: str) -> str:
    """Guidance for an agent whose install was refused or held for approval."""
    return (
        f"This would change {target}, which other programs on this computer "
        "share. Don't change the user's system to work around a tool problem: "
        "if a GAIA tool failed, report that failure instead. If the package is "
        "really needed, tell the user the exact command so they can run it "
        "themselves, or install it into a virtual environment inside the "
        "project (python -m venv .venv, then .venv's own python -m pip install)."
    )
