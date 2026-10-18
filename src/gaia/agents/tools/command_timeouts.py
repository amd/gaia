# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""How long a shell command is allowed to run, by what kind of command it is.

One flat 30s default kills test suites, builds and installs — the commands that
legitimately take minutes. The model *can* pass ``timeout=`` itself, but nothing
tells it how long the command it is about to run should take, so it rarely does.

This module answers that question up front: classify the command text, apply the
class default. Five classes, no hidden heuristics, table below.

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
| ``script``     |   600 s | a script run by an interpreter — ``python      |
|                |         | x.py``, ``python -m pkg``, ``python -c``,      |
|                |         | node, Rscript, bash/pwsh scripts, ``./x.sh``   |
+----------------+---------+------------------------------------------------+
| ``default``    |    30 s | everything else — the read-only allowlist      |
|                |         | (ls, cat, grep, stat, git status …)            |
+----------------+---------+------------------------------------------------+

An explicit ``timeout=`` argument always wins; this is only the default.
"""

import logging
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

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
SCRIPT = TimeoutClass("script", 600, "scripts run by an interpreter")
DEFAULT = TimeoutClass("default", 30, "everything else")

#: Every class, keyed by name. The enumerated set the tool description states.
TIMEOUT_CLASSES: Dict[str, TimeoutClass] = {
    cls.name: cls for cls in (TEST, BUILD, NETWORK, SCRIPT, DEFAULT)
}

# Python launchers. Peeled like wrappers so ``python -m pytest`` stays a test
# run, but whatever they run that is not a known tool is a script.
_PYTHON_LAUNCHERS = frozenset({"python", "python3", "py", "pypy", "pypy3"})

# Tokens that front a real command without changing what it is.
# ``python -m pytest`` is pytest; ``sudo make install`` is make.
_WRAPPERS = _PYTHON_LAUNCHERS | frozenset(
    {"sudo", "env", "nohup", "time", "npx", "uvx"}
)

# A file run directly (``./run_all.sh``) is a script, whatever its name.
_SCRIPT_SUFFIXES = (".py", ".sh", ".ps1", ".bat", ".cmd", ".js", ".rb", ".pl", ".r")

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

# Interpreters: running something is a script, ``node --version`` is not.
_INTERPRETERS = frozenset(
    {"node", "ruby", "perl", "php", "rscript", "julia"}
) | frozenset({"bash", "sh", "zsh", "pwsh", "powershell", "cmd"})
_SHELLS = frozenset({"bash", "sh", "zsh"})

# Dev servers never exit, so a longer budget only holds the turn longer with
# nothing to show; they stay ``default``. A server belongs in the background.
_SERVER_BINARIES = frozenset(
    {
        "uvicorn",
        "gunicorn",
        "hypercorn",
        "daphne",
        "waitress-serve",
        "http-server",
        "live-server",
        "nodemon",
    }
)
_SERVER_SUBCOMMANDS: Dict[str, frozenset] = {
    "flask": frozenset({"run"}),
    "streamlit": frozenset({"run"}),
    "jupyter": frozenset({"lab", "notebook"}),
    "next": frozenset({"dev", "start"}),
    "webpack": frozenset({"serve"}),
    "ng": frozenset({"serve"}),
    "vite": frozenset({"", "dev", "serve", "preview"}),
    "npm": frozenset({"start"}),
    "pnpm": frozenset({"start", "dev"}),
    "yarn": frozenset({"start", "dev"}),
}
# ``npm run dev`` and friends.
_SERVER_RUN_SCRIPTS = frozenset({"dev", "start", "serve", "preview", "watch"})
_SERVER_MODULES = frozenset(
    {"http.server", "uvicorn", "gunicorn", "flask", "streamlit", "jupyter"}
)
# ``python app.py`` / ``node server.js``: the conventional server entry points.
_SERVER_SCRIPT_STEMS = frozenset(
    {"server", "serve", "app", "wsgi", "asgi", "devserver", "dev_server"}
)
_SERVER_LAUNCHERS = frozenset({"node", "deno", "bun", "ts-node"})
_PATH_BACKSLASH = re.compile(r"\\(?=[A-Za-z0-9_.])")
_VERSIONED_PYTHON = re.compile(r"(?:python|pypy)\d+(?:\.\d+)*")
# Flags that hand the interpreter code or a script to run.
_RUN_FLAGS = frozenset({"-e", "-c", "--eval", "-command", "-file", "-encodedcommand"})

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

# A leading ``NAME=value`` changes the environment, not the command being
# timed. Keep this aligned with the shell tool's leading-assignment grammar.
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)

# Git accepts global options before its subcommand. Value-taking options must
# consume their values or a path such as ``repo`` would be mistaken for the
# subcommand and hide the network class of ``git -C repo pull``.
_GIT_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {
        "-C",
        "--git-dir",
        "--work-tree",
        "--namespace",
        "--super-prefix",
        "-c",
        "--config-env",
    }
)
_GIT_GLOBAL_OPTIONS_NO_VALUE = frozenset(
    {
        "-p",
        "-P",
        "--paginate",
        "--no-pager",
        "--exec-path",
        "--bare",
        "--no-lazy-fetch",
        "--no-replace-objects",
        "--literal-pathspecs",
        "--glob-pathspecs",
        "--noglob-pathspecs",
        "--icase-pathspecs",
        "--no-optional-locks",
    }
)


def command_basename(token: str) -> str:
    """``C:\\Tools\\PyTest.EXE`` -> ``pytest``: basename, no suffix, lowercase.

    Deliberately not ``gaia.skills.binaries.normalize_binary``, which returns
    ``""`` for anything path-spelled so that ``./gh`` can never match a grant.
    Classification wants the opposite — ``/opt/ci/pytest`` is still a test run —
    so the two keep different names rather than one name with two meanings.
    """
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _is_python_launcher(binary: str) -> bool:
    """``python``, ``py``, and versioned ones like ``python3.12``."""
    return binary in _PYTHON_LAUNCHERS or bool(_VERSIONED_PYTHON.fullmatch(binary))


def _starts_a_server(binary: str, args: List[str], launched_by_python: bool) -> bool:
    """Whether the command whose binary is *binary* runs a server.

    Only the command position counts: ``pip install uvicorn`` installs a
    server, it doesn't run one.
    """
    operands = [arg.lower() for arg in args if not arg.startswith("-")]
    first = operands[0] if operands else ""
    if binary in _SERVER_BINARIES:
        return True
    if launched_by_python and binary in _SERVER_MODULES:  # ``python -m http.server``
        return True
    if first in _SERVER_SUBCOMMANDS.get(binary, ()):
        return True
    if binary in _RUN_SCRIPT_BINARIES and first == "run":
        if len(operands) > 1 and operands[1] in _SERVER_RUN_SCRIPTS:
            return True
    if binary == "manage.py" and first == "runserver":
        return True
    if binary == "php" and "-s" in (arg.lower() for arg in args):
        return True
    if launched_by_python:
        script = binary
    elif binary in _SERVER_LAUNCHERS and operands:
        script = command_basename(operands[0])
    else:
        return False
    stem, _, suffix = script.rpartition(".")
    return suffix in ("py", "js", "mjs", "ts") and stem in _SERVER_SCRIPT_STEMS


def _classify_tokens(tokens: List[str]) -> TimeoutClass:
    """The class of one already-split command, wrappers peeled off."""
    command_class, launched_by_python = _classify_peeled(tokens)
    if command_class is DEFAULT and launched_by_python:
        return SCRIPT
    return command_class


def _classify_peeled(tokens: List[str]) -> Tuple[TimeoutClass, bool]:
    """``(class, whether a python launcher ran something)`` for one command."""
    index = 0
    launcher_seen = False
    while index < len(tokens):
        token = tokens[index]
        if _ENV_ASSIGNMENT.fullmatch(token):
            index += 1
            continue
        if token.startswith("-"):  # a flag, incl. python's -m
            # ``python -c CODE``: the code is the script.
            if launcher_seen and token == "-c":
                return SCRIPT, True
            index += 1
            continue
        binary = command_basename(token)
        if binary in _WRAPPERS or _is_python_launcher(binary):
            launcher_seen = launcher_seen or _is_python_launcher(binary)
            index += 1
            continue
        break
    else:
        return DEFAULT, False

    binary = command_basename(tokens[index])
    if _starts_a_server(binary, tokens[index + 1 :], launcher_seen):
        return DEFAULT, False
    if binary in _INTERPRETERS:
        return _classify_interpreter(binary, tokens[index + 1 :]), launcher_seen
    command_class = _classify_binary(tokens, index)
    # ``npm.cmd test`` is still a test run; only an unknown file is a script.
    if command_class is DEFAULT and tokens[index].lower().endswith(_SCRIPT_SUFFIXES):
        return SCRIPT, launcher_seen
    return command_class, launcher_seen


def _classify_interpreter(binary: str, args: List[str]) -> TimeoutClass:
    """``script`` when *binary* is given something to run, else ``default``.

    A shell handed a command line (``bash -lc "pip install x"``, ``cmd /c
    build.bat``, ``pwsh -Command "npm ci"``) takes that command line's class.
    """
    lowered = [arg.lower() for arg in args]
    if binary == "cmd":
        for i, arg in enumerate(lowered):
            if arg in ("/c", "/k"):
                return classify_command(" ".join(args[i + 1 :]))
        return DEFAULT
    if binary in ("pwsh", "powershell"):
        for i, arg in enumerate(lowered):
            if arg in ("-command", "-c"):
                return classify_command(" ".join(args[i + 1 :]))
            if arg in ("-file", "-encodedcommand"):
                return SCRIPT
            if not arg.startswith("-"):
                # A bare command line is what -Command would run.
                return classify_command(" ".join(args[i:]))
        return DEFAULT
    if binary in _SHELLS:
        for i, arg in enumerate(lowered):
            if not arg.startswith("-"):
                break  # ``bash run.sh -c cfg.yml``: a script's own flags
            # ``-c`` alone or in a cluster: ``-lc``, ``-ec``, ``-xec``.
            if re.fullmatch(r"-[a-z]*c[a-z]*", arg) and i + 1 < len(args):
                return classify_command(args[i + 1])
    if any(not arg.startswith("-") for arg in args) or _RUN_FLAGS & set(lowered):
        return SCRIPT
    return DEFAULT


def _classify_binary(tokens: List[str], index: int) -> TimeoutClass:
    """The class of the command whose binary is ``tokens[index]``."""

    binary = command_basename(tokens[index])
    operands: List[Tuple[int, str]] = []
    if binary == "git":
        subcommand = ""
        option_index = index + 1
        while option_index < len(tokens):
            token = tokens[option_index]
            if not token.startswith("-"):
                subcommand = command_basename(token)
                break
            option_name, has_inline_value, _ = token.partition("=")
            if option_name in _GIT_GLOBAL_OPTIONS_WITH_VALUE:
                option_index += 1 if has_inline_value else 2
                continue
            if option_name in _GIT_GLOBAL_OPTIONS_NO_VALUE:
                option_index += 1
                continue
            # Unknown options are still flags for timeout purposes; leave the
            # next non-option token available as the best subcommand guess.
            option_index += 1
    else:
        operands = [
            (position, command_basename(token))
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


_SEGMENT_SEPARATORS = frozenset({"|", "||", "&&", ";", "&"})


def split_pipeline(tokens: List[str]) -> List[List[str]]:
    """Already-tokenized *tokens* cut on shell separators.

    The shell validator has a separate tokenizer for security checks; this
    helper only segments the classifier's shell-like token stream.
    """
    segments: List[List[str]] = []
    current: List[str] = []
    for token in tokens:
        if token in _SEGMENT_SEPARATORS or (
            "\n" in token and all(char in ";&|\n" for char in token)
        ):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _tokenize(command: str) -> List[str]:
    """*command* split into tokens, with shell separators as punctuation.

    ``punctuation_chars`` is what makes ``ls|pytest`` classify as a test run:
    plain ``shlex.split`` keeps it as one token, so the pipeline is missed and
    the whole thing is timed as a 30s command. Newlines are preserved as
    separators too, while quoted text remains untouched.
    """
    # Keep a backslash before a path character, so a Windows path such as
    # C:\Python\python.exe keeps its separators; an escaped quote still works.
    command = _PATH_BACKSLASH.sub(r"\\\\", command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    # shlex.split() clears these; a raw shlex does not, and a URL fragment
    # (curl .../page#frag) would otherwise truncate the command mid-classify.
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return command.split()


def _heredoc_starts(line: str) -> List[Tuple[str, bool]]:
    """Return the heredoc delimiters opened on one shell command line."""
    lexer = shlex.shlex(line, posix=False, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        tokens = list(lexer)
    except ValueError:
        return []

    starts: List[Tuple[str, bool]] = []
    for index, token in enumerate(tokens):
        if token not in ("<<", "<<-") or index + 1 >= len(tokens):
            continue
        word = tokens[index + 1]
        strip_tabs = token == "<<-"
        if word == "-" and index + 2 < len(tokens):
            word, strip_tabs = tokens[index + 2], True
        elif word.startswith("-") and len(word) > 1:
            word, strip_tabs = word[1:], True
        try:
            delimiter = shlex.split(word, posix=True)
        except ValueError:
            continue
        if len(delimiter) == 1 and delimiter[0]:
            starts.append((delimiter[0], strip_tabs))
    return starts


def _strip_heredoc_bodies(command: str) -> str:
    """Remove heredoc input bodies before scanning shell command separators."""
    if "<<" not in command:
        return command

    lines = command.split("\n")
    kept: List[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        for delimiter, strip_tabs in _heredoc_starts(line):
            while index < len(lines):
                body_line = lines[index].lstrip("\t") if strip_tabs else lines[index]
                if body_line.rstrip("\r") == delimiter:
                    index += 1
                    break
                index += 1
            else:
                return "\n".join(kept)
    return "\n".join(kept)


def classify_command(command: str) -> TimeoutClass:
    """The timeout class *command* falls into.

    A pipeline takes the longest class of any segment: ``pytest -q | tail`` is a
    test run whose output happens to be filtered, and the shell waits for the
    whole pipeline anyway.
    """
    segments = split_pipeline(_tokenize(_strip_heredoc_bodies(command)))
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


#: How long the kill itself may take before the output is written off.
_KILL_GRACE_SECONDS = 5.0

#: ``taskkill /T`` walks the whole process table; on a busy box it takes over 5 s.
_TREE_KILL_SECONDS = 15.0


def terminate_process_tree(process: "subprocess.Popen") -> Tuple[str, str]:
    """Kill *process* and every descendant, then collect what it printed.

    A timeout that does not reach the grandchildren is not a timeout: the child
    dies, the grandchild keeps the pipes open, and the read blocks for as long
    as it lives — a 5s deadline measured at over two minutes on Windows before
    this existed.

    Returns:
        ``(stdout, stderr)`` buffered before the kill; empty strings if even the
        post-kill read will not complete, which is logged rather than hidden.
    """
    try:
        if os.name == "nt":
            # taskkill /T is the only way to reach a cmd.exe's children.
            subprocess.run(  # nosec B603 B607 - fixed argv, pid from our own Popen
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                timeout=_TREE_KILL_SECONDS,
            )
        else:
            os.killpg(os.getpgid(process.pid), 9)
    except (OSError, subprocess.SubprocessError) as exc:
        # Already dead, or unreachable — the kill below is the backstop.
        logger.warning(
            "Could not kill the process tree for pid %s: %s", process.pid, exc
        )

    process.kill()
    try:
        return process.communicate(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        logger.warning(
            "pid %s survived the kill; its partial output could not be read.",
            process.pid,
        )
        return "", ""
