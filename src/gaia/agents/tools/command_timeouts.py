# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""How long a shell command is allowed to run, by what kind of command it is.

One flat 30s default kills test suites, builds and installs — the commands that
legitimately take minutes. The model *can* pass ``timeout=`` itself, but nothing
tells it how long the command it is about to run should take, so it rarely does.

This module answers that question up front: classify the command text, apply the
class default. Four classes, no hidden heuristics, table below.

+----------------+---------+------------------------------------------------+
| class          | default | matches                                        |
+================+=========+================================================+
| ``test``       |   900 s | pytest/tox/nox, jest/vitest/mocha/playwright,  |
|                |         | ``cargo test``, ``go test``, ``npm test``,     |
|                |         | ``mvn test``, ``gaia eval``                    |
+----------------+---------+------------------------------------------------+
| ``build``      |  1800 s | pip/uv/poetry/conda/apt/brew installs, make,   |
|                |         | cmake, ninja, msbuild, tsc, webpack,           |
|                |         | ``cargo build``, ``npm ci``, ``docker build``  |
+----------------+---------+------------------------------------------------+
| ``network``    |   300 s | ``git clone/fetch/pull/push``, gh, curl, wget, |
|                |         | ssh/scp/rsync, ``docker pull``, hf downloads   |
+----------------+---------+------------------------------------------------+
| ``default``    |    30 s | everything else — the read-only allowlist      |
|                |         | (ls, cat, grep, stat, git status …)            |
+----------------+---------+------------------------------------------------+

An explicit ``timeout=`` argument always wins; this is only the default.
"""

import shlex
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

#: A timeout a caller may never exceed. Above this, ``run_shell_command``
#: refuses rather than clamping — a silently shortened timeout is a command
#: killed for a reason the caller cannot see.
MAX_COMMAND_TIMEOUT = 3600


@dataclass(frozen=True)
class TimeoutClass:
    """A named command class and the timeout every command in it gets."""

    name: str
    seconds: int
    summary: str


TEST = TimeoutClass("test", 900, "test runners")
BUILD = TimeoutClass("build", 1800, "builds, compiles and package installs")
NETWORK = TimeoutClass("network", 300, "VCS and network calls")
DEFAULT = TimeoutClass("default", 30, "everything else")

#: Every class, keyed by name. The enumerated set the tool description states.
TIMEOUT_CLASSES: Dict[str, TimeoutClass] = {
    cls.name: cls for cls in (TEST, BUILD, NETWORK, DEFAULT)
}

# Tokens that front a real command without changing what it is.
# ``python -m pytest`` is pytest; ``sudo make install`` is make.
_WRAPPERS = frozenset(
    {"python", "python3", "py", "pypy", "sudo", "env", "nohup", "time", "npx", "uvx"}
)

# ``<wrapper> run <the actual command>`` — classify the remainder instead.
_DELEGATING = frozenset({("uv", "run"), ("poetry", "run"), ("pipx", "run")})

# Binaries whose name alone settles the class.
_BINARY_CLASS: Dict[str, TimeoutClass] = {
    # test runners
    "pytest": TEST,
    "py.test": TEST,
    "tox": TEST,
    "nox": TEST,
    "nosetests": TEST,
    "jest": TEST,
    "vitest": TEST,
    "mocha": TEST,
    "karma": TEST,
    "playwright": TEST,
    "cypress": TEST,
    "ctest": TEST,
    "rspec": TEST,
    "phpunit": TEST,
    # builds / installs
    "make": BUILD,
    "gmake": BUILD,
    "nmake": BUILD,
    "cmake": BUILD,
    "ninja": BUILD,
    "meson": BUILD,
    "msbuild": BUILD,
    "bazel": BUILD,
    "tsc": BUILD,
    "webpack": BUILD,
    "rollup": BUILD,
    "esbuild": BUILD,
    "vite": BUILD,
    "gcc": BUILD,
    "g++": BUILD,
    "clang": BUILD,
    "clang++": BUILD,
    "cl": BUILD,
    "rustc": BUILD,
    "javac": BUILD,
    # VCS / network
    "curl": NETWORK,
    "wget": NETWORK,
    "ssh": NETWORK,
    "scp": NETWORK,
    "sftp": NETWORK,
    "rsync": NETWORK,
    "gh": NETWORK,
    "glab": NETWORK,
    "hg": NETWORK,
    "svn": NETWORK,
    "hf": NETWORK,
    "huggingface-cli": NETWORK,
    "aws": NETWORK,
    "az": NETWORK,
    "gcloud": NETWORK,
    "kubectl": NETWORK,
    "helm": NETWORK,
    "ping": NETWORK,
}

# Multiplexers: the subcommand decides. Anything not listed for a binary falls
# through to ``default`` — ``git status`` stays a 30s command.
_SUBCOMMAND_CLASS: Dict[str, Dict[str, TimeoutClass]] = {
    "git": {
        "clone": NETWORK,
        "fetch": NETWORK,
        "pull": NETWORK,
        "push": NETWORK,
        "submodule": NETWORK,
        "ls-remote": NETWORK,
        "lfs": NETWORK,
    },
    "pip": {"install": BUILD, "download": BUILD, "wheel": BUILD, "uninstall": BUILD},
    "pip3": {"install": BUILD, "download": BUILD, "wheel": BUILD, "uninstall": BUILD},
    "uv": {
        "pip": BUILD,
        "sync": BUILD,
        "add": BUILD,
        "remove": BUILD,
        "build": BUILD,
        "venv": BUILD,
        "tool": BUILD,
    },
    "poetry": {"install": BUILD, "add": BUILD, "build": BUILD, "update": BUILD},
    "conda": {"install": BUILD, "create": BUILD, "update": BUILD, "env": BUILD},
    "mamba": {"install": BUILD, "create": BUILD, "update": BUILD, "env": BUILD},
    "npm": {"install": BUILD, "ci": BUILD, "i": BUILD, "add": BUILD, "test": TEST},
    "pnpm": {"install": BUILD, "i": BUILD, "add": BUILD, "test": TEST},
    "yarn": {"install": BUILD, "add": BUILD, "test": TEST},
    "cargo": {
        "test": TEST,
        "bench": TEST,
        "build": BUILD,
        "install": BUILD,
        "check": BUILD,
        "clippy": BUILD,
        "fetch": NETWORK,
    },
    "go": {"test": TEST, "build": BUILD, "install": BUILD, "get": NETWORK},
    "dotnet": {"test": TEST, "build": BUILD, "restore": BUILD, "publish": BUILD},
    "mvn": {"test": TEST, "verify": TEST, "install": BUILD, "package": BUILD},
    "gradle": {"test": TEST, "check": TEST, "build": BUILD, "assemble": BUILD},
    "gradlew": {"test": TEST, "check": TEST, "build": BUILD, "assemble": BUILD},
    "docker": {"build": BUILD, "compose": BUILD, "pull": NETWORK, "push": NETWORK},
    "podman": {"build": BUILD, "pull": NETWORK, "push": NETWORK},
    "apt": {"install": BUILD, "upgrade": BUILD, "update": NETWORK},
    "apt-get": {"install": BUILD, "upgrade": BUILD, "update": NETWORK},
    "brew": {"install": BUILD, "upgrade": BUILD, "update": NETWORK},
    "choco": {"install": BUILD, "upgrade": BUILD},
    "winget": {"install": BUILD, "upgrade": BUILD},
    "scoop": {"install": BUILD, "update": BUILD},
    "gaia": {"eval": TEST, "test": TEST, "init": BUILD, "install": BUILD},
}

# ``npm run <script>`` / ``pnpm run`` / ``yarn run`` — the script name decides.
_RUN_SCRIPT_BINARIES = frozenset({"npm", "pnpm", "yarn"})


def normalize_binary(token: str) -> str:
    """``C:\\Tools\\PyTest.EXE`` -> ``pytest``: basename, no suffix, lowercase."""
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _classify_tokens(tokens: List[str]) -> TimeoutClass:
    """The class of one already-split command, wrappers peeled off."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):  # a flag, incl. python's -m
            index += 1
            continue
        binary = normalize_binary(token)
        if binary in _WRAPPERS:
            index += 1
            continue
        break
    else:
        return DEFAULT

    binary = normalize_binary(tokens[index])
    operands = [
        (position, normalize_binary(token))
        for position, token in enumerate(tokens[index + 1 :], start=index + 1)
        if not token.startswith("-")
    ]
    subcommand = operands[0][1] if operands else ""

    if (binary, subcommand) in _DELEGATING:
        return _classify_tokens(tokens[operands[0][0] + 1 :])

    if binary in _RUN_SCRIPT_BINARIES and subcommand == "run":
        script = operands[1][1] if len(operands) > 1 else ""
        return TEST if script.startswith("test") else BUILD

    subcommands = _SUBCOMMAND_CLASS.get(binary)
    if subcommands is not None:
        return subcommands.get(subcommand, DEFAULT)

    return _BINARY_CLASS.get(binary, DEFAULT)


def _split_segments(command: str) -> List[List[str]]:
    """Split *command* into pipeline segments of tokens."""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    segments: List[List[str]] = []
    current: List[str] = []
    for part in parts:
        if part == "|":
            if current:
                segments.append(current)
            current = []
        else:
            current.append(part)
    if current:
        segments.append(current)
    return segments


def classify_command(command: str) -> TimeoutClass:
    """The timeout class *command* falls into.

    A pipeline takes the longest class of any segment: ``pytest -q | tail`` is a
    test run whose output happens to be filtered, and the shell waits for the
    whole pipeline anyway.
    """
    segments = _split_segments(command)
    if not segments:
        return DEFAULT
    return max(
        (_classify_tokens(segment) for segment in segments),
        key=lambda cls: cls.seconds,
    )


def resolve_timeout(command: str, requested: Optional[int]) -> Tuple[int, str]:
    """``(seconds, class_name)`` to run *command* under.

    *requested* is the caller's explicit ``timeout=`` and always wins — the
    class default only fills the gap when it is None.

    Raises:
        ValueError: *requested* is not a positive number of seconds, or exceeds
            ``MAX_COMMAND_TIMEOUT``. Refused rather than clamped so the caller
            never gets a command killed at a limit it did not ask for.
    """
    command_class = classify_command(command)
    if requested is None:
        return command_class.seconds, command_class.name

    try:
        seconds = int(requested)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"timeout must be a whole number of seconds, got {requested!r}. "
            f"Omit it to use the {command_class.name} default "
            f"({command_class.seconds}s)."
        ) from exc

    if seconds <= 0:
        raise ValueError(
            f"timeout must be a positive number of seconds, got {seconds}. "
            f"Omit it to use the {command_class.name} default "
            f"({command_class.seconds}s)."
        )
    if seconds > MAX_COMMAND_TIMEOUT:
        raise ValueError(
            f"timeout of {seconds}s exceeds the {MAX_COMMAND_TIMEOUT}s ceiling "
            f"for a single shell command. Run the work in the background and "
            f"poll it with wait_for_condition, or split it into shorter steps."
        )
    return seconds, command_class.name


def timeout_table() -> str:
    """The class table as one line per class, for a tool description."""
    return "; ".join(
        f"{cls.name}={cls.seconds}s ({cls.summary})"
        for cls in (TEST, BUILD, NETWORK, DEFAULT)
    )
