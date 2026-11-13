# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An upgrade clears up after sidecar agents GAIA no longer ships."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

import pytest

from gaia.daemon import retired_agents
from gaia.daemon.scheduler import store
from gaia.daemon.scheduler.clock import DaemonClock
from gaia.daemon.sidecars import install
from gaia.daemon.sidecars.errors import StopFailedError
from gaia.hub import installer as hub_installer
from gaia.retired_agents import RETIRED_AGENT_IDS, RETIRED_AGENT_JOB_MESSAGE


def _leftover_email(root):
    agent_dir = root / "email"
    (agent_dir / "logs").mkdir(parents=True)
    (agent_dir / "gaia-agent-email.exe").write_bytes(b"binary")
    (agent_dir / ".installed").write_text(
        json.dumps({"id": "email", "version": "0.6.0"}), encoding="utf-8"
    )
    return agent_dir


# ---------------------------------------------------------------------------
# The install folder
# ---------------------------------------------------------------------------


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


@pytest.mark.parametrize("binary", ["email-agent", "email-agent.exe"])
def test_a_fetched_binary_with_no_sentinel_is_removed(tmp_path, binary):
    agent_dir = tmp_path / "email"
    agent_dir.mkdir()
    (agent_dir / binary).write_bytes(b"binary")
    (agent_dir / "binaries.lock.json").write_text("{}", encoding="utf-8")

    assert install.remove_retired_agents(install_root=tmp_path) == ["email"]
    assert not agent_dir.exists()


def test_a_persons_own_agent_named_email_survives(tmp_path, caplog):
    agent_dir = tmp_path / "email"
    agent_dir.mkdir()
    source = agent_dir / "agent.py"
    source.write_text("class MyEmailAgent: ...\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=install.logger.name):
        assert install.remove_retired_agents(install_root=tmp_path) == []

    assert source.read_text(encoding="utf-8") == "class MyEmailAgent: ...\n"
    message = caplog.records[-1].getMessage()
    assert "left" in message and "agent.py" in message
    assert str(agent_dir) in message


def test_an_own_agent_dropped_into_the_old_install_survives(tmp_path, caplog):
    agent_dir = _leftover_email(tmp_path)
    (agent_dir / "agent.py").write_text("x = 1\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=install.logger.name):
        assert install.remove_retired_agents(install_root=tmp_path) == []

    assert (agent_dir / "agent.py").is_file()
    assert (agent_dir / "gaia-agent-email.exe").is_file()


def test_a_mixed_folder_is_left_alone_and_names_what_is_foreign(tmp_path, caplog):
    agent_dir = _leftover_email(tmp_path)
    (agent_dir / "notes.txt").write_text("mine", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=install.logger.name):
        assert install.remove_retired_agents(install_root=tmp_path) == []

    assert (agent_dir / "notes.txt").read_text(encoding="utf-8") == "mine"
    message = caplog.records[-1].getMessage()
    assert "notes.txt" in message
    assert "Delete the folder yourself" in message


def test_a_folder_with_no_sign_of_the_old_install_is_left_alone(tmp_path):
    agent_dir = tmp_path / "email"
    (agent_dir / "logs").mkdir(parents=True)

    assert install.remove_retired_agents(install_root=tmp_path) == []
    assert agent_dir.is_dir()


def test_another_agents_sentinel_is_not_proof(tmp_path):
    agent_dir = tmp_path / "email"
    agent_dir.mkdir()
    (agent_dir / ".installed").write_text(json.dumps({"id": "other"}), "utf-8")

    assert install.remove_retired_agents(install_root=tmp_path) == []
    assert agent_dir.is_dir()


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
    assert source.index("clean_up_retired_agents()") < source.index(
        "_find_free_port(host)"
    )


def test_one_failing_step_does_not_skip_the_rest(monkeypatch, caplog):
    ran = []

    def _boom(*args, **kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(install, "remove_retired_agents", _boom)
    monkeypatch.setattr(
        retired_agents, "revoke_retired_grants", lambda: ran.append("grants")
    )
    monkeypatch.setattr(
        retired_agents, "report_abandoned_email_work", lambda: ran.append("mail")
    )
    with caplog.at_level(logging.ERROR, logger=retired_agents.logger.name):
        retired_agents.clean_up_retired_agents()

    assert ran == ["grants", "mail"]
    assert "disk went away" in caplog.records[-1].getMessage()


# ---------------------------------------------------------------------------
# Installing a retired agent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_id", RETIRED_AGENT_IDS)
def test_the_hub_installer_refuses_a_retired_agent(tmp_path, agent_id):
    def _no_network(*args, **kwargs):
        raise AssertionError("a retired agent must be refused before any fetch")

    with pytest.raises(hub_installer.RetiredAgentError) as exc:
        hub_installer.install(agent_id, install_root=tmp_path, fetcher=_no_network)

    message = str(exc.value)
    assert "removed from GAIA" in message
    assert "gaia chat" in message
    assert "https://amd-gaia.ai/docs/guides/email" in message
    assert not (tmp_path / agent_id).exists()


def test_a_retired_agent_refusal_is_an_install_error():
    assert issubclass(hub_installer.RetiredAgentError, hub_installer.InstallError)


# ---------------------------------------------------------------------------
# Connector access
# ---------------------------------------------------------------------------


@pytest.fixture
def grants_file(tmp_path, monkeypatch):
    from gaia.connectors import grants

    path = tmp_path / "connectors" / "grants.json"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(grants, "_grants_path", lambda: path)
    return path


def _ledger(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_removed_agents_grants_are_revoked_and_others_kept(
    tmp_path, grants_file, caplog
):
    mail = ["https://www.googleapis.com/auth/gmail.modify"]
    grants_file.write_text(
        json.dumps(
            {
                "google": {"installed:email": mail, "installed:gaia": ["g"]},
                "microsoft": {"installed:email": ["Mail.ReadWrite"]},
                "microsoft_work": {
                    "installed:email": ["Mail.Send"],
                    "custom:abc123:email": ["Mail.Read"],
                },
                "github": {"installed:gaia": ["repo"]},
            }
        ),
        encoding="utf-8",
    )
    marker = tmp_path / "host" / "done"

    with caplog.at_level(logging.WARNING, logger=retired_agents.logger.name):
        revoked = retired_agents.revoke_retired_grants(done_marker=marker)

    assert revoked == [
        "google:installed:email",
        "microsoft:installed:email",
        "microsoft_work:installed:email",
    ]
    assert _ledger(grants_file) == {
        "google": {"installed:gaia": ["g"]},
        "microsoft_work": {"custom:abc123:email": ["Mail.Read"]},
        "github": {"installed:gaia": ["repo"]},
    }
    logged = [r.getMessage() for r in caplog.records]
    assert sum("revoked the removed 'installed:email'" in m for m in logged) == 3


def test_revoking_twice_is_harmless_and_spares_a_later_grant(tmp_path, grants_file):
    grants_file.write_text(
        json.dumps({"google": {"installed:email": ["s"]}}), encoding="utf-8"
    )
    marker = tmp_path / "host" / "done"

    assert retired_agents.revoke_retired_grants(done_marker=marker) == [
        "google:installed:email"
    ]
    assert retired_agents.revoke_retired_grants(done_marker=marker) == []
    assert _ledger(grants_file) == {}

    # A grant a person makes after the upgrade is theirs to keep.
    grants_file.write_text(
        json.dumps({"google": {"installed:email": ["s"]}}), encoding="utf-8"
    )
    assert retired_agents.revoke_retired_grants(done_marker=marker) == []
    assert _ledger(grants_file) == {"google": {"installed:email": ["s"]}}


def test_no_ledger_at_all_is_a_no_op(tmp_path, grants_file):
    marker = tmp_path / "host" / "done"
    assert retired_agents.revoke_retired_grants(done_marker=marker) == []
    assert not grants_file.exists()
    assert marker.is_file()


# ---------------------------------------------------------------------------
# Mail the removed agent had scheduled
# ---------------------------------------------------------------------------


def _old_store(home: Path, rows) -> Path:
    home.mkdir(parents=True)
    db = sqlite3.connect(home / "state.db")
    db.execute(
        "CREATE TABLE email_scheduled_jobs (job_id TEXT PRIMARY KEY, kind TEXT, "
        "due_at REAL, payload_json TEXT, mailbox TEXT, status TEXT)"
    )
    db.executemany("INSERT INTO email_scheduled_jobs VALUES (?,?,?,?,?,?)", rows)
    db.commit()
    db.close()
    return home


def test_each_unsent_scheduled_mail_is_named_once(tmp_path, monkeypatch, caplog):
    due = time.time() + 3600
    home = _old_store(
        tmp_path / "email",
        [
            ("j1", "scheduled_send", due, '{"subject": "Q3 numbers"}', "g", "pending"),
            ("j2", "snooze", due, "{}", "g", "pending"),
            ("j3", "scheduled_send", due, '{"subject": "Old"}', "g", "fired"),
        ],
    )
    shown = []
    monkeypatch.setattr(
        retired_agents, "_notify", lambda items, where: shown.append(list(items))
    )

    with caplog.at_level(logging.WARNING, logger=retired_agents.logger.name):
        items = retired_agents.report_abandoned_email_work(email_home=home)

    assert len(items) == 2
    assert 'scheduled email "Q3 numbers"' in items[0]
    assert items[1].startswith("snooze")
    assert shown == [items]
    logged = [r.getMessage() for r in caplog.records]
    assert sum("will not happen" in m for m in logged) == 2
    assert any("Q3 numbers" in m and str(home) in m for m in logged)
    assert not any("Old" in m for m in logged)

    # The old data is untouched, and the second start says nothing more.
    assert (home / "state.db").is_file()
    assert retired_agents.report_abandoned_email_work(email_home=home) == []
    assert shown == [items]


def test_an_old_store_with_nothing_pending_notifies_no_one(tmp_path, monkeypatch):
    home = _old_store(tmp_path / "email", [])
    monkeypatch.setattr(
        retired_agents,
        "_notify",
        lambda *a: (_ for _ in ()).throw(AssertionError("nothing to say")),
    )
    assert retired_agents.report_abandoned_email_work(email_home=home) == []
    assert (home / "state.db").is_file()


def test_no_old_store_is_a_no_op(tmp_path):
    home = tmp_path / "email"
    assert retired_agents.report_abandoned_email_work(email_home=home) == []
    assert not home.exists()


def test_an_unreadable_old_store_is_logged_with_its_path(tmp_path, caplog):
    home = tmp_path / "email"
    home.mkdir()
    (home / "state.db").write_bytes(b"not a database, just some bytes" * 40)

    with caplog.at_level(logging.ERROR, logger=retired_agents.logger.name):
        assert retired_agents.report_abandoned_email_work(email_home=home) == []

    message = caplog.records[-1].getMessage()
    assert str(home / "state.db") in message
    assert "email_scheduled_jobs" in message
    assert not (home / retired_agents.NOTICE_MARKER).exists(), "must retry next start"


# ---------------------------------------------------------------------------
# A job the removed agent had handed to the daemon clock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["email", "email:schedule_store"])
def test_a_clock_job_from_the_removed_agent_fails_in_plain_words(
    tmp_path, monkeypatch, source
):
    told = []
    monkeypatch.setattr(
        DaemonClock,
        "_notify_failed",
        staticmethod(lambda job, error: told.append(error)),
    )
    clock = DaemonClock(str(tmp_path / "clock.db"), executors={})
    db = clock._open_db()
    job_id = store.register_job(
        db, source=source, kind="one_shot", fire_at=time.time() - 5
    )
    db.close_db()

    assert clock.fire_due()["failed"] == [job_id]

    db = clock._open_db()
    error = store.get_job(db, job_id=job_id)["error"]
    db.close_db()
    assert error == RETIRED_AGENT_JOB_MESSAGE
    assert "was not sent" in error
    assert "no executor registered" not in error
    assert told == [RETIRED_AGENT_JOB_MESSAGE]


def test_another_sources_missing_executor_keeps_its_technical_message(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        DaemonClock, "_notify_failed", staticmethod(lambda job, error: None)
    )
    clock = DaemonClock(str(tmp_path / "clock.db"), executors={})
    db = clock._open_db()
    job_id = store.register_job(
        db, source="calendar", kind="one_shot", fire_at=time.time() - 5
    )
    db.close_db()

    clock.fire_due()

    db = clock._open_db()
    error = store.get_job(db, job_id=job_id)["error"]
    db.close_db()
    assert "no executor registered" in error
