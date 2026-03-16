# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for ScratchpadToolsMixin tool registration and behavior."""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.tools.scratchpad_tools import ScratchpadToolsMixin

# ===== Helper: create a mock agent with captured tool functions =====


def _create_mixin_and_tools():
    """Create a ScratchpadToolsMixin instance and capture registered tools.

    Returns:
        (agent, registered_tools): The mock agent and a dict mapping
        tool function names to their callable implementations.
    """

    class MockAgent(ScratchpadToolsMixin):
        def __init__(self):
            self._scratchpad = None

    registered_tools = {}

    def mock_tool(atomic=True):
        def decorator(func):
            registered_tools[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", mock_tool):
        agent = MockAgent()
        agent.register_scratchpad_tools()

    return agent, registered_tools


# ===== Tool Registration Tests =====


class TestScratchpadToolRegistration:
    """Verify that register_scratchpad_tools() registers all expected tools."""

    def setup_method(self):
        self.agent, self.tools = _create_mixin_and_tools()

    def test_all_five_tools_registered(self):
        """All 5 scratchpad tools should be registered."""
        expected = {
            "create_table",
            "insert_data",
            "query_data",
            "list_tables",
            "drop_table",
        }
        assert set(self.tools.keys()) == expected

    def test_exactly_five_tools(self):
        """No extra tools should be registered."""
        assert len(self.tools) == 5

    def test_tools_are_callable(self):
        """Every registered tool must be callable."""
        for name, func in self.tools.items():
            assert callable(func), f"Tool '{name}' is not callable"


# ===== No-Service Error Tests (all tools, _scratchpad=None) =====


class TestScratchpadToolsNoService:
    """Each tool must return an error string when _scratchpad is None."""

    def setup_method(self):
        self.agent, self.tools = _create_mixin_and_tools()
        # Explicitly confirm scratchpad is None
        assert self.agent._scratchpad is None

    def test_create_table_no_service(self):
        """create_table returns error when scratchpad not initialized."""
        result = self.tools["create_table"]("test_table", "name TEXT, value REAL")
        assert "Error" in result
        assert "not initialized" in result

    def test_insert_data_no_service(self):
        """insert_data returns error when scratchpad not initialized."""
        result = self.tools["insert_data"]("test_table", '[{"name": "x"}]')
        assert "Error" in result
        assert "not initialized" in result

    def test_query_data_no_service(self):
        """query_data returns error when scratchpad not initialized."""
        result = self.tools["query_data"]("SELECT * FROM scratch_test")
        assert "Error" in result
        assert "not initialized" in result

    def test_list_tables_no_service(self):
        """list_tables returns error when scratchpad not initialized."""
        result = self.tools["list_tables"]()
        assert "Error" in result
        assert "not initialized" in result

    def test_drop_table_no_service(self):
        """drop_table returns error when scratchpad not initialized."""
        result = self.tools["drop_table"]("test_table")
        assert "Error" in result
        assert "not initialized" in result


# ===== create_table Tests =====


class TestCreateTable:
    """Test the create_table tool with a mocked scratchpad service."""

    def setup_method(self):
        self.agent, self.tools = _create_mixin_and_tools()
        self.agent._scratchpad = MagicMock()

    def test_success_passthrough(self):
        """create_table returns the service's confirmation message."""
        self.agent._scratchpad.create_table.return_value = (
            "Table 'expenses' created with columns: date TEXT, amount REAL"
        )
        result = self.tools["create_table"]("expenses", "date TEXT, amount REAL")
        assert result == "Table 'expenses' created with columns: date TEXT, amount REAL"
        self.agent._scratchpad.create_table.assert_called_once_with(
            "expenses", "date TEXT, amount REAL"
        )

    def test_value_error_propagation(self):
        """create_table returns formatted error on ValueError from service."""
        self.agent._scratchpad.create_table.side_effect = ValueError(
            "Table limit reached (100). Drop unused tables before creating new ones."
        )
        result = self.tools["create_table"]("overflow", "col TEXT")
        assert result.startswith("Error:")
        assert "Table limit reached" in result

    def test_value_error_empty_columns(self):
        """create_table returns formatted error for empty columns ValueError."""
        self.agent._scratchpad.create_table.side_effect = ValueError(
            "Column definitions cannot be empty."
        )
        result = self.tools["create_table"]("mytable", "")
        assert "Error:" in result
        assert "Column definitions cannot be empty" in result

    def test_generic_exception_handling(self):
        """create_table handles unexpected exceptions gracefully."""
        self.agent._scratchpad.create_table.side_effect = RuntimeError(
            "database is locked"
        )
        result = self.tools["create_table"]("test", "col TEXT")
        assert "Error creating table 'test'" in result
        assert "database is locked" in result


# ===== insert_data Tests =====


class TestInsertData:
    """Test the insert_data tool with a mocked scratchpad service."""

    def setup_method(self):
        self.agent, self.tools = _create_mixin_and_tools()
        self.agent._scratchpad = MagicMock()

    def test_valid_json_string_parsed(self):
        """insert_data parses a valid JSON string and calls insert_rows."""
        self.agent._scratchpad.insert_rows.return_value = 2
        data = json.dumps(
            [
                {"name": "Alice", "score": 95},
                {"name": "Bob", "score": 87},
            ]
        )
        result = self.tools["insert_data"]("students", data)
        assert "Inserted 2 row(s) into 'students'" in result
        # Verify the parsed list was passed to insert_rows
        call_args = self.agent._scratchpad.insert_rows.call_args
        assert call_args[0][0] == "students"
        assert len(call_args[0][1]) == 2
        assert call_args[0][1][0]["name"] == "Alice"

    def test_valid_list_passthrough(self):
        """insert_data passes a Python list directly without JSON parsing."""
        self.agent._scratchpad.insert_rows.return_value = 1
        data = [{"item": "widget", "qty": 10}]
        result = self.tools["insert_data"]("inventory", data)
        assert "Inserted 1 row(s) into 'inventory'" in result
        self.agent._scratchpad.insert_rows.assert_called_once_with("inventory", data)

    def test_invalid_json_string(self):
        """insert_data returns error for malformed JSON string."""
        result = self.tools["insert_data"]("test", "{not valid json")
        assert "Error" in result
        assert "Invalid JSON data" in result

    def test_non_list_data_rejected(self):
        """insert_data rejects JSON that parses to a non-list type."""
        result = self.tools["insert_data"]("test", '{"key": "value"}')
        assert "Error" in result
        assert "JSON array" in result

    def test_non_list_python_object_rejected(self):
        """insert_data rejects a Python dict passed directly."""
        result = self.tools["insert_data"]("test", {"key": "value"})
        assert "Error" in result
        assert "JSON array" in result

    def test_empty_array_rejected(self):
        """insert_data rejects an empty JSON array."""
        result = self.tools["insert_data"]("test", "[]")
        assert "Error" in result
        assert "empty" in result

    def test_empty_python_list_rejected(self):
        """insert_data rejects an empty Python list."""
        result = self.tools["insert_data"]("test", [])
        assert "Error" in result
        assert "empty" in result

    def test_non_dict_items_rejected(self):
        """insert_data rejects array items that are not dicts."""
        data = json.dumps([{"valid": "dict"}, "not a dict", 42])
        result = self.tools["insert_data"]("test", data)
        assert "Error" in result
        assert "Item 1" in result
        assert "not a JSON object" in result

    def test_non_dict_first_item_rejected(self):
        """insert_data rejects when the first item is not a dict."""
        data = json.dumps(["string_item"])
        result = self.tools["insert_data"]("test", data)
        assert "Error" in result
        assert "Item 0" in result

    def test_value_error_from_service(self):
        """insert_data returns formatted error on ValueError from service."""
        self.agent._scratchpad.insert_rows.side_effect = ValueError(
            "Table 'missing' does not exist. Create it first with create_table()."
        )
        data = json.dumps([{"col": "val"}])
        result = self.tools["insert_data"]("missing", data)
        assert "Error:" in result
        assert "does not exist" in result

    def test_value_error_row_limit(self):
        """insert_data returns error when row limit would be exceeded."""
        self.agent._scratchpad.insert_rows.side_effect = ValueError(
            "Row limit would be exceeded. Current: 999999, Adding: 10, Max: 1000000"
        )
        data = json.dumps([{"x": i} for i in range(10)])
        result = self.tools["insert_data"]("full_table", data)
        assert "Error:" in result
        assert "Row limit" in result

    def test_generic_exception_handling(self):
        """insert_data handles unexpected exceptions gracefully."""
        self.agent._scratchpad.insert_rows.side_effect = RuntimeError("disk I/O error")
        data = json.dumps([{"col": "val"}])
        result = self.tools["insert_data"]("test", data)
        assert "Error inserting data into 'test'" in result
        assert "disk I/O error" in result


# ===== query_data Tests =====


class TestQueryData:
    """Test the query_data tool with a mocked scratchpad service."""

    def setup_method(self):
        self.agent, self.tools = _create_mixin_and_tools()
        self.agent._scratchpad = MagicMock()

    def test_formatted_table_output_single_row(self):
        """query_data formats a single-row result as an ASCII table."""
        self.agent._scratchpad.query_data.return_value = [
            {"category": "groceries", "total": 150.50},
        ]
        result = self.tools["query_data"](
            "SELECT category, SUM(amount) as total FROM scratch_t GROUP BY category"
        )
        # Verify header row
        assert "category" in result
        assert "total" in result
        # Verify separator line
        assert "-+-" in result
        # Verify data row
        assert "groceries" in result
        assert "150.5" in result
        # Verify row count summary
        assert "(1 row returned)" in result

    def test_formatted_table_output_multiple_rows(self):
        """query_data formats multiple rows with plural summary."""
        self.agent._scratchpad.query_data.return_value = [
            {"name": "Alice", "score": 95},
            {"name": "Bob", "score": 87},
            {"name": "Charlie", "score": 92},
        ]
        result = self.tools["query_data"]("SELECT name, score FROM scratch_students")
        assert "name" in result
        assert "score" in result
        assert "Alice" in result
        assert "Bob" in result
        assert "Charlie" in result
        assert "(3 rows returned)" in result

    def test_column_width_calculation(self):
        """query_data calculates column widths based on data content."""
        self.agent._scratchpad.query_data.return_value = [
            {"short": "a", "long_column_name": "short_val"},
            {"short": "longer_value", "long_column_name": "x"},
        ]
        result = self.tools["query_data"]("SELECT * FROM scratch_test")
        lines = result.strip().split("\n")
        # Header line
        header = lines[0]
        # The "short" column should be wide enough for "longer_value"
        assert "short" in header
        assert "long_column_name" in header

    def test_table_format_structure(self):
        """query_data produces header, separator, data rows in correct order."""
        self.agent._scratchpad.query_data.return_value = [
            {"col_a": "val1", "col_b": "val2"},
        ]
        result = self.tools["query_data"]("SELECT col_a, col_b FROM scratch_t")
        lines = result.strip().split("\n")
        # Line 0: header
        assert "col_a" in lines[0]
        assert "col_b" in lines[0]
        # Line 1: separator (dashes and +--)
        assert set(lines[1].replace(" ", "")).issubset({"-", "+"})
        # Line 2: data row
        assert "val1" in lines[2]
        assert "val2" in lines[2]

    def test_column_separator_format(self):
        """query_data uses ' | ' as column separator in header and data."""
        self.agent._scratchpad.query_data.return_value = [
            {"x": "1", "y": "2"},
        ]
        result = self.tools["query_data"]("SELECT x, y FROM scratch_t")
        lines = result.strip().split("\n")
        # Header and data rows use " | " separator
        assert " | " in lines[0]
        assert " | " in lines[2]
        # Separator row uses "-+-"
        assert "-+-" in lines[1]

    def test_empty_results(self):
        """query_data returns a message when query returns no rows."""
        self.agent._scratchpad.query_data.return_value = []
        result = self.tools["query_data"]("SELECT * FROM scratch_empty")
        assert "no results" in result.lower()

    def test_none_results(self):
        """query_data handles None return from service as empty results."""
        self.agent._scratchpad.query_data.return_value = None
        result = self.tools["query_data"]("SELECT * FROM scratch_test")
        assert "no results" in result.lower()

    def test_value_error_non_select(self):
        """query_data returns error on ValueError (e.g., non-SELECT query)."""
        self.agent._scratchpad.query_data.side_effect = ValueError(
            "Only SELECT queries are allowed via query_data()."
        )
        result = self.tools["query_data"]("DROP TABLE scratch_test")
        assert "Error:" in result
        assert "SELECT" in result

    def test_value_error_dangerous_keyword(self):
        """query_data returns error on ValueError for dangerous SQL keywords."""
        self.agent._scratchpad.query_data.side_effect = ValueError(
            "Query contains disallowed keyword: DELETE"
        )
        result = self.tools["query_data"](
            "SELECT * FROM scratch_t; DELETE FROM scratch_t"
        )
        assert "Error:" in result
        assert "DELETE" in result

    def test_generic_exception_handling(self):
        """query_data handles unexpected exceptions gracefully."""
        self.agent._scratchpad.query_data.side_effect = RuntimeError(
            "no such table: scratch_missing"
        )
        result = self.tools["query_data"]("SELECT * FROM scratch_missing")
        assert "Error executing query" in result
        assert "no such table" in result

    def test_long_value_truncated_at_40_chars(self):
        """query_data truncates cell values longer than 40 characters."""
        long_val = "A" * 60
        self.agent._scratchpad.query_data.return_value = [
            {"data": long_val},
        ]
        result = self.tools["query_data"]("SELECT data FROM scratch_t")
        # The displayed value should be at most 40 chars of the original
        lines = result.strip().split("\n")
        data_line = lines[2]  # third line is first data row
        # The truncated value should be 40 A's, not 60
        assert "A" * 40 in data_line
        assert "A" * 41 not in data_line

    def test_column_width_capped_at_40(self):
        """query_data caps column widths at 40 characters."""
        long_val = "B" * 60
        self.agent._scratchpad.query_data.return_value = [
            {"col": long_val},
        ]
        result = self.tools["query_data"]("SELECT col FROM scratch_t")
        lines = result.strip().split("\n")
        # Separator line width indicates column width, should be capped at 40
        sep_line = lines[1]
        dash_segment = sep_line.strip()
        assert len(dash_segment) <= 40

    def test_missing_column_value_handled(self):
        """query_data handles rows missing some column keys gracefully."""
        self.agent._scratchpad.query_data.return_value = [
            {"a": "1", "b": "2"},
            {"a": "3"},  # missing "b"
        ]
        result = self.tools["query_data"]("SELECT a, b FROM scratch_t")
        # Should not raise, empty string used for missing key
        assert "1" in result
        assert "3" in result
        assert "(2 rows returned)" in result


# ===== query_data Detailed Formatting Tests =====


class TestQueryDataFormatting:
    """Detailed tests for the ASCII table formatting in query_data."""

    def setup_method(self):
        self.agent, self.tools = _create_mixin_and_tools()
        self.agent._scratchpad = MagicMock()

    def test_full_table_format_matches_expected(self):
        """Verify complete ASCII table output matches expected format."""
        self.agent._scratchpad.query_data.return_value = [
            {"name": "Alice", "age": 30},
            {"name": "Bob", "age": 25},
        ]
        result = self.tools["query_data"]("SELECT name, age FROM scratch_people")
        lines = result.strip().split("\n")

        # Should have: header, separator, 2 data rows, blank line, summary
        # (summary is on its own line after "\n\n")
        assert len(lines) >= 4  # header + separator + 2 data rows minimum

        # Header contains column names with pipe separator
        assert "name" in lines[0]
        assert "age" in lines[0]
        assert " | " in lines[0]

        # Separator uses dashes and -+-
        assert "-+-" in lines[1]
        for char in lines[1]:
            assert char in "-+ "

        # Data rows
        assert "Alice" in lines[2]
        assert "30" in lines[2]
        assert "Bob" in lines[3]
        assert "25" in lines[3]

    def test_single_column_no_pipe_separator(self):
        """Single-column result should not have pipe separators."""
        self.agent._scratchpad.query_data.return_value = [
            {"total": 42},
        ]
        result = self.tools["query_data"]("SELECT COUNT(*) as total FROM scratch_t")
        lines = result.strip().split("\n")
        # With only one column, there are no " | " separators
        assert " | " not in lines[0]
        assert "total" in lines[0]
        assert "42" in lines[2]

    def test_numeric_values_displayed_correctly(self):
        """Numeric values are converted to strings for display."""
        self.agent._scratchpad.query_data.return_value = [
            {"count": 100, "average": 3.14159, "name": "test"},
        ]
        result = self.tools["query_data"]("SELECT count, average, name FROM scratch_t")
        assert "100" in result
        assert "3.14159" in result
        assert "test" in result

    def test_none_value_in_cell(self):
        """None values in cells are displayed as empty strings via str()."""
        self.agent._scratchpad.query_data.return_value = [
            {"a": None, "b": "present"},
        ]
        result = self.tools["query_data"]("SELECT a, b FROM scratch_t")
        assert "present" in result
        # None becomes "None" via str()
        assert "None" in result

    def test_row_count_singular(self):
        """Row count summary uses singular 'row' for 1 result."""
        self.agent._scratchpad.query_data.return_value = [
            {"x": 1},
        ]
        result = self.tools["query_data"]("SELECT x FROM scratch_t")
        assert "(1 row returned)" in result

    def test_row_count_plural(self):
        """Row count summary uses plural 'rows' for multiple results."""
        self.agent._scratchpad.query_data.return_value = [
            {"x": 1},
            {"x": 2},
        ]
        result = self.tools["query_data"]("SELECT x FROM scratch_t")
        assert "(2 rows returned)" in result

    def test_wide_table_alignment(self):
        """Columns are left-justified and aligned in output."""
        self.agent._scratchpad.query_data.return_value = [
            {"short": "a", "medium_col": "hello"},
            {"short": "longer", "medium_col": "hi"},
        ]
        result = self.tools["query_data"]("SELECT short, medium_col FROM scratch_t")
        lines = result.strip().split("\n")

        # All data lines (header + rows) should have " | " at the same position
        pipe_positions = []
        for line in [lines[0], lines[2], lines[3]]:
            pos = line.index(" | ")
            pipe_positions.append(pos)
        # All pipe separators should be at the same column position
        assert (
            len(set(pipe_positions)) == 1
        ), f"Pipe positions not aligned: {pipe_positions}"


# ===== list_tables Tests =====


class TestListTables:
    """Test the list_tables tool with a mocked scratchpad service."""

    def setup_method(self):
        self.agent, self.tools = _create_mixin_and_tools()
        self.agent._scratchpad = MagicMock()

    def test_formatted_output_with_tables(self):
        """list_tables returns formatted table info."""
        self.agent._scratchpad.list_tables.return_value = [
            {
                "name": "expenses",
                "columns": [
                    {"name": "date", "type": "TEXT"},
                    {"name": "amount", "type": "REAL"},
                    {"name": "category", "type": "TEXT"},
                ],
                "rows": 42,
            },
        ]
        result = self.tools["list_tables"]()
        assert "Scratchpad Tables:" in result
        assert "expenses" in result
        assert "42 rows" in result
        assert "date (TEXT)" in result
        assert "amount (REAL)" in result
        assert "category (TEXT)" in result

    def test_multiple_tables_listed(self):
        """list_tables shows info for all tables."""
        self.agent._scratchpad.list_tables.return_value = [
            {
                "name": "transactions",
                "columns": [{"name": "id", "type": "INTEGER"}],
                "rows": 100,
            },
            {
                "name": "summaries",
                "columns": [{"name": "category", "type": "TEXT"}],
                "rows": 5,
            },
        ]
        result = self.tools["list_tables"]()
        assert "transactions" in result
        assert "100 rows" in result
        assert "summaries" in result
        assert "5 rows" in result

    def test_empty_list_output(self):
        """list_tables returns helpful message when no tables exist."""
        self.agent._scratchpad.list_tables.return_value = []
        result = self.tools["list_tables"]()
        assert "No scratchpad tables exist" in result
        assert "create_table()" in result

    def test_zero_row_table(self):
        """list_tables shows 0 rows for an empty table."""
        self.agent._scratchpad.list_tables.return_value = [
            {
                "name": "empty_table",
                "columns": [{"name": "col", "type": "TEXT"}],
                "rows": 0,
            },
        ]
        result = self.tools["list_tables"]()
        assert "empty_table" in result
        assert "0 rows" in result

    def test_columns_formatting(self):
        """list_tables formats columns as 'name (TYPE)' comma-separated."""
        self.agent._scratchpad.list_tables.return_value = [
            {
                "name": "people",
                "columns": [
                    {"name": "first_name", "type": "TEXT"},
                    {"name": "age", "type": "INTEGER"},
                ],
                "rows": 10,
            },
        ]
        result = self.tools["list_tables"]()
        assert "Columns: first_name (TEXT), age (INTEGER)" in result

    def test_generic_exception_handling(self):
        """list_tables handles unexpected exceptions gracefully."""
        self.agent._scratchpad.list_tables.side_effect = RuntimeError(
            "database connection lost"
        )
        result = self.tools["list_tables"]()
        assert "Error listing tables" in result
        assert "database connection lost" in result


# ===== drop_table Tests =====


class TestDropTable:
    """Test the drop_table tool with a mocked scratchpad service."""

    def setup_method(self):
        self.agent, self.tools = _create_mixin_and_tools()
        self.agent._scratchpad = MagicMock()

    def test_success_passthrough(self):
        """drop_table returns the service's confirmation message."""
        self.agent._scratchpad.drop_table.return_value = "Table 'expenses' dropped."
        result = self.tools["drop_table"]("expenses")
        assert result == "Table 'expenses' dropped."
        self.agent._scratchpad.drop_table.assert_called_once_with("expenses")

    def test_table_does_not_exist(self):
        """drop_table returns service message for non-existent table."""
        self.agent._scratchpad.drop_table.return_value = (
            "Table 'missing' does not exist."
        )
        result = self.tools["drop_table"]("missing")
        assert "does not exist" in result

    def test_generic_exception_handling(self):
        """drop_table handles unexpected exceptions gracefully."""
        self.agent._scratchpad.drop_table.side_effect = RuntimeError(
            "permission denied"
        )
        result = self.tools["drop_table"]("locked_table")
        assert "Error dropping table 'locked_table'" in result
        assert "permission denied" in result


# ===== Edge Cases and Integration-style Tests =====


class TestScratchpadToolsEdgeCases:
    """Edge cases and cross-tool interaction scenarios."""

    def setup_method(self):
        self.agent, self.tools = _create_mixin_and_tools()
        self.agent._scratchpad = MagicMock()

    def test_insert_data_with_unicode_json(self):
        """insert_data handles Unicode characters in JSON data."""
        self.agent._scratchpad.insert_rows.return_value = 1
        data = json.dumps([{"name": "Rene", "city": "Zurich"}])
        result = self.tools["insert_data"]("places", data)
        assert "Inserted 1 row(s)" in result

    def test_insert_data_with_nested_json_in_string_field(self):
        """insert_data handles string fields that contain JSON-like content."""
        self.agent._scratchpad.insert_rows.return_value = 1
        data = json.dumps([{"description": '{"nested": true}', "value": 42}])
        result = self.tools["insert_data"]("data", data)
        assert "Inserted 1 row(s)" in result

    def test_insert_data_large_batch(self):
        """insert_data handles a large batch of rows."""
        self.agent._scratchpad.insert_rows.return_value = 500
        data = json.dumps([{"idx": i, "val": f"item_{i}"} for i in range(500)])
        result = self.tools["insert_data"]("big_table", data)
        assert "Inserted 500 row(s)" in result

    def test_create_table_with_complex_columns(self):
        """create_table passes complex column definitions to service."""
        self.agent._scratchpad.create_table.return_value = (
            "Table 'financial' created with columns: "
            "date TEXT, amount REAL, category TEXT, notes TEXT, source TEXT"
        )
        result = self.tools["create_table"](
            "financial",
            "date TEXT, amount REAL, category TEXT, notes TEXT, source TEXT",
        )
        assert "financial" in result
        self.agent._scratchpad.create_table.assert_called_once()

    def test_query_data_sql_passed_verbatim(self):
        """query_data passes the SQL string to the service unchanged."""
        self.agent._scratchpad.query_data.return_value = [{"count": 5}]
        sql = (
            "SELECT category, COUNT(*) as count "
            "FROM scratch_expenses "
            "GROUP BY category "
            "ORDER BY count DESC"
        )
        self.tools["query_data"](sql)
        self.agent._scratchpad.query_data.assert_called_once_with(sql)

    def test_scratchpad_set_after_init(self):
        """Tools work when _scratchpad is set after registration."""
        agent, tools = _create_mixin_and_tools()
        # Initially no service
        result = tools["list_tables"]()
        assert "not initialized" in result

        # Now set the service
        agent._scratchpad = MagicMock()
        agent._scratchpad.list_tables.return_value = []
        result = tools["list_tables"]()
        assert "No scratchpad tables exist" in result

    def test_scratchpad_reset_to_none(self):
        """Tools return error if _scratchpad is reset to None."""
        self.agent._scratchpad = None
        result = self.tools["create_table"]("test", "col TEXT")
        assert "not initialized" in result

    def test_insert_data_number_as_data_type(self):
        """insert_data rejects a plain number passed as data."""
        result = self.tools["insert_data"]("test", "42")
        assert "Error" in result
        assert "JSON array" in result

    def test_insert_data_string_literal_as_data(self):
        """insert_data rejects a plain string literal (not array) as JSON."""
        result = self.tools["insert_data"]("test", '"just a string"')
        assert "Error" in result
        assert "JSON array" in result

    def test_insert_data_boolean_json(self):
        """insert_data rejects boolean JSON."""
        result = self.tools["insert_data"]("test", "true")
        assert "Error" in result
        assert "JSON array" in result

    def test_insert_data_null_json(self):
        """insert_data rejects null JSON."""
        result = self.tools["insert_data"]("test", "null")
        assert "Error" in result
        assert "JSON array" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
