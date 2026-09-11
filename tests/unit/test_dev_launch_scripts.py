# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The dev launch scripts must actually point the TUI at this checkout.

The TUI spawns the flagship as a child and resolves ``gaia-agent`` from PATH
before ``~/.gaia/agents/``. The repo's own install does not create that
executable — ``setup.py`` ships ``gaia``, ``gaia-cli`` and ``gaia-mcp`` only —
so without a shim on PATH a developer runs the last *installed* build and
concludes their new tool "doesn't work". PYTHONPATH cannot rescue that: the
installed agent is a frozen binary and ignores it.

These tests pin the two halves of that contract: the shim exists and launches
the source agent, and each launch script puts it on PATH.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_BIN = REPO_ROOT / "scripts" / "dev" / "bin"
LAUNCHERS = {
    "run-tui.sh": REPO_ROOT / "scripts" / "dev" / "run-tui.sh",
    "run-tui.bat": REPO_ROOT / "scripts" / "dev" / "run-tui.bat",
}


class TestAgentShim:
    @pytest.mark.parametrize("name", ["gaia-agent", "gaia-agent.cmd"])
    def test_shim_exists(self, name):
        """One per platform — a POSIX shell has no use for the .cmd and vice versa."""
        assert (DEV_BIN / name).is_file(), (
            f"{name} is missing from scripts/dev/bin. Without it the TUI "
            "silently runs the installed agent instead of this checkout."
        )

    @pytest.mark.parametrize("name", ["gaia-agent", "gaia-agent.cmd"])
    def test_shim_runs_the_source_agent(self, name):
        """It must invoke the agent's stdio entry point, not any built binary."""
        body = (DEV_BIN / name).read_text(encoding="utf-8")
        assert "gaia_agent.server" in body, (
            f"{name} must exec `python -m gaia_agent.server` so the TUI gets "
            "this checkout's source."
        )

    @pytest.mark.parametrize("name", ["gaia-agent", "gaia-agent.cmd"])
    def test_shim_exports_the_checkout_on_pythonpath(self, name):
        """The child is a fresh interpreter and inherits none of the parent's path."""
        body = (DEV_BIN / name).read_text(encoding="utf-8")
        assert "PYTHONPATH" in body, f"{name} must set PYTHONPATH"
        for package_root in ("src", "gaia", "chat"):
            assert package_root in body, (
                f"{name} must put {package_root} on PYTHONPATH, or the agent "
                "imports a different checkout's source."
            )


class TestLaunchScripts:
    @pytest.mark.parametrize("name", sorted(LAUNCHERS))
    def test_launcher_prepends_the_shim_directory(self, name):
        """Prepended, not appended: an installed gaia-agent must not win."""
        body = LAUNCHERS[name].read_text(encoding="utf-8")
        assert re.search(r"PATH\s*=.*scripts[\\/]dev[\\/]bin", body), (
            f"{name} must prepend scripts/dev/bin to PATH — that is the only "
            "hook the TUI offers for running the agent from source."
        )

    @pytest.mark.parametrize("name", sorted(LAUNCHERS))
    def test_launcher_does_not_promise_a_dev_mode_env_var(self, name):
        """GAIA_GAIA_AGENT_MODE selects a DAEMON sidecar build.

        The flagship is spawned directly over stdio and never consults it, so
        setting it here did nothing while reading as the load-bearing line.
        """
        body = LAUNCHERS[name].read_text(encoding="utf-8")
        assert "GAIA_GAIA_AGENT_MODE" not in body, (
            f"{name} sets GAIA_GAIA_AGENT_MODE, which only affects the daemon "
            "sidecar path. The TUI spawns the flagship as a subprocess and "
            "ignores it; PATH is what decides which agent runs."
        )
