# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Memory Dashboard REST API for GAIA Agent UI."""

import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory"])

# Valid categories for knowledge entries (mirrors MemoryMixin remember tool)
_VALID_CATEGORIES = {"fact", "preference", "error", "skill", "note", "reminder"}


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

_store = None
_store_lock = threading.Lock()


def _get_store():
    """Lazy-init a MemoryStore instance for dashboard queries (thread-safe)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                from gaia.agents.base.memory_store import MemoryStore

                _store = MemoryStore()
    return _store


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/api/memory/stats")
def memory_stats() -> Dict:
    """Aggregate stats for dashboard header cards."""
    return _get_store().get_stats()


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
    search: Optional[str] = Query(None, max_length=500),
    sort_by: str = Query(
        "updated_at",
        pattern="^(updated_at|confidence|created_at|category|context|content|use_count)$",
    ),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Dict:
    """Paginated, filterable, searchable knowledge entries."""
    return _get_store().get_all_knowledge(
        category=category,
        context=context,
        entity=entity,
        sensitive=sensitive,
        search=search,
        sort_by=sort_by,
        order=order,
        offset=offset,
        limit=limit,
    )


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
