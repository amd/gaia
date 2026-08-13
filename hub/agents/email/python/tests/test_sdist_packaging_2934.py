# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""sdist packaging guard for the email agent's build hook (#2934).

The wheel build depends on two files that live at the package ROOT, sibling
to ``pyproject.toml`` -- not inside ``gaia_agent_email/``: ``_build_hooks.py``
(wired via ``[tool.setuptools.cmdclass]``) and ``gaia-agent.yaml`` (staged
into the package by that hook -- see ``_build_hooks.py``). Neither is a
Python module inside the package, and neither matches
``[tool.setuptools.package-data]``'s glob (which cannot reach outside its own
package), so nothing declared them for sdist inclusion until ``MANIFEST.in``.

``test_skill_sets_2466.py`` already asserts the cmdclass wiring and loads
``_build_hooks.py`` by path from the source tree -- both pass even when the
sdist is broken, which is exactly why this bug shipped. This test instead
builds a REAL sdist with the real setuptools backend and reads its member
list, so a regression in what actually ships is caught, not a string match
against pyproject.toml/MANIFEST.in.

This is a narrow, single-package sdist-*contents* guard, not a reversal of
``publish_agents.yml``'s decision to stop building all 14 agent wheels on
every PR (that build was "pure noise" per that workflow's own comment,
deferring broken-wheel detection to release time) -- it runs one lightweight
``--sdist``-only build for the one package whose build depends on files
outside its own package data.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[1]

# setuptools' egg_info step writes SOURCES.txt into the SOURCE tree, not
# --outdir. A leftover copy from an earlier local build can make sdist skip
# regenerating the file list -- silently masking the exact MANIFEST.in
# regression this test exists to catch.
_STALE_BUILD_STATE = (
    _PKG_ROOT / "gaia_agent_email.egg-info",
    _PKG_ROOT / "build",
)


def _clear_stale_build_state() -> None:
    for stale_dir in _STALE_BUILD_STATE:
        shutil.rmtree(stale_dir, ignore_errors=True)


def test_sdist_includes_build_hook_and_agent_manifest(tmp_path):
    """A real ``--sdist`` build must ship both root-level files the build needs.

    Fails loudly (not skipped) if the ``build`` package isn't installed --
    it ships in the ``[publish]`` extra (``setup.py``), not ``[dev]``, so a
    plain ``pip install -e ".[dev,api]"`` will not have it.
    """
    if importlib.util.find_spec("build") is None:
        pytest.fail(
            "the 'build' package is required to run this test but is not "
            "installed. Install the [publish] extra: "
            "pip install -e '.[publish]' (see setup.py's 'publish' extra).",
            pytrace=False,
        )

    _clear_stale_build_state()
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--sdist",
                # Use the already-installed setuptools instead of creating an
                # isolated build env that fetches it from PyPI -- this suite
                # runs with no network.
                "--no-isolation",
                "--outdir",
                str(tmp_path),
                str(_PKG_ROOT),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        _clear_stale_build_state()

    assert result.returncode == 0, (
        f"sdist build failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )

    sdists = sorted(tmp_path.glob("*.tar.gz"))
    assert sdists, f"no sdist produced in {tmp_path}:\n{result.stdout}\n{result.stderr}"

    with tarfile.open(sdists[0]) as tf:
        names = tf.getnames()

    assert any(name.endswith("/_build_hooks.py") for name in names), (
        "_build_hooks.py is missing from the sdist. The wheel step imports it "
        "as a top-level module via [tool.setuptools.cmdclass], so rebuilding "
        "a wheel from this sdist (as a PyPI consumer with no matching wheel, "
        "or CI's build-agent-wheel action, would) fails with "
        "ModuleNotFoundError: _build_hooks. Declare it in MANIFEST.in."
    )
    assert any(name.endswith("/gaia-agent.yaml") for name in names), (
        "gaia-agent.yaml is missing from the sdist. It lives at the package "
        "root and [tool.setuptools.package-data] cannot reach outside "
        "gaia_agent_email/, so it needs its own MANIFEST.in entry -- without "
        "it, _build_hooks.py's build_py hook cannot stage it and the wheel "
        "build fails with 'gaia-agent.yaml not found next to pyproject.toml'."
    )
