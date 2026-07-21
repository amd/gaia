# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Crux test for issue #2358: a hub-installed WHEEL agent must be importable
by a FRESH ``gaia`` process, not just the process that ran the install.

``gaia.hub.installer.install()`` runs ``pip install --target
<install_root>/<id>/site-packages`` for a Python-agent artifact
(``_install_python_artifact``, ``installer.py:411``), but
``AgentRegistry.discover()`` (``src/gaia/agents/registry.py:593``) never
consults ``installer.list_installed()`` or adds any hub install's
``site-packages`` to ``sys.path`` before scanning
``importlib.metadata.entry_points()`` in ``_discover_installed_agents``
(``registry.py:722``). The only place that currently wires a hub install's
``site-packages`` onto ``sys.path`` at all is
``gaia.hub.installer._hot_register`` (``installer.py:704``) -- and only in
the *calling* process, as an in-memory ``sys.path`` mutation passed via the
``registry=`` kwarg. A brand-new ``gaia chat`` process (the real-world case
right after ``gaia init --profile chat`` exits) starts a fresh interpreter
with none of that state, so the just-installed agent is invisible to it.

This test proves the bug directly:

1. Install a REAL, self-contained fixture wheel (built by
   ``tests/fixtures/wheel_builder.py`` -- no mocked pip, no mocked
   ``run_pip``) via ``gaia.hub.installer.install()``, deliberately NOT
   passing ``registry=`` so no in-process hot-register can mask the gap.
2. Spawn a SEPARATE Python subprocess (not just a fresh ``AgentRegistry()``
   in this same process) and ask it to run ``AgentRegistry().discover()``
   from scratch, with ``$HOME`` redirected to the same temp home the install
   used.

A subprocess is used instead of an in-process fresh-registry check because
that is the only way to guarantee zero shared ``sys.path`` /
``importlib.metadata`` cache state with the install step -- it is what a
real second ``gaia`` invocation actually experiences, and it sidesteps any
ambiguity about whether ``pip install --target`` could have left stray
importable state (e.g. ``.pth`` files) in *this* interpreter.

Both the install step and the discovery step redirect ``$HOME`` to the same
temp directory via ``monkeypatch``/``env=`` (POSIX ``Path.home()`` honors
``$HOME``), because ``AgentRegistry.discover()`` step 2
(``Path.home() / ".gaia" / "agents"``, registry.py:601) and
``installer.default_install_root()`` (``Path.home() / ".gaia" / "agents"``,
installer.py:159) both hardcode ``Path.home()`` with no injectable param --
env-var redirection is the one seam that composes with both the in-process
install call and the subprocess discovery check.

MUST currently fail: the subprocess prints "NOTFOUND" because nothing
prepends the install's site-packages before entry-point discovery.
"""

import os
import subprocess
import sys

from gaia.hub import installer as hub_installer
from tests.fixtures.wheel_builder import (
    build_chat_shaped_fixture_wheel,
    build_wheel_fetcher,
    build_wheel_manifest,
)

BASE_URL = "https://hub.test"
FIXTURE_AGENT_ID = "fixturetestagent"

# Runs in a subprocess with $HOME redirected to the same temp home used for
# the install, and prints exactly "FOUND" or "NOTFOUND" so the parent test
# can assert on stdout without any other IPC machinery.
_DISCOVER_SCRIPT = """
from gaia.agents.registry import AgentRegistry

registry = AgentRegistry()
registry.discover()
print("FOUND" if registry.get({agent_id!r}) is not None else "NOTFOUND")
"""


def _real_pip_run_pip(python_exe):
    """A REAL ``run_pip`` (subprocess pip), not the mock-and-record pattern
    used elsewhere in this repo's hub-installer tests -- the crux test only
    proves anything if the wheel is genuinely installed."""

    def run_pip(args):
        subprocess.run(
            [python_exe, "-m", "pip", "install", *args],
            check=True,
            capture_output=True,
            text=True,
        )

    return run_pip


def test_hub_installed_wheel_agent_importable_in_fresh_process(tmp_path, monkeypatch):
    """Install for real, then check for the agent from a FRESH interpreter."""
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))

    wheel_bytes = build_chat_shaped_fixture_wheel(agent_id=FIXTURE_AGENT_ID)
    manifest, artifact_path = build_wheel_manifest(
        FIXTURE_AGENT_ID, "0.1.0", wheel_bytes
    )
    fetcher = build_wheel_fetcher(
        BASE_URL, artifact_path, wheel_bytes, FIXTURE_AGENT_ID
    )

    install_root = tmp_home / ".gaia" / "agents"
    result = hub_installer.install(
        FIXTURE_AGENT_ID,
        manifest=manifest,
        base_url=BASE_URL,
        fetcher=fetcher,
        run_pip=_real_pip_run_pip(sys.executable),
        install_root=install_root,
        # Deliberately NOT passing registry= -- this test is about proving
        # the cross-process gap, not papering over it with the in-process
        # hot-register path installer.install() also supports.
    )

    # Sanity: the wheel really did land on disk via a real pip subprocess.
    site_packages = install_root / FIXTURE_AGENT_ID / "site-packages"
    assert (site_packages / f"{FIXTURE_AGENT_ID}_pkg" / "agent.py").exists(), (
        "fixture wheel did not actually install -- test setup is broken, "
        "not the crux bug"
    )
    assert result.hot_registered is False  # no registry= was passed

    env = {**os.environ, "HOME": str(tmp_home)}
    proc = subprocess.run(
        [sys.executable, "-c", _DISCOVER_SCRIPT.format(agent_id=FIXTURE_AGENT_ID)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (
        proc.returncode == 0
    ), f"fresh-process discovery subprocess crashed: {proc.stderr}"
    # discover() logs INFO/WARNING lines to stdout too (see logger config), so
    # the sentinel is the LAST line, not the whole (polluted) stdout.
    last_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    assert last_line == "FOUND", (
        "A fresh `gaia` process could not see the hub-installed wheel agent "
        "-- this is the #2358 crux bug: gaia.hub.installer.install() only "
        "mutates sys.path in the calling process (_hot_register), and "
        "AgentRegistry.discover() never consults installer.list_installed() "
        f"to wire a hub install's site-packages onto sys.path. "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
