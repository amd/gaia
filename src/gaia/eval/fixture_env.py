# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where a run's staged fixtures live, and a fingerprint of what they were.

Scenario files name the staged fixtures as ``~/gaia-eval/...`` and the fixture
server as ``http://127.0.0.1:8765/...``. CI stages into the runner's home and
binds that port, so the text is true as written. A local run
(``util/run_eval_lane.py``) stages under its own ``--out`` folder and binds a
free port, so two runs never overwrite each other's fixtures or collide on the
port. It exports the two below, and the runner rewrites each scenario through
:func:`localize` before the driver and judge see it, so the user's message and
the ground truth name the same, real place.

:func:`fixture_fingerprint` hashes the fixture tree and the scenario corpus, so
a scorecard says which fixtures it measured and ``--compare`` can flag a diff
across two different sets.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Optional

#: The staged ``gaia-eval`` folder, when it is not ``~/gaia-eval``.
ROOT_ENV = "GAIA_EVAL_ROOT"
#: The fixture server's port, when it is not :data:`SCENARIO_PORT`.
PORT_ENV = "GAIA_EVAL_FIXTURE_PORT"

SCENARIO_ROOT = "~/gaia-eval"
SCENARIO_PORT = 8765

#: Where the port appears in scenario text: after the fixture host (also the
#: SSRF scenario's decimal spelling of it), and spelled out in a ground truth.
_PORT_PATTERNS = (
    re.compile(r"(?<=127\.0\.0\.1:)8765\b"),
    re.compile(r"(?<=2130706433:)8765\b"),
    re.compile(r"(?<=\bport )8765\b"),
)

#: Generated under the fixture tree at run time; not part of what was measured.
_UNHASHED_PARTS = frozenset({"__pycache__", ".pytest_cache"})
#: The prepared hub plus its .lock, .lock.d and .build-*/.old-* swap folders.
_UNHASHED_PREFIXES = ("_prepared",)


def from_env(environ=None) -> tuple[Optional[Path], Optional[int]]:
    """The staged root and fixture port this run was given, or None for each.

    Raises:
        ValueError: a variable is set but names no directory or no port.
    """
    environ = os.environ if environ is None else environ
    root = port = None
    raw_root = environ.get(ROOT_ENV)
    if raw_root:
        root = Path(raw_root)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError(
                f"{ROOT_ENV}={raw_root} is not an absolute path to a staged "
                "fixtures folder. Stage one with tests/fixtures/gaia/stage_eval_env.py "
                f"(util/run_eval_lane.py does it under --out), or unset {ROOT_ENV} "
                "to use ~/gaia-eval."
            )
    raw_port = environ.get(PORT_ENV)
    if raw_port:
        try:
            port = int(raw_port)
        except ValueError as e:
            raise ValueError(
                f"{PORT_ENV}={raw_port} is not a port number. Set it to the port "
                "tests/fixtures/gaia/serve_fixtures.py printed, or unset it to "
                f"use {SCENARIO_PORT}."
            ) from e
        if not 0 < port < 65536:
            raise ValueError(f"{PORT_ENV}={raw_port} is out of the port range.")
    return root, port


def localize_text(text: str, root: Optional[Path], port: Optional[int]) -> str:
    """*text* with the scenario root and port replaced by the run's own."""
    if root is not None:
        text = text.replace(SCENARIO_ROOT, root.as_posix())
    if port is not None:
        for pattern in _PORT_PATTERNS:
            text = pattern.sub(str(port), text)
    return text


def localize(data, root: Optional[Path], port: Optional[int]):
    """A copy of scenario *data* with every string rewritten by :func:`localize_text`."""
    if root is None and port is None:
        return data
    if isinstance(data, str):
        return localize_text(data, root, port)
    if isinstance(data, dict):
        return {k: localize(v, root, port) for k, v in data.items()}
    if isinstance(data, list):
        return [localize(v, root, port) for v in data]
    return data


def _tree_digest(root: Path) -> str:
    """SHA-256 over every file's relative path and content, line endings normalised.

    CRLF is folded to LF so a Windows checkout with autocrlf hashes the same as
    a Linux one; git never converts binaries, so they hash alike either way.
    """
    if not root.is_dir():
        raise FileNotFoundError(
            f"{root} does not exist, so the fixtures this run measures cannot be "
            "fingerprinted. Run the eval from a GAIA source checkout."
        )
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or _UNHASHED_PARTS.intersection(rel.parts):
            continue
        if any(part.startswith(_UNHASHED_PREFIXES) for part in rel.parts):
            continue
        if path.suffix == ".pyc":
            continue
        digest.update(rel.as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def fixture_fingerprint(repo_root: Path) -> dict:
    """Digests of ``tests/fixtures/gaia`` and ``eval/scenarios``, and of both."""
    fixtures = _tree_digest(repo_root / "tests" / "fixtures" / "gaia")
    scenarios = _tree_digest(repo_root / "eval" / "scenarios")
    combined = hashlib.sha256(f"{fixtures}\n{scenarios}".encode()).hexdigest()
    return {"fixtures": fixtures, "scenarios": scenarios, "combined": combined}
