# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Whether a Python project's declared dependencies are installed here.

An agent that cannot import the project spends step after step finding that
out — running its tests, reading the traceback, probing for the package,
trying another interpreter. The project's own manifest says what it needs, and
the interpreter says what it has: comparing them, and re-comparing only after
an install, turns that exploration into one line on each user turn.

Nothing from the project is imported or executed. The manifest is parsed as
data and the interpreter is asked only which distributions it has.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # 3.10
    import tomli as tomllib

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
        try:
            match = _SETUP_PY_REQUIRES.search(
                setup_py.read_text(encoding="utf-8", errors="replace")
            )
        except OSError as e:
            log.warning("[project-deps] %s unreadable: %s", setup_py, e)
            match = None
        if match:
            specs += _QUOTED.findall(match.group(1))
    requirements = root / "requirements.txt"
    if not specs and requirements.is_file():
        try:
            specs += requirements.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError as e:
            log.warning("[project-deps] %s unreadable: %s", requirements, e)
    names: List[str] = []
    for spec in specs:
        name = _requirement_name(spec)
        if name and name.lower() not in (n.lower() for n in names):
            names.append(name)
    return names


_PROBE = (
    "import json, os, sys, importlib.metadata as m\n"
    "missing = []\n"
    "for n in json.loads(sys.argv[1]):\n"
    "    try:\n"
    "        m.distribution(n)\n"
    "    except m.PackageNotFoundError:\n"
    "        missing.append(n)\n"
    "paths = [p for p in sys.path if p and os.path.isdir(p)]\n"
    "print(json.dumps({'missing': missing, 'paths': paths}))\n"
)

#: ``(python, names) -> (stamp, search paths, missing)``. Reused until a search
#: path's mtime changes, which an install or uninstall always causes.
_PROBE_CACHE: Dict[Tuple[str, Tuple[str, ...]], Tuple[str, List[str], List[str]]] = {}


def _stamp(paths: List[str]) -> str:
    parts = []
    for p in paths:
        try:
            parts.append(str(os.stat(p).st_mtime_ns))
        except OSError:
            parts.append("-")
    return "|".join(parts)


def missing_deps(names: List[str], python: Optional[str] = None) -> Optional[List[str]]:
    """Which of *names* the ``python`` on PATH lacks; None when it cannot be asked."""
    exe = python or shutil.which("python") or shutil.which("python3")
    if not names or not exe:
        return None
    key = (exe, tuple(names))
    cached = _PROBE_CACHE.get(key)
    if cached is not None and cached[0] == _stamp(cached[1]):
        return list(cached[2])
    try:
        # An empty cwd, so no project module can shadow what the probe imports.
        with tempfile.TemporaryDirectory() as cwd:
            done = subprocess.run(
                [exe, "-c", _PROBE, json.dumps(names)],
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_S,
                check=False,
                cwd=cwd,
            )
        if done.returncode != 0:
            log.warning(
                "[project-deps] %s could not list packages: %s", exe, done.stderr[-300:]
            )
            return None
        result = json.loads(done.stdout)
        missing, paths = list(result["missing"]), list(result["paths"])
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as e:
        log.warning("[project-deps] probing %s failed: %s", exe, e)
        return None
    _PROBE_CACHE[key] = (_stamp(paths), paths, missing)
    return list(missing)
