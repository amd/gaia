# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
CodeIndexAgent — dedicated agent for semantic code search and repository indexing.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool

log = logging.getLogger(__name__)

try:
    from gaia.code_index.git import GitIndexer
    from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK

    _CODE_INDEX_AVAILABLE = True
except ImportError:
    _CODE_INDEX_AVAILABLE = False

_SYSTEM_PROMPT = """\
You are a code search assistant that helps developers understand codebases.

Capabilities:
- index_codebase: Index a repository to build a searchable vector index
- search_code_index: Semantic search over indexed code, commits, and PRs
- code_index_status: Check the current index status
- clear_code_index: Clear and rebuild the index
- search_git_history: Search git commit messages directly (no index needed)

Workflow:
1. Call code_index_status to check if an index exists.
2. If no index exists, call index_codebase to build one.
3. Use search_code_index to find relevant code.
4. For git history queries, use search_git_history directly.

When presenting results:
- Always include file paths and line numbers when available
- Show symbol names (function/class names) when available
- Include relevant code snippets
- Explain what the code does based on the results
"""


class CodeIndexAgent(Agent):
    """Dedicated agent for semantic code search and repository indexing.

    Indexes a codebase using FAISS vector search and Lemonade embeddings,
    then enables natural language Q&A over the indexed code.

    Usage:
        agent = CodeIndexAgent(repo_path="/path/to/repo")
        agent.process_query("How does the authentication module work?")
    """

    def __init__(
        self,
        repo_path: str = ".",
        code_index_config: Optional[Any] = None,
        **kwargs,
    ):
        """Initialize the CodeIndexAgent.

        Args:
            repo_path: Path to the repository to index (default: current directory).
            code_index_config: Optional CodeIndexConfig. If None, a default config
                               is created from repo_path.
            **kwargs: Passed to the base Agent (max_steps, model_id, etc.)
        """
        kwargs.setdefault("max_steps", 10)
        kwargs.setdefault("model_id", "Qwen3.5-35B-A3B-GGUF")

        self._repo_path = os.path.abspath(repo_path)
        self._code_index_config = code_index_config
        self._code_index_sdk: Optional[Any] = None

        super().__init__(**kwargs)

    def _get_code_index_sdk(self) -> Optional[Any]:
        """Lazily initialise and return the CodeIndexSDK instance."""
        if not _CODE_INDEX_AVAILABLE:
            return None
        if self._code_index_sdk is None:
            config = self._code_index_config or CodeIndexConfig(
                repo_path=self._repo_path
            )
            self._code_index_sdk = CodeIndexSDK(config)
        return self._code_index_sdk

    def _register_tools(self):
        @tool
        def index_codebase(repo_path: str = "") -> str:
            """Index a code repository for semantic search.

            Scans the repository, parses source files (Python, JS, TS, Go, Rust,
            Java, C/C++), optionally indexes git history, and stores embeddings
            in a local FAISS index for fast semantic search.

            Args:
                repo_path: Absolute path to the repository root. Defaults to the
                           agent's configured repo_path if omitted.

            Returns:
                JSON summary with files_indexed, chunks_created, and status.
            """
            if not _CODE_INDEX_AVAILABLE:
                return json.dumps(
                    {"error": "code_index not available (faiss not installed?)"}
                )

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
                        "commits_indexed": result.commits_indexed,
                        "prs_indexed": result.prs_indexed,
                    }
                )
            except Exception as e:
                log.error(f"index_codebase failed: {e}")
                return json.dumps({"error": str(e)})

        @tool
        def search_code_index(
            query: str,
            scope: str = "all",
            top_k: int = 10,
        ) -> str:
            """Semantic search over an indexed codebase.

            Embeds the query and returns the most relevant code chunks, commits,
            or pull requests from the FAISS index.

            Args:
                query: Natural language or code snippet to search for.
                scope: Filter results. One of "all", "code", "commit", "pr".
                top_k: Maximum number of results to return (default 10).

            Returns:
                JSON list of search results with file_path, symbol_name,
                language, score, and a content snippet.
            """
            if not _CODE_INDEX_AVAILABLE:
                return json.dumps(
                    {"error": "code_index not available (faiss not installed?)"}
                )

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
                    if hasattr(chunk, "commit_hash"):
                        entry["commit_hash"] = chunk.commit_hash
                    if hasattr(chunk, "pr_number"):
                        entry["pr_number"] = chunk.pr_number
                    output.append(entry)
                return json.dumps(output, indent=2)
            except Exception as e:
                log.error(f"search_code_index failed: {e}")
                return json.dumps({"error": str(e)})

        @tool
        def code_index_status() -> str:
            """Return the current status of the code index.

            Returns:
                JSON with indexed state, total chunks, files tracked,
                embedding model, and cache path.
            """
            if not _CODE_INDEX_AVAILABLE:
                return json.dumps({"error": "code_index not available"})

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
                return json.dumps({"error": "code_index not available"})

            sdk = self._get_code_index_sdk()
            if sdk is None:
                return json.dumps({"error": "code_index SDK not initialised"})

            try:
                sdk.clear_index()
                self._code_index_sdk = None
                return json.dumps({"status": "ok", "message": "Index cleared"})
            except Exception as e:
                return json.dumps({"error": str(e)})

        @tool
        def search_git_history(query: str, max_results: int = 10) -> str:
            """Search git commit history for relevant changes.

            Runs a text search over commit messages and file paths.
            Does not require the FAISS index — uses git log directly.

            Args:
                query: Text to search for in commit messages.
                max_results: Maximum number of commits to return.

            Returns:
                JSON list of matching commits with sha, author, date,
                message, and files_changed.
            """
            if not _CODE_INDEX_AVAILABLE:
                return json.dumps({"error": "code_index not available"})

            import subprocess

            # Cap max_results to prevent excessive output
            max_results = min(max_results, 100)

            try:
                result = subprocess.run(
                    [
                        "git",
                        "-C",
                        self._repo_path,
                        "log",
                        "--fixed-strings",
                        f"--grep={query}",
                        "-i",
                        f"--max-count={max_results}",
                        "--format=%H\x1f%s\x1f%an\x1f%aI",
                        "--name-only",
                    ],
                    capture_output=True,
                    text=True,
                    shell=False,
                    check=False,
                )
            except FileNotFoundError:
                return json.dumps({"error": "git not found"})

            if result.returncode != 0:
                return json.dumps({"error": result.stderr.strip()})

            try:
                config = CodeIndexConfig(repo_path=self._repo_path)
                indexer = GitIndexer(config)
                commits = indexer._parse_git_log(result.stdout)

                output = [
                    {
                        "commit_hash": c.commit_hash,
                        "author": c.author,
                        "date": c.date,
                        "message": c.diff_summary,
                        "files_changed": c.files_changed,
                    }
                    for c in commits[:max_results]
                ]
                return json.dumps(output, indent=2)
            except Exception as e:
                return json.dumps({"error": str(e)})

    def _get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT
