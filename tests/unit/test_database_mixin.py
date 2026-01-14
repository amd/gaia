# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for DatabaseMixin."""

import pytest

from gaia.database import DatabaseMixin


class DB(DatabaseMixin):
    """Test helper class."""

    pass


# --- Initialization Tests ---


def test_init_memory():
    """In-memory database initializes correctly."""
    db = DB()
    db.init_db()
    assert db.db_ready
    db.close_db()
    assert not db.db_ready


def test_init_file(tmp_path):
    """File-based database creates file and parent dirs."""
    db_path = tmp_path / "subdir" / "test.db"
    db = DB()
    db.init_db(str(db_path))
    assert db_path.exists()
    assert db.db_ready
    db.close_db()


def test_reinit_closes_previous():
    """Calling init_db twice closes the first connection."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE t1 (id INTEGER)")
    assert db.table_exists("t1")

    db.init_db()  # Reinitialize with new in-memory db
    assert not db.table_exists("t1")  # New database, table gone
    db.close_db()


def test_require_init():
    """Operations fail before init_db is called."""
    db = DB()
    with pytest.raises(RuntimeError, match="init_db"):
        db.query("SELECT 1")


def test_close_idempotent():
    """close_db is safe to call multiple times."""
    db = DB()
    db.init_db()
    db.close_db()
    db.close_db()  # Should not raise
    assert not db.db_ready


# --- CRUD Tests ---


def test_crud():
    """Full CRUD cycle works correctly."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    # Insert
    assert db.insert("items", {"name": "apple"}) == 1
    assert db.insert("items", {"name": "banana"}) == 2

    # Query all
    items = db.query("SELECT * FROM items")
    assert len(items) == 2
    assert all(isinstance(item, dict) for item in items)

    # Query one
    item = db.query("SELECT * FROM items WHERE id = :id", {"id": 1}, one=True)
    assert item["name"] == "apple"

    # Query one - not found
    missing = db.query("SELECT * FROM items WHERE id = :id", {"id": 999}, one=True)
    assert missing is None

    # Update
    assert db.update("items", {"name": "apricot"}, "id = :id", {"id": 1}) == 1
    updated = db.query("SELECT name FROM items WHERE id = :id", {"id": 1}, one=True)
    assert updated["name"] == "apricot"

    # Delete
    assert db.delete("items", "id = :id", {"id": 2}) == 1
    assert len(db.query("SELECT * FROM items")) == 1

    db.close_db()


def test_insert_returns_id():
    """Insert returns auto-increment ID."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    id1 = db.insert("items", {"name": "a"})
    id2 = db.insert("items", {"name": "b"})
    id3 = db.insert("items", {"name": "c"})

    assert id1 == 1
    assert id2 == 2
    assert id3 == 3
    db.close_db()


def test_update_returns_count():
    """Update returns number of affected rows."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER, category TEXT)")
    db.insert("items", {"id": 1, "category": "fruit"})
    db.insert("items", {"id": 2, "category": "fruit"})
    db.insert("items", {"id": 3, "category": "vegetable"})

    count = db.update(
        "items", {"category": "food"}, "category = :cat", {"cat": "fruit"}
    )
    assert count == 2
    db.close_db()


def test_delete_returns_count():
    """Delete returns number of deleted rows."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER, category TEXT)")
    db.insert("items", {"id": 1, "category": "fruit"})
    db.insert("items", {"id": 2, "category": "fruit"})
    db.insert("items", {"id": 3, "category": "vegetable"})

    count = db.delete("items", "category = :cat", {"cat": "fruit"})
    assert count == 2
    assert len(db.query("SELECT * FROM items")) == 1
    db.close_db()


def test_query_empty_params():
    """Query works without params."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER)")
    db.insert("items", {"id": 1})

    items = db.query("SELECT * FROM items")
    assert len(items) == 1
    db.close_db()


# --- Transaction Tests ---


def test_transaction_commit():
    """Transaction commits on success."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    with db.transaction():
        db.insert("items", {"name": "a"})
        db.insert("items", {"name": "b"})

    assert len(db.query("SELECT * FROM items")) == 2
    db.close_db()


def test_transaction_rollback():
    """Transaction rolls back on exception."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    try:
        with db.transaction():
            db.insert("items", {"name": "a"})
            db.insert("items", {"name": "b"})
            raise ValueError("simulated error")
    except ValueError:
        pass

    assert len(db.query("SELECT * FROM items")) == 0
    db.close_db()


def test_transaction_nested_operations():
    """All operations within transaction are atomic."""
    db = DB()
    db.init_db()
    db.execute(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE profiles (id INTEGER PRIMARY KEY, user_id INTEGER, bio TEXT);
    """
    )

    try:
        with db.transaction():
            uid = db.insert("users", {"name": "Alice"})
            db.insert("profiles", {"user_id": uid, "bio": "Hello"})
            raise ValueError("abort")
    except ValueError:
        pass

    assert len(db.query("SELECT * FROM users")) == 0
    assert len(db.query("SELECT * FROM profiles")) == 0
    db.close_db()


# --- Schema Tests ---


def test_table_exists():
    """table_exists correctly detects tables."""
    db = DB()
    db.init_db()

    assert not db.table_exists("items")
    db.execute("CREATE TABLE items (id INTEGER)")
    assert db.table_exists("items")
    assert not db.table_exists("other")
    db.close_db()


def test_execute_multiple_statements():
    """execute() handles multiple SQL statements."""
    db = DB()
    db.init_db()
    db.execute(
        """
        CREATE TABLE t1 (id INTEGER);
        CREATE TABLE t2 (id INTEGER);
        CREATE TABLE t3 (id INTEGER);
    """
    )

    assert db.table_exists("t1")
    assert db.table_exists("t2")
    assert db.table_exists("t3")
    db.close_db()


def test_foreign_keys_enabled():
    """Foreign key constraints are enforced."""
    db = DB()
    db.init_db()
    db.execute(
        """
        CREATE TABLE parent (id INTEGER PRIMARY KEY);
        CREATE TABLE child (id INTEGER, parent_id INTEGER REFERENCES parent(id));
    """
    )

    # This should fail - no parent with id=999
    with pytest.raises(Exception):  # sqlite3.IntegrityError
        db.insert("child", {"id": 1, "parent_id": 999})

    db.close_db()


# --- Edge Cases ---


def test_empty_query_result():
    """Query returns empty list when no matches."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER)")

    result = db.query("SELECT * FROM items")
    assert result == []
    db.close_db()


def test_null_values():
    """NULL values are handled correctly."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    db.insert("items", {"id": 1, "name": None})

    item = db.query("SELECT * FROM items WHERE id = :id", {"id": 1}, one=True)
    assert item["name"] is None
    db.close_db()


def test_special_characters_in_values():
    """Special characters in values are handled safely."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    # These should not cause SQL injection or errors
    db.insert("items", {"name": "O'Reilly"})
    db.insert("items", {"name": 'Say "Hello"'})
    db.insert("items", {"name": "DROP TABLE items;--"})

    items = db.query("SELECT * FROM items")
    assert len(items) == 3
    assert db.table_exists("items")  # Table still exists
    db.close_db()


def test_unicode_values():
    """Unicode values are handled correctly."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    db.insert("items", {"name": "日本語"})
    db.insert("items", {"name": "émojis 🎉"})
    db.insert("items", {"name": "Ñoño"})

    items = db.query("SELECT * FROM items")
    assert len(items) == 3
    assert items[0]["name"] == "日本語"
    db.close_db()


def test_update_no_matches():
    """Update with no matching rows returns 0."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER, name TEXT)")

    count = db.update("items", {"name": "new"}, "id = :id", {"id": 999})
    assert count == 0
    db.close_db()


def test_delete_no_matches():
    """Delete with no matching rows returns 0."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER)")

    count = db.delete("items", "id = :id", {"id": 999})
    assert count == 0
    db.close_db()


# --- Instance Isolation Tests ---


def test_multiple_instances_isolated():
    """Multiple instances have separate databases."""
    db1 = DB()
    db2 = DB()

    db1.init_db()
    db2.init_db()

    db1.execute("CREATE TABLE t1 (id INTEGER)")
    db2.execute("CREATE TABLE t2 (id INTEGER)")

    # Each instance should only see its own tables
    assert db1.table_exists("t1")
    assert not db1.table_exists("t2")

    assert db2.table_exists("t2")
    assert not db2.table_exists("t1")

    # Data should be isolated too
    db1.insert("t1", {"id": 100})
    db2.insert("t2", {"id": 200})

    assert db1.query("SELECT id FROM t1", one=True)["id"] == 100
    assert db2.query("SELECT id FROM t2", one=True)["id"] == 200

    db1.close_db()
    db2.close_db()


def test_execute_inside_transaction_raises():
    """execute() raises error when called inside transaction."""
    db = DB()
    db.init_db()
    db.execute("CREATE TABLE items (id INTEGER)")

    with pytest.raises(RuntimeError, match="cannot be called inside a transaction"):
        with db.transaction():
            db.insert("items", {"id": 1})
            db.execute("CREATE TABLE other (id INTEGER)")  # Should raise

    db.close_db()
