# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""On Windows, gh run as argv (no shell) must still reach the stand-in, not real gh."""

import json
import os
import subprocess
import sys

import pytest

from gaia.eval.bench import ghstub

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="CreateProcess resolves only gh.exe"
)


@pytest.fixture
def agent_env(tmp_path, monkeypatch):
    """This process's environment made the agent's: CreateProcess searches OUR PATH."""
    sandbox = ghstub.install(tmp_path / "harness")
    for name in [k for k in os.environ if k.startswith("GH_")]:
        monkeypatch.delenv(name)
    for name, value in sandbox.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(sandbox.bin_dir), os.environ["PATH"]])
    )


def _gh(*args):
    # argv, no shell: how the flagship runs a skill-granted CLI.
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


@pytest.mark.usefixtures("agent_env")
def test_gh_as_argv_runs_the_stand_in():
    proc = _gh("--version")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ghstub.VERSION


@pytest.mark.usefixtures("agent_env")
def test_gh_as_argv_serves_the_fixture_issues_and_passes_arguments():
    proc = _gh("issue", "list", "--repo", "kovtcharov/toybox", "--json", "number")
    assert proc.returncode == 0, proc.stderr
    assert [i["number"] for i in json.loads(proc.stdout)] == [5, 4, 3, 2, 1]


def test_a_missing_launcher_template_fails_loudly(tmp_path, monkeypatch):
    import ensurepip

    # No pip in the environment (a uv venv) and no wheel bundled with ensurepip.
    monkeypatch.setitem(sys.modules, "pip._vendor.distlib.scripts", None)
    monkeypatch.setattr(ensurepip, "__file__", str(tmp_path / "ensurepip.py"))
    with pytest.raises(FileNotFoundError, match="gh.exe cannot be written"):
        ghstub.install(tmp_path / "harness")
