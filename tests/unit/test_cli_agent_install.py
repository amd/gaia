# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for `gaia agent install <id>` (issue #2347).

``handle_agent_install`` (src/gaia/cli.py) is the headless equivalent of the
Agent UI's Install button — the remedy the user-mode sidecar error now names.
It is a thin wrapper over ``gaia.hub.installer.install``; these tests mock the
installer and assert the CLI wiring: it forwards the id/version/trust flag,
prints an actionable success line (plus the daemon start hint for sidecar
agents), and exits non-zero with a loud message on failure.
"""

from argparse import Namespace
from pathlib import Path

import pytest

from gaia import cli
from gaia.hub import installer


class _InstallResult:
    """Stand-in for ``gaia.hub.installer.InstallResult``."""

    def __init__(self, agent_id="gaia", version="0.5.0"):
        self.id = agent_id
        self.version = version
        self.path = Path.home() / ".gaia" / "agents" / agent_id
        self.language = "python"
        self.updated = False
        self.hot_registered = False


def _args(agent_id="gaia", version=None, trusted=False):
    return Namespace(
        agent_action="install",
        agent_id=agent_id,
        version=version,
        trusted=trusted,
    )


def test_install_success_forwards_args_and_prints_next_step(monkeypatch, capsys):
    captured = {}

    def _fake_install(agent_id, *, version=None, trusted=False):
        captured["agent_id"] = agent_id
        captured["version"] = version
        captured["trusted"] = trusted
        return _InstallResult(agent_id=agent_id, version=version or "0.5.0")

    monkeypatch.setattr(installer, "install", _fake_install)
    cli.handle_agent_install(_args("gaia", version="0.5.0", trusted=True))

    assert captured == {"agent_id": "gaia", "version": "0.5.0", "trusted": True}
    out = capsys.readouterr().out
    assert "✅ Installed 'gaia' v0.5.0" in out
    # gaia is a daemon sidecar spec, so the start hint must appear.
    assert "gaia daemon start-agent gaia" in out


def test_install_non_sidecar_agent_omits_daemon_hint(monkeypatch, capsys):
    monkeypatch.setattr(
        installer,
        "install",
        lambda agent_id, *, version=None, trusted=False: _InstallResult(
            agent_id="some-tool", version="1.0.0"
        ),
    )
    cli.handle_agent_install(_args("some-tool"))
    out = capsys.readouterr().out
    assert "✅ Installed 'some-tool' v1.0.0" in out
    assert "daemon start-agent" not in out  # not a registered sidecar


def test_install_error_exits_nonzero_and_is_loud(monkeypatch, capsys):
    def _boom(agent_id, *, version=None, trusted=False):
        raise installer.ChecksumError("artifact checksum mismatch for X")

    monkeypatch.setattr(installer, "install", _boom)
    with pytest.raises(SystemExit) as exc:
        cli.handle_agent_install(_args("gaia"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Could not install 'gaia'" in err
    assert "checksum mismatch" in err


def test_install_unknown_id_names_no_removed_command(monkeypatch, capsys):
    # A typo'd id 404s on the manifest fetch (a plain requests error, not an
    # InstallError); the error must not send the user to `gaia agent list`.
    def _boom(agent_id, *, version=None, trusted=False):
        raise RuntimeError(
            "404 Client Error: Not Found for url: .../emial/manifest.json"
        )

    monkeypatch.setattr(installer, "install", _boom)
    with pytest.raises(SystemExit) as exc:
        cli.handle_agent_install(_args("emial"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Could not install 'emial'" in err
    assert "agent list" not in err


def test_agent_list_is_not_a_command(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["agent", "list"])
    assert exc.value.code == 2
    assert "invalid choice: 'list'" in capsys.readouterr().err


def test_install_trust_required_names_the_flag(monkeypatch, capsys):
    def _needs_trust(agent_id, *, version=None, trusted=False):
        raise installer.TrustRequiredError("'x' is a native agent in 'experimental'")

    monkeypatch.setattr(installer, "install", _needs_trust)
    with pytest.raises(SystemExit) as exc:
        cli.handle_agent_install(_args("x"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "--trust" in err
