# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Memory Dashboard REST API for GAIA Agent UI."""

import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from ..database import ChatDatabase
from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory"])

# Single source of truth imported from the data layer so that all three
# validation sites (remember tool, update_memory tool, REST router) stay
# in sync automatically when categories are added or removed.
from gaia.agents.base.memory_store import VALID_CATEGORIES as _VALID_CATEGORIES

# Safe defaults returned when the data-layer lacks v2 methods.
# Defined once here so memory_stats() doesn't repeat the literals.
_DEFAULT_EMBEDDING_STATS: Dict = {
    "total_items": 0,
    "with_embedding": 0,
    "without_embedding": 0,
    "coverage_pct": 0.0,
}
_DEFAULT_RECONCILIATION_STATS: Dict = {
    "last_run": None,
    "pairs_checked": 0,
    "contradictions_found": 0,
}

# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


def _validate_iso8601(field_name: str, v: Optional[str]) -> Optional[str]:
    """Shared ISO 8601 validator for date/time string fields.

    Returns v unchanged on success; raises ValueError (→ HTTP 422) on failure.
    This prevents ValueError from datetime.fromisoformat() bubbling up through
    route handlers as an unhandled exception (→ HTTP 500).
    """
    if v is not None:
        try:
            datetime.fromisoformat(v)
        except ValueError:
            raise ValueError(
                f"{field_name} must be a valid ISO 8601 datetime string, got {v!r}"
            )
    return v


class KnowledgeCreate(BaseModel):
    content: str
    category: str = "fact"
    domain: Optional[str] = None
    context: str = "global"
    entity: Optional[str] = None
    sensitive: bool = False
    due_at: Optional[str] = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be empty or whitespace-only")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in _VALID_CATEGORIES:
            raise ValueError(
                f"category must be one of {sorted(_VALID_CATEGORIES)}, got {v!r}"
            )
        return v

    @field_validator("due_at")
    @classmethod
    def validate_due_at(cls, v: Optional[str]) -> Optional[str]:
        return _validate_iso8601("due_at", v)


class KnowledgeUpdate(BaseModel):
    content: Optional[str] = None
    category: Optional[str] = None
    domain: Optional[str] = None
    context: Optional[str] = None
    entity: Optional[str] = None
    sensitive: Optional[bool] = None
    due_at: Optional[str] = None
    reminded_at: Optional[str] = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("content must not be empty or whitespace-only")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_CATEGORIES:
            raise ValueError(
                f"category must be one of {sorted(_VALID_CATEGORIES)}, got {v!r}"
            )
        return v

    @field_validator("due_at")
    @classmethod
    def validate_due_at(cls, v: Optional[str]) -> Optional[str]:
        return _validate_iso8601("due_at", v)

    @field_validator("reminded_at")
    @classmethod
    def validate_reminded_at(cls, v: Optional[str]) -> Optional[str]:
        return _validate_iso8601("reminded_at", v)


# ---------------------------------------------------------------------------
# MemoryStore access
# ---------------------------------------------------------------------------

# Module-level singleton — double-checked locking pattern for thread safety.
# NOTE: In test suites, call close_store() in teardown fixtures so the
# singleton is reset between tests and each test gets a fresh DB connection.
# Future improvement: accept MemoryStore via FastAPI Depends() for full
# dependency injection and easier per-test isolation.
_store = None
_store_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Agent-provided operation callbacks
# ---------------------------------------------------------------------------
# Operations that require a live LLM + FAISS (consolidation, reconciliation)
# cannot run from the standalone MemoryStore.  When a ChatAgent with
# MemoryMixin is active it registers its methods here so the dashboard can
# trigger them.  Set to None when no agent is running (→ 503).
#
# Set from gaia.ui._chat_helpers when a ChatAgent is created.
_consolidate_fn = None  # (max_sessions: int) -> Dict
_reconcile_fn = None  # (max_pairs: int) -> Dict


def _get_store():
    """Lazy-init a MemoryStore instance for dashboard queries (thread-safe)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from gaia.agents.base.memory_store import MemoryStore

                _store = MemoryStore()
    return _store


def close_store() -> None:
    """Close the singleton MemoryStore connection.

    Called from the FastAPI lifespan shutdown hook to checkpoint the WAL
    and release the SQLite file handle cleanly before process exit.
    """
    global _store
    with _store_lock:
        if _store is not None:
            try:
                _store.close()
            except Exception:
                pass
            finally:
                _store = None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/api/memory/stats")
def memory_stats() -> Dict:
    """Aggregate stats for dashboard header cards.

    Includes v2 embedding coverage and reconciliation stats when the
    data layer supports them.
    """
    store = _get_store()
    stats = store.get_stats()

    # v2: embedding coverage stats
    # AttributeError → method not on store yet; Exception → graceful degrade
    # but log at warning so real bugs aren't swallowed silently.
    try:
        stats["embedding"] = store.get_embedding_coverage()
    except AttributeError:
        logger.debug("[memory router] get_embedding_coverage not available yet")
        stats["embedding"] = _DEFAULT_EMBEDDING_STATS.copy()
    except Exception as exc:
        logger.warning("[memory router] embedding coverage failed: %s", exc)
        stats["embedding"] = _DEFAULT_EMBEDDING_STATS.copy()

    # v2: reconciliation stats
    try:
        stats["reconciliation"] = store.get_reconciliation_stats()
    except AttributeError:
        logger.debug("[memory router] get_reconciliation_stats not available yet")
        stats["reconciliation"] = _DEFAULT_RECONCILIATION_STATS.copy()
    except Exception as exc:
        logger.warning("[memory router] reconciliation stats failed: %s", exc)
        stats["reconciliation"] = _DEFAULT_RECONCILIATION_STATS.copy()

    return stats


@router.get("/api/memory/activity")
def memory_activity(days: int = Query(30, ge=1, le=365)) -> List[Dict]:
    """Daily activity timeline for the activity chart."""
    return _get_store().get_activity_timeline(days=days)


# ---------------------------------------------------------------------------
# Knowledge Browser
# ---------------------------------------------------------------------------


@router.get("/api/memory/knowledge")
def list_knowledge(
    category: Optional[str] = None,
    context: Optional[str] = None,
    entity: Optional[str] = None,
    sensitive: Optional[bool] = None,
    include_sensitive: bool = Query(
        False,
        description="Include sensitive items in results. Defaults to False.",
    ),
    include_superseded: bool = Query(
        False,
        description="Include items superseded by newer knowledge. "
        "By default, superseded items are hidden.",
    ),
    time_from: Optional[str] = Query(
        None,
        description="ISO 8601 lower bound for created_at (inclusive).",
    ),
    time_to: Optional[str] = Query(
        None,
        description="ISO 8601 upper bound for created_at (inclusive).",
    ),
    search: Optional[str] = Query(None, max_length=500),
    sort_by: str = Query(
        "updated_at",
        pattern="^(updated_at|confidence|created_at|category|context|content|use_count)$",
    ),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Dict:
    """Paginated, filterable, searchable knowledge entries.

    Sensitive items are excluded by default. Pass include_sensitive=true
    to include them, or sensitive=true to show only sensitive items.

    Superseded items (replaced by newer knowledge) are hidden by default.
    Pass include_superseded=true to include them.

    Use time_from / time_to for temporal range filtering on created_at.
    """
    # Validate ISO 8601 time boundaries (if provided).
    # _validate_iso8601 raises ValueError which Pydantic converts to 422 in
    # model validators.  In route handlers we must catch it ourselves.
    try:
        if time_from is not None:
            _validate_iso8601("time_from", time_from)
        if time_to is not None:
            _validate_iso8601("time_to", time_to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Exclude sensitive items unless the caller explicitly opts in.
    # sensitive=True overrides to show only sensitive items.
    # sensitive=False keeps the non-sensitive-only filter.
    # sensitive=None + include_sensitive=False → exclude sensitive (safe default).
    # sensitive=None + include_sensitive=True  → no filter (show all).
    effective_sensitive = sensitive
    if sensitive is None and not include_sensitive:
        effective_sensitive = False

    # Build kwargs for the store.  v2 params are added incrementally so
    # the router degrades gracefully across MemoryStore versions:
    #   - v2 full:  include_superseded + time_from + time_to
    #   - v2 partial:  include_superseded only (time filters not yet in store)
    #   - v1:  base params only
    base_kwargs = dict(
        category=category,
        context=context,
        entity=entity,
        sensitive=effective_sensitive,
        search=search,
        sort_by=sort_by,
        order=order,
        offset=offset,
        limit=limit,
    )

    # Only add non-None time boundaries so we don't trigger TypeError
    # on stores that lack time_from/time_to params.
    v2_kwargs: Dict = {"include_superseded": include_superseded}
    if time_from is not None:
        v2_kwargs["time_from"] = time_from
    if time_to is not None:
        v2_kwargs["time_to"] = time_to

    store = _get_store()
    try:
        return store.get_all_knowledge(**base_kwargs, **v2_kwargs)
    except TypeError:
        # Store may not support time_from/time_to yet — retry with
        # just include_superseded (which the v2 store does support).
        if "time_from" in v2_kwargs or "time_to" in v2_kwargs:
            try:
                return store.get_all_knowledge(
                    **base_kwargs, include_superseded=include_superseded
                )
            except TypeError:
                pass  # fall through to full v1 fallback
        # Full v1 fallback — store supports none of the v2 params
        logger.debug(
            "[memory router] get_all_knowledge does not accept v2 params, "
            "falling back to base query"
        )
        return store.get_all_knowledge(**base_kwargs)


@router.post("/api/memory/knowledge")
def create_knowledge(body: KnowledgeCreate) -> Dict:
    """Create a knowledge entry from the dashboard."""
    store = _get_store()
    knowledge_id = store.store(
        category=body.category,
        content=body.content,
        domain=body.domain,
        context=body.context,
        entity=body.entity,
        sensitive=body.sensitive,
        due_at=body.due_at,
        source="user",
        confidence=0.8,
    )
    return {"status": "created", "knowledge_id": knowledge_id}


@router.put("/api/memory/knowledge/{knowledge_id}")
def edit_knowledge(knowledge_id: str, body: KnowledgeUpdate) -> Dict:
    """Edit a knowledge entry from the dashboard."""
    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not kwargs:
        raise HTTPException(400, "No fields to update")
    success = _get_store().update(knowledge_id, **kwargs)
    if not success:
        raise HTTPException(404, f"Knowledge entry {knowledge_id} not found")
    return {"status": "updated", "knowledge_id": knowledge_id}


@router.delete("/api/memory/knowledge/{knowledge_id}")
def delete_knowledge(knowledge_id: str) -> Dict:
    """Delete a knowledge entry from the dashboard."""
    success = _get_store().delete(knowledge_id)
    if not success:
        raise HTTPException(404, f"Knowledge entry {knowledge_id} not found")
    return {"status": "deleted", "knowledge_id": knowledge_id}


# ---------------------------------------------------------------------------
# Entities & Contexts
# ---------------------------------------------------------------------------


@router.get("/api/memory/entities")
def list_entities(limit: int = Query(100, ge=1, le=500)) -> List[Dict]:
    """List all unique entities with their knowledge counts."""
    return _get_store().get_entities(limit=limit)


@router.get("/api/memory/entities/{entity}")
def get_entity(entity: str, limit: int = Query(20, ge=1, le=200)) -> List[Dict]:
    """Get all knowledge linked to a specific entity."""
    return _get_store().get_by_entity(entity, limit=limit)


@router.get("/api/memory/contexts")
def list_contexts(limit: int = Query(100, ge=1, le=500)) -> List[Dict]:
    """List all contexts with their knowledge counts."""
    return _get_store().get_contexts(limit=limit)


# ---------------------------------------------------------------------------
# Tool Performance
# ---------------------------------------------------------------------------


@router.get("/api/memory/tools")
def tool_summary() -> List[Dict]:
    """Per-tool performance stats."""
    return _get_store().get_tool_summary()


@router.get("/api/memory/tools/{tool_name}/history")
def tool_history(tool_name: str, limit: int = Query(50, ge=1, le=200)) -> List[Dict]:
    """Recent call history for a specific tool."""
    return _get_store().get_tool_history(tool_name, limit=limit)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@router.get("/api/memory/errors")
def recent_errors(limit: int = Query(20, ge=1, le=100)) -> List[Dict]:
    """Recent tool errors across all tools."""
    return _get_store().get_recent_errors(limit=limit)


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@router.get("/api/memory/conversations")
def list_sessions(limit: int = Query(20, ge=1, le=100)) -> List[Dict]:
    """List conversation sessions with timestamps and turn counts."""
    return _get_store().get_sessions(limit=limit)


@router.get("/api/memory/conversations/search")
def search_conversations(
    query: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(20, ge=1, le=100),
) -> List[Dict]:
    """Full-text search across all conversations."""
    return _get_store().search_conversations(query, limit=limit)


@router.get("/api/memory/conversations/{session_id}")
def get_session(
    session_id: str,
    limit: int = Query(200, ge=1, le=500),
) -> List[Dict]:
    """Get turns for a specific conversation session."""
    return _get_store().get_history(session_id=session_id, limit=limit)


# ---------------------------------------------------------------------------
# Temporal
# ---------------------------------------------------------------------------


@router.get("/api/memory/upcoming")
def upcoming_items(days: int = Query(7, ge=1, le=90)) -> List[Dict]:
    """Time-sensitive items due within N days + overdue."""
    return _get_store().get_upcoming(within_days=days)


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


@router.post("/api/memory/consolidate")
def trigger_consolidation(
    max_sessions: int = Query(5, ge=1, le=50),
) -> Dict:
    """Manually trigger conversation consolidation.

    Distils old conversation sessions into semantic knowledge entries.
    Returns ``{consolidated: int, extracted_items: int}``.

    Requires an active ChatAgent session (start a chat first).
    """
    if _consolidate_fn is None:
        raise HTTPException(
            503,
            "Consolidation requires an active agent session. "
            "Send a chat message first to initialize the agent.",
        )
    try:
        result = _consolidate_fn(max_sessions=max_sessions)
        return result
    except Exception as exc:
        logger.error("[memory router] consolidation failed: %s", exc)
        raise HTTPException(500, f"Consolidation failed: {type(exc).__name__}")


@router.post("/api/memory/rebuild-embeddings")
def rebuild_embeddings() -> Dict:
    """Trigger embedding backfill for items missing embeddings.

    Returns ``{backfilled: int, total_without: int}``.
    """
    try:
        import numpy as np

        from gaia.agents.base.memory import EMBEDDING_MODEL
        from gaia.llm.providers.lemonade import LemonadeProvider

        provider = LemonadeProvider(model=EMBEDDING_MODEL)

        def _embed_fn(text: str) -> bytes:
            results = provider.embed([text], model=EMBEDDING_MODEL)
            vec = np.array(results[0], dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            return vec.astype(np.float32).tobytes()

        result = _get_store().backfill_embeddings(_embed_fn)
        return result
    except Exception as exc:
        logger.error("[memory router] rebuild-embeddings failed: %s", exc)
        raise HTTPException(500, f"Embedding rebuild failed: {type(exc).__name__}: {exc}")


@router.post("/api/memory/reconcile")
def trigger_reconciliation() -> Dict:
    """Manually trigger background memory reconciliation.

    Compares knowledge entries for contradictions and reinforcements.
    Returns ``{pairs_checked, reinforced, contradicted, weakened, neutral}``.

    Requires an active ChatAgent session (start a chat first).
    """
    if _reconcile_fn is None:
        raise HTTPException(
            503,
            "Reconciliation requires an active agent session. "
            "Send a chat message first to initialize the agent.",
        )
    try:
        result = _reconcile_fn()
        return result
    except AttributeError:
        raise HTTPException(501, "Reconciliation not yet implemented in data layer")
    except Exception as exc:
        logger.error("[memory router] reconciliation failed: %s", exc)
        raise HTTPException(500, f"Reconciliation failed: {type(exc).__name__}")


@router.get("/api/memory/embedding-coverage")
def embedding_coverage() -> Dict:
    """Return embedding status for all knowledge items.

    Returns ``{total_items, with_embedding, without_embedding, coverage_pct}``.
    """
    try:
        return _get_store().get_embedding_coverage()
    except AttributeError:
        raise HTTPException(501, "Embedding coverage not yet implemented in data layer")
    except Exception as exc:
        logger.error("[memory router] embedding-coverage failed: %s", exc)
        raise HTTPException(
            500, f"Embedding coverage query failed: {type(exc).__name__}"
        )


@router.post("/api/memory/rebuild-fts")
def rebuild_fts() -> Dict:
    """Rebuild all FTS5 indexes from source tables.

    Call if search results seem wrong or incomplete.
    """
    try:
        _get_store().rebuild_fts()
        return {"status": "rebuilt"}
    except Exception as exc:
        logger.error("[memory router] rebuild_fts failed: %s", exc)
        raise HTTPException(500, f"FTS rebuild failed: {type(exc).__name__}")


@router.post("/api/memory/prune")
def prune_memory(days: int = Query(90, ge=7, le=365)) -> Dict:
    """Prune old tool history, conversations, and low-confidence knowledge.

    Keeps data from the last N days. Returns counts of deleted rows.
    """
    try:
        return _get_store().prune(days=days)
    except Exception as exc:
        logger.error("[memory router] prune failed: %s", exc)
        raise HTTPException(500, f"Prune failed: {type(exc).__name__}")


@router.delete("/api/memory/all")
def clear_all_memory() -> Dict:
    """Permanently delete all knowledge, tool history, and conversation data.

    This is irreversible.  Returns counts of deleted rows per table:
    ``{knowledge: int, tool_history: int, conversations: int}``.
    """
    try:
        return _get_store().clear_all()
    except Exception as exc:
        logger.error("[memory router] clear_all failed: %s", exc)
        raise HTTPException(500, f"Clear failed: {type(exc).__name__}")


# ---------------------------------------------------------------------------
# System Context Refresh
# ---------------------------------------------------------------------------


@router.post("/api/memory/refresh-system-context")
def refresh_system_context() -> Dict:
    """Re-collect OS, hardware, installed apps, and version facts.

    Deletes stale source='system' entries and stores fresh ones.
    No LLM required — runs in a few seconds.

    Returns ``{stored: int, skipped: bool}``.
    """
    try:
        from gaia.agents.base.memory import _system_context_is_enabled
        from gaia.agents.base.system_context import collect_system_info

        if not _system_context_is_enabled():
            return {"stored": 0, "skipped": True, "reason": "system_context_disabled"}

        store = _get_store()

        # Replace stale facts atomically
        store.delete_by_source("system")

        facts = collect_system_info()
        stored = 0
        for fact in facts:
            try:
                store.store(
                    category="system",
                    content=fact["content"],
                    domain=fact.get("domain"),
                    context="global",
                    confidence=1.0,
                    source="system",
                )
                stored += 1
            except Exception:
                pass

        logger.info("[memory router] refresh-system-context: stored %d facts", stored)
        return {"stored": stored, "skipped": False}

    except Exception as exc:
        logger.error("[memory router] refresh-system-context failed: %s", exc)
        raise HTTPException(500, f"System context refresh failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_MCP_MEMORY_ENABLED_KEY = "mcp_memory_enabled"
_MEMORY_ENABLED_KEY = "memory_enabled"


def _get_memory_settings_dict(db: ChatDatabase) -> Dict:
    """Read all memory settings from the DB and return as a dict."""
    return {
        "memory_enabled": db.get_setting(_MEMORY_ENABLED_KEY, "true") == "true",
        "mcp_memory_enabled": db.get_setting(_MCP_MEMORY_ENABLED_KEY, "false") == "true",
    }


@router.get("/api/memory/settings")
def get_memory_settings(db: ChatDatabase = Depends(get_db)) -> Dict:
    """Return memory-related feature settings.

    Keys:
    - ``memory_enabled`` (bool): global memory on/off. Default true.
    - ``mcp_memory_enabled`` (bool): expose read tools to MCP clients. Default false.
    """
    return _get_memory_settings_dict(db)


@router.put("/api/memory/settings")
def update_memory_settings(
    body: Dict,
    db: ChatDatabase = Depends(get_db),
) -> Dict:
    """Update memory-related feature settings.

    Supported keys:
    - ``memory_enabled`` (bool): globally enable/disable all memory storage.
      When false, no knowledge or conversation data is written during any
      chat session (equivalent to every session being private). Default true.
    - ``mcp_memory_enabled`` (bool): expose memory read tools to MCP clients
      for debug/troubleshooting. Default false.
    """
    if "memory_enabled" in body:
        db.set_setting(
            _MEMORY_ENABLED_KEY, "true" if body["memory_enabled"] else "false"
        )
    if "mcp_memory_enabled" in body:
        db.set_setting(
            _MCP_MEMORY_ENABLED_KEY, "true" if body["mcp_memory_enabled"] else "false"
        )
    return _get_memory_settings_dict(db)
