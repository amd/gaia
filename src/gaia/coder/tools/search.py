# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SearchToolsMixin — grep / find_symbol / list_files / semantic_search.

The three regex/AST tools in §15.2 of docs/plans/coder-agent.mdx, plus
``semantic_search`` which delegates to :class:`gaia.code_index.CodeIndexSDK`
for embeddings-backed retrieval (§6.5). ``find_symbol`` is Python-only for
v1 (uses ``ast``); other languages return an empty list and emit a WARN —
callers can then fall back to ``grep`` without surprise.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from typing import List, Literal, Optional, TypedDict

from gaia.agents.base.tools import tool
from gaia.coder.tools.file import FileToolsMixin, SearchHit

logger = logging.getLogger(__name__)


SymbolKind = Literal["function", "class", "import"]


class SymbolHit(TypedDict):
    """One match from :meth:`find_symbol`."""

    path: str
    line_number: int
    kind: SymbolKind
    qualname: str


class SemanticHit(TypedDict):
    """One match from :meth:`semantic_search`."""

    path: str
    start_line: int
    end_line: int
    score: float
    symbol_name: Optional[str]
    snippet: str


class SearchToolsMixin(FileToolsMixin):
    """Mixin providing ``grep`` / ``find_symbol`` / ``list_files``.

    Inherits from :class:`FileToolsMixin` so ``grep`` can delegate to the same
    regex engine as ``search_code`` without duplicating logic. Agents that opt
    into ``SearchToolsMixin`` therefore also get the six file tools — which is
    fine because in practice you always want both together.
    """

    def register_search_tools(self) -> None:
        """Register ``grep`` / ``find_symbol`` / ``list_files`` in the registry."""
        # Reuse file-tool implementations so ``glob`` / ``search_code`` are
        # always available when search tools are.
        self.register_file_tools()

        @tool
        def grep(
            pattern: str,
            path: str = ".",
            glob: Optional[str] = None,
        ) -> List[SearchHit]:
            """Thin wrapper around :func:`search_code` with its default flags."""
            # Fetch via the public accessor rather than reaching into the
            # private registry; preserves monkey-patch behaviour in tests.
            from gaia.agents.base.tools import get_tool_metadata

            meta = get_tool_metadata("search_code")
            if meta is None:
                raise RuntimeError(
                    "grep(): search_code tool is not registered — "
                    "SearchToolsMixin must be registered after FileToolsMixin."
                )
            return meta["function"](pattern, path=path, glob=glob)

        @tool
        def find_symbol(
            symbol: str,
            kind: Optional[SymbolKind] = None,
        ) -> List[SymbolHit]:
            """Find ``symbol`` in Python source files via ``ast``.

            Walks the CWD recursively looking for ``*.py`` files. If there are
            no Python sources under the CWD the call logs a WARN and returns
            ``[]`` — it does NOT silently skip (per §2 principle 3).

            ``kind`` filters by ``function`` / ``class`` / ``import``; ``None``
            returns all three.
            """
            hits: List[SymbolHit] = []
            py_files = list(Path(".").rglob("*.py"))
            if not py_files:
                logger.warning(
                    "find_symbol: no Python source files under cwd; "
                    "non-Python languages are unsupported in v1"
                )
                return hits
            for py in py_files:
                try:
                    source = py.read_text(encoding="utf-8")
                    tree = ast.parse(source, filename=str(py))
                except (SyntaxError, UnicodeDecodeError, OSError) as e:
                    logger.debug("find_symbol: skipping %s (%s)", py, e)
                    continue
                for node in ast.walk(tree):
                    for hit in _symbol_from_node(node, symbol, py):
                        if kind is None or hit["kind"] == kind:
                            hits.append(hit)
            return hits

        @tool
        def list_files(
            path: str,
            pattern: Optional[str] = None,
            recursive: bool = True,
        ) -> List[str]:
            """List files under ``path`` filtered by ``pattern`` (POSIX paths)."""
            base = Path(path)
            iterator = (
                base.rglob(pattern or "*") if recursive else base.glob(pattern or "*")
            )
            return sorted(p.as_posix() for p in iterator if p.is_file())

        @tool
        def semantic_search(
            query: str,
            top_k: int = 10,
            repo_path: Optional[str] = None,
        ) -> List[SemanticHit]:
            """Semantic code search backed by ``gaia.code_index.CodeIndexSDK``.

            Use this when ``grep`` would miss the answer because the code
            uses different terminology than the question — e.g. ``"how do
            we verify webhook signatures"`` should find HMAC-related code
            even if the literal phrase "webhook" never appears.

            Reads from the cached FAISS index at
            ``~/.gaia/code_index/<repo_hash>/``. If the cache doesn't
            exist yet, returns ``[]`` plus a WARN log pointing at
            ``CodeIndexSDK.index_repository()`` — does not auto-index
            (that's an explicit, expensive operation that goes through the
            EM via ``rag refresh`` per §6.9).

            Args:
                query: Natural-language query. Embedded with the same
                    model the index was built with.
                top_k: Maximum hits to return.
                repo_path: Override the repo root. Defaults to ``$PWD``.

            Returns:
                List of :class:`SemanticHit` ordered by descending score.
            """
            return _semantic_search_impl(query, top_k=top_k, repo_path=repo_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _semantic_search_impl(
    query: str,
    *,
    top_k: int,
    repo_path: Optional[str],
) -> List[SemanticHit]:
    """Module-level worker for :meth:`SearchToolsMixin.semantic_search`.

    Lazy-imports :mod:`gaia.code_index` so :class:`SearchToolsMixin` can
    still load on systems without ``faiss-cpu`` / ``numpy`` (the §6.5
    plan calls semantic search optional). Missing deps surface as a
    clear ``RuntimeError`` rather than the bare ``ImportError`` the SDK
    would otherwise raise.
    """
    try:
        from gaia.code_index import CodeIndexConfig, CodeIndexSDK
    except ImportError as e:
        raise RuntimeError(
            "semantic_search requires the 'rag' extra: "
            "pip install -e '.[rag]' (installs faiss-cpu + numpy)"
        ) from e

    repo_root = repo_path or os.getcwd()
    sdk = CodeIndexSDK(CodeIndexConfig(repo_path=repo_root))

    # If no cache exists, surface the actionable next step instead of
    # silently auto-indexing (that's an EM-driven operation per §6.9).
    status = sdk.get_status()
    if not status.get("indexed"):
        logger.warning(
            "semantic_search: no index found for %s. "
            "Run `gaia code-index <repo>` (or call "
            "`CodeIndexSDK(...).index_repository()`) to build one. "
            "Status: %s",
            repo_root,
            status,
        )
        return []

    results = sdk.search(query, top_k=top_k)
    hits: List[SemanticHit] = []
    for r in results:
        chunk = r.chunk
        snippet = chunk.content[:400]  # cap to keep the tool result small
        hits.append(
            {
                "path": chunk.file_path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "score": r.score,
                "symbol_name": chunk.symbol_name,
                "snippet": snippet,
            }
        )
    return hits


def _symbol_from_node(node: ast.AST, target: str, path: Path) -> List[SymbolHit]:
    """Yield :class:`SymbolHit`s when ``node`` defines/imports ``target``."""
    results: List[SymbolHit] = []
    if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == target
    ):
        results.append(
            {
                "path": path.as_posix(),
                "line_number": node.lineno,
                "kind": "function",
                "qualname": node.name,
            }
        )
    elif isinstance(node, ast.ClassDef) and node.name == target:
        results.append(
            {
                "path": path.as_posix(),
                "line_number": node.lineno,
                "kind": "class",
                "qualname": node.name,
            }
        )
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name == target or (alias.asname == target):
                results.append(
                    {
                        "path": path.as_posix(),
                        "line_number": node.lineno,
                        "kind": "import",
                        "qualname": f"{node.module or ''}.{alias.name}".lstrip("."),
                    }
                )
    elif isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name == target or alias.asname == target:
                results.append(
                    {
                        "path": path.as_posix(),
                        "line_number": node.lineno,
                        "kind": "import",
                        "qualname": alias.name,
                    }
                )
    return results
