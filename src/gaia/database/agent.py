# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""DatabaseAgent - Agent with built-in database tools."""

from typing import Any, Dict, Optional

from gaia.agents.base import Agent, tool
from gaia.database.mixin import DatabaseMixin


class DatabaseAgent(Agent, DatabaseMixin):
    """
    Agent with built-in SQLite database tools.

    Extends Agent with database capabilities, automatically registering
    tools that allow the LLM to query and modify the database.

    Example:
        class PatientAgent(DatabaseAgent):
            def __init__(self, **kwargs):
                super().__init__(db_path="data/patients.db", **kwargs)

                if not self.table_exists("patients"):
                    self.execute('''
                        CREATE TABLE patients (
                            id INTEGER PRIMARY KEY,
                            name TEXT NOT NULL,
                            dob TEXT
                        )
                    ''')

            def _get_system_prompt(self) -> str:
                return "You help manage patient records."

        # LLM can now use: db_query, db_insert, db_update, db_delete

    Security Note:
        The db_query tool allows the LLM to execute arbitrary SELECT queries.
        For production use, consider overriding _register_db_tools() to expose
        only domain-specific, validated operations.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        **kwargs,
    ):
        """
        Initialize DatabaseAgent.

        Args:
            db_path: Path to SQLite database file, or ":memory:" for in-memory.
                     Parent directories are created automatically.
            **kwargs: Additional arguments passed to Agent.
        """
        super().__init__(**kwargs)
        self.init_db(db_path)
        self._register_db_tools()

    def _register_db_tools(self) -> None:
        """Register database tools for LLM use."""
        agent = self

        @tool
        def db_query(sql: str, params: Optional[Dict[str, Any]] = None) -> Dict:
            """
            Execute a SELECT query and return results.

            Args:
                sql: SQL SELECT query with :param placeholders
                params: Optional dictionary of parameter values

            Returns:
                Dictionary with 'rows' (list of row dicts) and 'count'

            Example:
                db_query("SELECT * FROM users WHERE age > :min_age", {"min_age": 18})
            """
            rows = agent.query(sql, params or {})
            return {"rows": rows, "count": len(rows)}

        @tool
        def db_insert(table: str, data: Dict[str, Any]) -> Dict:
            """
            Insert a row into a table.

            Args:
                table: Table name
                data: Dictionary of column names to values

            Returns:
                Dictionary with 'id' (the inserted row's ID) and 'success'

            Example:
                db_insert("users", {"name": "Alice", "email": "alice@example.com"})
            """
            row_id = agent.insert(table, data)
            return {"id": row_id, "success": True}

        @tool
        def db_update(
            table: str, data: Dict[str, Any], where: str, params: Dict[str, Any]
        ) -> Dict:
            """
            Update rows matching a condition.

            Args:
                table: Table name
                data: Dictionary of column names to new values
                where: WHERE clause with :param placeholders (without WHERE keyword)
                params: Dictionary of parameter values for WHERE clause

            Returns:
                Dictionary with 'updated' (number of rows affected)

            Example:
                db_update("users", {"email": "new@example.com"}, "id = :id", {"id": 42})
            """
            count = agent.update(table, data, where, params)
            return {"updated": count}

        @tool
        def db_delete(table: str, where: str, params: Dict[str, Any]) -> Dict:
            """
            Delete rows matching a condition.

            Args:
                table: Table name
                where: WHERE clause with :param placeholders (without WHERE keyword)
                params: Dictionary of parameter values for WHERE clause

            Returns:
                Dictionary with 'deleted' (number of rows deleted)

            Example:
                db_delete("sessions", "expires_at < :now", {"now": "2024-01-01"})
            """
            count = agent.delete(table, where, params)
            return {"deleted": count}

        @tool
        def db_tables() -> Dict:
            """
            List all tables in the database.

            Returns:
                Dictionary with 'tables' (list of table names)
            """
            rows = agent.query(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return {"tables": [row["name"] for row in rows]}

        @tool
        def db_schema(table: str) -> Dict:
            """
            Get the schema of a table.

            Args:
                table: Table name

            Returns:
                Dictionary with 'columns' (list of column info dicts)
            """
            rows = agent.query(f"PRAGMA table_info({table})")
            columns = [
                {
                    "name": row["name"],
                    "type": row["type"],
                    "nullable": not row["notnull"],
                    "primary_key": bool(row["pk"]),
                }
                for row in rows
            ]
            return {"table": table, "columns": columns}
