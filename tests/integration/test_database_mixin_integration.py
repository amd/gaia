# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Integration tests for DatabaseMixin with Agent class."""

import pytest

from gaia import Agent, DatabaseMixin, tool
from gaia.database import temp_db


class ItemAgent(Agent, DatabaseMixin):
    """Test agent that manages items using DatabaseMixin."""

    def __init__(self, db_path: str = None, **kwargs):
        # Set defaults for testing
        kwargs.setdefault("skip_lemonade", True)
        kwargs.setdefault("silent_mode", True)
        super().__init__(**kwargs)

        # Initialize database
        if db_path:
            self.init_db(db_path)
        else:
            self.init_db()  # In-memory for testing

        # Create schema if needed
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
        return "You are an item management agent."

    def _register_tools(self):
        agent = self  # Capture reference for closures

        @tool
        def add_item(name: str, quantity: int = 1) -> dict:
            """Add a new item to inventory."""
            item_id = agent.insert("items", {"name": name, "quantity": quantity})
            return {"id": item_id, "name": name, "quantity": quantity}

        @tool
        def get_item(item_id: int) -> dict:
            """Get an item by ID."""
            item = agent.query(
                "SELECT * FROM items WHERE id = :id", {"id": item_id}, one=True
            )
            if item:
                return dict(item)
            return {"error": "Item not found"}

        @tool
        def list_items() -> dict:
            """List all items."""
            items = agent.query("SELECT * FROM items")
            return {"items": items, "count": len(items)}

        @tool
        def update_quantity(item_id: int, quantity: int) -> dict:
            """Update item quantity."""
            count = agent.update(
                "items", {"quantity": quantity}, "id = :id", {"id": item_id}
            )
            return {"updated": count}

        @tool
        def delete_item(item_id: int) -> dict:
            """Delete an item."""
            count = agent.delete("items", "id = :id", {"id": item_id})
            return {"deleted": count}

        @tool
        def transfer_items(from_id: int, to_id: int, amount: int) -> dict:
            """Transfer quantity between items (atomic operation)."""
            with agent.transaction():
                # Get current quantities
                from_item = agent.query(
                    "SELECT quantity FROM items WHERE id = :id",
                    {"id": from_id},
                    one=True,
                )
                to_item = agent.query(
                    "SELECT quantity FROM items WHERE id = :id", {"id": to_id}, one=True
                )

                if not from_item or not to_item:
                    raise ValueError("One or both items not found")

                if from_item["quantity"] < amount:
                    raise ValueError("Insufficient quantity")

                # Update both items
                agent.update(
                    "items",
                    {"quantity": from_item["quantity"] - amount},
                    "id = :id",
                    {"id": from_id},
                )
                agent.update(
                    "items",
                    {"quantity": to_item["quantity"] + amount},
                    "id = :id",
                    {"id": to_id},
                )

            return {"transferred": amount, "from": from_id, "to": to_id}


# --- Integration Tests ---


def test_agent_with_database_in_memory():
    """Agent works with in-memory database."""
    agent = ItemAgent()

    # Add items
    agent.insert("items", {"name": "Apple", "quantity": 10})
    agent.insert("items", {"name": "Banana", "quantity": 5})

    # Query
    items = agent.query("SELECT * FROM items")
    assert len(items) == 2

    agent.close_db()


def test_agent_with_temp_db_fixture():
    """Agent works with temp_db fixture."""
    schema = """
        CREATE TABLE items (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            quantity INTEGER DEFAULT 0
        )
    """

    with temp_db(schema) as db_path:
        agent = ItemAgent(db_path=db_path)

        # Verify schema already exists (from fixture)
        assert agent.table_exists("items")

        # Add data
        agent.insert("items", {"name": "Test", "quantity": 100})
        items = agent.query("SELECT * FROM items")
        assert len(items) == 1

        agent.close_db()


def test_agent_tools_use_database():
    """Tools registered on agent can use database methods."""
    agent = ItemAgent()

    # Get tools from registry (they're registered during _register_tools)
    # Directly call database methods which tools would use
    item_id = agent.insert("items", {"name": "Widget", "quantity": 50})
    assert item_id == 1

    item = agent.query("SELECT * FROM items WHERE id = :id", {"id": item_id}, one=True)
    assert item["name"] == "Widget"
    assert item["quantity"] == 50

    agent.close_db()


def test_agent_transaction_in_tool():
    """Transaction within agent tool works correctly."""
    agent = ItemAgent()

    # Create two items
    id1 = agent.insert("items", {"name": "Source", "quantity": 100})
    id2 = agent.insert("items", {"name": "Dest", "quantity": 0})

    # Transfer using transaction
    with agent.transaction():
        source = agent.query(
            "SELECT quantity FROM items WHERE id = :id", {"id": id1}, one=True
        )
        dest = agent.query(
            "SELECT quantity FROM items WHERE id = :id", {"id": id2}, one=True
        )

        agent.update(
            "items", {"quantity": source["quantity"] - 30}, "id = :id", {"id": id1}
        )
        agent.update(
            "items", {"quantity": dest["quantity"] + 30}, "id = :id", {"id": id2}
        )

    # Verify transfer
    source = agent.query(
        "SELECT quantity FROM items WHERE id = :id", {"id": id1}, one=True
    )
    dest = agent.query(
        "SELECT quantity FROM items WHERE id = :id", {"id": id2}, one=True
    )

    assert source["quantity"] == 70
    assert dest["quantity"] == 30

    agent.close_db()


def test_agent_transaction_rollback():
    """Failed transaction in agent context rolls back."""
    agent = ItemAgent()

    # Create items
    id1 = agent.insert("items", {"name": "Source", "quantity": 100})
    id2 = agent.insert("items", {"name": "Dest", "quantity": 0})

    # Attempt transfer that fails mid-way
    try:
        with agent.transaction():
            agent.update("items", {"quantity": 50}, "id = :id", {"id": id1})
            agent.update("items", {"quantity": 50}, "id = :id", {"id": id2})
            raise ValueError("Simulated failure")
    except ValueError:
        pass

    # Verify rollback - original values preserved
    source = agent.query(
        "SELECT quantity FROM items WHERE id = :id", {"id": id1}, one=True
    )
    dest = agent.query(
        "SELECT quantity FROM items WHERE id = :id", {"id": id2}, one=True
    )

    assert source["quantity"] == 100  # Unchanged
    assert dest["quantity"] == 0  # Unchanged

    agent.close_db()


def test_agent_multiple_instances_isolated():
    """Multiple agent instances have isolated databases."""
    agent1 = ItemAgent()
    agent2 = ItemAgent()

    # Add different data to each
    agent1.insert("items", {"name": "Agent1-Item", "quantity": 1})
    agent2.insert("items", {"name": "Agent2-Item", "quantity": 2})

    # Verify isolation
    items1 = agent1.query("SELECT * FROM items")
    items2 = agent2.query("SELECT * FROM items")

    assert len(items1) == 1
    assert len(items2) == 1
    assert items1[0]["name"] == "Agent1-Item"
    assert items2[0]["name"] == "Agent2-Item"

    agent1.close_db()
    agent2.close_db()


def test_agent_reinitialize_database():
    """Agent can reinitialize database."""
    agent = ItemAgent()

    # Add data
    agent.insert("items", {"name": "First", "quantity": 1})
    assert len(agent.query("SELECT * FROM items")) == 1

    # Reinitialize (creates new in-memory db)
    agent.init_db()

    # Schema needs to be recreated
    if not agent.table_exists("items"):
        agent.execute(
            """
            CREATE TABLE items (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                quantity INTEGER DEFAULT 0
            )
        """
        )

    # Old data is gone
    assert len(agent.query("SELECT * FROM items")) == 0

    agent.close_db()


def test_agent_file_database_persistence(tmp_path):
    """Agent data persists in file database."""
    db_path = str(tmp_path / "test.db")

    # First agent creates data
    agent1 = ItemAgent(db_path=db_path)
    agent1.insert("items", {"name": "Persistent", "quantity": 42})
    agent1.close_db()

    # Second agent reads same file
    agent2 = ItemAgent(db_path=db_path)
    items = agent2.query("SELECT * FROM items")

    assert len(items) == 1
    assert items[0]["name"] == "Persistent"
    assert items[0]["quantity"] == 42

    agent2.close_db()


def test_agent_db_ready_property():
    """Agent db_ready property works correctly."""
    agent = ItemAgent()
    assert agent.db_ready

    agent.close_db()
    assert not agent.db_ready


def test_agent_foreign_key_enforcement():
    """Foreign keys are enforced in agent database."""
    agent = ItemAgent()

    # Add related tables
    agent.execute(
        """
        CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            category_id INTEGER REFERENCES categories(id),
            name TEXT
        );
    """
    )

    # This should fail - no category with id=999
    with pytest.raises(Exception):
        agent.insert("products", {"category_id": 999, "name": "Orphan"})

    agent.close_db()


def test_agent_complex_workflow():
    """Test a complex multi-step workflow."""
    agent = ItemAgent()

    # Batch insert
    for i in range(10):
        agent.insert("items", {"name": f"Item-{i}", "quantity": i * 10})

    # Query with conditions
    high_qty = agent.query("SELECT * FROM items WHERE quantity >= :min", {"min": 50})
    assert len(high_qty) == 5  # Items 5-9 have qty >= 50

    # Update multiple
    count = agent.update(
        "items", {"quantity": 0}, "quantity < :threshold", {"threshold": 30}
    )
    assert count == 3  # Items 0, 1, 2

    # Delete multiple
    deleted = agent.delete("items", "quantity = :qty", {"qty": 0})
    assert deleted == 3

    # Final count
    remaining = agent.query("SELECT COUNT(*) as cnt FROM items", one=True)
    assert remaining["cnt"] == 7

    agent.close_db()
