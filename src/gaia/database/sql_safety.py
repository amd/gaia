# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""SQL safety primitives shared by DatabaseMixin and DatabaseAgent.

SQLite binds *values*, never identifiers or predicates. Anything that reaches
the SQL string itself — table names, column names, WHERE fragments — has to be
validated or constructed rather than interpolated. This module is the single
place that happens, so the guarantee can be audited in one file.
"""

import json
import re
import sqlite3
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Sequence, Tuple

# fullmatch, not match: `$` would also accept a trailing newline.
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# SQLite's default bound-variable ceiling is 999 on builds before 3.32.
_MAX_IN_VALUES = 900

# Operator allowlist for structured conditions. Deliberately closed and
# conjunction-only: OR is the entire injection payload class ("OR 1=1"), so an
# AND-chain of allowlisted predicates can only ever narrow the row set.
SCALAR_OPS = ("=", "!=", "<", "<=", ">", ">=", "LIKE", "NOT LIKE")
LIST_OPS = ("IN", "NOT IN")
NULLARY_OPS = ("IS NULL", "IS NOT NULL")
ALL_OPS = SCALAR_OPS + LIST_OPS + NULLARY_OPS

_ALLOWED_KEYS = frozenset({"column", "op", "value"})

# Types sqlite3 can bind directly.
_BINDABLE = (type(None), bool, int, float, str, bytes)

# Pragmas that only read schema metadata. PRAGMA is a single authorizer action
# regardless of which pragma ran, so the allowlist is the only gate.
READONLY_PRAGMAS = frozenset(
    {"table_info", "table_xinfo", "index_list", "index_info", "foreign_key_list"}
)

# Where-params are ``__w<int>``; UPDATE's set-params are ``__set_<identifier>``.
# The two languages diverge at character three, so they can never collide.
_WHERE_PARAM_PREFIX = "__w"

_OPS_HELP = ", ".join(ALL_OPS)


def validate_identifier(name: Any, kind: str, context: str) -> str:
    """Return *name* if it is a bare SQL identifier, else raise ValueError.

    Args:
        name: The candidate identifier.
        kind: Human label for the error, e.g. ``"table name"``.
        context: Caller name for the error, e.g. ``"db_delete"``.

    Raises:
        ValueError: If *name* is not a string matching ``[A-Za-z_][A-Za-z0-9_]*``.
    """
    if not isinstance(name, str):
        raise ValueError(
            f"{context}: {kind} must be a string, got {type(name).__name__}. "
            f"Pass a bare identifier such as 'items'."
        )
    if not _IDENTIFIER_RE.fullmatch(name):
        hint = (
            "Call db_tables to list valid table names."
            if kind == "table name"
            else "Call db_schema('<table>') to list a table's columns."
        )
        raise ValueError(
            f"{context}: invalid {kind} {name!r}. {kind.capitalize()}s must be "
            f"bare identifiers matching [A-Za-z_][A-Za-z0-9_]*. {hint}"
        )
    return name


def coerce_conditions(where: Any, context: str) -> List[Dict[str, Any]]:
    """Normalize *where* to a list of condition dicts.

    Accepts the list itself or its JSON encoding, because the tool schema
    advertises this parameter as a string (see ``gaia.agents.base.tools``).

    Raises:
        ValueError: If *where* is neither form, or holds non-dict entries.
    """
    if isinstance(where, str):
        try:
            where = json.loads(where)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"{context}: 'where' was a string but is not valid JSON: {e}. "
                f'Pass a list of condition objects, e.g. [{{"column": "id", '
                f'"op": "=", "value": 42}}].'
            ) from e
    if where is None:
        return []
    if not isinstance(where, (list, tuple)):
        raise ValueError(
            f"{context}: 'where' must be a list of condition objects (or its "
            f"JSON encoding), got {type(where).__name__}. Example: "
            f'[{{"column": "id", "op": "=", "value": 42}}].'
        )
    for i, cond in enumerate(where):
        if not isinstance(cond, dict):
            raise ValueError(
                f"{context}: condition {i} must be an object with keys "
                f"column/op/value, got {type(cond).__name__}."
            )
    return list(where)


_TRUE_SPELLINGS = frozenset({"true", "1"})
_FALSE_SPELLINGS = frozenset({"false", "0", "none", "null", ""})


def coerce_bool(value: Any, name: str, context: str) -> bool:
    """Normalize a boolean flag that may arrive as its string form.

    The tool schema advertises bool params as strings, so models send "true",
    "false", "0", "1". Spellings are enumerated rather than passed through
    ``bool()`` — a truthy string like "no" must never read as True.

    Raises:
        ValueError: If *value* is not a recognized boolean spelling.
    """
    if value is True or value is False:
        return value
    if value is None:
        return False
    if isinstance(value, int):
        if value in (0, 1):
            return value == 1
    elif isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_SPELLINGS:
            return True
        if token in _FALSE_SPELLINGS:
            return False
    raise ValueError(
        f"{context}: {name!r} must be true or false, got {value!r}. "
        f'Accepted: true/false (or the strings "true"/"false").'
    )


def coerce_object(value: Any, name: str, context: str) -> Dict[str, Any]:
    """Normalize a mapping parameter that may arrive as its JSON encoding.

    Same boundary problem as ``coerce_conditions``: the tool schema advertises
    object params as strings, so models send JSON.

    Raises:
        ValueError: If *value* is neither a dict nor JSON encoding one.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"{context}: {name!r} was a string but is not valid JSON: {e}. "
                f'Pass an object, e.g. {{"name": "Alice"}}.'
            ) from e
    if not isinstance(value, dict):
        raise ValueError(
            f"{context}: {name!r} must be an object mapping column names to "
            f'values, got {type(value).__name__}. Example: {{"name": "Alice"}}.'
        )
    return value


def build_where(
    conditions: Sequence[Dict[str, Any]], *, context: str
) -> Tuple[str, Dict[str, Any]]:
    """Build a parameterized WHERE fragment from structured conditions.

    Each condition is ``{"column": str, "op": str, "value": Any}``; ``op``
    defaults to ``"="``. Conditions are joined with AND. Every value is bound,
    so no caller-supplied text reaches the SQL string.

    Args:
        conditions: Condition dicts, already normalized by ``coerce_conditions``.
        context: Caller name for error messages, e.g. ``"db_delete"``.

    Returns:
        ``(fragment, params)`` — e.g. ``("id = :__w0", {"__w0": 42})``.

    Raises:
        ValueError: On any malformed condition.
    """
    if not conditions:
        raise ValueError(f"{context}: no conditions supplied.")

    clauses: List[str] = []
    params: Dict[str, Any] = {}

    for i, cond in enumerate(conditions):
        unknown = set(cond) - _ALLOWED_KEYS
        if unknown:
            raise ValueError(
                f"{context}: condition {i} has unknown key(s) "
                f"{sorted(unknown)!r}. Allowed keys: column, op, value."
            )
        if "column" not in cond:
            raise ValueError(
                f"{context}: condition {i} is missing required key 'column'."
            )
        column = validate_identifier(cond["column"], "column name", context)

        raw_op = cond.get("op", "=")
        if not isinstance(raw_op, str):
            raise ValueError(
                f"{context}: condition {i} operator must be a string, got "
                f"{type(raw_op).__name__}. Allowed: {_OPS_HELP}."
            )
        # Case-folding against a closed allowlist cannot widen what's accepted.
        op = " ".join(raw_op.upper().split())
        if op not in ALL_OPS:
            raise ValueError(
                f"{context}: unsupported operator {raw_op!r} in condition {i}. "
                f"Allowed: {_OPS_HELP}. Conditions are joined with AND; raw SQL "
                f"is not accepted. For OR or nested logic, expose a "
                f"domain-specific tool instead — see "
                f"docs/sdk/mixins/database-mixin.mdx."
            )

        has_value = "value" in cond
        if op in NULLARY_OPS:
            if has_value:
                raise ValueError(
                    f"{context}: operator {op} in condition {i} takes no value; "
                    f"remove the 'value' key."
                )
            clauses.append(f"{column} {op}")
            continue

        if not has_value:
            raise ValueError(
                f"{context}: operator {op} in condition {i} requires a 'value' key."
            )
        value = cond["value"]

        if op in LIST_OPS:
            if not isinstance(value, (list, tuple)):
                raise ValueError(
                    f"{context}: operator {op} in condition {i} requires a list "
                    f'value, got {type(value).__name__}. Example: {{"column": '
                    f'"status", "op": "IN", "value": ["a", "b"]}}.'
                )
            if not value:
                raise ValueError(
                    f"{context}: operator {op} in condition {i} got an empty "
                    f"list, which matches no rows. Supply at least one value, "
                    f"or omit this condition."
                )
            if len(value) > _MAX_IN_VALUES:
                raise ValueError(
                    f"{context}: operator {op} in condition {i} got "
                    f"{len(value)} values, over the {_MAX_IN_VALUES} SQLite "
                    f"can bind. Narrow the filter, or run the operation in "
                    f"batches."
                )
            names = []
            for j, item in enumerate(value):
                _require_bindable(item, i, context)
                name = f"{_WHERE_PARAM_PREFIX}{i}_{j}"
                params[name] = item
                names.append(f":{name}")
            clauses.append(f"{column} {op} ({', '.join(names)})")
            continue

        if op in ("LIKE", "NOT LIKE") and not isinstance(value, str):
            raise ValueError(
                f"{context}: operator {op} in condition {i} requires a string "
                f"value, got {type(value).__name__}."
            )
        _require_bindable(value, i, context)
        name = f"{_WHERE_PARAM_PREFIX}{i}"
        params[name] = value
        clauses.append(f"{column} {op} :{name}")

    return " AND ".join(clauses), params


def _require_bindable(value: Any, index: int, context: str) -> None:
    if not isinstance(value, _BINDABLE):
        raise ValueError(
            f"{context}: condition {index} value has type "
            f"{type(value).__name__}, which SQLite cannot bind. Use a string, "
            f"number, boolean, or null."
        )


@dataclass
class _Window:
    """An open read-only window. ``conn`` is held so its id() stays unique."""

    depth: int
    denied: List[str]
    conn: sqlite3.Connection


# Open read-only windows keyed by connection id, so a nested call joins the
# outer window instead of disarming it on exit.
_OPEN_WINDOWS: Dict[int, _Window] = {}
_WINDOWS_LOCK = threading.Lock()

# set_authorizer(None) only detaches the authorizer from 3.11 on (bpo-44491).
# Before that it installs None as the callback, and the C trampoline turns the
# resulting TypeError into SQLITE_DENY — bricking the connection for good.
_SET_AUTHORIZER_NONE_DETACHES = sys.version_info >= (3, 11)


def _allow_all(_action, _arg1, _arg2, _db_name, _trigger):
    """Authorizer that permits everything — a stand-in for having none."""
    return sqlite3.SQLITE_OK


def _disarm(conn: sqlite3.Connection) -> None:
    """Stop enforcing the read-only window on *conn*."""
    conn.set_authorizer(None if _SET_AUTHORIZER_NONE_DETACHES else _allow_all)


@contextmanager
def readonly(conn: sqlite3.Connection) -> Iterator[List[str]]:
    """Block writes, DDL, ATTACH, and non-allowlisted pragmas on *conn*.

    Yields the list of denied actions. It is empty unless the authorizer
    refused something, which lets callers tell an authorization failure from an
    ordinary SQL error without matching on SQLite's message text — the message
    varies ("not authorized", "authorization denied") by statement.

    Re-entrant: a nested call joins the open window and only the outermost
    exit disarms it. Because the authorizer is connection-scoped, a concurrent
    writer on the same connection is denied for as long as any window is open.
    Any authorizer already installed on *conn* is not restored on exit.
    """
    key = id(conn)
    with _WINDOWS_LOCK:
        entry = _OPEN_WINDOWS.get(key)
        if entry is None:
            denied: List[str] = []

            def _authorizer(action, arg1, _arg2, _db_name, _trigger):
                if action in (
                    sqlite3.SQLITE_SELECT,
                    sqlite3.SQLITE_READ,
                    sqlite3.SQLITE_FUNCTION,
                    sqlite3.SQLITE_RECURSIVE,
                ):
                    return sqlite3.SQLITE_OK
                if (
                    action == sqlite3.SQLITE_PRAGMA
                    and (arg1 or "").lower() in READONLY_PRAGMAS
                ):
                    return sqlite3.SQLITE_OK
                denied.append(f"action={action} arg={arg1!r}")
                return sqlite3.SQLITE_DENY

            # Arm before publishing: a failed set_authorizer must not leave a
            # registered window that later calls would join and skip arming.
            conn.set_authorizer(_authorizer)
            # Hold conn so its id() can't be recycled onto a stale entry.
            entry = _Window(depth=0, denied=denied, conn=conn)
            _OPEN_WINDOWS[key] = entry
        entry.depth += 1

    try:
        yield entry.denied
    finally:
        with _WINDOWS_LOCK:
            entry.depth -= 1
            if entry.depth == 0:
                del _OPEN_WINDOWS[key]
                _disarm(conn)
