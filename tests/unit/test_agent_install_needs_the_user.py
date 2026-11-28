# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Installing or importing agent code is the user's step, never an agent's.

Hub installs and bundle imports put code under ``~/.gaia/agents`` that GAIA
later runs, so they need the user's own terminal, like changing skill trust.
Nothing real is installed: the hub fetcher is stubbed and every root is a temp
folder.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import gaia
from gaia.agents.base.python_human_only import human_only_in_python
from gaia.daemon.client import _daemon_env
from gaia.hub import installer
from gaia.installer.export_import import import_agent_bundle
from gaia.skills.human_only import (
    AGENT_CHILD_ENV,
    AGENT_INSTALL_MESSAGE,
    SkillHumanOnlyError,
    agent_tool_running,
)

pytestmark = pytest.mark.usefixtures("user_terminal")


class _NoHub(Exception):
    """Raised by the stub fetcher: the install got past the user check."""


def _no_hub(*args, **kwargs):
    raise _NoHub()


@pytest.fixture
def agents_root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / ".gaia" / "agents"
    root.mkdir(parents=True)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("GAIA_HOME", str(home / ".gaia"))
    return root


@pytest.fixture
def bundle(tmp_path):
    path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "bundle.json", json.dumps({"format_version": 1, "agent_ids": ["x"]})
        )
        zf.writestr("x/agent.py", "print('hi')\n")
    return path


def _calls(agents_root: Path, bundle: Path):
    return {
        "hub install": lambda: installer.install(
            "some-agent", fetcher=_no_hub, install_root=agents_root
        ),
        "bundle import": lambda: import_agent_bundle(bundle),
    }


@pytest.mark.parametrize("action", ["hub install", "bundle import"])
@pytest.mark.parametrize("where", ["agent child", "agent tool"])
def test_an_agent_cannot_install_agent_code(
    agents_root, bundle, monkeypatch, action, where
):
    call = _calls(agents_root, bundle)[action]

    if where == "agent child":
        monkeypatch.setenv(AGENT_CHILD_ENV, "1")
        with pytest.raises(SkillHumanOnlyError, match=AGENT_INSTALL_MESSAGE):
            call()
    else:
        with (
            agent_tool_running(),
            pytest.raises(SkillHumanOnlyError, match=AGENT_INSTALL_MESSAGE),
        ):
            call()

    assert list(agents_root.iterdir()) == []


def test_the_user_terminal_still_reaches_the_hub(agents_root, bundle):
    with pytest.raises(_NoHub):
        _calls(agents_root, bundle)["hub install"]()


def test_the_user_terminal_still_imports_a_bundle(agents_root, bundle):
    result = import_agent_bundle(bundle)

    assert result.imported == ["x"]
    assert (agents_root / "x" / "agent.py").is_file()


@pytest.mark.parametrize(
    "code",
    [
        "from gaia.hub.installer import install\ninstall('x')",
        "from gaia.hub import installer\ninstaller.install('x')",
        "import gaia.hub.installer as i\ni.install('x')",
        "import importlib\nimportlib.import_module('gaia.hub.installer').install('x')",
        "from gaia.installer.export_import import import_agent_bundle\n"
        "import_agent_bundle('b.zip')",
        "from gaia.installer import export_import\n"
        "export_import.import_agent_bundle('b.zip')",
    ],
)
def test_python_that_installs_agent_code_is_refused_early(code, tmp_path):
    assert human_only_in_python(code, str(tmp_path))


@pytest.mark.parametrize(
    "code",
    [
        "from gaia.installer import model_choice",
        "from gaia.hub import catalog",
        "import gaia.hub.installer_notes",
    ],
)
def test_python_that_only_reads_installer_helpers_still_runs(code, tmp_path):
    assert human_only_in_python(code, str(tmp_path)) == ""


def test_a_daemon_an_agent_shell_started_still_installs_for_the_user(
    agents_root, monkeypatch
):
    """The daemon is GAIA itself; the agent-child marker must not reach it."""
    monkeypatch.setenv(AGENT_CHILD_ENV, "1")
    monkeypatch.setenv("PYTHONPATH", str(Path(gaia.__file__).resolve().parents[1]))

    env = _daemon_env()

    assert AGENT_CHILD_ENV not in env
    assert env["GAIA_HOME"] == str(agents_root.parent)
    seen = subprocess.run(
        [
            sys.executable,
            "-c",
            "from gaia.skills.human_only import started_by_agent as s; print(s())",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout.strip()
    assert seen == "False"


def test_the_agent_shell_itself_still_cannot_install(agents_root, monkeypatch):
    """Dropping the marker for the daemon leaves the agent's own process refused."""
    monkeypatch.setenv(AGENT_CHILD_ENV, "1")
    _daemon_env()

    with pytest.raises(SkillHumanOnlyError, match=AGENT_INSTALL_MESSAGE):
        installer.install("some-agent", fetcher=_no_hub, install_root=agents_root)
