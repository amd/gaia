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


def _args(agent_id="email", version=None, trusted=False):
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
    cli.handle_agent_install(_args("email", version="0.5.0", trusted=True))

    assert captured == {"agent_id": "email", "version": "0.5.0", "trusted": True}
    out = capsys.readouterr().out
    assert "✅ Installed 'email' v0.5.0" in out
    # email is a daemon sidecar spec, so the start hint must appear.
    assert "gaia daemon start-agent email" in out


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
        cli.handle_agent_install(_args("email"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Could not install 'email'" in err
    assert "checksum mismatch" in err


def test_install_unknown_id_suggests_agent_list(monkeypatch, capsys):
    # A typo'd id 404s on the manifest fetch (a plain requests error, not an
    # InstallError) — the generic branch must point the user at `gaia agent list`.
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
    assert "gaia agent list" in err


def test_install_trust_required_names_the_flag(monkeypatch, capsys):
    def _needs_trust(agent_id, *, version=None, trusted=False):
        raise installer.TrustRequiredError("'x' is a native agent in 'experimental'")

    monkeypatch.setattr(installer, "install", _needs_trust)
    with pytest.raises(SystemExit) as exc:
        cli.handle_agent_install(_args("x"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "--trust" in err


# ---------------------------------------------------------------------------
# gaia agent list
# ---------------------------------------------------------------------------


class _CatalogResult:
    def __init__(self, agents, offline=False):
        self.agents = agents
        self.offline = offline


class _Installed:
    def __init__(self, version):
        self.version = version


def _patch_list(monkeypatch, *, agents, installed, offline=False, raise_exc=None):
    from gaia.hub import catalog
    from gaia.hub import installer as inst

    monkeypatch.setattr(inst, "list_installed", lambda *a, **k: installed)
    if raise_exc is not None:

        def _boom(*a, **k):
            raise raise_exc

        monkeypatch.setattr(catalog, "load_index", _boom)
    else:
        monkeypatch.setattr(
            catalog, "load_index", lambda *a, **k: _CatalogResult(agents, offline)
        )


def test_list_shows_catalog_with_installed_marker(monkeypatch, capsys):
    _patch_list(
        monkeypatch,
        agents=[
            {"id": "email", "latest_version": "0.5.0"},
            {"id": "summarize", "latest_version": "1.2.0"},
        ],
        installed={"email": _Installed("0.5.0")},
    )
    cli.handle_agent_list(Namespace(agent_action="list"))
    out = capsys.readouterr().out
    assert "email" in out and "0.5.0" in out
    assert "[installed]" in out  # email is marked installed
    assert "summarize" in out
    assert "gaia agent install <id>" in out


def test_list_degrades_loudly_when_hub_unreachable(monkeypatch, capsys):
    from gaia.hub import catalog

    _patch_list(
        monkeypatch,
        agents=[],
        installed={"email": _Installed("0.5.0")},
        raise_exc=catalog.CatalogError("hub down"),
    )
    cli.handle_agent_list(Namespace(agent_action="list"))
    captured = capsys.readouterr()
    # Loud note on stderr, not a silent empty result.
    assert "Could not reach the Agent Hub catalog" in captured.err
    # Still lists what IS installed.
    assert "email" in captured.out
    assert "Installed (not in the hub catalog)" in captured.out


def test_list_empty_state_is_actionable(monkeypatch, capsys):
    from gaia.hub import catalog

    _patch_list(
        monkeypatch,
        agents=[],
        installed={},
        raise_exc=catalog.CatalogError("hub down"),
    )
    cli.handle_agent_list(Namespace(agent_action="list"))
    out = capsys.readouterr().out
    assert "No agents installed" in out
