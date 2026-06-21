# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tavily web-search wrapper with caching, credit accounting, and a fallback.

Sits between GAIA and the ``tavily-python`` SDK so callers get one front door
with four behaviours layered on top of the raw API:

- **SQLite cache** (``DatabaseMixin``): a normalized query/params hash maps to a
  stored response with a TTL, so repeat queries don't re-spend credits.
- **Credit ledger**: every billable call records its credit cost, read from the
  SDK response when present, otherwise estimated from Tavily's published pricing.
- **Budget gate**: warn once usage crosses a soft threshold, block once it
  exceeds the hard cap. Blocking is the default; ``block=False`` downgrades the
  cap to a warning.
- **DuckDuckGo fallback**: when the ``mcp-tavily`` connector isn't configured,
  ``search`` degrades to the keyless DuckDuckGo path instead of failing.

The API key is read from the connector's keyring entry, never passed around in
plaintext config. See ``gaia.connectors.catalog.mcp_servers._TAVILY``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from gaia.database.mixin import DatabaseMixin
from gaia.logger import get_logger
from gaia.web.client import WebClient

log = get_logger(__name__)

# The ``tavily-python`` SDK is an optional dependency: an unconfigured install
# still works via the DuckDuckGo fallback. Guard the import like web/client.py
# does for beautifulsoup4.
try:
    from tavily import AsyncTavilyClient as _SdkAsyncTavilyClient
    from tavily import TavilyClient as _SdkTavilyClient

    TAVILY_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only when SDK absent
    _SdkTavilyClient = None
    _SdkAsyncTavilyClient = None
    TAVILY_SDK_AVAILABLE = False

# Matches ConnectorSpec.id in the catalog and the keyring env key it stores.
_CONNECTOR_ID = "mcp-tavily"
_API_KEY_ENV = "TAVILY_API_KEY"

_DEFAULT_DB_PATH = Path.home() / ".gaia" / "tavily_cache.db"
_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # results go stale; re-fetch after a day

# Credit cost per (operation, depth), used when the SDK response carries no
# usage metadata. Mirrors Tavily's published pricing (basic search = 1 credit,
# advanced = 2). Crawl cost is page-dependent — the table value is only the
# pre-call estimate for the budget gate; the ledger records the response value
# when the SDK provides one.
_CREDIT_COST = {
    ("search", "basic"): 1,
    ("search", "advanced"): 2,
    ("extract", "basic"): 1,
    ("extract", "advanced"): 2,
    ("crawl", "basic"): 1,
    ("crawl", "advanced"): 2,
}


class TavilyError(Exception):
    """Base class for Tavily wrapper errors."""


class TavilyConfigError(TavilyError):
    """The Tavily connector / SDK isn't usable for the requested operation."""


class TavilyBudgetExceeded(TavilyError):
    """A call was blocked because it would exceed the configured credit cap."""


@dataclass
class BudgetConfig:
    """Credit budget for a wrapper session.

    ``cap`` is the hard limit in credits; ``None`` means unlimited (the gate is
    a no-op). ``warn_threshold`` is the fraction of the cap at which a warning
    is logged. ``block`` decides what happens once the cap is exceeded: raise
    ``TavilyBudgetExceeded`` (default) or merely warn and proceed.
    """

    cap: Optional[int] = None
    warn_threshold: float = 0.8
    block: bool = True


def _normalize_query(query: str) -> str:
    """Lowercase, trim, and collapse whitespace so trivial variants share a key."""
    return re.sub(r"\s+", " ", query.strip().lower())


def _load_api_key() -> Optional[str]:
    """Return the Tavily API key from the connector keyring, or ``None``.

    ``None`` means the ``mcp-tavily`` connector isn't usable — the caller should
    fall back to DuckDuckGo. Imports are deferred so that merely importing this
    module doesn't pull in ``keyring``; if ``keyring`` isn't installed at all
    (it lives in the ``[ui]`` extras), the connector can't have been configured,
    so we treat that as "not configured" rather than crashing.
    """
    try:
        from gaia.connectors.handler import get_credential_sync
        from gaia.connectors.mcp_server import is_mcp_server_configured
    except ImportError as e:
        log.info("Connector subsystem unavailable (%s); using DuckDuckGo.", e)
        return None

    if not is_mcp_server_configured(_CONNECTOR_ID):
        return None
    cred = get_credential_sync(_CONNECTOR_ID)
    return cred["env"][_API_KEY_ENV]


async def _load_api_key_async() -> Optional[str]:
    """Async counterpart to :func:`_load_api_key`.

    Awaits ``get_credential`` instead of the sync wrapper, so it is safe to call
    from inside a running event loop — where ``get_credential_sync`` raises. The
    async client uses this so its constructor never blocks the loop.
    """
    try:
        from gaia.connectors.handler import get_credential
        from gaia.connectors.mcp_server import is_mcp_server_configured
    except ImportError as e:
        log.info("Connector subsystem unavailable (%s); using DuckDuckGo.", e)
        return None

    if not is_mcp_server_configured(_CONNECTOR_ID):
        return None
    cred = await get_credential(_CONNECTOR_ID)
    return cred["env"][_API_KEY_ENV]


class _TavilyBase(DatabaseMixin):
    """Shared cache, ledger, budget, and fallback logic for both clients.

    Subclasses set ``_SDK_CLASS`` and implement the public ``search`` / ``extract``
    / ``crawl`` methods (sync vs. async); everything credit- and cache-related
    lives here so the two clients can't drift apart.
    """

    _SDK_CLASS: Any = None

    def __init__(
        self,
        *,
        db_path: Union[str, Path] = _DEFAULT_DB_PATH,
        budget: Optional[BudgetConfig] = None,
        cache_ttl: int = _DEFAULT_TTL_SECONDS,
        sdk_client: Any = None,
        api_key: Optional[str] = None,
        web_client: Optional[WebClient] = None,
    ) -> None:
        self.init_db(str(db_path))
        self._ensure_schema()
        self._budget = budget or BudgetConfig()
        self._cache_ttl = cache_ttl
        self._web_client = web_client
        self._explicit_api_key = api_key

        # Resolution order: injected client (tests) → explicit key → connector
        # keyring. No key at all = unconfigured = DuckDuckGo fallback mode.
        if sdk_client is not None:
            self._sdk = sdk_client
            self._configured = True
            self._key_resolved = True
            return

        self._sdk = None
        self._configured = False
        self._key_resolved = False
        self._resolve_key_eagerly()

    def _resolve_key_eagerly(self) -> None:
        """Resolve the API key during construction.

        The sync client does this safely. ``AsyncTavilyClient`` overrides it to
        defer resolution to first use, because synchronous resolution calls
        ``get_credential_sync()``, which raises inside a running event loop.
        """
        key = (
            self._explicit_api_key
            if self._explicit_api_key is not None
            else _load_api_key()
        )
        self._apply_key(key)

    def _apply_key(self, key: Optional[str]) -> None:
        """Wire up the SDK client from a resolved key, or enter fallback mode."""
        if key is None:
            self._sdk = None
            self._configured = False
        elif self._SDK_CLASS is None:
            raise TavilyConfigError(
                "The Tavily connector is configured but the 'tavily-python' SDK "
                "is not installed. Install it with `pip install tavily-python` "
                "(or `uv pip install -e .`), then retry."
            )
        else:
            self._sdk = self._SDK_CLASS(api_key=key)
            self._configured = True
        self._key_resolved = True

    @property
    def configured(self) -> bool:
        """True when a real Tavily client is in use (vs. fallback mode)."""
        return self._configured

    def close(self) -> None:
        """Close the cache DB and any lazily-created web client."""
        self.close_db()
        if self._web_client is not None:
            self._web_client.close()

    # -- Schema --------------------------------------------------------------

    def _ensure_schema(self) -> None:
        self.execute("""
            CREATE TABLE IF NOT EXISTS tavily_cache (
                cache_key  TEXT PRIMARY KEY,
                operation  TEXT NOT NULL,
                response   TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tavily_ledger (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                operation  TEXT NOT NULL,
                credits    INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            """)

    # -- Cache ---------------------------------------------------------------

    @staticmethod
    def _cache_key(operation: str, payload: Any, params: Dict[str, Any]) -> str:
        """Hash of operation + normalized payload + result-affecting params.

        Params are part of the key because the same text with a different
        search depth or result count is a genuinely different request.
        """
        norm = {
            "op": operation,
            "payload": payload,
            "params": {k: params[k] for k in sorted(params)},
        }
        raw = json.dumps(norm, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        row = self.query(
            "SELECT response, created_at FROM tavily_cache WHERE cache_key = :k",
            {"k": key},
            one=True,
        )
        if row is None:
            return None
        if time.time() - row["created_at"] > self._cache_ttl:
            # Stale: a miss, overwritten only on re-fetch of THIS key. Rows for
            # queries never searched again are never pruned, so the cache file
            # grows unbounded over time (disk creep only — results stay correct).
            # Add a periodic ``DELETE ... WHERE created_at < cutoff`` sweep if it bites.
            return None
        return json.loads(row["response"])

    def _record(
        self, key: str, operation: str, response: Dict[str, Any], credits: int
    ) -> None:
        """Atomically cache the response and append the credit ledger entry."""
        now = time.time()
        with self.transaction():
            self.delete("tavily_cache", "cache_key = :k", {"k": key})
            self.insert(
                "tavily_cache",
                {
                    "cache_key": key,
                    "operation": operation,
                    "response": json.dumps(response),
                    "created_at": now,
                },
            )
            self.insert(
                "tavily_ledger",
                {"operation": operation, "credits": credits, "created_at": now},
            )

    # -- Credits / budget ----------------------------------------------------

    def _credits_used(self) -> int:
        row = self.query(
            "SELECT COALESCE(SUM(credits), 0) AS total FROM tavily_ledger", one=True
        )
        return int(row["total"]) if row else 0

    @staticmethod
    def _estimate_credits(operation: str, depth: str) -> int:
        """Pre-call cost estimate (no response yet) from the pricing table."""
        return _CREDIT_COST.get(
            (operation, depth), _CREDIT_COST.get((operation, "basic"), 1)
        )

    @classmethod
    def _actual_credits(
        cls, operation: str, depth: str, response: Dict[str, Any]
    ) -> int:
        """Credits to record: prefer SDK-reported usage, else the estimate."""
        if isinstance(response, dict):
            usage = response.get("usage")
            if isinstance(usage, dict) and "credits" in usage:
                return int(usage["credits"])
            if "credits" in response:
                return int(response["credits"])
        return cls._estimate_credits(operation, depth)

    def _check_budget(self, operation: str, depth: str) -> None:
        """Warn near the cap, block (or warn) once a call would exceed it."""
        cap = self._budget.cap
        if cap is None:
            return
        used = self._credits_used()
        est = self._estimate_credits(operation, depth)
        projected = used + est

        if projected > cap:
            msg = (
                f"Tavily budget exceeded: {used} credits used + ~{est} for this "
                f"{operation} would reach {projected}, but the cap is {cap}."
            )
            if self._budget.block:
                raise TavilyBudgetExceeded(
                    msg + " Raise the cap, or pass block=False / --no-block to "
                    "warn instead of blocking."
                )
            log.warning("%s Proceeding (budget is in warn-only mode).", msg)
        elif projected >= self._budget.warn_threshold * cap:
            log.warning(
                "Tavily budget warning: %d/%d credits used (~%d more for this %s), "
                "past the %.0f%% threshold.",
                used,
                cap,
                est,
                operation,
                self._budget.warn_threshold * 100,
            )

    def usage(self) -> Dict[str, Any]:
        """Return a credit-usage summary for this cache DB."""
        used = self._credits_used()
        rows = self.query(
            "SELECT operation, COUNT(*) AS calls, COALESCE(SUM(credits), 0) AS credits "
            "FROM tavily_ledger GROUP BY operation"
        )
        cap = self._budget.cap
        return {
            "total_credits": used,
            "cap": cap,
            "remaining": (cap - used) if cap is not None else None,
            "by_operation": {
                r["operation"]: {"calls": r["calls"], "credits": r["credits"]}
                for r in rows
            },
        }

    # -- Fallback helpers ----------------------------------------------------

    def _get_web_client(self) -> WebClient:
        if self._web_client is None:
            self._web_client = WebClient()
        return self._web_client

    def _shape_ddg(self, query: str, results: List[Dict[str, str]]) -> Dict[str, Any]:
        """Render DuckDuckGo results in the same shape as a Tavily response."""
        return {
            "query": query,
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("snippet", ""),
                }
                for r in results
            ],
            "source": "duckduckgo",
        }


class TavilyClient(_TavilyBase):
    """Synchronous Tavily wrapper.

    Example:
        client = TavilyClient(budget=BudgetConfig(cap=100))
        result = client.search("AMD ROCm latest version")
    """

    _SDK_CLASS = _SdkTavilyClient

    def search(
        self,
        query: str,
        *,
        search_depth: str = "basic",
        max_results: int = 5,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not self._configured:
            log.info(
                "Tavily connector not configured; using DuckDuckGo for query=%r",
                query,
            )
            results = self._get_web_client().search_duckduckgo(query, max_results)
            return self._shape_ddg(query, results)

        params = {"search_depth": search_depth, "max_results": max_results, **kwargs}
        key = self._cache_key("search", _normalize_query(query), params)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        self._check_budget("search", search_depth)
        response = self._sdk.search(
            query=query, search_depth=search_depth, max_results=max_results, **kwargs
        )
        self._record(
            key,
            "search",
            response,
            self._actual_credits("search", search_depth, response),
        )
        return response

    def extract(
        self,
        urls: Union[str, Sequence[str]],
        *,
        extract_depth: str = "basic",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        urls = [urls] if isinstance(urls, str) else list(urls)
        if not self._configured:
            raise TavilyConfigError(
                "extract requires the Tavily connector (the DuckDuckGo fallback "
                "only covers search). Configure it with "
                "`gaia connectors configure mcp-tavily --set TAVILY_API_KEY=tvly-...`."
            )

        params = {"extract_depth": extract_depth, **kwargs}
        key = self._cache_key("extract", sorted(urls), params)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        self._check_budget("extract", extract_depth)
        response = self._sdk.extract(urls=urls, extract_depth=extract_depth, **kwargs)
        self._record(
            key,
            "extract",
            response,
            self._actual_credits("extract", extract_depth, response),
        )
        return response

    def crawl(
        self, url: str, *, extract_depth: str = "basic", **kwargs: Any
    ) -> Dict[str, Any]:
        if not self._configured:
            raise TavilyConfigError(
                "crawl requires the Tavily connector. Configure it with "
                "`gaia connectors configure mcp-tavily --set TAVILY_API_KEY=tvly-...`."
            )

        params = {"extract_depth": extract_depth, **kwargs}
        key = self._cache_key("crawl", url, params)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        self._check_budget("crawl", extract_depth)
        response = self._sdk.crawl(url=url, extract_depth=extract_depth, **kwargs)
        self._record(
            key,
            "crawl",
            response,
            self._actual_credits("crawl", extract_depth, response),
        )
        return response

    def __enter__(self) -> "TavilyClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class AsyncTavilyClient(_TavilyBase):
    """Asynchronous Tavily wrapper for concurrent multi-query research.

    Mirrors :class:`TavilyClient` but awaits the SDK; the synchronous
    DuckDuckGo fallback is offloaded with ``asyncio.to_thread`` so it doesn't
    block the event loop.
    """

    _SDK_CLASS = _SdkAsyncTavilyClient

    def _resolve_key_eagerly(self) -> None:
        # Defer: synchronous key resolution (get_credential_sync) raises inside a
        # running event loop. Resolve lazily in _ensure_resolved() via await.
        self._resolve_lock = asyncio.Lock()

    async def _ensure_resolved(self) -> None:
        """Resolve the API key on first use (idempotent, concurrency-safe)."""
        if self._key_resolved:
            return
        async with self._resolve_lock:
            if self._key_resolved:
                return
            key = (
                self._explicit_api_key
                if self._explicit_api_key is not None
                else await _load_api_key_async()
            )
            self._apply_key(key)

    async def search(
        self,
        query: str,
        *,
        search_depth: str = "basic",
        max_results: int = 5,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        await self._ensure_resolved()
        if not self._configured:
            log.info(
                "Tavily connector not configured; using DuckDuckGo for query=%r",
                query,
            )
            results = await asyncio.to_thread(
                self._get_web_client().search_duckduckgo, query, max_results
            )
            return self._shape_ddg(query, results)

        params = {"search_depth": search_depth, "max_results": max_results, **kwargs}
        key = self._cache_key("search", _normalize_query(query), params)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        self._check_budget("search", search_depth)
        response = await self._sdk.search(
            query=query, search_depth=search_depth, max_results=max_results, **kwargs
        )
        self._record(
            key,
            "search",
            response,
            self._actual_credits("search", search_depth, response),
        )
        return response

    async def extract(
        self,
        urls: Union[str, Sequence[str]],
        *,
        extract_depth: str = "basic",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        await self._ensure_resolved()
        urls = [urls] if isinstance(urls, str) else list(urls)
        if not self._configured:
            raise TavilyConfigError(
                "extract requires the Tavily connector (the DuckDuckGo fallback "
                "only covers search). Configure it with "
                "`gaia connectors configure mcp-tavily --set TAVILY_API_KEY=tvly-...`."
            )

        params = {"extract_depth": extract_depth, **kwargs}
        key = self._cache_key("extract", sorted(urls), params)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        self._check_budget("extract", extract_depth)
        response = await self._sdk.extract(
            urls=urls, extract_depth=extract_depth, **kwargs
        )
        self._record(
            key,
            "extract",
            response,
            self._actual_credits("extract", extract_depth, response),
        )
        return response

    async def aclose(self) -> None:
        """Close the async SDK client, then the cache DB and web client.

        The real ``AsyncTavilyClient`` wraps an ``httpx.AsyncClient`` that must
        be awaited shut; without this, long-lived async callers leak connections.
        """
        if self._sdk is not None and hasattr(self._sdk, "aclose"):
            await self._sdk.aclose()
        self.close()

    async def __aenter__(self) -> "AsyncTavilyClient":
        await self._ensure_resolved()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
