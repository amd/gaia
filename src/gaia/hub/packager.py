# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Build a distributable Python wheel from a hub agent package.

Phase 2 of the Agent Hub restructure (``docs/spec/agent-hub-restructure.mdx``):
a community/AMD agent lives in ``hub/agents/<id>/python/`` with its own
``pyproject.toml`` (declaring the ``gaia.agent`` entry point and an
``amd-gaia>={min_gaia_version}`` dependency). :func:`pack` turns that source
package into ``dist/gaia_agent_<id>-<version>-py3-none-any.whl`` via
``python -m build --wheel`` and computes the artifact's SHA-256 so the publisher
(and the R2 Worker) can verify integrity.

Per ``CLAUDE.md`` (No Silent Fallbacks): every step either succeeds or raises a
:class:`PackagerError` naming *what* failed, *what* to do, and *where* to look.
There is no degraded "best-effort" wheel — a build failure is loud.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from gaia.hub import manifest as hub_manifest
from gaia.logger import get_logger

log = get_logger(__name__)

# Read SHA-256 in 1 MiB chunks so a large native-deps wheel doesn't balloon RAM.
_HASH_CHUNK = 1024 * 1024

_SPEC_URL = "https://amd-gaia.ai/docs/spec/agent-hub-restructure"


class PackagerError(Exception):
    """Raised when a wheel cannot be built from an agent package.

    The message always names *what* failed, *what* to do, and *where* to look,
    per the project's fail-loudly rule.
    """


@dataclass
class PackResult:
    """The outcome of a successful :func:`pack`."""

    wheel_path: Path
    sha256: str
    size_bytes: int
    agent_id: str
    version: str
    dist_name: str

    @property
    def filename(self) -> str:
        return self.wheel_path.name


def _normalize_wheel_stem(dist_name: str) -> str:
    """Return the wheel-escaped distribution name.

    PEP 427 escapes runs of ``-_.`` in the distribution name to a single ``_``,
    so ``gaia-agent-summarize`` becomes ``gaia_agent_summarize`` in the wheel
    filename. We use this to locate the wheel ``python -m build`` produced.
    """
    return re.sub(r"[-_.]+", "_", dist_name).lower()


def _sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of *path* (streamed)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_pyproject_name(pyproject: Path) -> Optional[str]:
    """Best-effort read of ``[project] name`` from a pyproject.toml.

    Used only to locate the built wheel; the manifest id is authoritative for
    everything else. Returns ``None`` if the field is absent so the caller can
    fall back to the ``gaia-agent-<id>`` convention.
    """
    pattern = re.compile(r'^\s*name\s*=\s*["\']([^"\']+)["\']\s*$')
    in_project = False
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project:
            m = pattern.match(line)
            if m:
                return m.group(1)
    return None


def pack(
    package_dir,
    *,
    output_dir=None,
    runner=None,
) -> PackResult:
    """Build a wheel from the agent package at *package_dir*.

    Args:
        package_dir: Directory containing ``gaia-agent.yaml`` and
            ``pyproject.toml`` (the agent package root).
        output_dir: Where to write the wheel. Defaults to ``<package_dir>/dist``.
        runner: Optional callable ``(cmd: list[str], cwd: Path) -> (rc, output)``
            used to run the build (injected by tests). Defaults to a real
            subprocess invocation of ``python -m build --wheel``.

    Returns:
        A :class:`PackResult` with the wheel path, its SHA-256, size, and the
        agent's id/version.

    Raises:
        PackagerError: If the package is missing required files, is not a python
            agent, the build tool is unavailable, the build fails, or no wheel
            is produced.
    """
    pkg_dir = Path(package_dir).expanduser().resolve()
    if not pkg_dir.is_dir():
        raise PackagerError(
            f"agent package directory not found: {pkg_dir}. Pass the path to a "
            f"hub agent package (the directory containing gaia-agent.yaml)."
        )

    # The manifest is the source of truth for id/version and gates non-python
    # packages. A C++ agent ships a binary, not a wheel — fail loudly.
    try:
        parsed = hub_manifest.parse(pkg_dir)
    except hub_manifest.ManifestError as exc:
        raise PackagerError(
            f"cannot pack {pkg_dir}: its gaia-agent.yaml is invalid: {exc}"
        ) from exc

    if parsed.language != "python":
        raise PackagerError(
            f"'gaia agent pack' builds Python wheels, but {pkg_dir} is a "
            f"{parsed.language!r} agent. Native (cpp) agents ship a binary built "
            f"by CMake, not a wheel. See {_SPEC_URL}."
        )

    pyproject = pkg_dir / "pyproject.toml"
    if not pyproject.exists():
        raise PackagerError(
            f"pyproject.toml not found at {pyproject}. A Python agent package "
            f"needs one to declare its build backend, dependencies, and the "
            f"'gaia.agent' entry point. See {_SPEC_URL}."
        )

    out_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else pkg_dir / "dist"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    dist_name = _read_pyproject_name(pyproject) or f"gaia-agent-{parsed.id}"

    # Snapshot existing wheels (name -> mtime) so we can identify exactly what
    # this build adds or rewrites — a stale wheel from a previous version must
    # not be mistaken for the new one, while a same-version rebuild (e.g.
    # 'gaia agent pack' then 'gaia agent publish') overwrites in place and
    # still counts as produced by this build.
    before = {p.name: p.stat().st_mtime_ns for p in out_dir.glob("*.whl")}

    cmd = [
        sys.executable,
        "-m",
        "build",
        "--wheel",
        "--outdir",
        str(out_dir),
        str(pkg_dir),
    ]
    run = runner or _default_runner
    log.debug("packager: building wheel: %s", " ".join(cmd))
    rc, output = run(cmd, pkg_dir)
    if rc != 0:
        raise PackagerError(
            f"wheel build failed for agent {parsed.id!r} (exit {rc}).\n"
            f"{output.strip()}\n"
            f"Ensure the build backend is installed ('uv pip install \"amd-gaia"
            f"[publish]\"' or 'uv pip install build') and that pyproject.toml is "
            f"valid, then re-run 'gaia agent pack'."
        )

    wheel = _locate_wheel(out_dir, before, dist_name, parsed.version)
    sha256 = _sha256_file(wheel)
    size = wheel.stat().st_size
    log.debug("packager: built %s (%d bytes, sha256=%s)", wheel.name, size, sha256)
    return PackResult(
        wheel_path=wheel,
        sha256=sha256,
        size_bytes=size,
        agent_id=parsed.id,
        version=parsed.version,
        dist_name=dist_name,
    )


def _locate_wheel(out_dir: Path, before: dict, dist_name: str, version: str) -> Path:
    """Return the wheel this build produced, failing loudly if ambiguous.

    *before* maps pre-build wheel names to their mtime_ns; a wheel counts as
    produced by this build if its name is new or its mtime changed (build
    overwrites a same-version wheel in place).
    """
    new_wheels = [
        p
        for p in out_dir.glob("*.whl")
        if p.name not in before or p.stat().st_mtime_ns != before[p.name]
    ]
    stem = _normalize_wheel_stem(dist_name)
    expected_prefix = f"{stem}-{version}-"

    # Prefer a freshly built wheel matching <normalized_name>-<version>.
    matches = [p for p in new_wheels if p.name.lower().startswith(expected_prefix)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(sorted(p.name for p in matches))
        raise PackagerError(
            f"build produced multiple wheels matching {expected_prefix!r}: "
            f"{names}. Clean {out_dir} and re-run 'gaia agent pack'."
        )

    # No new wheel matched the expected name — surface what *was* produced so
    # the author can see a name/version mismatch between pyproject and manifest.
    if new_wheels:
        produced = ", ".join(sorted(p.name for p in new_wheels))
        raise PackagerError(
            f"build produced wheel(s) [{produced}] but none matched the expected "
            f"name {expected_prefix!r} (from pyproject name {dist_name!r} and "
            f"manifest version {version}). Make sure pyproject.toml 'name' and "
            f"'version' agree with gaia-agent.yaml."
        )
    raise PackagerError(
        f"'python -m build' reported success but no .whl appeared in {out_dir}. "
        f"Check the build output and your pyproject.toml build backend."
    )


def _default_runner(cmd: List[str], cwd: Path):
    """Run *cmd* in *cwd*; return ``(returncode, combined stdout+stderr)``."""
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        return 1, (
            f"could not run {cmd[0]!r}: {exc}. Install the Python build "
            f"frontend with 'uv pip install \"amd-gaia[publish]\"'."
        )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
