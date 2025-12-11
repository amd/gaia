# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
File Tools Mixin for Chat Agent.

Provides directory monitoring for auto-indexing.
NOTE: File search is handled by shell_tools.run_shell_command for flexibility.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


class FileToolsMixin:
    """
    Mixin providing directory monitoring for auto-indexing.

    Tools provided:
    - add_watch_directory: Monitor directory for file changes and auto-index
    """

    def register_file_tools(self) -> None:
        """Register file operation tools."""
        from gaia.agents.base.tools import tool

        @tool(
            name="add_watch_directory",
            description="Add a directory to monitor for new documents. Files will be automatically indexed when created or modified.",
            parameters={
                "directory": {
                    "type": "str",
                    "description": "Directory path to watch",
                    "required": True,
                }
            },
        )
        def add_watch_directory(directory: str) -> Dict[str, Any]:
            """Add directory to watch list with path validation and auto-indexing."""
            try:
                # Validate path with PathValidator (handles user prompting and persistence)
                if not self.path_validator.is_path_allowed(directory):
                    return {"status": "error", "error": f"Access denied: {directory}"}

                if not os.path.exists(directory):
                    return {
                        "status": "error",
                        "error": f"Directory not found: {directory}",
                    }

                if directory not in self.watch_directories:
                    self.watch_directories.append(directory)
                    self._watch_directory(directory)

                    # Index existing files in the directory
                    path = Path(directory)
                    pdf_files = list(path.glob("*.pdf"))
                    indexed_count = 0

                    for pdf_file in pdf_files:
                        try:
                            if self.rag.index_document(str(pdf_file)):
                                self.indexed_files.add(str(pdf_file))
                                indexed_count += 1
                                if hasattr(self, "debug") and self.debug:
                                    logger.debug(f"Auto-indexed: {pdf_file}")
                        except Exception as e:
                            logger.warning(f"Failed to index {pdf_file}: {e}")

                    # Auto-save session after adding watch directory
                    self._auto_save_session()

                    return {
                        "status": "success",
                        "message": f"Now watching: {directory}",
                        "total_files_found": len(pdf_files),
                        "files_indexed": indexed_count,
                    }
                else:
                    return {
                        "status": "success",
                        "message": f"Already watching: {directory}",
                    }
            except Exception as e:
                logger.error(f"Error adding watch directory: {e}")
                return {
                    "status": "error",
                    "error": str(e),
                    "has_errors": True,
                    "operation": "add_watch_directory",
                    "directory": directory,
                    "hint": "Failed to start watching directory. Check if directory exists and is readable.",
                }
