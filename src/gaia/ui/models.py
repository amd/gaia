# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pydantic models for GAIA Agent UI API."""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

try:
    from gaia.version import __version__ as _gaia_version
except ImportError:
    _gaia_version = "0.1.0"

# ── System ──────────────────────────────────────────────────────────────────


class SystemStatus(BaseModel):
    """System readiness status."""

    lemonade_running: bool = False
    model_loaded: Optional[str] = None
    embedding_model_loaded: bool = False
    disk_space_gb: float = 0.0
    memory_available_gb: float = 0.0
    initialized: bool = False
    version: str = _gaia_version


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
    message: str = Field(..., max_length=100_000)
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


class CommandOutputResponse(BaseModel):
    """Structured output from a shell command execution."""

    command: str = ""
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    cwd: Optional[str] = None
    duration_seconds: Optional[float] = None
    truncated: bool = False


class AgentStepResponse(BaseModel):
    """A single step in the agent's execution (persisted)."""

    id: int
    type: str  # 'thinking' | 'tool' | 'plan' | 'status' | 'error'
    label: str
    detail: Optional[str] = None
    tool: Optional[str] = None
    result: Optional[str] = None
    success: Optional[bool] = None
    active: bool = False
    planSteps: Optional[List[str]] = None
    timestamp: int = 0
    commandOutput: Optional[CommandOutputResponse] = None


class MessageResponse(BaseModel):
    """A single message."""

    id: int
    session_id: str
    role: str
    content: str
    created_at: str
    rag_sources: Optional[List[SourceInfo]] = None
    agent_steps: Optional[List[AgentStepResponse]] = None


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
    indexing_status: str = (
        "complete"  # pending | indexing | complete | failed | cancelled
    )


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


# ── File Browsing ──────────────────────────────────────────────────────────


class FileEntry(BaseModel):
    """A single file or folder entry in a directory listing."""

    name: str
    path: str
    type: str = Field(..., description="Either 'file' or 'folder'")
    size: int = 0
    extension: Optional[str] = None
    modified: Optional[str] = None


class QuickLink(BaseModel):
    """A quick-access link to a common filesystem location."""

    name: str
    path: str
    icon: str = "folder"


class BrowseResponse(BaseModel):
    """Response from the file/folder browse endpoint."""

    current_path: str
    parent_path: Optional[str] = None
    entries: List[FileEntry]
    quick_links: List[QuickLink] = Field(default_factory=list)


# ── Folder Indexing ────────────────────────────────────────────────────────


class IndexFolderRequest(BaseModel):
    """Request to index all supported documents in a folder."""

    folder_path: str
    recursive: bool = True


class IndexFolderResponse(BaseModel):
    """Response from folder indexing operation."""

    indexed: int = 0
    failed: int = 0
    documents: List[DocumentResponse] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# ── File Search & Preview ─────────────────────────────────────────────


class FileSearchRequest(BaseModel):
    """Request to search for files across the filesystem."""

    query: str = Field(..., description="Search pattern (file name or keywords)")
    file_types: Optional[str] = Field(
        None, description="Comma-separated extensions to filter (e.g., 'csv,xlsx,pdf')"
    )
    locations: Optional[List[str]] = Field(
        None, description="Specific directories to search in"
    )
    max_results: int = Field(default=20, ge=1, le=100)


class FileSearchResult(BaseModel):
    """A single file search result."""

    name: str
    path: str
    size: int
    size_display: str
    extension: str
    modified: str
    directory: str


class FileSearchResponse(BaseModel):
    """Response from file search."""

    results: List[FileSearchResult]
    total: int
    query: str
    searched_locations: List[str] = Field(default_factory=list)


class FilePreviewResponse(BaseModel):
    """Response with file content preview."""

    path: str
    name: str
    size: int
    size_display: str
    extension: str
    modified: str
    is_text: bool
    preview_lines: List[str] = Field(default_factory=list)
    total_lines: Optional[int] = None
    columns: Optional[List[str]] = None
    row_count: Optional[int] = None
    encoding: Optional[str] = None
