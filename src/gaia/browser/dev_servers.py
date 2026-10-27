# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Dev servers: ``<project>/.gaia/launch.json``, parsed here and only here.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.agents.base.call_risk import DESTRUCTIVE, shell_command_risk
from gaia.agents.base.tool_grants import launch_digest

LAUNCH_FILE = Path(".gaia") / "launch.json"

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
        return hashlib.sha256(f"{self.cwd}\0{self.name}".encode("utf-8")).hexdigest()[
            :16
        ]

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
    "DevServerSpec",
    "LaunchConfigError",
    "describe",
    "env_name_allowed",
    "find_projects",
    "launch_file",
    "load_specs",
    "pick_running",
    "spec_named",
]
