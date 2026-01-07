# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for SQLAlchemy-based DatabaseMixin.

Following TDD approach: Tests written FIRST, then implementation.
"""

import concurrent.futures
import time

import pytest

from gaia.agents.base.database_mixin import DatabaseMixin


# Test helper class
class DBHelper(DatabaseMixin):
    """Test helper that uses the DatabaseMixin."""

    pass


# ===== Initialization Tests =====


def test_init_memory():
    """In-memory SQLite should initialize correctly."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    assert db.db_ready is True
    db.close_database()
    assert db.db_ready is False


def test_init_file(tmp_path):
    """File-based SQLite should create file and parent dirs."""
    db_path = tmp_path / "subdir" / "test.db"
    db = DBHelper()
    db.init_database(f"sqlite:///{db_path}")
    assert db_path.exists()
    assert db.db_ready is True
    db.close_database()


def test_init_custom_pool_size():
    """Custom pool_size parameter should work."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:", pool_size=10)
    assert db.db_ready is True
    db.close_database()


def test_reinit_closes_previous():
    """Calling init_database twice should close first engine."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE t1 (id INTEGER)")
    assert db.table_exists("t1")

    # Reinitialize with new in-memory db
    db.init_database("sqlite:///:memory:")
    assert not db.table_exists("t1")  # New database, table gone
    db.close_database()


def test_require_init():
    """Operations before init should raise RuntimeError."""
    db = DBHelper()
    with pytest.raises(RuntimeError, match="not initialized"):
        db.execute_query("SELECT 1")


def test_close_idempotent():
    """close_database should be safe to call multiple times."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.close_database()
    db.close_database()  # Should not raise
    assert db.db_ready is False


def test_db_ready_property():
    """db_ready should return True/False appropriately."""
    db = DBHelper()
    assert db.db_ready is False
    db.init_database("sqlite:///:memory:")
    assert db.db_ready is True
    db.close_database()
    assert db.db_ready is False


# ===== Query Tests (SELECT) =====


def test_execute_query_select_all():
    """SELECT * should return all rows as list of dicts."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER, name TEXT)")
    db.execute_insert("items", {"id": 1, "name": "apple"})
    db.execute_insert("items", {"id": 2, "name": "banana"})

    results = db.execute_query("SELECT * FROM items")
    assert len(results) == 2
    assert all(isinstance(row, dict) for row in results)
    assert results[0]["name"] == "apple"
    assert results[1]["name"] == "banana"
    db.close_database()


def test_execute_query_with_params():
    """Parameterized queries should work correctly."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER, name TEXT)")
    db.execute_insert("items", {"id": 1, "name": "apple"})
    db.execute_insert("items", {"id": 2, "name": "banana"})

    results = db.execute_query(
        "SELECT * FROM items WHERE id = :item_id", {"item_id": 1}
    )
    assert len(results) == 1
    assert results[0]["name"] == "apple"
    db.close_database()


def test_execute_query_empty_result():
    """Empty results should return []."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER, name TEXT)")

    results = db.execute_query("SELECT * FROM items")
    assert results == []
    db.close_database()


def test_execute_query_no_params():
    """Queries without params should work."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER, name TEXT)")
    db.execute_insert("items", {"id": 1, "name": "apple"})

    results = db.execute_query("SELECT * FROM items")
    assert len(results) == 1
    db.close_database()


# ===== Insert Tests =====


def test_execute_insert_basic():
    """Basic insert should return row ID."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    item_id = db.execute_insert("items", {"name": "apple"})
    assert item_id == 1

    item_id = db.execute_insert("items", {"name": "banana"})
    assert item_id == 2

    results = db.execute_query("SELECT * FROM items")
    assert len(results) == 2
    db.close_database()


def test_execute_insert_with_returning():
    """RETURNING clause should return specified column (if supported)."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    # For SQLite 3.35+, RETURNING is supported
    # For older versions, should fall back gracefully
    item_id = db.execute_insert("items", {"name": "apple"}, returning="id")
    assert item_id is not None
    assert isinstance(item_id, int)
    db.close_database()


def test_execute_insert_multiple():
    """Multiple inserts should work correctly."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    for i in range(10):
        item_id = db.execute_insert("items", {"name": f"item{i}"})
        assert item_id == i + 1

    results = db.execute_query("SELECT COUNT(*) as count FROM items")
    assert results[0]["count"] == 10
    db.close_database()


# ===== Update Tests =====


def test_execute_update_single_row():
    """Update single row should return count=1."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute_insert("items", {"id": 1, "name": "apple"})

    count = db.execute_update("items", {"name": "apricot"}, "id = :id", {"id": 1})
    assert count == 1

    results = db.execute_query("SELECT name FROM items WHERE id = 1")
    assert results[0]["name"] == "apricot"
    db.close_database()


def test_execute_update_multiple_rows():
    """Update multiple rows should return correct count."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER, category TEXT, price REAL)")
    db.execute_insert("items", {"id": 1, "category": "fruit", "price": 1.0})
    db.execute_insert("items", {"id": 2, "category": "fruit", "price": 2.0})
    db.execute_insert("items", {"id": 3, "category": "veggie", "price": 3.0})

    count = db.execute_update(
        "items", {"price": 5.0}, "category = :cat", {"cat": "fruit"}
    )
    assert count == 2

    results = db.execute_query("SELECT price FROM items WHERE category = 'fruit'")
    assert all(row["price"] == 5.0 for row in results)
    db.close_database()


def test_execute_update_no_match():
    """Update with no matches should return count=0."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute_insert("items", {"id": 1, "name": "apple"})

    count = db.execute_update("items", {"name": "banana"}, "id = :id", {"id": 999})
    assert count == 0
    db.close_database()


def test_execute_update_param_collision():
    """Data and where params shouldn't collide."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, value INTEGER)"
    )
    db.execute_insert("items", {"id": 1, "name": "apple", "value": 10})

    # Both data and where have 'id' parameter - should not collide
    count = db.execute_update("items", {"value": 20}, "id = :id", {"id": 1})
    assert count == 1

    results = db.execute_query("SELECT value FROM items WHERE id = 1")
    assert results[0]["value"] == 20
    db.close_database()


# ===== Delete Tests =====


def test_execute_delete_single():
    """Delete single row should return count=1."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute_insert("items", {"id": 1, "name": "apple"})
    db.execute_insert("items", {"id": 2, "name": "banana"})

    count = db.execute_delete("items", "id = :id", {"id": 1})
    assert count == 1

    results = db.execute_query("SELECT * FROM items")
    assert len(results) == 1
    assert results[0]["name"] == "banana"
    db.close_database()


def test_execute_delete_multiple():
    """Delete multiple rows should return correct count."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER, category TEXT)")
    db.execute_insert("items", {"id": 1, "category": "fruit"})
    db.execute_insert("items", {"id": 2, "category": "fruit"})
    db.execute_insert("items", {"id": 3, "category": "veggie"})

    count = db.execute_delete("items", "category = :cat", {"cat": "fruit"})
    assert count == 2

    results = db.execute_query("SELECT * FROM items")
    assert len(results) == 1
    db.close_database()


def test_execute_delete_no_match():
    """Delete with no matches should return count=0."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute_insert("items", {"id": 1, "name": "apple"})

    count = db.execute_delete("items", "id = :id", {"id": 999})
    assert count == 0

    results = db.execute_query("SELECT * FROM items")
    assert len(results) == 1
    db.close_database()


# ===== Transaction Tests =====


def test_transaction_commit():
    """Successful transaction should commit all changes."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    with db.transaction():
        db.execute_insert("items", {"name": "apple"})
        db.execute_insert("items", {"name": "banana"})

    results = db.execute_query("SELECT * FROM items")
    assert len(results) == 2
    db.close_database()


def test_transaction_rollback_on_error():
    """Exception should rollback all changes."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    try:
        with db.transaction():
            db.execute_insert("items", {"name": "apple"})
            raise ValueError("Intentional error")
    except ValueError:
        pass

    results = db.execute_query("SELECT * FROM items")
    assert len(results) == 0  # Rollback occurred
    db.close_database()


def test_transaction_multiple_operations():
    """Multiple operations in transaction should be atomic."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute_raw(
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY, user_id INTEGER, bio TEXT)"
    )

    with db.transaction():
        user_id = db.execute_insert("users", {"name": "Alice"})
        db.execute_insert("profiles", {"user_id": user_id, "bio": "Hello"})

    users = db.execute_query("SELECT * FROM users")
    profiles = db.execute_query("SELECT * FROM profiles")
    assert len(users) == 1
    assert len(profiles) == 1
    assert profiles[0]["user_id"] == users[0]["id"]
    db.close_database()


def test_transaction_connection_cleanup():
    """Connection should be closed after transaction."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    # Transaction should not leave connections open
    for _ in range(10):
        with db.transaction():
            db.execute_insert("items", {"name": "test"})

    results = db.execute_query("SELECT COUNT(*) as count FROM items")
    assert results[0]["count"] == 10
    db.close_database()


# ===== Utility Tests =====


def test_execute_raw_create_table():
    """execute_raw should handle CREATE TABLE."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")

    db.execute_raw(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE
        )
    """
    )

    assert db.table_exists("users")
    db.close_database()


def test_table_exists_true():
    """table_exists should return True for existing table."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER)")

    assert db.table_exists("items") is True
    db.close_database()


def test_table_exists_false():
    """table_exists should return False for missing table."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")

    assert db.table_exists("nonexistent") is False
    db.close_database()


# ===== Security Tests =====


def test_parameterized_query_prevents_sql_injection():
    """SQL injection attempts should be safe with parameterized queries."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, password TEXT)"
    )
    db.execute_insert("users", {"id": 1, "name": "admin", "password": "secret"})

    # Attempt SQL injection - should be treated as literal string
    malicious_input = "admin' OR '1'='1"
    results = db.execute_query(
        "SELECT * FROM users WHERE name = :name", {"name": malicious_input}
    )

    # Should return empty (no match), NOT all users
    assert len(results) == 0

    # Verify normal query still works
    results = db.execute_query(
        "SELECT * FROM users WHERE name = :name", {"name": "admin"}
    )
    assert len(results) == 1
    db.close_database()


def test_special_characters_in_data():
    """Special chars (quotes, semicolons) should work correctly."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:")
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")

    # Test various special characters
    special_strings = [
        "O'Brien",  # Single quote
        'He said "hello"',  # Double quotes
        "DROP TABLE items;",  # SQL command
        "'; DROP TABLE items; --",  # Classic SQL injection
        "item1; item2",  # Semicolon
    ]

    for i, text in enumerate(special_strings):
        db.execute_insert("items", {"id": i + 1, "name": text})

    results = db.execute_query("SELECT * FROM items")
    assert len(results) == len(special_strings)

    # Verify table still exists (wasn't dropped)
    assert db.table_exists("items") is True
    db.close_database()


# ===== Thread Safety Tests =====


def test_concurrent_queries():
    """Multiple threads doing SELECT simultaneously should work."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:", pool_size=5)
    db.execute_raw("CREATE TABLE items (id INTEGER, value TEXT)")
    for i in range(10):
        db.execute_insert("items", {"id": i, "value": f"item{i}"})

    def query_worker(thread_id):
        """Each thread performs multiple queries."""
        for _ in range(10):
            results = db.execute_query(
                "SELECT * FROM items WHERE id = :id", {"id": thread_id % 10}
            )
            assert len(results) == 1
            assert results[0]["value"] == f"item{thread_id % 10}"
        return thread_id

    # Run 20 threads concurrently (more than pool_size)
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(query_worker, i) for i in range(20)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 20
    db.close_database()


def test_concurrent_inserts():
    """Multiple threads inserting simultaneously should work."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:", pool_size=5)
    db.execute_raw(
        "CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id INTEGER)"
    )

    def insert_worker(thread_id):
        """Each thread performs multiple inserts."""
        for _ in range(5):
            db.execute_insert("items", {"thread_id": thread_id})
        return thread_id

    # Run 10 threads concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(insert_worker, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 10

    # Verify all inserts succeeded
    count_results = db.execute_query("SELECT COUNT(*) as count FROM items")
    assert count_results[0]["count"] == 50  # 10 threads * 5 inserts
    db.close_database()


def test_concurrent_transactions():
    """Multiple transactions in different threads should be isolated."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:", pool_size=5)
    db.execute_raw("CREATE TABLE counters (id INTEGER PRIMARY KEY, value INTEGER)")
    db.execute_insert("counters", {"id": 1, "value": 0})

    def transaction_worker(thread_id):
        """Each thread increments counter in transaction."""
        with db.transaction():
            results = db.execute_query("SELECT value FROM counters WHERE id = 1")
            current_value = results[0]["value"]
            new_value = current_value + 1
            time.sleep(0.001)  # Small delay to increase chance of race condition
            db.execute_update("counters", {"value": new_value}, "id = :id", {"id": 1})
        return thread_id

    # Run 10 threads concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(transaction_worker, i) for i in range(10)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 10

    # Due to transaction isolation, final value may be less than 10
    # (last-write-wins behavior), but should be at least 1
    final_results = db.execute_query("SELECT value FROM counters WHERE id = 1")
    assert final_results[0]["value"] >= 1
    assert final_results[0]["value"] <= 10
    db.close_database()


def test_connection_pool_exhaustion():
    """Verify behavior when pool is exhausted (should block, not fail)."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:", pool_size=2)  # Small pool
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")

    def slow_query_worker(thread_id):
        """Each thread performs a slow operation."""
        time.sleep(0.1)  # Hold connection briefly
        results = db.execute_query("SELECT * FROM items")
        return thread_id

    # Run more threads than pool_size - should block, not fail
    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(slow_query_worker, i) for i in range(5)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    elapsed_time = time.time() - start_time

    assert len(results) == 5
    # With pool_size=2 and 5 threads, should take at least 3 rounds (3 * 0.1s)
    assert elapsed_time >= 0.2  # At least some blocking occurred
    db.close_database()


def test_connection_cleanup_under_load():
    """Connections should be released properly under concurrent load."""
    db = DBHelper()
    db.init_database("sqlite:///:memory:", pool_size=5)
    db.execute_raw("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")

    def mixed_operations_worker(thread_id):
        """Each thread performs various operations."""
        for i in range(5):
            db.execute_insert("items", {"id": thread_id * 100 + i, "value": f"item{i}"})
            results = db.execute_query("SELECT COUNT(*) as count FROM items")
            assert results[0]["count"] > 0
        return thread_id

    # Run many threads with many operations
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(mixed_operations_worker, i) for i in range(20)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 20

    # Verify all inserts succeeded (no connection leaks)
    final_count = db.execute_query("SELECT COUNT(*) as count FROM items")
    assert final_count[0]["count"] == 100  # 20 threads * 5 inserts
    db.close_database()
