// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Types shared across the GAIA Chat UI frontend. */

export interface Session {
    id: string;
    title: string;
    created_at: string;
    updated_at: string;
    model: string;
    system_prompt: string | null;
    message_count: number;
    document_ids: string[];
}

export interface Message {
    id: number;
    session_id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    created_at: string;
    rag_sources: SourceInfo[] | null;
}

export interface SourceInfo {
    document_id: string;
    filename: string;
    chunk: string;
    score: number;
    page: number | null;
}

export interface Document {
    id: string;
    filename: string;
    filepath: string;
    file_size: number;
    chunk_count: number;
    indexed_at: string;
    last_accessed_at: string | null;
    sessions_using: number;
}

export interface SystemStatus {
    lemonade_running: boolean;
    model_loaded: string | null;
    embedding_model_loaded: boolean;
    disk_space_gb: number;
    memory_available_gb: number;
    initialized: boolean;
    version: string;
}

export interface StreamEvent {
    type: 'chunk' | 'done' | 'error';
    content?: string;
    message_id?: number;
}
