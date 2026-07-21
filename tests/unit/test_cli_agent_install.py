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

    def __init__(self, agent_id="email", version="0.5.0"):
        self.id = agent_id
        self.version = version
        self.path = Path.home() / ".gaia" / "agents" / agent_id
        self.language = "python"
        self.updated = False
        self.hot_registered = False


def _args(agent_id="email", version=None, trust_native=False):
    return Namespace(
        agent_action="install",
        agent_id=agent_id,
        version=version,
        trust_native=trust_native,
    )


def test_install_success_forwards_args_and_prints_next_step(monkeypatch, capsys):
    captured = {}

    def _fake_install(agent_id, *, version=None, trust_native=False):
        captured["agent_id"] = agent_id
        captured["version"] = version
        captured["trust_native"] = trust_native
        return _InstallResult(agent_id=agent_id, version=version or "0.5.0")

    monkeypatch.setattr(installer, "install", _fake_install)
    cli.handle_agent_install(_args("email", version="0.5.0", trust_native=True))

    assert captured == {"agent_id": "email", "version": "0.5.0", "trust_native": True}
    out = capsys.readouterr().out
    assert "✅ Installed 'email' v0.5.0" in out
    # email is a daemon sidecar spec, so the start hint must appear.
    assert "gaia daemon start-agent email" in out


def test_install_non_sidecar_agent_omits_daemon_hint(monkeypatch, capsys):
    monkeypatch.setattr(
        installer,
        "install",
        lambda agent_id, *, version=None, trust_native=False: _InstallResult(
            agent_id="some-tool", version="1.0.0"
        ),
    )
    cli.handle_agent_install(_args("some-tool"))
    out = capsys.readouterr().out
    assert "✅ Installed 'some-tool' v1.0.0" in out
    assert "daemon start-agent" not in out  # not a registered sidecar


def test_install_error_exits_nonzero_and_is_loud(monkeypatch, capsys):
    def _boom(agent_id, *, version=None, trust_native=False):
        raise installer.ChecksumError("artifact checksum mismatch for X")

    monkeypatch.setattr(installer, "install", _boom)
    with pytest.raises(SystemExit) as exc:
        cli.handle_agent_install(_args("email"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Could not install 'email'" in err
    assert "checksum mismatch" in err


def test_install_trust_required_names_the_flag(monkeypatch, capsys):
    def _needs_trust(agent_id, *, version=None, trust_native=False):
        raise installer.TrustRequiredError("'x' is a native agent in 'experimental'")

    monkeypatch.setattr(installer, "install", _needs_trust)
    with pytest.raises(SystemExit) as exc:
        cli.handle_agent_install(_args("x"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "--trust-native" in err
