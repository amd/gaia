# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""MCP server exposing GAIA's semantic code search to external code assistants.

Wraps :class:`gaia.code_index.sdk.CodeIndexSDK` for one repository, so Claude
Code, Cursor, or any MCP client can search it by meaning. Embeddings run on the
local Lemonade Server; nothing leaves the machine.

Tools:
    search_code(query, scope, top_k)   ranked code chunks for a query
    get_index_status()                 whether an index exists, and its size
    rebuild_index(max_files, model)    build or refresh the index

Usage:
    gaia mcp code-index --repo /path/to/repo          # stdio
    claude mcp add gaia-code-index -- gaia mcp code-index --repo .
"""

from __future__ import annotations

import argparse
import threading
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from gaia.code_index.sdk import CodeIndexConfig, CodeIndexSDK
from gaia.logger import get_logger

if TYPE_CHECKING:
    from mcp.server import MCPServer

logger = get_logger(__name__)

SCOPES = ("all", "code")
MAX_TOP_K = 50
#: Characters of each chunk returned; enough to judge a hit without flooding context.
SNIPPET_CHARS = 1500


def _refuse_home_directory(repo: Path) -> None:
    if repo.resolve() == Path.home().resolve():
        raise ValueError(
            f"Refusing to serve a code index for your home directory ({repo}): "
            "it would embed every repo, download, and cache you own. Pass the "
            "repository instead, e.g. `gaia mcp code-index --repo ~/src/myrepo`."
        )


def _result_to_dict(result: Any) -> Dict[str, Any]:
    chunk = result.chunk
    return {
        "file_path": chunk.file_path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "language": chunk.language,
        "symbol_name": chunk.symbol_name,
        "symbol_type": chunk.symbol_type,
        "score": round(result.score, 4),
        "content": chunk.content[:SNIPPET_CHARS],
    }


class CodeIndexService:
    """The tool logic, independent of the MCP transport."""

    def __init__(
        self,
        config: CodeIndexConfig,
        sdk_factory: Callable[[CodeIndexConfig], Any] = CodeIndexSDK,
    ):
        repo = Path(config.repo_path).expanduser()
        _refuse_home_directory(repo)
        self._factory = sdk_factory
        self._config = replace(config, repo_path=str(repo.resolve()))
        self._sdk = sdk_factory(self._config)
        self._rebuild_lock = threading.Lock()
        self._building: Optional[Any] = None

    @property
    def repo_path(self) -> str:
        return self._config.repo_path

    def search_code(self, query: str, scope: str, top_k: int) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            raise ValueError("query must not be empty.")
        if scope not in SCOPES:
            raise ValueError(
                f"scope must be one of {', '.join(SCOPES)}, got {scope!r}."
            )
        if not 1 <= top_k <= MAX_TOP_K:
            raise ValueError(f"top_k must be between 1 and {MAX_TOP_K}, got {top_k}.")
        if not self._sdk.is_indexed():
            raise RuntimeError(
                f"No code index for {self.repo_path} yet. Call rebuild_index "
                "first; it embeds the repository through Lemonade Server."
            )
        return [_result_to_dict(r) for r in self._sdk.search(query, scope, top_k)]

    def get_index_status(self) -> Dict[str, Any]:
        status = dict(self._sdk.get_status())
        building = self._building
        status["rebuild_running"] = building is not None
        if building is not None:
            status["progress"] = dict(building.progress)
        return status

    def rebuild_index(
        self, max_files: Optional[int] = None, model: Optional[str] = None
    ) -> Dict[str, Any]:
        if max_files is not None and max_files < 1:
            raise ValueError(f"max_files must be at least 1, got {max_files}.")
        if model is not None and not model.strip():
            raise ValueError(
                "model must not be empty; omit it to keep the current one."
            )
        if not self._rebuild_lock.acquire(blocking=False):
            raise RuntimeError(
                "A rebuild is already running for this repository. Poll "
                "get_index_status until rebuild_running is false."
            )
        try:
            config = self._config
            if max_files is not None:
                config = replace(config, max_files=max_files)
            if model is not None:
                config = replace(config, embedding_model=model.strip())
            # A fresh SDK, so searches keep using the old index until this one is done.
            sdk = self._factory(config)
            self._building = sdk
            result = sdk.index_repository()
            self._config, self._sdk = config, sdk
        finally:
            self._building = None
            self._rebuild_lock.release()

        report: Dict[str, Any] = {
            "repo_path": self.repo_path,
            "embedding_model": config.embedding_model,
            "files_indexed": result.files_indexed,
            "chunks_created": result.chunks_created,
            "duration_seconds": round(result.duration_seconds, 2),
        }
        if result.chunks_dropped:
            report["chunks_dropped"] = result.chunks_dropped
            report["warning"] = (
                f"{result.chunks_dropped} chunk(s) failed to embed and are not "
                "searchable; call rebuild_index again to retry them."
            )
        return report


def create_code_index_mcp(service: CodeIndexService) -> "MCPServer":
    """Create the MCP server for *service*'s repository."""
    import anyio
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer(name="gaia-code-index")

    async def _run(fn: Callable[..., Any], *args: Any) -> Any:
        try:
            return await anyio.to_thread.run_sync(fn, *args)
        # The SDK hides any other exception's text; these carry the remedy.
        except (ValueError, RuntimeError, ImportError, OSError) as e:
            raise ToolError(str(e)) from e

    @server.tool()
    async def search_code(
        query: str, scope: str = "all", top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """Semantic search over the repository's code.

        Finds code by meaning, not exact text, e.g. "where are auth tokens
        refreshed". Returns up to top_k chunks, best first, each with
        file_path, start_line, end_line, symbol_name, score, and content.
        Call rebuild_index first if get_index_status reports no index.

        Args:
            query: What you are looking for, in plain words or as code.
            scope: "all" or "code".
            top_k: How many results to return, 1-50.
        """
        return await _run(service.search_code, query, scope, top_k)

    @server.tool()
    async def get_index_status() -> Dict[str, Any]:
        """Report whether the repository is indexed, with chunk and file counts,
        the embedding model, and progress of a rebuild that is running."""
        return await _run(service.get_index_status)

    @server.tool()
    async def rebuild_index(
        max_files: Optional[int] = None, model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Build or refresh the code index. Unchanged files are reused, so a
        refresh is fast; the first build or a model change embeds every file
        and can take minutes on a large repository.

        Args:
            max_files: Most source files to index (default 5000).
            model: Lemonade embedding model to use; changing it re-embeds all.
        """
        return await _run(service.rebuild_index, max_files, model)

    return server


def main(argv: Optional[List[str]] = None) -> None:
    """Run the code-index MCP server over stdio."""
    from gaia.mcp.servers.tui_mcp import route_logging_to_stderr

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Repository to index and search (default: current directory)",
    )
    args = parser.parse_args(argv)

    route_logging_to_stderr()
    service = CodeIndexService(CodeIndexConfig(repo_path=str(args.repo)))
    logger.info("Serving code index for %s over stdio", service.repo_path)
    create_code_index_mcp(service).run(transport="stdio")


if __name__ == "__main__":
    main()
