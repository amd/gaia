# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for ScratchpadService."""

from unittest.mock import patch

import pytest

from gaia.scratchpad.service import ScratchpadService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scratchpad(tmp_path):
    """Create a ScratchpadService backed by a temp database."""
    db_path = str(tmp_path / "test_scratchpad.db")
    service = ScratchpadService(db_path=db_path)
    yield service
    service.close_db()


# ---------------------------------------------------------------------------
# Table creation tests
# ---------------------------------------------------------------------------


class TestCreateTable:
    """Tests for scratchpad table creation."""

    def test_create_table(self, scratchpad):
        """Create a table and verify it exists."""
        scratchpad.create_table("expenses", "date TEXT, amount REAL, note TEXT")

        tables = scratchpad.list_tables()
        assert len(tables) == 1
        assert tables[0]["name"] == "expenses"

    def test_create_table_returns_confirmation(self, scratchpad):
        """Check return message contains table name and columns."""
        result = scratchpad.create_table(
            "sales", "product TEXT, quantity INTEGER"
        )

        assert isinstance(result, str)
        assert "sales" in result
        assert "product TEXT, quantity INTEGER" in result

    def test_create_table_sanitizes_name(self, scratchpad):
        """Name with special characters gets cleaned to alphanumeric + underscore."""
        result = scratchpad.create_table(
            "my-data!@#table", "value TEXT"
        )

        # Special chars replaced with underscores
        assert "my_data___table" in result

        tables = scratchpad.list_tables()
        assert len(tables) == 1
        assert tables[0]["name"] == "my_data___table"

    def test_create_table_rejects_empty_columns(self, scratchpad):
        """Raises ValueError when columns string is empty."""
        with pytest.raises(ValueError, match="empty"):
            scratchpad.create_table("bad_table", "")

        with pytest.raises(ValueError, match="empty"):
            scratchpad.create_table("bad_table", "   ")

    def test_create_table_limit(self, scratchpad):
        """Creating more than MAX_TABLES raises ValueError."""
        # Temporarily set MAX_TABLES to 3 for speed
        with patch.object(ScratchpadService, "MAX_TABLES", 3):
            scratchpad.create_table("t1", "id INTEGER")
            scratchpad.create_table("t2", "id INTEGER")
            scratchpad.create_table("t3", "id INTEGER")

            with pytest.raises(ValueError, match="Table limit reached"):
                scratchpad.create_table("t4", "id INTEGER")

    def test_create_table_rejects_empty_name(self, scratchpad):
        """Raises ValueError when table name is empty or None."""
        with pytest.raises(ValueError, match="empty"):
            scratchpad.create_table("", "id INTEGER")

    def test_create_table_idempotent(self, scratchpad):
        """Creating the same table twice does not raise (CREATE IF NOT EXISTS)."""
        scratchpad.create_table("dup", "id INTEGER")
        result = scratchpad.create_table("dup", "id INTEGER")

        assert isinstance(result, str)
        tables = scratchpad.list_tables()
        assert len(tables) == 1


# ---------------------------------------------------------------------------
# Row insertion tests
# ---------------------------------------------------------------------------


class TestInsertRows:
    """Tests for row insertion."""

    def test_insert_rows(self, scratchpad):
        """Create table, insert rows, verify count."""
        scratchpad.create_table("items", "name TEXT, price REAL")

        data = [
            {"name": "Apple", "price": 1.50},
            {"name": "Banana", "price": 0.75},
            {"name": "Cherry", "price": 3.00},
        ]
        count = scratchpad.insert_rows("items", data)

        assert count == 3

        tables = scratchpad.list_tables()
        assert tables[0]["rows"] == 3

    def test_insert_rows_nonexistent_table(self, scratchpad):
        """Raises ValueError for nonexistent table."""
        with pytest.raises(ValueError, match="does not exist"):
            scratchpad.insert_rows("ghost_table", [{"val": 1}])

    def test_insert_rows_empty_list(self, scratchpad):
        """Inserting empty list returns 0."""
        scratchpad.create_table("empty_test", "val INTEGER")

        count = scratchpad.insert_rows("empty_test", [])
        assert count == 0

    def test_insert_rows_large_batch(self, scratchpad):
        """Insert a larger batch of rows successfully."""
        scratchpad.create_table("batch", "idx INTEGER, label TEXT")

        data = [{"idx": i, "label": f"row_{i}"} for i in range(100)]
        count = scratchpad.insert_rows("batch", data)

        assert count == 100

        tables = scratchpad.list_tables()
        assert tables[0]["rows"] == 100


# ---------------------------------------------------------------------------
# Query tests
# ---------------------------------------------------------------------------


class TestQueryData:
    """Tests for query_data with SELECT and security restrictions."""

    def test_query_data_select(self, scratchpad):
        """Create table, insert data, query with SELECT."""
        scratchpad.create_table("orders", "product TEXT, qty INTEGER, price REAL")
        scratchpad.insert_rows(
            "orders",
            [
                {"product": "Widget", "qty": 10, "price": 5.0},
                {"product": "Gadget", "qty": 3, "price": 15.0},
                {"product": "Widget", "qty": 7, "price": 5.0},
            ],
        )

        results = scratchpad.query_data(
            "SELECT * FROM scratch_orders WHERE product = 'Widget'"
        )
        assert len(results) == 2
        assert all(r["product"] == "Widget" for r in results)

    def test_query_data_aggregation(self, scratchpad):
        """Test SUM, COUNT, GROUP BY queries."""
        scratchpad.create_table("sales", "region TEXT, amount REAL")
        scratchpad.insert_rows(
            "sales",
            [
                {"region": "North", "amount": 100.0},
                {"region": "North", "amount": 200.0},
                {"region": "South", "amount": 150.0},
            ],
        )

        # COUNT
        results = scratchpad.query_data(
            "SELECT COUNT(*) AS cnt FROM scratch_sales"
        )
        assert results[0]["cnt"] == 3

        # SUM + GROUP BY
        results = scratchpad.query_data(
            "SELECT region, SUM(amount) AS total "
            "FROM scratch_sales GROUP BY region ORDER BY region"
        )
        assert len(results) == 2
        assert results[0]["region"] == "North"
        assert results[0]["total"] == 300.0
        assert results[1]["region"] == "South"
        assert results[1]["total"] == 150.0

    def test_query_data_rejects_insert(self, scratchpad):
        """INSERT statement raises ValueError."""
        scratchpad.create_table("safe", "val TEXT")

        with pytest.raises(ValueError, match="Only SELECT"):
            scratchpad.query_data("INSERT INTO scratch_safe VALUES ('hack')")

    def test_query_data_rejects_drop(self, scratchpad):
        """DROP statement raises ValueError."""
        scratchpad.create_table("safe", "val TEXT")

        with pytest.raises(ValueError, match="Only SELECT"):
            scratchpad.query_data("DROP TABLE scratch_safe")

    def test_query_data_rejects_delete(self, scratchpad):
        """DELETE statement raises ValueError."""
        scratchpad.create_table("safe", "val TEXT")

        with pytest.raises(ValueError, match="Only SELECT"):
            scratchpad.query_data("DELETE FROM scratch_safe WHERE 1=1")

    def test_query_data_rejects_update(self, scratchpad):
        """UPDATE statement raises ValueError."""
        scratchpad.create_table("safe", "val TEXT")

        with pytest.raises(ValueError, match="Only SELECT"):
            scratchpad.query_data("UPDATE scratch_safe SET val='hacked'")

    def test_query_data_rejects_dangerous_in_subquery(self, scratchpad):
        """Dangerous keywords embedded in SELECT are blocked."""
        scratchpad.create_table("safe", "val TEXT")

        with pytest.raises(ValueError, match="disallowed keyword"):
            scratchpad.query_data(
                "SELECT * FROM scratch_safe; DROP TABLE scratch_safe"
            )

    def test_query_data_rejects_alter(self, scratchpad):
        """ALTER statement raises ValueError."""
        with pytest.raises(ValueError, match="Only SELECT"):
            scratchpad.query_data("ALTER TABLE scratch_safe ADD COLUMN hack TEXT")


# ---------------------------------------------------------------------------
# Table listing tests
# ---------------------------------------------------------------------------


class TestListTables:
    """Tests for list_tables."""

    def test_list_tables(self, scratchpad):
        """Create multiple tables, verify list."""
        scratchpad.create_table("alpha", "val TEXT")
        scratchpad.create_table("beta", "val INTEGER")
        scratchpad.create_table("gamma", "val REAL")

        tables = scratchpad.list_tables()
        assert len(tables) == 3

        table_names = {t["name"] for t in tables}
        assert table_names == {"alpha", "beta", "gamma"}

    def test_list_tables_empty(self, scratchpad):
        """Empty scratchpad returns empty list."""
        tables = scratchpad.list_tables()
        assert tables == []

    def test_list_tables_includes_schema(self, scratchpad):
        """list_tables returns column schema information."""
        scratchpad.create_table("typed", "name TEXT, age INTEGER, score REAL")

        tables = scratchpad.list_tables()
        assert len(tables) == 1

        columns = tables[0]["columns"]
        col_names = [c["name"] for c in columns]
        assert "name" in col_names
        assert "age" in col_names
        assert "score" in col_names

    def test_list_tables_includes_row_count(self, scratchpad):
        """list_tables returns correct row count."""
        scratchpad.create_table("counted", "val INTEGER")
        scratchpad.insert_rows("counted", [{"val": i} for i in range(5)])

        tables = scratchpad.list_tables()
        assert tables[0]["rows"] == 5


# ---------------------------------------------------------------------------
# Table dropping tests
# ---------------------------------------------------------------------------


class TestDropTable:
    """Tests for drop_table and clear_all."""

    def test_drop_table(self, scratchpad):
        """Create then drop, verify gone."""
        scratchpad.create_table("temp", "val TEXT")
        assert len(scratchpad.list_tables()) == 1

        result = scratchpad.drop_table("temp")
        assert "dropped" in result.lower()
        assert len(scratchpad.list_tables()) == 0

    def test_drop_nonexistent_table(self, scratchpad):
        """Returns message, no error."""
        result = scratchpad.drop_table("nonexistent")
        assert isinstance(result, str)
        assert "does not exist" in result.lower()

    def test_clear_all(self, scratchpad):
        """Create multiple tables, clear_all, verify empty."""
        scratchpad.create_table("t1", "val TEXT")
        scratchpad.create_table("t2", "val TEXT")
        scratchpad.create_table("t3", "val TEXT")

        assert len(scratchpad.list_tables()) == 3

        result = scratchpad.clear_all()
        assert "3" in result
        assert len(scratchpad.list_tables()) == 0

    def test_clear_all_empty(self, scratchpad):
        """clear_all on empty scratchpad returns zero count."""
        result = scratchpad.clear_all()
        assert "0" in result


# ---------------------------------------------------------------------------
# Name sanitization tests
# ---------------------------------------------------------------------------


class TestSanitizeName:
    """Tests for _sanitize_name."""

    def test_sanitize_name_special_chars(self, scratchpad):
        """Verify _sanitize_name cleans special characters to underscores."""
        assert scratchpad._sanitize_name("hello-world") == "hello_world"
        assert scratchpad._sanitize_name("my table!") == "my_table_"
        assert scratchpad._sanitize_name("test@#$%") == "test____"

    def test_sanitize_name_digit_prefix(self, scratchpad):
        """Name starting with digit gets t_ prefix."""
        assert scratchpad._sanitize_name("123abc") == "t_123abc"
        assert scratchpad._sanitize_name("9tables") == "t_9tables"

    def test_sanitize_name_valid_name_unchanged(self, scratchpad):
        """Valid names with only alphanumerics and underscores pass through."""
        assert scratchpad._sanitize_name("my_table") == "my_table"
        assert scratchpad._sanitize_name("TestData") == "TestData"
        assert scratchpad._sanitize_name("a1b2c3") == "a1b2c3"

    def test_sanitize_name_empty_raises(self, scratchpad):
        """Empty or None name raises ValueError."""
        with pytest.raises(ValueError, match="empty"):
            scratchpad._sanitize_name("")

        with pytest.raises(ValueError, match="empty"):
            scratchpad._sanitize_name(None)

    def test_sanitize_name_truncates_long_names(self, scratchpad):
        """Names longer than 64 characters are truncated."""
        long_name = "a" * 100
        result = scratchpad._sanitize_name(long_name)
        assert len(result) == 64


# ---------------------------------------------------------------------------
# Table prefix isolation tests
# ---------------------------------------------------------------------------


class TestTablePrefixIsolation:
    """Tests verifying that scratchpad tables use scratch_ prefix in actual DB."""

    def test_table_prefix_isolation(self, scratchpad):
        """Verify tables use scratch_ prefix in actual DB."""
        scratchpad.create_table("mydata", "val TEXT")

        # The actual SQLite table should be named 'scratch_mydata'
        assert scratchpad.table_exists("scratch_mydata")

        # But list_tables should show the user-facing name without prefix
        tables = scratchpad.list_tables()
        assert len(tables) == 1
        assert tables[0]["name"] == "mydata"

    def test_prefix_does_not_collide_with_other_tables(self, scratchpad):
        """Non-scratch_ tables in the same DB are not listed."""
        # Create a non-scratch table directly
        scratchpad.execute("CREATE TABLE IF NOT EXISTS other_data (id INTEGER)")

        # list_tables should not include it
        tables = scratchpad.list_tables()
        assert len(tables) == 0

        # Create a scratch table and verify only it shows
        scratchpad.create_table("real", "val TEXT")
        tables = scratchpad.list_tables()
        assert len(tables) == 1
        assert tables[0]["name"] == "real"


# ---------------------------------------------------------------------------
# Size estimation tests
# ---------------------------------------------------------------------------


class TestGetSizeBytes:
    """Tests for get_size_bytes estimation."""

    def test_get_size_bytes_empty(self, scratchpad):
        """Empty scratchpad returns 0 bytes."""
        assert scratchpad.get_size_bytes() == 0

    def test_get_size_bytes_with_data(self, scratchpad):
        """Scratchpad with data returns nonzero estimate."""
        scratchpad.create_table("sized", "val TEXT")
        scratchpad.insert_rows(
            "sized",
            [{"val": f"row_{i}"} for i in range(10)],
        )

        size = scratchpad.get_size_bytes()
        assert size > 0
        # 10 rows * 200 bytes estimated = 2000
        assert size == 10 * 200
