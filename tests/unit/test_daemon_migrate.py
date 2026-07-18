# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the one-time versioned ``~/.gaia`` state migration (issue #2155).

Covers the acceptance criteria from V2-13:

* warm-state: a populated pre-v2 fixture migrates into the custody layout;
* idempotent: a second run is a no-op (the version stamp gates re-runs);
* cold-state: a fresh profile with nothing to migrate still stamps and succeeds
  (per the CLAUDE.md hidden-state rule);
* mid-migration failure leaves the originals intact, the version un-stamped, and
  is recoverable on retry — and fails loud;
* schema version stamped and checked; an unknown-newer version → loud refusal.

Fixtures are *real* SQLite databases built by the actual pre-v2 store classes
(``MemoryStore`` / ``ChatDatabase``), so the migration is exercised against the
same on-disk schema an upgrader really has — not a hand-rolled stand-in.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from gaia.daemon import migrate, paths
from gaia.daemon.errors import MigrationError


@pytest.fixture()
def homes(tmp_path, monkeypatch):
    """Isolate BOTH the legacy ``~/.gaia`` root and the v2 host dir under tmp.

    Legacy state (``GAIA_CONFIG_DIR``) and custody (``GAIA_DAEMON_HOME``) live in
    separate tmp subdirs so a test never touches the developer's real state.
    """
    legacy = tmp_path / "gaia"
    host = tmp_path / "gaia" / "host"
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
# Cold state (nothing to migrate)
# ---------------------------------------------------------------------------


def test_cold_state_stamps_and_reports(homes):
    result = migrate.run_migrations()
    assert result["status"] == migrate.MigrationState.MIGRATED
    assert result["from_version"] == 0
    assert result["to_version"] == migrate.SCHEMA_VERSION
    assert result["applied"] == []  # no legacy stores existed
    assert migrate.read_schema_version() == migrate.SCHEMA_VERSION
    # No custody stores were fabricated out of nothing.
    assert not migrate.custody_sessions_db().exists()
    assert not migrate.custody_memory_db().exists()


# ---------------------------------------------------------------------------
# Warm state (populated fixture)
# ---------------------------------------------------------------------------


def test_warm_state_migrates_sessions_and_memory(homes):
    legacy, _ = homes
    sessions_src, session_id = _seed_legacy_sessions(legacy)
    memory_src = _seed_legacy_memory(legacy)

    result = migrate.run_migrations()

    assert result["status"] == migrate.MigrationState.MIGRATED
    assert result["to_version"] == migrate.SCHEMA_VERSION
    assert len(result["applied"]) == 2

    # Custody copies exist under the v2 layout...
    assert migrate.custody_sessions_db().exists()
    assert migrate.custody_memory_db().exists()

    # ...and the originals are preserved (non-destructive).
    assert sessions_src.exists()
    assert memory_src.exists()

    # The migrated sessions DB carries the real pre-v2 row.
    conn = sqlite3.connect(str(migrate.custody_sessions_db()))
    try:
        row = conn.execute(
            "SELECT title FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == "pre-v2 chat"


def test_second_run_is_noop(homes):
    legacy, _ = homes
    _seed_legacy_sessions(legacy)
    _seed_legacy_memory(legacy)

    first = migrate.run_migrations()
    assert first["status"] == migrate.MigrationState.MIGRATED

    sessions_dst = migrate.custody_sessions_db()
    mtime_before = sessions_dst.stat().st_mtime_ns

    second = migrate.run_migrations()
    assert second["status"] == migrate.MigrationState.CURRENT
    assert second["applied"] == []
    # The second run did not rewrite the already-migrated store.
    assert sessions_dst.stat().st_mtime_ns == mtime_before


def test_second_run_ignores_new_legacy_writes(homes):
    """Once stamped, the migration never re-touches legacy state — even if the old
    location is written again — so it can't clobber post-migration custody data."""
    legacy, _ = homes
    _seed_legacy_sessions(legacy)
    migrate.run_migrations()

    # Simulate a stray later write to the OLD location; the stamped migration
    # must ignore it (idempotent by version, not by content diffing).
    migrate.legacy_memory_db().write_bytes(b"stray-post-migration-bytes")
    result = migrate.run_migrations()
    assert result["status"] == migrate.MigrationState.CURRENT
    assert not migrate.custody_memory_db().exists()


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


# ---------------------------------------------------------------------------
# Mid-migration failure: originals intact, version un-stamped, recoverable
# ---------------------------------------------------------------------------


def test_failed_step_preserves_originals_and_does_not_stamp(homes, monkeypatch):
    legacy, _ = homes
    sessions_src, _ = _seed_legacy_sessions(legacy)
    memory_src = _seed_legacy_memory(legacy)

    def boom(src, dst, mode=0o600):
        raise OSError("disk full")

    monkeypatch.setattr(paths, "atomic_copy_file", boom)

    with pytest.raises(MigrationError) as ei:
        migrate.run_migrations()
    assert "No data was lost" in str(ei.value)

    # Originals untouched; version NOT stamped (so the next start retries).
    assert sessions_src.exists()
    assert memory_src.exists()
    assert migrate.read_schema_version() == 0


def test_retry_after_failure_completes(homes, monkeypatch):
    legacy, _ = homes
    _seed_legacy_sessions(legacy)
    _seed_legacy_memory(legacy)

    calls = {"n": 0}
    real_copy = paths.atomic_copy_file

    def flaky(src, dst, mode=0o600):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("transient")
        return real_copy(src, dst, mode)

    monkeypatch.setattr(paths, "atomic_copy_file", flaky)
    with pytest.raises(MigrationError):
        migrate.run_migrations()
    assert migrate.read_schema_version() == 0

    # Retry (fault cleared) completes and stamps.
    monkeypatch.setattr(paths, "atomic_copy_file", real_copy)
    result = migrate.run_migrations()
    assert result["status"] == migrate.MigrationState.MIGRATED
    assert migrate.read_schema_version() == migrate.SCHEMA_VERSION
    assert migrate.custody_sessions_db().exists()
    assert migrate.custody_memory_db().exists()
