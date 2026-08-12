# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for gaia.database.sql_safety."""

import re
import sqlite3
import time

import pytest

from gaia.database.sql_safety import (
    _OPEN_WINDOWS,
    build_where,
    coerce_bool,
    coerce_conditions,
    coerce_object,
    readonly,
    validate_identifier,
)

# --- validate_identifier ---


@pytest.mark.parametrize("name", ["items", "_x", "a1_b", "T", "_"])
def test_validate_identifier_accepts_bare_identifiers(name):
    assert validate_identifier(name, "table name", "ctx") == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "1abc",
        "a-b",
        "a b",
        "a;b",
        "items WHERE 1=1 --",
        '"quoted"',
        "items) VALUES (1); --",
        "naïve",
    ],
)
def test_validate_identifier_rejects_unsafe(name):
    with pytest.raises(ValueError, match="invalid table name"):
        validate_identifier(name, "table name", "db_delete")


@pytest.mark.parametrize("name", [None, 123, ["items"]])
def test_validate_identifier_rejects_non_strings(name):
    with pytest.raises(ValueError, match="must be a string"):
        validate_identifier(name, "table name", "db_delete")


def test_validate_identifier_error_names_value_and_grammar():
    with pytest.raises(ValueError) as exc:
        validate_identifier("items WHERE 1=1 --", "table name", "db_delete")
    msg = str(exc.value)
    assert "db_delete" in msg
    assert "items WHERE 1=1 --" in msg
    assert "[A-Za-z_][A-Za-z0-9_]*" in msg
    assert "db_tables" in msg  # tells the caller how to recover


# --- coerce_conditions ---


def test_coerce_conditions_accepts_list():
    conds = [{"column": "id", "op": "=", "value": 1}]
    assert coerce_conditions(conds, "db_delete") == conds


def test_coerce_conditions_accepts_json_string():
    # The tool schema advertises 'where' as a string, so models send JSON.
    raw = '[{"column": "id", "op": "=", "value": 1}]'
    assert coerce_conditions(raw, "db_delete") == [
        {"column": "id", "op": "=", "value": 1}
    ]


def test_coerce_conditions_none_is_empty():
    assert coerce_conditions(None, "db_delete") == []


def test_coerce_conditions_rejects_free_text():
    with pytest.raises(ValueError, match="not valid JSON"):
        coerce_conditions("id = :id OR 1=1", "db_delete")


def test_coerce_conditions_rejects_wrong_type():
    with pytest.raises(ValueError, match="must be a list of condition objects"):
        coerce_conditions(42, "db_delete")


def test_coerce_conditions_rejects_non_dict_entries():
    with pytest.raises(ValueError, match="condition 0 must be an object"):
        coerce_conditions([["id", "=", 1]], "db_delete")


# --- build_where: happy paths ---


def test_build_where_single_scalar():
    assert build_where([{"column": "id", "op": "=", "value": 42}], context="c") == (
        "id = :__w0",
        {"__w0": 42},
    )


def test_build_where_op_defaults_to_equals():
    sql, params = build_where([{"column": "id", "value": 42}], context="c")
    assert sql == "id = :__w0"
    assert params == {"__w0": 42}


def test_build_where_repeated_column_uses_positional_params():
    # Param names are indexed by position, not column, so a column can repeat.
    sql, params = build_where(
        [
            {"column": "age", "op": ">=", "value": 18},
            {"column": "age", "op": "<=", "value": 65},
        ],
        context="c",
    )
    assert sql == "age >= :__w0 AND age <= :__w1"
    assert params == {"__w0": 18, "__w1": 65}


def test_build_where_in_list():
    sql, params = build_where(
        [{"column": "status", "op": "IN", "value": ["a", "b", "c"]}], context="c"
    )
    assert sql == "status IN (:__w0_0, :__w0_1, :__w0_2)"
    assert params == {"__w0_0": "a", "__w0_1": "b", "__w0_2": "c"}


def test_build_where_not_in():
    sql, _ = build_where(
        [{"column": "status", "op": "NOT IN", "value": ["a"]}], context="c"
    )
    assert sql == "status NOT IN (:__w0_0)"


@pytest.mark.parametrize("op", ["IS NULL", "IS NOT NULL"])
def test_build_where_nullary_binds_nothing(op):
    sql, params = build_where([{"column": "deleted_at", "op": op}], context="c")
    assert sql == f"deleted_at {op}"
    assert params == {}


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("is not null", "IS NOT NULL"),
        ("IS  NOT  NULL", "IS NOT NULL"),
        ("in", "IN"),
        ("like", "LIKE"),
    ],
)
def test_build_where_normalizes_operator_case_and_spacing(raw, expected):
    cond = {"column": "c", "op": raw}
    if expected == "IN":
        cond["value"] = ["x"]
    elif expected == "LIKE":
        cond["value"] = "x%"
    sql, _ = build_where([cond], context="c")
    assert expected in sql


def test_build_where_null_value_binds_none():
    _, params = build_where([{"column": "c", "op": "=", "value": None}], context="c")
    assert params == {"__w0": None}


# --- build_where: rejections ---


def test_build_where_rejects_empty_conditions():
    with pytest.raises(ValueError, match="no conditions supplied"):
        build_where([], context="db_delete")


def test_build_where_rejects_unknown_key():
    with pytest.raises(ValueError, match="unknown key"):
        build_where([{"column": "id", "value": 1, "table": "x"}], context="c")


def test_build_where_rejects_missing_column():
    with pytest.raises(ValueError, match="missing required key 'column'"):
        build_where([{"op": "=", "value": 1}], context="c")


def test_build_where_rejects_bad_column_name():
    with pytest.raises(ValueError, match="invalid column name"):
        build_where([{"column": "1", "op": "=", "value": 1}], context="c")


def test_build_where_rejects_or_operator():
    # "OR 1=1" is the canonical injection payload; OR is not in the allowlist.
    with pytest.raises(ValueError) as exc:
        build_where([{"column": "id", "op": "OR", "value": 1}], context="db_delete")
    msg = str(exc.value)
    assert "unsupported operator" in msg
    assert "joined with AND" in msg
    assert "IS NOT NULL" in msg  # enumerates what IS allowed so a model recovers


@pytest.mark.parametrize("op", ["=; DROP TABLE t --", "GLOB", "MATCH", "REGEXP", ""])
def test_build_where_rejects_operators_outside_allowlist(op):
    with pytest.raises(ValueError, match="unsupported operator"):
        build_where([{"column": "id", "op": op, "value": 1}], context="c")


def test_build_where_rejects_non_string_operator():
    with pytest.raises(ValueError, match="operator must be a string"):
        build_where([{"column": "id", "op": 1, "value": 1}], context="c")


def test_build_where_rejects_value_on_nullary_op():
    with pytest.raises(ValueError, match="takes no value"):
        build_where([{"column": "c", "op": "IS NULL", "value": None}], context="c")


def test_build_where_rejects_scalar_op_without_value():
    with pytest.raises(ValueError, match="requires a 'value' key"):
        build_where([{"column": "c", "op": "="}], context="c")


def test_build_where_rejects_in_with_scalar_value():
    with pytest.raises(ValueError, match="requires a list value"):
        build_where([{"column": "c", "op": "IN", "value": 1}], context="c")


def test_build_where_rejects_in_with_empty_list():
    with pytest.raises(ValueError, match="matches no rows"):
        build_where([{"column": "c", "op": "IN", "value": []}], context="c")


def test_build_where_rejects_like_with_non_string():
    with pytest.raises(ValueError, match="requires a string value"):
        build_where([{"column": "c", "op": "LIKE", "value": 5}], context="c")


@pytest.mark.parametrize("value", [{"a": 1}, [1, 2], object()])
def test_build_where_rejects_unbindable_value(value):
    with pytest.raises(ValueError, match="cannot bind"):
        build_where([{"column": "c", "op": "=", "value": value}], context="c")


def test_build_where_rejects_unbindable_item_inside_in_list():
    with pytest.raises(ValueError, match="cannot bind"):
        build_where([{"column": "c", "op": "IN", "value": [{"a": 1}]}], context="c")


# --- Param-name collision with UPDATE's __set_ prefix ---


def test_where_params_never_collide_with_set_params():
    _, params = build_where(
        [
            {"column": "set_x", "op": "=", "value": 1},
            {"column": "w0", "op": "=", "value": 2},
            {"column": "_", "op": "IN", "value": [3]},
        ],
        context="c",
    )
    assert params  # sanity
    for key in params:
        assert not key.startswith("__set_")
        assert re.fullmatch(r"__w\d+(_\d+)?", key), key


def test_build_where_output_binds_cleanly_in_sqlite():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    conn.executemany("INSERT INTO t VALUES (?,?)", [(1, "a"), (2, "b"), (3, "c")])
    sql, params = build_where(
        [{"column": "id", "op": "IN", "value": [1, 3]}], context="c"
    )
    rows = conn.execute(f"SELECT name FROM t WHERE {sql}", params).fetchall()
    assert [r[0] for r in rows] == ["a", "c"]


# --- readonly() authorizer ---


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    c.executemany("INSERT INTO t VALUES (?,?)", [(1, "a"), (2, "b"), (3, "c")])
    c.commit()
    yield c
    c.close()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t",
        "SELECT COUNT(*) FROM t WHERE id > 1",
        "WITH c AS (SELECT * FROM t) SELECT * FROM c",
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<3)"
        " SELECT * FROM n",
        "SELECT upper(name) FROM t",
        "PRAGMA table_info(t)",
        "SELECT name FROM sqlite_master WHERE type='table'",
    ],
)
def test_readonly_allows_reads(conn, sql):
    with readonly(conn):
        conn.execute(sql).fetchall()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM t",
        "UPDATE t SET name='x'",
        "INSERT INTO t VALUES (4,'d')",
        "DROP TABLE t",
        "CREATE TABLE z (a INT)",
        "ALTER TABLE t ADD COLUMN extra TEXT",
        "ATTACH DATABASE ':memory:' AS x",
        "PRAGMA writable_schema=ON",
        "PRAGMA journal_mode=WAL",
    ],
)
def test_readonly_denies_writes_and_ddl(conn, sql):
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        with readonly(conn):
            conn.execute(sql).fetchall()
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3


def test_readonly_restores_authorizer_on_success(conn):
    with readonly(conn):
        conn.execute("SELECT * FROM t").fetchall()
    conn.execute("DELETE FROM t WHERE id=1")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2


def test_readonly_restores_authorizer_after_denial(conn):
    # A leaked authorizer would break every later write in the process.
    with pytest.raises(sqlite3.DatabaseError):
        with readonly(conn):
            conn.execute("DELETE FROM t")
    conn.execute("INSERT INTO t VALUES (4,'d')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 4


def test_readonly_restores_authorizer_after_unrelated_exception(conn):
    with pytest.raises(RuntimeError):
        with readonly(conn):
            raise RuntimeError("boom")
    conn.execute("INSERT INTO t VALUES (5,'e')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 4


def test_validate_identifier_rejects_trailing_newline():
    """`$` would accept a trailing newline; the grammar we advertise doesn't."""
    with pytest.raises(ValueError, match="invalid table name"):
        validate_identifier("items\n", "table name", "db_delete")


def test_build_where_rejects_oversized_in_list():
    with pytest.raises(ValueError, match="SQLite can bind"):
        build_where(
            [{"column": "id", "op": "IN", "value": list(range(5000))}], context="c"
        )


def test_readonly_reports_denials_via_flag(conn):
    """Denials are flagged by the authorizer, not sniffed from the message."""
    with pytest.raises(sqlite3.DatabaseError):
        with readonly(conn) as denied:
            conn.execute("DELETE FROM t").fetchall()
    assert denied


def test_readonly_flag_stays_empty_for_ordinary_sql_errors(conn):
    with pytest.raises(sqlite3.OperationalError):
        with readonly(conn) as denied:
            conn.execute("SELECT * FROM nope").fetchall()
    assert denied == []


def test_readonly_denies_vacuum(conn):
    """VACUUM reports 'authorization denied', not 'not authorized'."""
    with pytest.raises(sqlite3.DatabaseError):
        with readonly(conn) as denied:
            conn.execute("VACUUM")
    assert denied


def test_readonly_nesting_does_not_disarm_outer_window(conn):
    with readonly(conn):
        with readonly(conn):
            pass
        # Inner exit must not clear the outer authorizer.
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM t")
    conn.execute("DELETE FROM t WHERE id = 1")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2


# --- coerce_bool ---


@pytest.mark.parametrize("value", [True, 1, "true", "True", " TRUE ", "1"])
def test_coerce_bool_true_spellings(value):
    assert coerce_bool(value, "all_rows", "c") is True


@pytest.mark.parametrize("value", [False, 0, "false", "False", "0", None, ""])
def test_coerce_bool_false_spellings(value):
    assert coerce_bool(value, "all_rows", "c") is False


@pytest.mark.parametrize("value", ["no", "yes", "off", 2, -1, [], {}, 1.5])
def test_coerce_bool_rejects_ambiguous(value):
    """bool() would read "no" as True; only enumerated spellings are accepted."""
    with pytest.raises(ValueError, match="must be true or false"):
        coerce_bool(value, "all_rows", "c")


# --- coerce_object ---


def test_coerce_object_accepts_dict_and_json():
    assert coerce_object({"a": 1}, "data", "c") == {"a": 1}
    assert coerce_object('{"a": 1}', "data", "c") == {"a": 1}


@pytest.mark.parametrize("value", ["abc", "[1,2]", "123", 5, ["a"], None])
def test_coerce_object_rejects_non_objects(value):
    with pytest.raises(ValueError):
        coerce_object(value, "data", "c")


# --- readonly() window bookkeeping ---


def test_readonly_registry_is_empty_after_use(conn):
    with readonly(conn):
        conn.execute("SELECT * FROM t").fetchall()
    assert not _OPEN_WINDOWS


def test_readonly_registry_is_empty_after_denial(conn):
    with pytest.raises(sqlite3.DatabaseError):
        with readonly(conn):
            conn.execute("DELETE FROM t")
    assert not _OPEN_WINDOWS


def test_readonly_does_not_leak_window_when_arming_fails():
    """A failed set_authorizer must not register a window later calls join."""
    closed = sqlite3.connect(":memory:")
    closed.close()
    with pytest.raises(sqlite3.ProgrammingError):
        with readonly(closed):
            pass
    assert not _OPEN_WINDOWS


def test_readonly_nesting_refcounts(conn):
    with readonly(conn):
        with readonly(conn):
            with readonly(conn):
                pass
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM t")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM t")
    assert not _OPEN_WINDOWS
    conn.execute("DELETE FROM t WHERE id = 1")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2


def test_readonly_concurrent_windows_stay_armed():
    """One thread exiting must not disarm a window another thread still holds.

    init_db() opens with check_same_thread=False, so this sharing is reachable.
    """
    import threading

    shared = sqlite3.connect(":memory:", check_same_thread=False)
    shared.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    shared.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    shared.commit()

    start = threading.Barrier(2)
    result = {}

    def worker():
        with readonly(shared):
            start.wait(timeout=5)
            time.sleep(0.2)  # outer window exits during this
            try:
                shared.execute("DELETE FROM t")
                result["leaked"] = True
            except sqlite3.DatabaseError:
                result["leaked"] = False

    t = threading.Thread(target=worker)
    t.start()
    with readonly(shared):
        start.wait(timeout=5)
    t.join(timeout=5)

    assert result.get("leaked") is False
    assert not _OPEN_WINDOWS
    assert shared.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    shared.close()
