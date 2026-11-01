# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia agent version`` moves every version file a package has, or fails (#4075).

A hub agent's version lives in the manifest, ``pyproject.toml``, the Python
package, npm ``package.json`` and npm ``package-lock.json``. The bump used to
move only the first three and ignore whether those edits landed, so the email
agent shipped 0.6.0 with a 0.5.0 lockfile. These tests run the command against
copies of the REAL hub packages and their real stampers.
"""

import json
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from gaia import cli_agent

REPO = Path(__file__).resolve().parents[2]

# Per agent: the Python package files that carry its version.
HUB_AGENTS = {
    "email": ["gaia_agent_email/__init__.py", "gaia_agent_email/version.py"],
    "gaia": ["gaia_agent/__init__.py"],
}


def _version_args(bump, path):
    return Namespace(agent_action="version", bump=bump, path=str(path))


def _copy_hub_agent(agent_id: str, root: Path) -> Path:
    """Copy just the version-bearing files of a real hub agent under ``root``."""
    src = REPO / "hub" / "agents" / agent_id
    dst = root / "hub" / "agents" / agent_id
    files = [
        "python/gaia-agent.yaml",
        "python/pyproject.toml",
        "python/packaging/stamp_version.py",
        *(f"python/{f}" for f in HUB_AGENTS[agent_id]),
        *(
            str(p.relative_to(src)).replace("\\", "/")
            for p in (src / "npm").iterdir()
            if p.is_file()
        ),
    ]
    if (src / "npm" / "assets" / "architecture.html").exists():
        files.append("npm/assets/architecture.html")
    for rel in files:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dst / rel)
    return dst / "python"


def _versions(pkg: Path, agent_id: str) -> dict:
    npm = pkg.parent / "npm"
    lock = json.loads((npm / "package-lock.json").read_text(encoding="utf-8"))
    binaries = json.loads((npm / "binaries.lock.json").read_text(encoding="utf-8"))
    source = (pkg / HUB_AGENTS[agent_id][-1]).read_text(encoding="utf-8")
    pyproject = (pkg / "pyproject.toml").read_text(encoding="utf-8")
    return {
        "gaia-agent.yaml": yaml.safe_load(
            (pkg / "gaia-agent.yaml").read_text(encoding="utf-8")
        )["version"],
        "pyproject.toml": cli_agent._PYPROJECT_VERSION_RE.search(pyproject)
        .group(0)
        .split('"')[1],
        "python": cli_agent._PY_VERSION_RE.search(source).group(0).split('"')[1],
        "package.json": json.loads((npm / "package.json").read_text(encoding="utf-8"))[
            "version"
        ],
        "package-lock.json": lock["version"],
        'package-lock.json packages[""]': lock["packages"][""]["version"],
        "binaries.lock.json": binaries["agentVersion"],
    }


def _stamper_check(pkg: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(pkg / "packaging" / "stamp_version.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("agent_id", sorted(HUB_AGENTS))
def test_real_hub_agents_are_in_sync_on_this_checkout(agent_id):
    """The committed tree itself: every version file of the agent agrees."""
    pkg = REPO / "hub" / "agents" / agent_id / "python"
    values = _versions(pkg, agent_id)
    assert len(set(values.values())) == 1, values


@pytest.mark.parametrize("agent_id", sorted(HUB_AGENTS))
def test_bump_moves_every_version_file(tmp_path, agent_id):
    pkg = _copy_hub_agent(agent_id, tmp_path)
    before = _versions(pkg, agent_id)["gaia-agent.yaml"]
    major, minor, _ = (int(x) for x in before.split("."))

    cli_agent.cmd_version(_version_args("minor", pkg))

    after = _versions(pkg, agent_id)
    assert after == dict.fromkeys(after, f"{major}.{minor + 1}.0")
    assert _stamper_check(pkg).returncode == 0


def test_partial_bump_with_stale_lockfile_is_caught(tmp_path):
    """The real 0.5.0 -> 0.6.0 history: every file moved except the lockfile."""
    pkg = _copy_hub_agent("email", tmp_path)
    lock_path = pkg.parent / "npm" / "package-lock.json"
    current = _versions(pkg, "email")["gaia-agent.yaml"]
    lock_path.write_text(
        lock_path.read_text(encoding="utf-8").replace(
            f'"version": "{current}"', '"version": "0.5.0"', 2
        ),
        encoding="utf-8",
    )

    run = _stamper_check(pkg)
    assert run.returncode != 0
    assert "package-lock.json" in run.stdout
    assert "0.5.0" in run.stdout

    # A bump from that state still lands every file on one version.
    cli_agent.cmd_version(_version_args("patch", pkg))
    assert len(set(_versions(pkg, "email").values())) == 1


def test_npm_package_without_a_stamper_fails_before_writing(tmp_path):
    pkg = _copy_hub_agent("gaia", tmp_path)
    (pkg / "packaging" / "stamp_version.py").unlink()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    with pytest.raises(cli_agent.AgentWorkflowError) as err:
        cli_agent.cmd_version(_version_args("patch", pkg))

    assert "package-lock.json" in str(err.value)
    assert "Nothing was changed" in str(err.value)
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_stamper_failure_is_raised_with_its_output(tmp_path):
    pkg = _copy_hub_agent("gaia", tmp_path)
    (pkg.parent / "npm" / "package-lock.json").unlink()

    with pytest.raises(cli_agent.AgentWorkflowError) as err:
        cli_agent.cmd_version(_version_args("patch", pkg))

    message = str(err.value)
    assert "stamp_version.py" in message
    assert "package-lock.json" in message
    assert "file absent" in message


# --- packages outside the hub tree (no npm, no stamper) ---------------------


def _plain_package(root: Path, init_py: str, pyproject: str) -> Path:
    pkg = root / "demo"
    (pkg / "gaia_agent_demo").mkdir(parents=True)
    (pkg / "gaia-agent.yaml").write_text("id: demo\nversion: 1.0.0\n", "utf-8")
    (pkg / "pyproject.toml").write_text(pyproject, "utf-8")
    (pkg / "gaia_agent_demo" / "__init__.py").write_text(init_py, "utf-8")
    return pkg


STATIC_PYPROJECT = '[project]\nname = "demo"\nversion = "1.0.0"\n'


@pytest.mark.parametrize(
    "init_py",
    [
        "from importlib.metadata import version\n__version__ = version('demo')\n",
        "__version__ = '1.0.0'\n",  # single quotes: not a literal we rewrite
    ],
)
def test_unmovable_python_version_fails_before_writing(tmp_path, init_py):
    pkg = _plain_package(tmp_path, init_py, STATIC_PYPROJECT)
    with pytest.raises(cli_agent.AgentWorkflowError, match="__init__.py"):
        cli_agent.cmd_version(_version_args("patch", pkg))
    assert "version: 1.0.0" in (pkg / "gaia-agent.yaml").read_text("utf-8")
    assert 'version = "1.0.0"' in (pkg / "pyproject.toml").read_text("utf-8")


def test_pyproject_without_a_version_fails(tmp_path):
    pkg = _plain_package(
        tmp_path, '__version__ = "1.0.0"\n', '[project]\nname = "demo"\n'
    )
    with pytest.raises(cli_agent.AgentWorkflowError, match="pyproject.toml"):
        cli_agent.cmd_version(_version_args("patch", pkg))
    assert "version: 1.0.0" in (pkg / "gaia-agent.yaml").read_text("utf-8")


def test_dynamic_pyproject_version_is_not_a_declaration(tmp_path):
    pyproject = '[project]\nname = "demo"\ndynamic = ["version"]\n'
    pkg = _plain_package(tmp_path, '__version__ = "1.0.0"\n', pyproject)
    cli_agent.cmd_version(_version_args("patch", pkg))
    assert (pkg / "pyproject.toml").read_text("utf-8") == pyproject
    init_py = (pkg / "gaia_agent_demo" / "__init__.py").read_text("utf-8")
    assert init_py == '__version__ = "1.0.1"\n'


def test_package_without_a_version_declaration_is_fine(tmp_path):
    pkg = _plain_package(tmp_path, '"""No version here."""\n', STATIC_PYPROJECT)
    cli_agent.cmd_version(_version_args("patch", pkg))
    assert 'version = "1.0.1"' in (pkg / "pyproject.toml").read_text("utf-8")


def test_version_py_and_init_literals_both_move(tmp_path):
    pkg = _plain_package(tmp_path, '__version__ = "1.0.0"\n', STATIC_PYPROJECT)
    (pkg / "gaia_agent_demo" / "version.py").write_text(
        'AGENT_VERSION = "1.0.0"\nAPI_VERSION = "3.0.0"\n', "utf-8"
    )
    cli_agent.cmd_version(_version_args("major", pkg))
    version_py = (pkg / "gaia_agent_demo" / "version.py").read_text("utf-8")
    assert version_py == 'AGENT_VERSION = "2.0.0"\nAPI_VERSION = "3.0.0"\n'
    init_py = (pkg / "gaia_agent_demo" / "__init__.py").read_text("utf-8")
    assert init_py == '__version__ = "2.0.0"\n'


def test_tests_package_is_never_rewritten(tmp_path):
    pkg = _plain_package(tmp_path, '__version__ = "1.0.0"\n', STATIC_PYPROJECT)
    (pkg / "tests").mkdir()
    (pkg / "tests" / "__init__.py").write_text('__version__ = "9.9.9"\n', "utf-8")
    cli_agent.cmd_version(_version_args("patch", pkg))
    assert (pkg / "tests" / "__init__.py").read_text("utf-8") == (
        '__version__ = "9.9.9"\n'
    )
