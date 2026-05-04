# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
CodeIndexToolsMixin — exposes CodeIndexSDK operations as @tool methods.

Compose onto any Agent subclass to grant it the four code-index tools:
``index_codebase``, ``search_code_index``, ``get_index_status``,
``clear_code_index``.

Follows the same pattern as ``RAGToolsMixin`` and ``FileIOToolsMixin``:
the mixin exposes a ``register_code_index_tools()`` method that the
consuming agent calls from its ``_register_tools`` hook.

State (``_repo_path``, ``_code_index_config``, ``_code_index_sdk``) is
set up lazily on first tool invocation if the consumer did not
initialise it explicitly. This keeps the mixin composable without
requiring cooperative ``__init__`` chaining.
"""

import json
import os
from typing import Any, Dict, Optional

from gaia.agents.base.tools import tool
from gaia.logger import get_logger

logger = get_logger(__name__)

try:
    from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK

    _CODE_INDEX_AVAILABLE = True
except ImportError:
    _CODE_INDEX_AVAILABLE = False

_MISSING_DEPS_MSG = (
    "code_index dependencies missing. Install with: pip install -e '.[rag]'"
)


class CodeIndexToolsMixin:
    """Mixin providing semantic code-index tools.

    Tools provided:
    - ``index_codebase``: Build a FAISS vector index over a repository
    - ``search_code_index``: Semantic search over the index
    - ``get_index_status``: Report current index state
    - ``clear_code_index``: Remove the cached index

    Consumer responsibilities:
    - Call ``self.register_code_index_tools()`` from ``_register_tools``.
    - Optionally set ``self._repo_path`` (absolute path) before first use.
      If unset, defaults to the current working directory.
    - Optionally set ``self._code_index_config`` to a pre-built
      ``CodeIndexConfig``; otherwise one is constructed from
      ``_repo_path``.
    """

    def _init_code_index_state(
        self,
        repo_path: str = ".",
        code_index_config: Optional[Any] = None,
    ) -> None:
        """Initialise mixin state. Safe to call multiple times (idempotent).

        Args:
            repo_path: Repository root (absolute or relative, resolved here).
            code_index_config: Optional pre-built ``CodeIndexConfig``.
        """
        self._repo_path = os.path.abspath(repo_path)
        self._code_index_config = code_index_config
        self._code_index_sdk: Optional[Any] = None

    def _ensure_code_index_state(self) -> None:
        """Populate default state on first access if consumer skipped init."""
        if not hasattr(self, "_repo_path"):
            self._repo_path = os.path.abspath(".")
        if not hasattr(self, "_code_index_config"):
            self._code_index_config = None
        if not hasattr(self, "_code_index_sdk"):
            self._code_index_sdk = None

    def _get_code_index_sdk(self) -> Optional[Any]:
        """Lazily construct and return the ``CodeIndexSDK`` instance."""
        if not _CODE_INDEX_AVAILABLE:
            return None
        self._ensure_code_index_state()
        if self._code_index_sdk is None:
            config = self._code_index_config or CodeIndexConfig(
                repo_path=self._repo_path
            )
            self._code_index_sdk = CodeIndexSDK(config)
        return self._code_index_sdk

    def register_code_index_tools(self) -> None:
        """Register code-index tools with the agent's tool registry."""

        @tool
        def index_codebase(repo_path: str = "") -> str:
            """Index a code repository for semantic search.

            Scans the repository, parses source files (Python, JS, TS, Go,
            Rust, Java, C/C++), and stores embeddings in a local FAISS index
            for fast semantic search.

            Args:
                repo_path: Absolute path to the repository root. Defaults to
                           the agent's configured repo_path if omitted.

            Returns:
                JSON summary with files_indexed, chunks_created, and status.
            """
            if not _CODE_INDEX_AVAILABLE:
                return json.dumps({"error": _MISSING_DEPS_MSG})

            self._ensure_code_index_state()

            if repo_path:
                resolved = os.path.abspath(repo_path)
                if not os.path.isdir(resolved):
                    return json.dumps({"error": f"Not a directory: {resolved}"})
                # Restrict to the agent's original repo_path to prevent
                # LLM-directed path traversal to arbitrary directories.
                original = os.path.abspath(self._repo_path)
                if not resolved.startswith(original + os.sep) and resolved != original:
                    return json.dumps({"error": f"repo_path must be within {original}"})
                self._repo_path = resolved
                self._code_index_config = None
                self._code_index_sdk = None

            sdk = self._get_code_index_sdk()
            if sdk is None:
                return json.dumps({"error": "code_index SDK not initialised"})

            try:
                result = sdk.index_repository()
                return json.dumps(
                    {
                        "status": "ok",
                        "files_indexed": result.files_indexed,
                        "chunks_created": result.chunks_created,
                    }
                )
            except Exception as e:
                logger.error("index_codebase failed: %s", e)
                return json.dumps({"error": str(e)})

        @tool
        def search_code_index(
            query: str,
            scope: str = "all",
            top_k: int = 10,
        ) -> str:
            """Semantic search over an indexed codebase.

            Embeds the query and returns the most relevant code chunks from
            the FAISS index.

            Args:
                query: Natural language or code snippet to search for.
                scope: Filter results. One of "all", "code".
                top_k: Maximum number of results to return (default 10).

            Returns:
                JSON list of search results with file_path, symbol_name,
                language, score, and a content snippet.
            """
            if not _CODE_INDEX_AVAILABLE:
                return json.dumps({"error": _MISSING_DEPS_MSG})

            sdk = self._get_code_index_sdk()
            if sdk is None:
                return json.dumps({"error": "code_index SDK not initialised"})

            try:
                results = sdk.search(query, scope=scope, top_k=top_k)
                output = []
                for r in results:
                    chunk = r.chunk
                    entry: Dict[str, Any] = {
                        "score": round(r.score, 4),
                        "type": r.result_type,
                        "content": chunk.content[:500],
                    }
                    if hasattr(chunk, "file_path"):
                        entry["file_path"] = chunk.file_path
                    if hasattr(chunk, "symbol_name") and chunk.symbol_name:
                        entry["symbol_name"] = chunk.symbol_name
                    if hasattr(chunk, "language"):
                        entry["language"] = chunk.language
                    if hasattr(chunk, "start_line"):
                        entry["start_line"] = chunk.start_line
                    output.append(entry)
                return json.dumps(output, indent=2)
            except Exception as e:
                logger.error("search_code_index failed: %s", e)
                return json.dumps({"error": str(e)})

        @tool
        def get_index_status() -> str:
            """Return the current status of the code index.

            Returns:
                JSON with indexed state, total chunks, files tracked,
                embedding model, and cache path.
            """
            if not _CODE_INDEX_AVAILABLE:
                return json.dumps({"error": _MISSING_DEPS_MSG})

            sdk = self._get_code_index_sdk()
            if sdk is None:
                return json.dumps({"error": "code_index SDK not initialised"})

            return json.dumps(sdk.get_status(), indent=2)

        @tool
        def clear_code_index() -> str:
            """Remove the cached code index for the current repository.

            Forces a full re-index on next use. Useful after large refactors
            or when switching embedding models.

            Returns:
                JSON confirmation message.
            """
            if not _CODE_INDEX_AVAILABLE:
                return json.dumps({"error": _MISSING_DEPS_MSG})

            sdk = self._get_code_index_sdk()
            if sdk is None:
                return json.dumps({"error": "code_index SDK not initialised"})

            try:
                sdk.clear_index()
                self._code_index_sdk = None
                return json.dumps({"status": "ok", "message": "Index cleared"})
            except Exception as e:
                logger.error("clear_code_index failed: %s", e)
                return json.dumps({"error": str(e)})
