# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Notes Agent Example

A simple agent that manages personal notes using DatabaseMixin.

Usage:
    python examples/notes_agent.py

This will start an interactive session where you can ask the agent to:
- Create notes: "Create a note called 'Shopping List' with content 'milk, eggs, bread'"
- List notes: "Show me all my notes"
- Search notes: "Find notes about shopping"
- Delete notes: "Delete note 1"
"""

from gaia import Agent, DatabaseMixin, tool


class NotesAgent(Agent, DatabaseMixin):
    """Agent that manages personal notes."""

    def __init__(self, db_path: str = "data/notes.db", **kwargs):
        super().__init__(**kwargs)
        self.init_db(db_path)

        # Create schema on first run
        if not self.table_exists("notes"):
            self.execute(
                """
                CREATE TABLE notes (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

    def _get_system_prompt(self) -> str:
        return """You are a notes assistant. Help users manage their personal notes.

Available actions:
- Create new notes with add_note(title, content)
- List all notes with list_notes()
- Search notes with search_notes(query)
- Delete notes with delete_note(note_id)

Always confirm actions and provide helpful feedback."""

    def _register_tools(self):
        agent = self

        @tool
        def add_note(title: str, content: str = "") -> dict:
            """Create a new note with a title and optional content."""
            note_id = agent.insert("notes", {"title": title, "content": content})
            return {
                "success": True,
                "id": note_id,
                "message": f"Created note '{title}' with ID {note_id}",
            }

        @tool
        def list_notes() -> dict:
            """List all notes with their IDs, titles, and creation dates."""
            notes = agent.query(
                "SELECT id, title, created_at FROM notes ORDER BY created_at DESC"
            )
            return {
                "notes": notes,
                "count": len(notes),
                "message": f"Found {len(notes)} note(s)",
            }

        @tool
        def get_note(note_id: int) -> dict:
            """Get the full content of a specific note by ID."""
            note = agent.query(
                "SELECT * FROM notes WHERE id = :id", {"id": note_id}, one=True
            )
            if note:
                return {"note": dict(note), "found": True}
            return {"found": False, "message": f"Note {note_id} not found"}

        @tool
        def search_notes(query: str) -> dict:
            """Search notes by title or content."""
            notes = agent.query(
                "SELECT * FROM notes WHERE title LIKE :q OR content LIKE :q",
                {"q": f"%{query}%"},
            )
            return {
                "results": notes,
                "count": len(notes),
                "message": f"Found {len(notes)} note(s) matching '{query}'",
            }

        @tool
        def update_note(note_id: int, title: str = None, content: str = None) -> dict:
            """Update a note's title and/or content."""
            updates = {}
            if title is not None:
                updates["title"] = title
            if content is not None:
                updates["content"] = content

            if not updates:
                return {"success": False, "message": "No updates provided"}

            count = agent.update("notes", updates, "id = :id", {"id": note_id})
            if count > 0:
                return {"success": True, "message": f"Updated note {note_id}"}
            return {"success": False, "message": f"Note {note_id} not found"}

        @tool
        def delete_note(note_id: int) -> dict:
            """Delete a note by ID."""
            count = agent.delete("notes", "id = :id", {"id": note_id})
            if count > 0:
                return {"success": True, "message": f"Deleted note {note_id}"}
            return {"success": False, "message": f"Note {note_id} not found"}


def main():
    """Run the Notes Agent interactively."""
    print("=" * 60)
    print("Notes Agent - Personal Notes Manager")
    print("=" * 60)
    print("\nExamples:")
    print("  - 'Create a note called Shopping List with milk, eggs, bread'")
    print("  - 'Show all my notes'")
    print("  - 'Search for notes about shopping'")
    print("  - 'Delete note 1'")
    print("\nType 'quit' or 'exit' to stop.\n")

    # Create agent (uses local Lemonade server by default)
    try:
        agent = NotesAgent()
        print(f"Database initialized. Ready!\n")
    except Exception as e:
        print(f"Error initializing agent: {e}")
        print("\nMake sure Lemonade server is running:")
        print("  lemonade-server serve")
        return

    # Interactive loop
    while True:
        try:
            user_input = input("You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                print("Goodbye!")
                break

            # Process the message (console prints the output)
            agent.process_query(user_input)
            print()  # Add spacing

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")

    # Cleanup
    agent.close_db()


if __name__ == "__main__":
    main()
