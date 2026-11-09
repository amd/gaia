# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An upgrade removes installs of sidecar agents GAIA no longer ships."""

from __future__ import annotations

import logging

from gaia.daemon.sidecars import install
from gaia.daemon.sidecars.errors import StopFailedError


def _leftover_email(root):
    agent_dir = root / "email"
    (agent_dir / "logs").mkdir(parents=True)
    (agent_dir / "gaia-agent-email.exe").write_bytes(b"binary")
    (agent_dir / ".installed").write_text("{}", encoding="utf-8")
    return agent_dir


def test_a_leftover_email_install_is_removed_and_logged(tmp_path, caplog):
    agent_dir = _leftover_email(tmp_path)
    (tmp_path / "gaia").mkdir()

    with caplog.at_level(logging.INFO, logger=install.logger.name):
        removed = install.remove_retired_agents(install_root=tmp_path)

    assert removed == ["email"]
    assert not agent_dir.exists()
    assert (tmp_path / "gaia").is_dir(), "a shipped agent must be left alone"
    assert any(
        "removed the retired 'email' agent" in r.getMessage() for r in caplog.records
    )


def test_nothing_to_remove_is_a_quiet_no_op(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger=install.logger.name):
        assert install.remove_retired_agents(install_root=tmp_path) == []
    assert not caplog.records


def test_a_dir_a_live_process_runs_from_is_kept_and_logged(
    tmp_path, monkeypatch, caplog
):
    agent_dir = _leftover_email(tmp_path)

    def _still_running(path, agent_id):
        raise StopFailedError(f"process 4242 is still running from {path}")

    monkeypatch.setattr(install, "assert_no_live_process_in", _still_running)
    with caplog.at_level(logging.ERROR, logger=install.logger.name):
        removed = install.remove_retired_agents(install_root=tmp_path)

    assert removed == []
    assert agent_dir.is_dir()
    message = caplog.records[-1].getMessage()
    assert "could not remove the retired 'email' agent" in message
    assert "try again at its next start" in message


def test_the_daemon_runs_the_cleanup_at_start():
    import inspect

    from gaia.daemon import server

    source = inspect.getsource(server.run)
    assert source.index("remove_retired_agents()") < source.index(
        "_find_free_port(host)"
    )
