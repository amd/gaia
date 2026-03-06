# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pydantic models for GAIA Chat UI API."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

# ── System ──────────────────────────────────────────────────────────────────


class SystemStatus(BaseModel):
    """System readiness status."""

    lemonade_running: bool = False
    model_loaded: Optional[str] = None
    embedding_model_loaded: bool = False
    disk_space_gb: float = 0.0
    memory_available_gb: float = 0.0
    initialized: bool = False
    version: str = "0.1.0"


# ── Sessions ────────────────────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    """Request to create a new chat session."""

    title: Optional[str] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    document_ids: List[str] = Field(default_factory=list)


class UpdateSessionRequest(BaseModel):
    """Request to update a session."""

    title: Optional[str] = None
    system_prompt: Optional[str] = None


class SessionResponse(BaseModel):
    """A chat session."""

    id: str
    title: str
    created_at: str
    updated_at: str
    model: str
    system_prompt: Optional[str] = None
    message_count: int = 0
    document_ids: List[str] = Field(default_factory=list)


class SessionListResponse(BaseModel):
    """List of sessions."""

    sessions: List[SessionResponse]
    total: int


# ── Messages ────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    """Request to send a chat message."""

    session_id: str
    message: str
    document_ids: Optional[List[str]] = None
    stream: bool = True


class SourceInfo(BaseModel):
    """RAG source citation."""

    document_id: str
    filename: str
    chunk: str
    score: float
    page: Optional[int] = None


class ChatResponse(BaseModel):
    """Response from a chat message."""

    message_id: int
    content: str
    sources: List[SourceInfo] = Field(default_factory=list)
    tokens: Optional[Dict[str, int]] = None


class MessageResponse(BaseModel):
    """A single message."""

    id: int
    session_id: str
    role: str
    content: str
    created_at: str
    rag_sources: Optional[List[SourceInfo]] = None


class MessageListResponse(BaseModel):
    """List of messages for a session."""

    messages: List[MessageResponse]
    total: int


# ── Documents ───────────────────────────────────────────────────────────────


class DocumentResponse(BaseModel):
    """A document in the library."""

    id: str
    filename: str
    filepath: str
    file_size: int
    chunk_count: int
    indexed_at: str
    last_accessed_at: Optional[str] = None
    sessions_using: int = 0


class DocumentListResponse(BaseModel):
    """List of documents."""

    documents: List[DocumentResponse]
    total: int
    total_size_bytes: int
    total_chunks: int


class DocumentUploadRequest(BaseModel):
    """Request to index a document by path."""

    filepath: str


class AttachDocumentRequest(BaseModel):
    """Request to attach a document to a session."""

    document_id: str
