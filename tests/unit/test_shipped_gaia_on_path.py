# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent the desktop app starts can run the ``gaia`` the app ships (#7968).

The Windows app ships ``gaia.exe`` in ``resources/core`` and puts nothing on the
user's PATH, so the automations skill refused to load in every chat.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from gaia import env as gaia_env
from gaia.skills.binaries import resolve_binary_policies
from gaia.skills.errors import SkillPermissionError
from gaia.skills.format import parse_skill_file

AUTOMATIONS = (
    Path(__file__).resolve().parents[2] / "hub" / "skills" / "automations" / "SKILL.md"
)
GAIA_NAME = "gaia.exe" if sys.platform == "win32" else "gaia"


def _app(tmp_path: Path) -> Path:
    """A packaged app's layout: ``resources/core/gaia(.exe)`` beside ``_internal``."""
    core = tmp_path / "GAIA" / "resources" / "core"
    (core / "_internal").mkdir(parents=True)
    exe = core / GAIA_NAME
    exe.write_bytes(b"")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def frozen_app(tmp_path, monkeypatch):
    exe = _app(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


def _user_path(tmp_path: Path) -> str:
    """A PATH with no ``gaia`` on it, as on a PC with only the desktop app."""
    bin_dir = tmp_path / "user-bin"
    bin_dir.mkdir(exist_ok=True)
    return str(bin_dir)


def test_puts_the_shipped_core_first(frozen_app, tmp_path):
    env = {"PATH": _user_path(tmp_path)}
    gaia_env.expose_shipped_gaia(env)
    assert env["PATH"].split(os.pathsep) == [
        str(frozen_app.parent),
        _user_path(tmp_path),
    ]
    found = shutil.which("gaia", path=env["PATH"])
    assert found and Path(found).resolve() == frozen_app.resolve()


def test_keeps_windows_spelling_of_path(frozen_app, tmp_path):
    env = {"Path": _user_path(tmp_path)}
    gaia_env.expose_shipped_gaia(env)
    assert list(env) == ["Path"]
    assert env["Path"].split(os.pathsep)[0] == str(frozen_app.parent)


def test_moves_the_core_to_the_front_once(frozen_app, tmp_path):
    core = str(frozen_app.parent)
    env = {"PATH": os.pathsep.join([_user_path(tmp_path), core])}
    gaia_env.expose_shipped_gaia(env)
    gaia_env.expose_shipped_gaia(env)
    assert env["PATH"].split(os.pathsep) == [core, _user_path(tmp_path)]


def test_an_empty_path_gets_only_the_core(frozen_app):
    env: dict = {}
    gaia_env.expose_shipped_gaia(env)
    assert env == {"PATH": str(frozen_app.parent)}


def test_untouched_when_not_frozen(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(_app(tmp_path)))
    env = {"PATH": _user_path(tmp_path)}
    gaia_env.expose_shipped_gaia(env)
    assert env == {"PATH": _user_path(tmp_path)}


def test_untouched_when_no_gaia_sits_beside_the_executable(tmp_path, monkeypatch):
    other = tmp_path / "agent" / "gaia-agent.exe"
    other.parent.mkdir()
    other.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(other))
    env = {"PATH": _user_path(tmp_path)}
    gaia_env.expose_shipped_gaia(env)
    assert env == {"PATH": _user_path(tmp_path)}


def test_the_spawned_agent_gets_the_core_on_path(frozen_app, tmp_path, monkeypatch):
    """The daemon (frozen ``gaia``) starts the chat agent with core on PATH."""
    from gaia.daemon.sidecars import manager as mgr
    from gaia.daemon.sidecars.errors import SidecarSpawnError
    from gaia.daemon.sidecars.spec import builtin_specs

    calls = []

    def fake_popen(*_args, **kwargs):
        calls.append(kwargs)
        raise OSError("spawn captured")

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setenv("PATH", _user_path(tmp_path))
    # Dev mode delivers the secret by file, so no installed binary is needed.
    monkeypatch.setenv("GAIA_EMAIL_AGENT_MODE", "dev")
    m = mgr.AgentSidecarManager(
        builtin_specs()["email"], cache_dir=tmp_path, log_dir=tmp_path / "logs"
    )
    with pytest.raises(SidecarSpawnError):
        m._spawn_process(["sidecar"], {}, 9127)

    path = next(v for k, v in calls[0]["env"].items() if k.upper() == "PATH")
    assert path.split(os.pathsep)[0] == str(frozen_app.parent)
    found = shutil.which("gaia", path=path)
    assert found and Path(found).resolve() == frozen_app.resolve()


def _automations_permissions():
    return parse_skill_file(AUTOMATIONS).parsed_permissions()


@pytest.mark.usefixtures("frozen_app")
def test_automations_loads_with_the_shipped_core(tmp_path, monkeypatch):
    agent_env = gaia_env.expose_shipped_gaia({"PATH": _user_path(tmp_path)})
    monkeypatch.setenv("PATH", agent_env["PATH"])
    policies = resolve_binary_policies(
        _automations_permissions(), skill_name="automations"
    )
    assert [p.binary for p in policies] == ["gaia"]


def test_automations_refuses_honestly_without_gaia(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("PATH", _user_path(tmp_path))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SkillPermissionError) as excinfo:
        resolve_binary_policies(_automations_permissions(), skill_name="automations")
    message = str(excinfo.value)
    assert "needs the 'gaia' command" in message
    assert "cannot do what they asked" in message
    assert "saving a memory" in message
