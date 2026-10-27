# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Dev servers: ``<project>/.gaia/launch.json``, parsed here and only here.

A folder with no launch.json but with HTML files gets a static server
(:func:`static_spec`): Python's ``http.server`` bound to 127.0.0.1. This
module also knows what a server command looks like (:func:`server_in_command`,
:func:`server_in_python`), so the tools that wait for a command to finish
refuse one and point at ``dev_server`` instead.

The agent writes the file with ``write_file`` and the user can edit it. It has
the shape of Claude Code's ``.claude/launch.json``::

    {"version": "0.0.1",
     "configurations": [
       {"name": "web", "runtimeExecutable": "npm", "runtimeArgs": ["run", "dev"],
        "port": 5173, "cwd": "apps/web", "env": {"NODE_ENV": "development"},
        "url": "/"}]}

GAIA desktop's main process runs the servers (``dev-servers.cjs``). This
module turns a name into a :class:`DevServerSpec` main can run, never a shell
string, and gives it the digest its approval is keyed on, so writing or
editing the file approves nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import socket
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.agents.base.call_risk import DESTRUCTIVE, shell_command_risk
from gaia.agents.base.tool_grants import DEV_SERVER_TOOL, launch_digest

LAUNCH_FILE = Path(".gaia") / "launch.json"
STATIC_NAME = "static"
#: A folder's static server prefers one port in this range, so "Always" sticks.
STATIC_PORTS = range(18000, 19000)

#: Environment names a configuration may set. ``dev-servers.cjs`` applies the
#: same rule; both test it against ``bridge/dev_server_env.json``.
ENV_NAMES = frozenset(
    {
        "BROWSER",
        "CI",
        "DEBUG",
        "FORCE_COLOR",
        "HOST",
        "HOSTNAME",
        "LOG_LEVEL",
        "NODE_ENV",
        "NO_COLOR",
        "PORT",
        "PYTHONUNBUFFERED",
        "TZ",
    }
)
ENV_PREFIXES = ("VITE_", "NEXT_PUBLIC_", "REACT_APP_", "PUBLIC_")

DEFAULT_READY_TIMEOUT_S = 60
MAX_READY_TIMEOUT_S = 120

_EXAMPLE = (
    '{"version": "0.0.1", "configurations": [{"name": "web", '
    '"runtimeExecutable": "npm", "runtimeArgs": ["run", "dev"], "port": 5173}]}'
)
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class LaunchConfigError(ValueError):
    """What is wrong with launch.json, written for the model: the file, the field, the fix."""


@dataclass(frozen=True)
class DevServerSpec:
    """One configuration, resolved: what main runs, and where."""

    name: str
    argv: Tuple[str, ...]
    cwd: Path
    port: int
    env: Tuple[Tuple[str, str], ...]
    url_path: str
    ready_timeout_s: int

    @property
    def command_line(self) -> str:
        """What the approval prompt shows. Never run through a shell."""
        return shlex.join(self.argv)

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}{self.url_path}"

    @property
    def key(self) -> str:
        """Main's handle: two projects may both have a server named "web"."""
        return server_key(self.cwd, self.name)

    @property
    def digest(self) -> str:
        """What an approval covers. An edit to argv, cwd, port or env changes it."""
        return launch_digest(
            self.command_line, str(self.cwd), self.port, dict(self.env)
        )

    def prompt_args(self) -> Dict[str, Any]:
        """The approval prompt's arguments; ``tool_grants`` keys "Always" on them."""
        args: Dict[str, Any] = {
            "action": "start",
            "name": self.name,
            "command": self.command_line,
            "folder": str(self.cwd),
            "port": self.port,
        }
        if self.env:
            args["env"] = dict(self.env)
        return args

    def to_wire(self) -> Dict[str, Any]:
        """``DevServerSpec`` in contract.d.ts."""
        return {
            "name": self.name,
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "port": self.port,
            "env": dict(self.env),
            "url": self.url,
            "readyTimeoutMs": self.ready_timeout_s * 1000,
        }


def server_key(cwd: Path, name: str) -> str:
    return hashlib.sha256(f"{cwd}\0{name}".encode("utf-8")).hexdigest()[:16]


def env_name_allowed(name: str) -> bool:
    return name in ENV_NAMES or (
        name.startswith(ENV_PREFIXES) and re.fullmatch(r"[A-Z0-9_]+", name) is not None
    )


def launch_file(project: Path) -> Path:
    return project / LAUNCH_FILE


def load_specs(
    project: Path, *, allowed: Callable[[Path], bool]
) -> List[DevServerSpec]:
    """Every configuration in ``project``'s launch.json, or one error naming the fix.

    ``allowed`` says whether a folder is inside the chat's allowed folders;
    the project and every ``cwd`` must be. Never a partial list.
    """
    project = project.expanduser().resolve()
    if not allowed(project):
        raise LaunchConfigError(
            f"{project} is outside the folders this chat may use. Ask the user to "
            "give GAIA access to it, then try again."
        )
    path = launch_file(project)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LaunchConfigError(
            f"{path} does not exist. Write it with write_file, then call "
            f"dev_server again. Example: {_EXAMPLE}"
        ) from None
    except OSError as e:
        raise LaunchConfigError(f"Could not read {path}: {e}") from e
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise LaunchConfigError(
            f"{path} is not valid JSON ({e}). Example: {_EXAMPLE}"
        ) from e
    configs = data.get("configurations") if isinstance(data, dict) else None
    if not isinstance(configs, list) or not configs:
        raise LaunchConfigError(
            f'{path} needs a "configurations" list with at least one server. Example: {_EXAMPLE}'
        )
    specs = [_parse(path, project, n, c, allowed) for n, c in enumerate(configs, 1)]
    names = [s.name for s in specs]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise LaunchConfigError(
            f"{path} names more than one server {', '.join(dupes)}; names must differ."
        )
    return specs


def spec_named(
    project: Path, name: str, *, allowed: Callable[[Path], bool]
) -> DevServerSpec:
    """The configuration called ``name``, or the only one when ``name`` is empty."""
    specs = load_specs(project, allowed=allowed)
    if not name and len(specs) == 1:
        return specs[0]
    for spec in specs:
        if spec.name == name:
            return spec
    listed = ", ".join(s.name for s in specs)
    what = f'no server named "{name}"' if name else "more than one server"
    raise LaunchConfigError(
        f"{launch_file(project)} has {what}. Its servers: {listed}. Pass name=."
    )


def resolve_spec(
    project: Path,
    name: str,
    *,
    allowed: Callable[[Path], bool],
    static_port: Callable[[str], int],
) -> DevServerSpec:
    """launch.json's server called ``name``; for a plain HTML folder, its static server.

    ``static_port`` maps the static server's key to its port, so the prompt
    and the run agree on one port without picking it twice.
    """
    project = project.expanduser().resolve()
    if launch_file(project).is_file():
        return spec_named(project, name, allowed=allowed)
    if not allowed(project):
        raise LaunchConfigError(
            f"{project} is outside the folders this chat may use. Ask the user to "
            "give GAIA access to it, then try again."
        )
    if not project.is_dir():
        raise LaunchConfigError(f"{project} is not a folder.")
    if not any(project.glob("*.htm*")):
        raise LaunchConfigError(
            f"{project} has no .html file and no {LAUNCH_FILE}. For a plain web "
            "page, write index.html there and call dev_server again; it is served "
            f"with no setup. For an app with its own dev command, write "
            f"{launch_file(project)} with write_file. Example: {_EXAMPLE}"
        )
    if name and name != STATIC_NAME:
        raise LaunchConfigError(
            f'{project} has no {LAUNCH_FILE}, so its only server is "{STATIC_NAME}", '
            f"which serves its files. Leave name empty, or write {LAUNCH_FILE} to "
            f'define "{name}".'
        )
    return static_spec(project, static_port(server_key(project, STATIC_NAME)))


def static_spec(folder: Path, port: int) -> DevServerSpec:
    """Serve ``folder``'s files on 127.0.0.1 only; nothing else on the network sees it."""
    folder = folder.expanduser().resolve()
    return DevServerSpec(
        name=STATIC_NAME,
        argv=(
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
            "--directory",
            str(folder),
        ),
        cwd=folder,
        port=port,
        env=(),
        url_path="/",
        ready_timeout_s=DEFAULT_READY_TIMEOUT_S,
    )


def static_port(key: str, running: List[Dict[str, Any]]) -> int:
    """The port a static server keeps: its running one, else its folder's own, else any free one."""
    for server in running:
        if server.get("key") == key and server.get("status") in ("starting", "ready"):
            return int(server["port"])
    preferred = STATIC_PORTS.start + zlib.crc32(key.encode()) % len(STATIC_PORTS)
    return _free_loopback_port(preferred) or _free_loopback_port(0)


def _free_loopback_port(port: int) -> Optional[int]:
    """``port`` if nothing holds it on 127.0.0.1, None if taken; 0 asks the OS for one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            if port == 0:
                raise
            return None
        return s.getsockname()[1]


def _parse(
    path: Path, project: Path, n: int, config: Any, allowed: Callable[[Path], bool]
) -> DevServerSpec:
    where = f"{path}, configuration {n}"

    def bad(message: str) -> LaunchConfigError:
        return LaunchConfigError(f"{where}: {message} Example: {_EXAMPLE}")

    if not isinstance(config, dict):
        raise bad("each configuration must be an object.")
    name = config.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise bad('"name" must be a short name like "web" (letters, digits, - _ .).')
    exe = config.get("runtimeExecutable")
    if not isinstance(exe, str) or not exe.strip():
        raise bad(
            '"runtimeExecutable" must be the program to run, like "npm" or "node".'
        )
    rest = config.get("runtimeArgs", [])
    if not isinstance(rest, list) or not all(isinstance(a, str) for a in rest):
        raise bad('"runtimeArgs" must be a list of strings, like ["run", "dev"].')
    argv = (exe, *rest)
    if any("\x00" in a for a in argv):
        raise bad("the command holds a NUL character.")
    if shell_command_risk(shlex.join(argv)) == DESTRUCTIVE:
        raise bad(f"{shlex.join(argv)} deletes files; a dev server command must not.")
    port = config.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise bad('"port" must be the port the server listens on, from 1024 to 65535.')
    rel = config.get("cwd", ".")
    if not isinstance(rel, str):
        raise bad('"cwd" must be a folder inside the project, like "apps/web".')
    cwd = (project / rel).resolve()
    if cwd != project and project not in cwd.parents:
        raise bad(f'"cwd" {rel!r} leaves the project folder {project}.')
    if not cwd.is_dir():
        raise bad(f'"cwd" {rel!r} is not a folder ({cwd}).')
    if not allowed(cwd):
        raise bad(f"{cwd} is outside the folders this chat may use.")
    env = config.get("env", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise bad(
            '"env" must map names to string values, like {"NODE_ENV": "development"}.'
        )
    refused = sorted(k for k in env if not env_name_allowed(k))
    if refused:
        raise bad(
            f"env {', '.join(refused)} is not allowed. Allowed: {', '.join(sorted(ENV_NAMES))}, "
            f"and names starting {', '.join(ENV_PREFIXES)}."
        )
    url = config.get("url", "/")
    if not isinstance(url, str) or not url.startswith("/"):
        raise bad('"url" must be a path on the server, like "/" or "/settings".')
    timeout = config.get("readyTimeoutSeconds", DEFAULT_READY_TIMEOUT_S)
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 1 <= timeout <= MAX_READY_TIMEOUT_S
    ):
        raise bad(f'"readyTimeoutSeconds" must be from 1 to {MAX_READY_TIMEOUT_S}.')
    return DevServerSpec(
        name=name,
        argv=argv,
        cwd=cwd,
        port=port,
        env=tuple(sorted(env.items())),
        url_path=url,
        ready_timeout_s=timeout,
    )


# ----------------------------------------------------- servers run the wrong way

#: What a never-ending server looks like, as one command after
#: :func:`_normalize` (``python -m X`` is ``X``, ``npx X`` is ``X``, a package
#: script is ``script NAME``). ``(what, pattern)``; ``what`` goes in the refusal.
SHELL_SERVERS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = tuple(
    (what, re.compile(pattern))
    for what, pattern in (
        ("Python's http.server", r"^(http\.server|SimpleHTTPServer)\b"),
        ("a package dev script", r"^script (dev|start|serve|preview)\b"),
        ("Vite", r"^vite( (dev|serve|preview)\b| -|$)"),
        ("Next.js", r"^next (dev|start)\b"),
        (
            "a static file server",
            r"^(serve|http-server|live-server|browser-sync|webpack-dev-server)\b",
        ),
        ("webpack's dev server", r"^webpack (serve|s)\b"),
        ("Parcel", r"^parcel (?!build\b)"),
        ("Flask", r"^flask (\S+ )*run\b"),
        (
            "an ASGI/WSGI server",
            r"^(uvicorn|gunicorn|hypercorn|daphne|waitress-serve)\b",
        ),
        ("Django's runserver", r"^manage\.py runserver\b"),
        ("PHP's built-in server", r"^php (\S+ )*-S\b"),
        ("a site server", r"^(jekyll|hugo|rails) (serve|server|s)\b"),
    )
)

#: The same, in Python source run by run_python or execute_python_file.
PYTHON_SERVERS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = tuple(
    (what, re.compile(pattern))
    for what, pattern in (
        ("serve_forever()", r"\.serve_forever\s*\("),
        (
            "an http.server / socketserver server",
            r"\b(HTTPServer|ThreadingHTTPServer|TCPServer|ThreadingTCPServer)\s*\(",
        ),
        (
            "Python's http.server",
            r"""http\.server\.test\s*\(|['"](-m\s+)?http\.server\b""",
        ),
        ("app.run()", r"\bapp\.run\s*\("),
        ("uvicorn.run()", r"\buvicorn\.run\s*\("),
        ("aiohttp's run_app()", r"\bweb\.run_app\s*\("),
        ("waitress.serve()", r"\bwaitress\.serve\s*\("),
    )
)

_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "(", ")", ";;"})
_WRAPPERS = frozenset({"nohup", "exec", "env", "time", "sudo", "command", "caffeinate"})
_RUNNERS = {"npx", "pnpx", "bunx"}
_SCRIPT_RUNNERS = {"npm", "pnpm", "yarn", "bun"}
_SHELLS = {"sh", "bash", "zsh"}
_PYTHON = re.compile(r"python[\d.]*|py")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def _is_assignment(token: str) -> bool:
    return _ASSIGNMENT.match(token) is not None


@dataclass(frozen=True)
class ServerMatch:
    """A server the call would start: what it is, and the folder it serves if the line says."""

    what: str
    folder: Optional[str] = None


def server_in_command(command: str) -> Optional[ServerMatch]:
    """The never-ending server ``command`` starts, or None. ``grep vite package.json`` is None."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        tokens = command.split()
    folder: Optional[str] = None
    segment: List[str] = []
    for token in [*tokens, ";"]:
        if token not in _SEPARATORS:
            segment.append(token)
            continue
        if segment[:1] == ["cd"] and len(segment) > 1:
            folder = _join(folder, segment[1])
        found = _segment_server(segment)
        if found is not None:
            return ServerMatch(found.what, _join(folder, found.folder))
        segment = []
    return None


def _join(folder: Optional[str], then: Optional[str]) -> Optional[str]:
    if folder is None or then is None:
        return then or folder
    return str(Path(folder) / then)


def _segment_server(argv: List[str]) -> Optional[ServerMatch]:
    while argv and (_is_assignment(argv[0]) or argv[0] in _WRAPPERS):
        argv = argv[1:]
    if len(argv) >= 3 and argv[1] == "-c":
        head = Path(argv[0]).name
        if head in _SHELLS:
            return server_in_command(argv[2])
        if _PYTHON.fullmatch(head):
            return server_in_python(argv[2])
    line = " ".join(_normalize(argv))
    for what, pattern in SHELL_SERVERS:
        if pattern.search(line):
            return ServerMatch(what, _flag_value(argv, ("--directory", "-d")))
    return None


def _normalize(argv: List[str]) -> List[str]:
    """``python -m X`` -> ``X``, ``python app.py`` -> ``app.py``, ``npx X`` -> ``X``, ``npm run X`` -> ``script X``."""
    if not argv:
        return argv
    head, rest = Path(argv[0]).name, argv[1:]
    if _PYTHON.fullmatch(head):
        while rest and rest[0].startswith("-") and rest[0] != "-m":
            rest = rest[1:]
        if rest[:1] == ["-m"]:
            return rest[1:]
        return [Path(rest[0]).name, *rest[1:]] if rest else []
    if head in _RUNNERS or argv[:2] in (
        ["pnpm", "dlx"],
        ["yarn", "dlx"],
        ["npm", "exec"],
    ):
        rest = rest[1:] if head not in _RUNNERS else rest
        while rest and rest[0].startswith("-"):
            rest = rest[1:]
        return _normalize(rest)
    if argv[:2] == ["bundle", "exec"]:
        return _normalize(argv[2:])
    if head in _SCRIPT_RUNNERS:
        if rest[:1] == ["run"]:
            rest = rest[1:]
        return ["script", *rest] if rest else [head]
    return [head, *rest]


def _flag_value(argv: List[str], flags: Tuple[str, ...]) -> Optional[str]:
    for n, arg in enumerate(argv[:-1]):
        if arg in flags:
            return argv[n + 1]
    return None


def server_in_python(source: str) -> Optional[ServerMatch]:
    """The never-ending server this Python source starts, or None."""
    for what, pattern in PYTHON_SERVERS:
        if pattern.search(source):
            return ServerMatch(what)
    return None


def dev_server_offered(agent: Any) -> bool:
    """Whether this agent has dev_server, loaded or loadable: it registers only in GAIA desktop."""
    return DEV_SERVER_TOOL in (getattr(agent, "_tools_registry", None) or {})


def server_refusal(what: str, folder: Optional[str], offered: bool) -> str:
    """The refusal for a server started the wrong way, naming the right way."""
    head = (
        f"Not run: this starts {what}, a server that never exits. This tool waits "
        "for its command to finish, so it would hang the turn until it times out."
    )
    return f"{head} {_how_to_serve(folder, offered)}"


def file_url_refusal(url: str, offered: bool) -> str:
    """browser_open's answer to a file:// URL: serve the file's folder instead."""
    from urllib.parse import urlsplit
    from urllib.request import url2pathname

    path = Path(url2pathname(urlsplit(url).path))
    folder = path if path.is_dir() else path.parent
    return f"Error: the browser does not open file:// pages ({url}). " + _how_to_serve(
        str(folder), offered
    )


def _how_to_serve(folder: Optional[str], offered: bool) -> str:
    target = folder or "<the folder holding index.html>"
    if offered:
        return (
            f'To show the page in the browser pane, call dev_server(action="start", '
            f'project="{target}"); call load_tools("dev_server") first if it is not '
            "in your tools. A folder of HTML files needs no setup."
        )
    return (
        "Starting servers needs GAIA desktop's browser pane, which this chat does not "
        "have. Ask the user to run the server in their own terminal."
    )


def find_projects(roots: List[Path]) -> List[Path]:
    """The roots that hold a launch.json, for a call that names no project."""
    return [r for r in roots if launch_file(r).is_file()]


def describe(server: Dict[str, Any]) -> str:
    """One line per server, for the model: name, status, where it answers."""
    status = server.get("status")
    line = f"{server.get('name')}: {status}"
    if status in ("starting", "ready"):
        line += f" on port {server.get('port')} ({server.get('url')})"
    elif server.get("exitCode") is not None:
        line += f" (exit code {server['exitCode']})"
    return f"{line}, `{shlex.join(server.get('argv') or [])}` in {server.get('cwd')}"


def pick_running(servers: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """The server called ``name``, or the only one when ``name`` is empty."""
    if not name:
        return servers[0] if len(servers) == 1 else None
    found = [s for s in servers if s.get("name") == name]
    return found[0] if len(found) == 1 else None


__all__ = [
    "ENV_NAMES",
    "ENV_PREFIXES",
    "LAUNCH_FILE",
    "PYTHON_SERVERS",
    "SHELL_SERVERS",
    "STATIC_NAME",
    "DevServerSpec",
    "LaunchConfigError",
    "ServerMatch",
    "describe",
    "dev_server_offered",
    "env_name_allowed",
    "file_url_refusal",
    "find_projects",
    "launch_file",
    "load_specs",
    "pick_running",
    "resolve_spec",
    "server_in_command",
    "server_in_python",
    "server_refusal",
    "spec_named",
    "static_port",
    "static_spec",
]
