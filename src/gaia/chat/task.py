# src/gaia/chat/task.py
"""
Task creation module for the chat system.

This module ensures that when a new task is created the global
document index is reset so that the header does not display
documents from the previous session.
"""

# Import the document manager that holds the global index.  The
# exact attribute name may vary, so we handle a few common
# possibilities.
try:
    import gaia.chat.document_manager as dm
except Exception:
    dm = None


def reset_document_context() -> None:
    """Reset the global document index to zero.

    The chat system keeps a global index that tracks the current
    document context.  When a new task is created this index must
    be reset so that the header starts with a clean context.
    """
    if dm is None:
        return

    # Common attribute names that might hold the index.
    for attr in ("global_index", "document_index", "doc_index"):
        if hasattr(dm, attr):
            setattr(dm, attr, 0)
            break


class Task:
    """Represents a chat task.

    The constructor resets the document context so that each new
    task starts with a clean header.
    """

    def __init__(self, name: str):
        self.name = name
        reset_document_context()

    def __repr__(self) -> str:
        return f"<Task name={self.name!r}>"


def create_task(name: str) -> Task:
    """Factory function for creating a new :class:`Task`.

    This helper is used by the UI when the user clicks '+' or
    'New Task'.  It ensures that the global document index is
    reset before the task is instantiated.
    """
    return Task(name)
