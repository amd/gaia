"""`gaia mcp` subcommands must exit non-zero when they fail.

Regression tests for issue #4781: the `gaia mcp` failure paths used to
print an error and fall through to exit code 0, so scripts and CI checks
gating on the exit code saw success.
"""

import socket

import pytest

from gaia.cli import main


def _run(monkeypatch, tmp_path, argv):
    """Run gaia.cli.main() with config paths redirected to tmp_path."""
    from gaia import config as config_mod

    monkeypatch.setattr(config_mod, "GAIA_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config_mod, "GAIA_CONFIG_DIR", tmp_path)
    monkeypatch.setattr("sys.argv", ["gaia"] + argv)
    with pytest.raises(SystemExit) as excinfo:
        main()
    return excinfo.value


def _closed_port():
    """Grab a loopback port nothing is listening on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_mcp_status_unreachable_server_exits_nonzero(monkeypatch, tmp_path):
    """`gaia mcp status` against a closed port exits non-zero (issue #4781)."""
    exc = _run(
        monkeypatch,
        tmp_path,
        ["mcp", "status", "--host", "127.0.0.1", "--port", str(_closed_port())],
    )
    assert exc.code not in (0, None)


def test_mcp_test_unreachable_server_exits_nonzero(monkeypatch, tmp_path):
    """`gaia mcp test` that cannot connect exits non-zero (issue #4781)."""
    exc = _run(
        monkeypatch,
        tmp_path,
        ["mcp", "test", "--host", "127.0.0.1", "--port", str(_closed_port())],
    )
    assert exc.code not in (0, None)


def test_mcp_unknown_action_exits_nonzero(monkeypatch, tmp_path):
    exc = _run(monkeypatch, tmp_path, ["mcp", "definitely-not-an-action"])
    assert exc.code not in (0, None)


def test_mcp_no_action_exits_nonzero(monkeypatch, tmp_path):
    exc = _run(monkeypatch, tmp_path, ["mcp"])
    assert exc.code not in (0, None)
