# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Data Scratchpad Tools for structured data analysis.

Provides SQLite working memory tools that allow agents to accumulate,
transform, and query structured data extracted from documents. Enables
multi-document analysis workflows like financial analysis, tax preparation,
and research reviews.
"""

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class ScratchpadToolsMixin:
    """SQLite scratchpad tools for structured data analysis.

    Gives the agent working memory to accumulate, transform, and query
    data extracted from documents. Enables multi-document analysis
    workflows like financial analysis, tax preparation, research reviews.

    Tool registration follows GAIA pattern: register_scratchpad_tools() method.

    The mixin expects self._scratchpad to be set to a ScratchpadService instance
    before tools are used. If not set, tools return helpful error messages.
    """

    _scratchpad = None  # ScratchpadService instance, set by agent init

    def register_scratchpad_tools(self) -> None:
        """Register scratchpad tools for structured data analysis."""
        from gaia.agents.base.tools import tool

        mixin = self  # Capture self for nested functions

        def _ensure_scratchpad() -> bool:
            """Check that scratchpad service is available."""
            if mixin._scratchpad is None:
                return False
            return True

        @tool(atomic=True)
        def create_table(
            table_name: str,
            columns: str,
        ) -> str:
            """Create a table in the scratchpad database for storing extracted data.

            Use this to set up structured storage before processing documents.
            Column definitions follow SQLite syntax.

            Example usage:
                create_table("transactions",
                    "date TEXT, description TEXT, amount REAL, category TEXT, source_file TEXT")
                create_table("research_papers",
                    "title TEXT, authors TEXT, year INTEGER, journal TEXT, abstract TEXT, key_findings TEXT")

            Args:
                table_name: Name for the new table (alphanumeric and underscores only)
                columns: Column definitions in SQLite syntax, e.g. "name TEXT, value REAL, count INTEGER"
            """
            if not _ensure_scratchpad():
                return (
                    "Error: Scratchpad service not initialized. Cannot create tables."
                )

            try:
                result = mixin._scratchpad.create_table(table_name, columns)
                return result
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                logger.error(f"Error creating scratchpad table: {e}")
                return f"Error creating table '{table_name}': {e}"

        @tool(atomic=True)
        def insert_data(
            table_name: str,
            data: str,
        ) -> str:
            """Insert rows into a scratchpad table.

            Data is a JSON array of objects matching the table columns.
            Use this after extracting structured data from a document.

            Example usage:
                insert_data("transactions", '[
                    {"date": "2026-01-05", "description": "NETFLIX", "amount": 15.99,
                     "category": "subscription", "source_file": "jan-statement.pdf"},
                    {"date": "2026-01-07", "description": "WHOLE FOODS", "amount": 87.32,
                     "category": "groceries", "source_file": "jan-statement.pdf"}
                ]')

            Args:
                table_name: Name of the scratchpad table to insert into
                data: JSON array of objects, each object is a row with column:value pairs
            """
            if not _ensure_scratchpad():
                return "Error: Scratchpad service not initialized."

            try:
                # Parse JSON data
                if isinstance(data, str):
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError as e:
                        return f"Error: Invalid JSON data. {e}"
                else:
                    parsed = data

                if not isinstance(parsed, list):
                    return "Error: Data must be a JSON array of objects."

                if not parsed:
                    return "Error: Data array is empty."

                # Validate each item is a dict
                for i, item in enumerate(parsed):
                    if not isinstance(item, dict):
                        return (
                            f"Error: Item {i} is not a JSON object (dict). "
                            "Each item must be a dict with column names as keys."
                        )

                count = mixin._scratchpad.insert_rows(table_name, parsed)
                return f"Inserted {count} row(s) into '{table_name}'."

            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                logger.error(f"Error inserting data: {e}")
                return f"Error inserting data into '{table_name}': {e}"

        @tool(atomic=True)
        def query_data(
            sql: str,
        ) -> str:
            """Run a SQL query against the scratchpad database.

            Use SELECT queries to analyze accumulated data. Supports all SQLite
            functions: SUM, AVG, COUNT, GROUP BY, ORDER BY, JOINs, subqueries, etc.

            IMPORTANT: Table names in queries must use the 'scratch_' prefix.
            For example, if you created a table called 'transactions', query it as 'scratch_transactions'.

            Examples:
                "SELECT category, SUM(amount) as total FROM scratch_transactions GROUP BY category ORDER BY total DESC"
                "SELECT description, COUNT(*) as freq, SUM(amount) as total FROM scratch_transactions GROUP BY description HAVING freq > 1 ORDER BY freq DESC"
                "SELECT strftime('%Y-%m', date) as month, SUM(amount) FROM scratch_transactions GROUP BY month"

            Args:
                sql: SQL SELECT query to execute against the scratchpad database
            """
            if not _ensure_scratchpad():
                return "Error: Scratchpad service not initialized."

            try:
                results = mixin._scratchpad.query_data(sql)

                if not results:
                    return "Query returned no results."

                # Format results as a readable table
                columns = list(results[0].keys())

                # Calculate column widths
                col_widths = {col: len(col) for col in columns}
                for row in results[:100]:  # Use first 100 rows for width calc
                    for col in columns:
                        val = str(row.get(col, ""))
                        col_widths[col] = max(col_widths[col], min(len(val), 40))

                # Build table output
                lines = []

                # Header
                header = " | ".join(col.ljust(col_widths[col])[:40] for col in columns)
                lines.append(header)
                lines.append("-+-".join("-" * col_widths[col] for col in columns))

                # Rows
                for row in results:
                    row_str = " | ".join(
                        str(row.get(col, ""))[:40].ljust(col_widths[col])
                        for col in columns
                    )
                    lines.append(row_str)

                output = "\n".join(lines)

                # Add summary
                output += (
                    f"\n\n({len(results)} row"
                    f"{'s' if len(results) != 1 else ''} returned)"
                )

                return output

            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                logger.error(f"Error querying data: {e}")
                return f"Error executing query: {e}"

        @tool(atomic=True)
        def list_tables() -> str:
            """List all tables in the scratchpad database with their schemas and row counts.

            Use this to see what data has been accumulated so far.
            Shows table names, column definitions, and row counts.
            """
            if not _ensure_scratchpad():
                return "Error: Scratchpad service not initialized."

            try:
                tables = mixin._scratchpad.list_tables()

                if not tables:
                    return (
                        "No scratchpad tables exist yet. "
                        "Use create_table() to create one."
                    )

                lines = ["Scratchpad Tables:\n"]
                for t in tables:
                    cols_str = ", ".join(
                        f"{c['name']} ({c['type']})" for c in t["columns"]
                    )
                    lines.append(f"  {t['name']} ({t['rows']} rows)")
                    lines.append(f"    Columns: {cols_str}")
                    lines.append("")

                return "\n".join(lines)

            except Exception as e:
                logger.error(f"Error listing tables: {e}")
                return f"Error listing tables: {e}"

        @tool(atomic=True)
        def drop_table(table_name: str) -> str:
            """Remove a scratchpad table when analysis is complete.

            Use this to clean up after a task is done. The data will be permanently deleted.

            Args:
                table_name: Name of the scratchpad table to drop
            """
            if not _ensure_scratchpad():
                return "Error: Scratchpad service not initialized."

            try:
                result = mixin._scratchpad.drop_table(table_name)
                return result
            except Exception as e:
                logger.error(f"Error dropping table: {e}")
                return f"Error dropping table '{table_name}': {e}"
