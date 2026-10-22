# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia diagnostics`` bundles the logs a bug report needs, and no secrets."""

import argparse
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

LEMONADE_KEY = "lemonade-key-must-not-ship"
DAEMON_TOKEN = "daemon-token-must-not-ship"


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A GAIA home with a daemon, embedded Lemonade, sidecar and UI backend."""
    gaia_dir = tmp_path / ".gaia"
    daemon_dir = tmp_path / "daemon-home"
    for d in (gaia_dir / "lemonade", gaia_dir / "agents/gaia/logs", daemon_dir):
        d.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("GAIA_HOME", str(gaia_dir))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(daemon_dir))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)

    (gaia_dir / "gaia.log").write_text("gaia\n", encoding="utf-8")
    (gaia_dir / "electron-updater.log").write_text("updater\n", encoding="utf-8")
    (daemon_dir / "daemon.log").write_text("daemon\n", encoding="utf-8")
    (daemon_dir / "daemon.log.1").write_text("daemon older\n", encoding="utf-8")
    (daemon_dir / "instance.json").write_text(
        json.dumps({"pid": os.getpid(), "port": 51001, "token": DAEMON_TOKEN}),
        encoding="utf-8",
    )
    (gaia_dir / "lemonade" / "lemond.log").write_text("lemond\n", encoding="utf-8")
    (gaia_dir / "lemonade" / "state.json").write_text(
        json.dumps({"pid": os.getpid(), "port": 51002, "api_key": LEMONADE_KEY}),
        encoding="utf-8",
    )
    sidecar = gaia_dir / "agents" / "gaia" / "logs" / "sidecar-51003.log"
    sidecar.write_text("sidecar\n", encoding="utf-8")
    (gaia_dir / "backend.pid").write_text("4242\n", encoding="utf-8")

    calls = []

    def fake_run(cmd, **_):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    from gaia import cli

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return tmp_path, calls


def _bundle(tmp_path, no_logs=False):
    from gaia import cli

    out = tmp_path / "bundle.tgz"
    cli.handle_diagnostics_command(argparse.Namespace(output=str(out), no_logs=no_logs))
    with tarfile.open(out) as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


def test_bundles_daemon_lemonade_and_sidecar_logs(install):
    tmp_path, _ = install
    members = _bundle(tmp_path)

    assert {
        "gaia.log",
        "electron-updater.log",
        "host/daemon.log",
        "host/daemon.log.1",
        "lemonade/lemond.log",
        "agents/gaia/logs/sidecar-51003.log",
    } <= set(members)
    assert members["lemonade/lemond.log"] == b"lemond\n"


def test_no_secrets_and_no_state_files(install):
    tmp_path, _ = install
    members = _bundle(tmp_path)

    assert not any(name.endswith(("state.json", "instance.json")) for name in members)
    for blob in members.values():
        assert LEMONADE_KEY.encode() not in blob
        assert DAEMON_TOKEN.encode() not in blob


def test_no_logs_omits_every_log(install):
    tmp_path, _ = install
    members = _bundle(tmp_path, no_logs=True)

    assert not [name for name in members if ".log" in name]
    assert "LOGS-OMITTED.txt" in members


def test_macos_uses_macos_tools_and_gaias_real_ports(install, monkeypatch):
    tmp_path, calls = install
    monkeypatch.setattr(sys, "platform", "darwin")
    info = _bundle(tmp_path)["system-info.txt"].decode()

    tools = {cmd[0] for cmd in calls}
    assert "lsb_release" not in tools and "ss" not in tools
    assert ["sw_vers"] in calls
    assert ["lsof", "-nP", "-iTCP:51001", "-sTCP:LISTEN"] in calls
    assert ["lsof", "-nP", "-iTCP:51002", "-sTCP:LISTEN"] in calls
    assert ["lsof", "-nP", "-a", "-p", "4242", "-iTCP", "-sTCP:LISTEN"] in calls
    assert not any("4200" in " ".join(cmd) for cmd in calls)
    assert "daemon: pid=" in info and "port=51001" in info
    assert "lemonade: pid=" in info and "port=51002" in info
    assert "ui backend: pid=4242" in info


def test_linux_keeps_its_tools(install, monkeypatch):
    tmp_path, calls = install
    monkeypatch.setattr(sys, "platform", "linux")
    _bundle(tmp_path)

    assert ["lsb_release", "-a"] in calls
    assert ["ss", "-tlnp"] in calls
    assert ["sw_vers"] not in calls


def test_windows_skips_posix_tools(install, monkeypatch):
    tmp_path, calls = install
    monkeypatch.setattr(sys, "platform", "win32")
    _bundle(tmp_path)

    tools = {cmd[0] for cmd in calls}
    assert tools == {"cmd", "netstat"}
