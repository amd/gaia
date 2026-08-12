# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for DatabaseAgent."""

import pytest

from gaia import DatabaseAgent
from gaia.database import temp_db


class SimpleDBAgent(DatabaseAgent):
    """Simple test agent with database tools."""

    def __init__(self, **kwargs):
        kwargs.setdefault("skip_lemonade", True)
        kwargs.setdefault("silent_mode", True)
        super().__init__(**kwargs)

        # Create test schema
        if not self.table_exists("items"):
            self.execute("""
                CREATE TABLE items (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    quantity INTEGER DEFAULT 0
                )
            """)

    def _get_system_prompt(self) -> str:
        return "You manage items."

    def _register_tools(self):
        """No additional tools needed - DatabaseAgent registers db tools."""
        pass


def test_database_agent_inherits_from_agent():
    """DatabaseAgent is a subclass of Agent."""
    from gaia import Agent

    assert issubclass(DatabaseAgent, Agent)


def test_database_agent_has_mixin():
    """DatabaseAgent includes DatabaseMixin functionality."""
    from gaia.database import DatabaseMixin

    agent = SimpleDBAgent()
    assert isinstance(agent, DatabaseMixin)
    assert agent.db_ready
    agent.close_db()


def test_database_agent_creates_schema():
    """DatabaseAgent can create tables in __init__."""
    agent = SimpleDBAgent()
    assert agent.table_exists("items")
    agent.close_db()


def test_database_agent_tools_registered():
    """DatabaseAgent registers database tools."""
    agent = SimpleDBAgent()

    # Check that tools are registered by looking at tool registry
    from gaia.agents.base.tools import _TOOL_REGISTRY

    assert "db_query" in _TOOL_REGISTRY
    assert "db_insert" in _TOOL_REGISTRY
    assert "db_update" in _TOOL_REGISTRY
    assert "db_delete" in _TOOL_REGISTRY
    assert "db_tables" in _TOOL_REGISTRY
    assert "db_schema" in _TOOL_REGISTRY

    agent.close_db()


def test_db_insert_tool():
    """db_insert tool works correctly."""
    agent = SimpleDBAgent()

    # Call the tool directly (simulating LLM call)
    from gaia.agents.base.tools import _TOOL_REGISTRY

    db_insert = _TOOL_REGISTRY["db_insert"]["function"]

    result = db_insert("items", {"name": "Apple", "quantity": 10})
    assert result["success"] is True
    assert result["id"] == 1

    result = db_insert("items", {"name": "Banana", "quantity": 5})
    assert result["id"] == 2

    agent.close_db()


def test_db_query_tool():
    """db_query tool works correctly."""
    agent = SimpleDBAgent()

    # Insert some data
    agent.insert("items", {"name": "Apple", "quantity": 10})
    agent.insert("items", {"name": "Banana", "quantity": 5})

    # Get the tool
    from gaia.agents.base.tools import _TOOL_REGISTRY

    db_query = _TOOL_REGISTRY["db_query"]["function"]

    # Query all
    result = db_query("SELECT * FROM items")
    assert result["count"] == 2
    assert len(result["rows"]) == 2

    # Query with params
    result = db_query("SELECT * FROM items WHERE quantity > :min", {"min": 7})
    assert result["count"] == 1
    assert result["rows"][0]["name"] == "Apple"

    agent.close_db()


def test_db_update_tool():
    """db_update tool works correctly."""
    agent = SimpleDBAgent()

    agent.insert("items", {"name": "Apple", "quantity": 10})

    from gaia.agents.base.tools import _TOOL_REGISTRY

    db_update = _TOOL_REGISTRY["db_update"]["function"]

    result = db_update(
        "items", {"quantity": 20}, [{"column": "name", "op": "=", "value": "Apple"}]
    )
    assert result["updated"] == 1

    # Verify update
    item = agent.query(
        "SELECT quantity FROM items WHERE name = :name", {"name": "Apple"}, one=True
    )
    assert item["quantity"] == 20

    agent.close_db()


def test_db_update_tool_accepts_json_string_where():
    """The tool schema advertises 'where' as a string, so models send JSON."""
    agent = SimpleDBAgent()
    agent.insert("items", {"name": "Apple", "quantity": 10})

    from gaia.agents.base.tools import _TOOL_REGISTRY

    db_update = _TOOL_REGISTRY["db_update"]["function"]

    result = db_update(
        "items", {"quantity": 20}, '[{"column": "name", "op": "=", "value": "Apple"}]'
    )
    assert result["updated"] == 1
    agent.close_db()


def test_db_delete_tool():
    """db_delete tool works correctly."""
    agent = SimpleDBAgent()

    agent.insert("items", {"name": "Apple", "quantity": 10})
    agent.insert("items", {"name": "Banana", "quantity": 5})

    from gaia.agents.base.tools import _TOOL_REGISTRY

    db_delete = _TOOL_REGISTRY["db_delete"]["function"]

    result = db_delete("items", [{"column": "name", "op": "=", "value": "Apple"}])
    assert result["deleted"] == 1

    # Verify delete
    items = agent.query("SELECT * FROM items")
    assert len(items) == 1
    assert items[0]["name"] == "Banana"

    agent.close_db()


def test_db_delete_tool_accepts_json_string_where():
    """The tool schema advertises 'where' as a string, so models send JSON."""
    agent = SimpleDBAgent()
    agent.insert("items", {"name": "Apple", "quantity": 10})
    agent.insert("items", {"name": "Banana", "quantity": 5})

    from gaia.agents.base.tools import _TOOL_REGISTRY

    db_delete = _TOOL_REGISTRY["db_delete"]["function"]

    result = db_delete("items", '[{"column": "name", "op": "=", "value": "Apple"}]')
    assert result["deleted"] == 1
    assert [i["name"] for i in agent.query("SELECT * FROM items")] == ["Banana"]
    agent.close_db()


def test_db_delete_tool_supports_in_operator():
    """IN takes a list value and binds each element."""
    agent = SimpleDBAgent()
    for n in ("Apple", "Banana", "Cherry"):
        agent.insert("items", {"name": n, "quantity": 1})

    from gaia.agents.base.tools import _TOOL_REGISTRY

    db_delete = _TOOL_REGISTRY["db_delete"]["function"]

    result = db_delete(
        "items", [{"column": "name", "op": "IN", "value": ["Apple", "Cherry"]}]
    )
    assert result["deleted"] == 2
    assert [i["name"] for i in agent.query("SELECT * FROM items")] == ["Banana"]
    agent.close_db()


def test_db_tables_tool():
    """db_tables tool works correctly."""
    agent = SimpleDBAgent()

    from gaia.agents.base.tools import _TOOL_REGISTRY

    db_tables = _TOOL_REGISTRY["db_tables"]["function"]

    result = db_tables()
    assert "items" in result["tables"]

    agent.close_db()


def test_db_schema_tool():
    """db_schema tool works correctly."""
    agent = SimpleDBAgent()

    from gaia.agents.base.tools import _TOOL_REGISTRY

    db_schema = _TOOL_REGISTRY["db_schema"]["function"]

    result = db_schema("items")
    assert result["table"] == "items"

    column_names = [c["name"] for c in result["columns"]]
    assert "id" in column_names
    assert "name" in column_names
    assert "quantity" in column_names

    agent.close_db()


def test_database_agent_with_temp_db():
    """DatabaseAgent works with temp_db fixture."""
    schema = "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)"

    with temp_db(schema) as db_path:
        agent = SimpleDBAgent(db_path=db_path)

        # Table exists from fixture
        assert agent.table_exists("items")

        agent.close_db()


def test_database_agent_file_persistence(tmp_path):
    """DatabaseAgent persists data to file."""
    db_path = str(tmp_path / "test.db")

    # First agent creates data
    agent1 = SimpleDBAgent(db_path=db_path)
    agent1.insert("items", {"name": "Persistent", "quantity": 42})
    agent1.close_db()

    # Second agent reads it
    agent2 = SimpleDBAgent(db_path=db_path)
    items = agent2.query("SELECT * FROM items")
    assert len(items) == 1
    assert items[0]["name"] == "Persistent"
    agent2.close_db()


# --- Injection regressions ---
#
# Each seeds 3 rows, fires a payload that previously mutated the table, and
# asserts the row count is unchanged. The tool layer is the LLM trust boundary.


def _seeded_agent():
    agent = SimpleDBAgent()
    for n, q in (("Apple", 10), ("Banana", 5), ("Cherry", 7)):
        agent.insert("items", {"name": n, "quantity": q})
    return agent


def _tools():
    from gaia.agents.base.tools import _TOOL_REGISTRY

    return _TOOL_REGISTRY


@pytest.mark.parametrize(
    "where",
    [
        "name = :name OR 1=1",
        "1=1",
        "id = :id OR 1=1",
    ],
)
def test_db_delete_rejects_free_text_where(where):
    """A raw WHERE fragment is no longer an accepted shape."""
    agent = _seeded_agent()
    db_delete = _tools()["db_delete"]["function"]

    with pytest.raises(ValueError):
        db_delete("items", where)

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


def test_db_delete_rejects_or_operator():
    """OR is not in the operator allowlist, so `OR 1=1` has no structured form."""
    agent = _seeded_agent()
    db_delete = _tools()["db_delete"]["function"]

    with pytest.raises(ValueError, match="unsupported operator"):
        db_delete("items", '[{"column": "id", "op": "OR", "value": 1}]')

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


def test_db_delete_rejects_injected_column_name():
    """A predicate smuggled through the column slot fails identifier validation."""
    agent = _seeded_agent()
    db_delete = _tools()["db_delete"]["function"]

    with pytest.raises(ValueError, match="invalid column name"):
        db_delete(
            "items",
            [
                {"column": "id", "op": "=", "value": 1},
                {"column": "1", "op": "=", "value": 1},
            ],
        )

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


def test_db_delete_rejects_table_name_injection():
    """The table slot is interpolated, so it must be a bare identifier."""
    agent = _seeded_agent()
    db_delete = _tools()["db_delete"]["function"]

    with pytest.raises(ValueError, match="invalid table name"):
        db_delete("items WHERE 1=1 --", [{"column": "id", "op": "=", "value": 1}])

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


def test_db_update_rejects_table_name_injection():
    agent = _seeded_agent()
    db_update = _tools()["db_update"]["function"]

    with pytest.raises(ValueError, match="invalid table name"):
        db_update(
            "items WHERE 1=1 --",
            {"quantity": 0},
            [{"column": "id", "op": "=", "value": 1}],
        )

    assert (
        agent.query("SELECT quantity FROM items WHERE id = 1", one=True)["quantity"]
        == 10
    )
    agent.close_db()


def test_db_insert_rejects_column_injection():
    agent = _seeded_agent()
    db_insert = _tools()["db_insert"]["function"]

    with pytest.raises(ValueError, match="invalid column name"):
        db_insert("items", {"name) VALUES ('x'); --": "x"})

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM items",
        "UPDATE items SET quantity = 0",
        "INSERT INTO items (name) VALUES ('Injected')",
        "DROP TABLE items",
        "CREATE TABLE other (id INTEGER)",
        "ATTACH DATABASE ':memory:' AS side",
        "PRAGMA writable_schema=ON",
    ],
)
def test_db_query_is_read_only(sql):
    """db_query executes statements; SQLite refuses anything but a read."""
    agent = _seeded_agent()
    db_query = _tools()["db_query"]["function"]

    with pytest.raises(PermissionError, match="Read-only query blocked"):
        db_query(sql)

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


def test_db_query_still_works_after_blocked_write():
    """A leaked authorizer would break every later write in the process."""
    agent = _seeded_agent()
    db_query = _tools()["db_query"]["function"]

    with pytest.raises(PermissionError):
        db_query("DELETE FROM items")

    assert db_query("SELECT * FROM items")["count"] == 3
    agent.insert("items", {"name": "Date", "quantity": 1})
    assert len(agent.query("SELECT * FROM items")) == 4
    agent.close_db()


def test_db_delete_requires_all_rows_for_full_table():
    """An empty condition list must not silently become a mass delete."""
    agent = _seeded_agent()
    db_delete = _tools()["db_delete"]["function"]

    with pytest.raises(ValueError, match="all_rows"):
        db_delete("items", [])
    assert len(agent.query("SELECT * FROM items")) == 3

    assert db_delete("items", [], all_rows=True)["deleted"] == 3
    assert agent.query("SELECT * FROM items") == []
    agent.close_db()


def test_db_update_requires_all_rows_for_full_table():
    agent = _seeded_agent()
    db_update = _tools()["db_update"]["function"]

    with pytest.raises(ValueError, match="all_rows"):
        db_update("items", {"quantity": 0}, [])
    assert (
        agent.query("SELECT quantity FROM items WHERE id = 1", one=True)["quantity"]
        == 10
    )

    assert db_update("items", {"quantity": 0}, [], all_rows=True)["updated"] == 3
    agent.close_db()


def test_db_delete_rejects_all_rows_with_conditions():
    """Ambiguous intent is refused rather than resolved silently."""
    agent = _seeded_agent()
    db_delete = _tools()["db_delete"]["function"]

    with pytest.raises(ValueError, match="not both"):
        db_delete("items", [{"column": "id", "op": "=", "value": 1}], all_rows=True)

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


def test_db_schema_raises_on_bad_table():
    """db_schema raises instead of returning an error dict a model reads as data."""
    agent = _seeded_agent()
    db_schema = _tools()["db_schema"]["function"]

    with pytest.raises(ValueError, match="invalid table name"):
        db_schema("items; DROP TABLE items")

    assert agent.table_exists("items")
    agent.close_db()


@pytest.mark.parametrize("all_rows", ["no", "yes", 2, [], {}])
def test_db_delete_rejects_non_boolean_all_rows(all_rows):
    """A truthy string like "false" must not authorize a full-table delete."""
    agent = _seeded_agent()
    db_delete = _tools()["db_delete"]["function"]

    with pytest.raises(ValueError, match="must be true or false"):
        db_delete("items", [], all_rows)

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


def test_db_update_rejects_non_boolean_all_rows():
    agent = _seeded_agent()
    db_update = _tools()["db_update"]["function"]

    with pytest.raises(ValueError, match="must be true or false"):
        db_update("items", {"quantity": 0}, [], "nope")

    assert (
        agent.query("SELECT quantity FROM items WHERE id = 1", one=True)["quantity"]
        == 10
    )
    agent.close_db()


@pytest.mark.parametrize("data", ["abc", "[1,2]", "123", '"s"'])
def test_db_insert_rejects_non_object_data(data):
    agent = _seeded_agent()
    db_insert = _tools()["db_insert"]["function"]

    with pytest.raises(ValueError):
        db_insert("items", data)

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


def test_db_insert_accepts_json_string_data():
    """The tool schema types object params as strings, so models send JSON."""
    agent = _seeded_agent()
    db_insert = _tools()["db_insert"]["function"]

    assert db_insert("items", '{"name": "Date", "quantity": 2}')["success"] is True
    assert len(agent.query("SELECT * FROM items")) == 4
    agent.close_db()


def test_db_update_accepts_json_string_data():
    agent = _seeded_agent()
    db_update = _tools()["db_update"]["function"]

    result = db_update(
        "items", '{"quantity": 42}', [{"column": "name", "op": "=", "value": "Apple"}]
    )
    assert result["updated"] == 1
    agent.close_db()


def test_db_query_rejects_vacuum():
    agent = _seeded_agent()
    db_query = _tools()["db_query"]["function"]

    with pytest.raises(PermissionError):
        db_query("VACUUM")

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


def test_db_insert_error_names_the_tool_not_the_python_api():
    """The model can call db_insert; it cannot call DatabaseMixin.insert."""
    agent = _seeded_agent()
    db_insert = _tools()["db_insert"]["function"]

    with pytest.raises(ValueError, match="^db_insert:"):
        db_insert("items WHERE 1=1 --", {"name": "x"})

    agent.close_db()


@pytest.mark.parametrize("falsey", ["false", "False", " FALSE "])
def test_db_delete_string_false_does_not_authorize_mass_delete(falsey):
    """The tool schema types all_rows as a string, so "false" must mean false."""
    agent = _seeded_agent()
    db_delete = _tools()["db_delete"]["function"]

    with pytest.raises(ValueError, match="all_rows"):
        db_delete("items", [], falsey)

    assert len(agent.query("SELECT * FROM items")) == 3
    agent.close_db()


@pytest.mark.parametrize("truthy", ["true", "True", " TRUE "])
def test_db_delete_string_true_authorizes_mass_delete(truthy):
    agent = _seeded_agent()
    db_delete = _tools()["db_delete"]["function"]

    assert db_delete("items", [], truthy)["deleted"] == 3
    agent.close_db()
