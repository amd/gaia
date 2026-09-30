# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Whether a Python project's declared dependencies are installed here.

An agent that cannot import the project spends step after step finding that
out — running its tests, reading the traceback, probing for the package,
trying another interpreter. The project's own manifest says what it needs, and
the interpreter says what it has: comparing them once, at task start, turns
that exploration into one line of the project map.

Nothing from the project is imported or executed. The manifest is parsed as
data and the interpreter is asked only which distributions it has.
"""

from __future__ import annotations

import configparser
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import List, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

PROBE_TIMEOUT_S = 20
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_SETUP_PY_REQUIRES = re.compile(r"install_requires\s*=\s*\[([^\]]*)\]", re.S)
_QUOTED = re.compile(r"""["']([^"']+)["']""")


def _requirement_name(spec: str) -> Optional[str]:
    """The distribution a requirement names, or None when its marker excludes this host."""
    spec = spec.strip()
    if not spec or spec.startswith(("#", "-")):
        return None
    try:
        from packaging.requirements import InvalidRequirement, Requirement
    except ImportError:  # packaging absent: keep the name, skip the marker
        match = _NAME.match(spec)
        return match.group(1) if match else None
    try:
        req = Requirement(spec)
    except InvalidRequirement:
        return None
    if req.marker is not None and not req.marker.evaluate():
        return None
    return req.name


def declared_python_deps(root: Path) -> List[str]:
    """Runtime dependencies the project's manifest declares, in manifest order."""
    specs: List[str] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            specs += list((data.get("project") or {}).get("dependencies") or [])
        except (OSError, tomllib.TOMLDecodeError) as e:
            log.warning("[project-deps] %s unreadable: %s", pyproject, e)
    setup_cfg = root / "setup.cfg"
    if not specs and setup_cfg.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(setup_cfg, encoding="utf-8")
            specs += parser.get("options", "install_requires", fallback="").splitlines()
        except configparser.Error as e:
            log.warning("[project-deps] %s unreadable: %s", setup_cfg, e)
    setup_py = root / "setup.py"
    if not specs and setup_py.is_file():
        match = _SETUP_PY_REQUIRES.search(
            setup_py.read_text(encoding="utf-8", errors="replace")
        )
        if match:
            specs += _QUOTED.findall(match.group(1))
    requirements = root / "requirements.txt"
    if not specs and requirements.is_file():
        specs += requirements.read_text(encoding="utf-8", errors="replace").splitlines()
    names: List[str] = []
    for spec in specs:
        name = _requirement_name(spec)
        if name and name.lower() not in (n.lower() for n in names):
            names.append(name)
    return names


_PROBE = (
    "import json, sys, importlib.metadata as m\n"
    "missing = []\n"
    "for n in json.loads(sys.argv[1]):\n"
    "    try:\n"
    "        m.distribution(n)\n"
    "    except m.PackageNotFoundError:\n"
    "        missing.append(n)\n"
    "print(json.dumps(missing))\n"
)


def missing_deps(names: List[str], python: Optional[str] = None) -> Optional[List[str]]:
    """Which of *names* the ``python`` on PATH lacks; None when it cannot be asked."""
    exe = python or shutil.which("python") or shutil.which("python3")
    if not names or not exe:
        return None
    try:
        done = subprocess.run(
            [exe, "-I", "-c", _PROBE, json.dumps(names)],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
        if done.returncode != 0:
            log.warning(
                "[project-deps] %s could not list packages: %s", exe, done.stderr[-300:]
            )
            return None
        return json.loads(done.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        log.warning("[project-deps] probing %s failed: %s", exe, e)
        return None
