# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the daemon's custody schema-version stamp (``daemon.migrate``).

* cold state: a fresh profile stamps the current version and starts;
* warm state: a populated install is stamped without copying its chat history or
  memory into ``host/custody/`` (nothing reads those copies, #7092);
* idempotent: an already-stamped install is a no-op;
* a corrupt stamp or an unknown-newer version is refused loudly.

The populated fixture uses the real ``MemoryStore`` / ``ChatDatabase`` classes, so
it is the on-disk state a user really has.
"""

from __future__ import annotations

import json

import pytest

from gaia.daemon import migrate
from gaia.daemon.errors import MigrationError


@pytest.fixture()
def homes(tmp_path, monkeypatch):
    """Isolate BOTH the legacy ``~/.gaia`` root and the v2 host dir under tmp.

    Legacy state (``GAIA_CONFIG_DIR``) and custody (``GAIA_DAEMON_HOME``) live in
    separate tmp subdirs so a test never touches the developer's real state.
    """
    legacy = tmp_path / "gaia"
    host = tmp_path / "gaia" / "host"
    # GAIA_HOME outranks GAIA_CONFIG_DIR, so an exported one would escape tmp.
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(legacy))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(host))
    return legacy, host


def _seed_legacy_sessions(legacy_dir):
    """Create a real pre-v2 sessions DB at ``~/.gaia/chat/gaia_chat.db``."""
    from gaia.ui.database import ChatDatabase

    db_path = legacy_dir / "chat" / "gaia_chat.db"
    db = ChatDatabase(db_path=str(db_path))
    try:
        session = db.create_session(title="pre-v2 chat")
        session_id = session["id"]
        db.add_message(session_id, role="user", content="hello from before v2")
    finally:
        db.close()
    return db_path, session_id


def _seed_legacy_memory(legacy_dir):
    """Create a real pre-v2 memory DB at ``~/.gaia/memory.db``."""
    from gaia.agents.base.memory_store import MemoryStore

    db_path = legacy_dir / "memory.db"
    store = MemoryStore(db_path=db_path)
    try:
        store.store(category="preference", content="user prefers dark mode")
    finally:
        store.close()
    return db_path


# ---------------------------------------------------------------------------
# Schema-version stamp
# ---------------------------------------------------------------------------


def test_read_schema_version_absent_is_zero(homes):
    assert migrate.read_schema_version() == 0


def test_read_schema_version_after_migrate(homes):
    migrate.run_migrations()
    assert migrate.read_schema_version() == migrate.SCHEMA_VERSION


def test_read_schema_version_corrupt_raises_loud(homes):
    stamp = migrate.schema_stamp_path()
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(MigrationError) as ei:
        migrate.read_schema_version()
    assert str(stamp) in str(ei.value)


def test_read_schema_version_missing_key_raises_loud(homes):
    stamp = migrate.schema_stamp_path()
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({"unrelated": 1}), encoding="utf-8")
    with pytest.raises(MigrationError):
        migrate.read_schema_version()


# ---------------------------------------------------------------------------
# Cold state (fresh install)
# ---------------------------------------------------------------------------


def test_cold_state_stamps_and_reports(homes):
    result = migrate.run_migrations()
    assert result["status"] == migrate.MigrationState.MIGRATED
    assert result["from_version"] == 0
    assert result["to_version"] == migrate.SCHEMA_VERSION
    assert result["applied"] == []
    assert migrate.read_schema_version() == migrate.SCHEMA_VERSION
    # Only the stamp is written under custody.
    assert [p.name for p in migrate.custody_dir().iterdir()] == ["schema.json"]


# ---------------------------------------------------------------------------
# Warm state: live chat history and memory are never copied
# ---------------------------------------------------------------------------


def test_populated_install_is_stamped_without_copying_databases(homes):
    legacy, _ = homes
    sessions_src, _ = _seed_legacy_sessions(legacy)
    memory_src = _seed_legacy_memory(legacy)
    sessions_before = sessions_src.read_bytes()
    memory_before = memory_src.read_bytes()

    result = migrate.run_migrations()

    assert result["status"] == migrate.MigrationState.MIGRATED
    assert result["to_version"] == migrate.SCHEMA_VERSION
    assert result["applied"] == []
    assert [p.name for p in migrate.custody_dir().iterdir()] == ["schema.json"]
    assert not list(migrate.custody_dir().rglob("*.db"))
    # The live stores are untouched.
    assert sessions_src.read_bytes() == sessions_before
    assert memory_src.read_bytes() == memory_before


def test_already_migrated_install_starts_and_keeps_old_copies(homes):
    """An install stamped at v1 by an older build (with its copies) still starts,
    and the copies already on disk are left alone."""
    stamp = migrate.schema_stamp_path()
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(
        json.dumps({"schema_version": 1, "applied": ["sessions:gaia_chat.db"]}),
        encoding="utf-8",
    )
    old_copy = migrate.custody_dir() / "agents" / "chat" / "sessions.db"
    old_copy.parent.mkdir(parents=True)
    old_copy.write_bytes(b"old copy")
    stamp_before = stamp.read_text(encoding="utf-8")

    result = migrate.run_migrations()

    assert result["status"] == migrate.MigrationState.CURRENT
    assert result["applied"] == []
    assert stamp.read_text(encoding="utf-8") == stamp_before
    assert old_copy.read_bytes() == b"old copy"


def test_second_run_is_noop(homes):
    first = migrate.run_migrations()
    assert first["status"] == migrate.MigrationState.MIGRATED
    stamp_before = migrate.schema_stamp_path().read_text(encoding="utf-8")

    second = migrate.run_migrations()
    assert second["status"] == migrate.MigrationState.CURRENT
    assert second["from_version"] == migrate.SCHEMA_VERSION
    assert second["applied"] == []
    assert migrate.schema_stamp_path().read_text(encoding="utf-8") == stamp_before


def test_stamp_lands_under_an_isolated_gaia_home(tmp_path, monkeypatch):
    isolated = tmp_path / "isolated"
    monkeypatch.setenv("GAIA_HOME", str(isolated))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.delenv("GAIA_DAEMON_HOME", raising=False)

    migrate.run_migrations()

    assert migrate.schema_stamp_path().is_relative_to(isolated)
    assert migrate.read_schema_version() == migrate.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Unknown-newer version → loud refusal
# ---------------------------------------------------------------------------


def test_unknown_newer_version_refuses_loud(homes):
    stamp = migrate.schema_stamp_path()
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(
        json.dumps({"schema_version": migrate.SCHEMA_VERSION + 5}), encoding="utf-8"
    )
    with pytest.raises(MigrationError) as ei:
        migrate.run_migrations()
    msg = str(ei.value)
    assert "NEWER" in msg or "newer" in msg
