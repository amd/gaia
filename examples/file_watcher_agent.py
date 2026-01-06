# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
File Watcher Agent Example

A simple agent that watches a directory for new files and processes them.
Uses FileWatcherMixin for clean integration with the Agent lifecycle.

Usage:
    python examples/file_watcher_agent.py [directory]

This will start watching the specified directory (default: ./watched_files)
for new files and process them automatically.

Examples:
    # Watch default directory
    python examples/file_watcher_agent.py

    # Watch a specific directory
    python examples/file_watcher_agent.py ./my_documents

    # Create test files to see it work
    echo "Hello World" > watched_files/test.txt
"""

import sys
import time
from pathlib import Path

from gaia import Agent, FileWatcherMixin, tool


class FileWatcherAgent(Agent, FileWatcherMixin):
    """Agent that watches a directory and processes new files."""

    def __init__(self, watch_dir: str = "./watched_files", **kwargs):
        # Set attributes BEFORE super().__init__() since it may call _get_system_prompt()
        self._watch_dir = Path(watch_dir)
        self.processed_files = []
        super().__init__(**kwargs)

        # Create watch directory if it doesn't exist
        self._watch_dir.mkdir(parents=True, exist_ok=True)

        # Use mixin to watch directory
        self.watch_directory(
            self._watch_dir,
            on_created=self._on_file_created,
            on_modified=self._on_file_modified,
            on_deleted=self._on_file_deleted,
            on_moved=self._on_file_moved,
            extensions=[],  # Watch all file types
            debounce_seconds=1.0,
        )

    def _get_system_prompt(self) -> str:
        return f"""You are a file processing assistant watching: {self._watch_dir}

When files are added, you automatically process them and can answer questions about them.

Available actions:
- list_processed(): Show all files that have been processed
- get_file_info(filename): Get details about a specific file
- get_stats(): Get watching statistics
"""

    def _register_tools(self):
        agent = self

        @tool
        def list_processed() -> dict:
            """List all files that have been processed."""
            return {
                "files": agent.processed_files,
                "count": len(agent.processed_files),
            }

        @tool
        def get_file_info(filename: str) -> dict:
            """Get information about a processed file."""
            for f in agent.processed_files:
                if f["name"] == filename:
                    return {"found": True, "file": f}
            return {"found": False, "message": f"File '{filename}' not found"}

        @tool
        def get_stats() -> dict:
            """Get file watching statistics."""
            return {
                "directory": str(agent._watch_dir),
                "watching": [str(d) for d in agent.watching_directories],
                "telemetry": agent.watcher_telemetry,
                "processed_count": len(agent.processed_files),
            }

    def _on_file_created(self, path: str) -> None:
        """Called when a new file is created."""
        file_path = Path(path)
        size = file_path.stat().st_size if file_path.exists() else 0
        file_info = {
            "name": file_path.name,
            "path": str(file_path.absolute()),
            "size": size,
            "extension": file_path.suffix,
            "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.processed_files.append(file_info)
        self.console.print_file_created(
            filename=file_path.name,
            size=size,
            extension=file_info["extension"] or "none",
        )

    def _on_file_modified(self, path: str) -> None:
        """Called when a file is modified."""
        file_path = Path(path)
        self.console.print_file_modified(file_path.name)

    def _on_file_deleted(self, path: str) -> None:
        """Called when a file is deleted."""
        file_path = Path(path)
        self.console.print_file_deleted(file_path.name)
        # Remove from processed list
        self.processed_files = [
            f for f in self.processed_files if f["name"] != file_path.name
        ]

    def _on_file_moved(self, src_path: str, dest_path: str) -> None:
        """Called when a file is renamed/moved."""
        src_file = Path(src_path)
        dest_file = Path(dest_path)
        self.console.print_file_moved(src_file.name, dest_file.name)
        # Update the filename in processed list
        for f in self.processed_files:
            if f["name"] == src_file.name:
                f["name"] = dest_file.name
                f["path"] = str(dest_file.absolute())
                f["extension"] = dest_file.suffix
                break


def main():
    """Run the File Watcher Agent."""
    # Get directory from command line or use default
    watch_dir = sys.argv[1] if len(sys.argv) > 1 else "./watched_files"

    print("=" * 60)
    print("File Watcher Agent (using FileWatcherMixin)")
    print("=" * 60)
    print(f"\nWatching directory: {watch_dir}")
    print("\nTo test, create files in the watched directory:")
    print(f'  echo "Hello" > {watch_dir}/test.txt')
    print("\nCommands:")
    print("  - 'list' or 'show files' - Show processed files")
    print("  - 'stats' - Show watching statistics")
    print("  - 'quit' or 'exit' - Stop and exit")
    print()

    agent = None
    try:
        agent = FileWatcherAgent(watch_dir=watch_dir)
        print("Ready! Waiting for files...\n")
    except Exception as e:
        print(f"Error initializing agent: {e}")
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

            # Process the message
            agent.process_query(user_input)
            print()

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {e}\n")

    # Cleanup - mixin handles stopping watchers
    if agent:
        agent.stop_all_watchers()


if __name__ == "__main__":
    main()
