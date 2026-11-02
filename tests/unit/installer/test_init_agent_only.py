# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia init --agent-only`: the desktop app's agent refresh after an update.

It must reach the hub install and nothing else, and every way the agent can
stay out of date must exit non-zero with the cause as stderr's last line.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia import cli
from gaia.hub import catalog as hub_catalog
from gaia.hub import installer as hub_installer
from gaia.hub import prerelease
from gaia.installer import init_command
from gaia.installer.init_command import InitCommand, run_agent_refresh


def installed(sha="old"):
    return hub_installer.InstalledAgent(
        id="gaia",
        version="0.25.3",
        path=Path("/nowhere"),
        installed_at="2026-01-01T00:00:00Z",
        language="binary",
        artifact_sha256=sha,
    )


@pytest.fixture
def hub(monkeypatch):
    """A final-release hub that publishes `new`, with `old` installed."""
    monkeypatch.setattr(prerelease, "flagship_source", lambda *a, **k: None)
    monkeypatch.setattr(InitCommand, "_is_hub_agent_available", lambda *a: True)
    monkeypatch.setattr(hub_installer, "read_sentinel", lambda *a: installed())
    monkeypatch.setattr(
        hub_installer, "published_artifact_sha256", lambda *a, **k: "new"
    )
    monkeypatch.setattr(InitCommand, "_restart_replaced_agent", lambda *a: None)
    index = MagicMock(agents=[{"id": "gaia"}])
    monkeypatch.setattr(hub_catalog, "load_index", lambda: index)
    install = MagicMock(return_value=MagicMock(path="not-a-real-path"))
    monkeypatch.setattr(hub_installer, "install", install)
    return install


def test_a_stale_agent_is_replaced_and_no_setup_step_runs(hub, monkeypatch):
    for step in ("_choose_lemonade", "_ensure_lemonade_ready", "_download_models"):
        monkeypatch.setattr(
            InitCommand, step, MagicMock(side_effect=AssertionError(step))
        )
    assert run_agent_refresh() == 0
    hub.assert_called_once_with("gaia", trusted=True)


def test_a_current_agent_is_left_alone(hub, monkeypatch):
    monkeypatch.setattr(
        hub_installer, "published_artifact_sha256", lambda *a, **k: "old"
    )
    assert run_agent_refresh() == 0
    hub.assert_not_called()


def test_a_machine_with_no_build_is_not_a_failure(hub):
    hub.side_effect = hub_installer.UnsupportedPlatformError("no build for arm64")
    assert run_agent_refresh() == 0


@pytest.mark.parametrize(
    "broken, cause",
    [
        ("published_artifact_sha256", "Could not compare"),
        ("load_index", "Could not reach"),
        ("not_listed", "does not list"),
        ("install", "disk full"),
    ],
)
def test_every_failure_exits_nonzero_with_its_cause_last(
    hub, monkeypatch, capsys, broken, cause
):
    boom = hub_installer.InstallError("disk full")
    if broken == "published_artifact_sha256":
        monkeypatch.setattr(
            hub_installer,
            broken,
            MagicMock(side_effect=hub_installer.InstallError("x")),
        )
    elif broken == "load_index":
        monkeypatch.setattr(hub_catalog, broken, MagicMock(side_effect=OSError("down")))
    elif broken == "not_listed":
        monkeypatch.setattr(hub_catalog, "load_index", lambda: MagicMock(agents=[]))
    else:
        hub.side_effect = boom

    assert run_agent_refresh() == 1

    last = capsys.readouterr().err.strip().splitlines()[-1]
    assert cause in last
    assert "gaia init" not in last


def test_a_broken_release_candidate_fails_loudly(hub, monkeypatch, capsys):
    monkeypatch.setattr(
        prerelease,
        "flagship_source",
        MagicMock(side_effect=prerelease.PrereleaseFlagshipError("bad manifest")),
    )
    assert run_agent_refresh() == 1
    assert "bad manifest" in capsys.readouterr().err


def run_cli(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["gaia", "init", *argv])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    return exit_info.value.code


def test_the_flag_reaches_the_refresh_and_carries_its_exit_code(monkeypatch):
    monkeypatch.setattr(init_command, "run_agent_refresh", lambda: 7)
    monkeypatch.setattr(init_command, "run_init", MagicMock(side_effect=AssertionError))
    assert run_cli(monkeypatch, "--agent-only") == 7


@pytest.mark.parametrize("extra", [("--minimal",), ("--check",), ("-p", "npu")])
def test_the_flag_refuses_options_it_would_ignore(monkeypatch, capsys, extra):
    assert run_cli(monkeypatch, "--agent-only", *extra) == 2
    assert "--agent-only" in capsys.readouterr().err
