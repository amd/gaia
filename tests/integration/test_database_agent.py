# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for DatabaseAgent."""

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
            self.execute(
                """
                CREATE TABLE items (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    quantity INTEGER DEFAULT 0
                )
            """
            )

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

    result = db_update("items", {"quantity": 20}, "name = :name", {"name": "Apple"})
    assert result["updated"] == 1

    # Verify update
    item = agent.query(
        "SELECT quantity FROM items WHERE name = :name", {"name": "Apple"}, one=True
    )
    assert item["quantity"] == 20

    agent.close_db()


def test_db_delete_tool():
    """db_delete tool works correctly."""
    agent = SimpleDBAgent()

    agent.insert("items", {"name": "Apple", "quantity": 10})
    agent.insert("items", {"name": "Banana", "quantity": 5})

    from gaia.agents.base.tools import _TOOL_REGISTRY

    db_delete = _TOOL_REGISTRY["db_delete"]["function"]

    result = db_delete("items", "name = :name", {"name": "Apple"})
    assert result["deleted"] == 1

    # Verify delete
    items = agent.query("SELECT * FROM items")
    assert len(items) == 1
    assert items[0]["name"] == "Banana"

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
