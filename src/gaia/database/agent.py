# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""DatabaseAgent - Agent with built-in database tools."""

from typing import Any, Dict, Optional, Tuple

from gaia.agents.base import Agent, tool
from gaia.database.mixin import DatabaseMixin
from gaia.database.sql_safety import (
    build_where,
    coerce_bool,
    coerce_conditions,
    coerce_object,
    validate_identifier,
)


def _resolve_where(
    where: Any, all_rows: Any, table: str, context: str
) -> Tuple[str, Dict[str, Any]]:
    """Turn a tool's ``where``/``all_rows`` pair into (clause, params)."""
    conditions = coerce_conditions(where, context)
    verb = "delete" if context == "db_delete" else "update"
    # Strict parse: a truthy string like "no" must not authorize a mass delete.
    all_rows = coerce_bool(all_rows, "all_rows", context)
    if all_rows:
        if conditions:
            raise ValueError(
                f"{context}: pass either 'where' conditions or all_rows=True, "
                f"not both. Got {len(conditions)} condition(s) with "
                f"all_rows=True."
            )
        return "1 = 1", {}
    if not conditions:
        raise ValueError(
            f"{context}: refusing to {verb} every row in {table!r}. Supply at "
            f"least one condition, or pass all_rows=True to confirm a "
            f"full-table {verb}."
        )
    return build_where(conditions, context=context)


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
        The LLM cannot supply raw SQL to the write tools:

        - db_query is read-only. A SQLite authorizer blocks writes, DDL,
          ATTACH, and every pragma outside a schema-inspection allowlist.
        - db_update and db_delete take structured conditions, never a WHERE
          string. GAIA builds the predicate and binds every value.
        - Table and column names are validated as bare identifiers.
        - A full-table update or delete requires an explicit all_rows=True.

        Not covered: these tools still grant the LLM read and write access to
        *every* table in the database. There is no per-table authorization.
        Point db_path at a database that holds only what this agent should be
        able to change, or override _register_db_tools() to expose
        domain-specific operations instead.
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
            Execute a read-only SELECT query and return results.

            Writes, DDL, and ATTACH are refused by SQLite itself. Use
            db_insert, db_update, or db_delete to modify data.

            Args:
                sql: SQL SELECT query with :param placeholders
                params: Optional dictionary of parameter values

            Returns:
                Dictionary with 'rows' (list of row dicts) and 'count'

            Example:
                db_query("SELECT * FROM users WHERE age > :min_age", {"min_age": 18})
            """
            params = coerce_object(params, "params", "db_query") if params else {}
            rows = agent.query_readonly(sql, params)
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
            table = validate_identifier(table, "table name", "db_insert")
            data = coerce_object(data, "data", "db_insert")
            row_id = agent.insert(table, data)
            return {"id": row_id, "success": True}

        @tool
        def db_update(
            table: str,
            data: Dict[str, Any],
            where: Any,
            all_rows: bool = False,
        ) -> Dict:
            """
            Update rows, where is a list of {"column","op","value"} conditions.

            Conditions are combined with AND. Raw SQL is not accepted.

            Args:
                table: Table name
                data: Dictionary of column names to new values
                where: List of condition objects. Each has "column", an
                    optional "op" (default "="), and "value". Allowed ops:
                    =, !=, <, <=, >, >=, LIKE, NOT LIKE, IN, NOT IN, IS NULL,
                    IS NOT NULL. "IN"/"NOT IN" take a list value;
                    "IS NULL"/"IS NOT NULL" take no value.
                all_rows: Set true (or the string "true") to update every
                    row. Required when where is empty, so a full-table update
                    is always deliberate.

            Returns:
                Dictionary with 'updated' (number of rows affected)

            Example:
                db_update("users", {"email": "new@x.com"},
                          [{"column": "id", "op": "=", "value": 42}])
                db_update("users", {"active": 0},
                          [{"column": "role", "op": "IN",
                            "value": ["guest", "trial"]},
                           {"column": "last_seen", "op": "IS NULL"}])
                db_update("users", {"active": 1}, [], all_rows=True)
            """
            table = validate_identifier(table, "table name", "db_update")
            data = coerce_object(data, "data", "db_update")
            clause, params = _resolve_where(where, all_rows, table, "db_update")
            return {"updated": agent.update(table, data, clause, params)}

        @tool
        def db_delete(table: str, where: Any, all_rows: bool = False) -> Dict:
            """
            Delete rows, where is a list of {"column","op","value"} conditions.

            Conditions are combined with AND. Raw SQL is not accepted.

            Args:
                table: Table name
                where: List of condition objects. Each has "column", an
                    optional "op" (default "="), and "value". Allowed ops:
                    =, !=, <, <=, >, >=, LIKE, NOT LIKE, IN, NOT IN, IS NULL,
                    IS NOT NULL. "IN"/"NOT IN" take a list value;
                    "IS NULL"/"IS NOT NULL" take no value.
                all_rows: Set true (or the string "true") to delete every
                    row. Required when where is empty, so a full-table delete
                    is always deliberate.

            Returns:
                Dictionary with 'deleted' (number of rows deleted)

            Example:
                db_delete("sessions", [{"column": "id", "op": "=", "value": 7}])
                db_delete("sessions", [{"column": "expires_at", "op": "<",
                                        "value": "2024-01-01"}])
                db_delete("sessions", [], all_rows=True)
            """
            table = validate_identifier(table, "table name", "db_delete")
            clause, params = _resolve_where(where, all_rows, table, "db_delete")
            return {"deleted": agent.delete(table, clause, params)}

        @tool
        def db_tables() -> Dict:
            """
            List all tables in the database.

            Returns:
                Dictionary with 'tables' (list of table names)
            """
            rows = agent.query_readonly(
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
            # PRAGMA can't bind parameters, so the name is interpolated.
            table = validate_identifier(table, "table name", "db_schema")
            rows = agent.query_readonly(f"PRAGMA table_info({table})")
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
