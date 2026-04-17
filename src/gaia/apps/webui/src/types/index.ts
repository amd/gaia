// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Types shared across the GAIA Agent UI frontend. */

export interface Session {
    id: string;
    title: string;
    created_at: string;
    updated_at: string;
    model: string;
    system_prompt: string | null;
    message_count: number;
    document_ids: string[];
    agent_type?: string;
}

export interface AgentInfo {
    id: string;
    name: string;
    description: string;
    source: string;
    conversation_starters: string[];
    models: string[];
}

export interface InferenceStats {
    tokens_per_second: number;
    time_to_first_token: number;
    input_tokens: number;
    output_tokens: number;
}

export interface Message {
    id: number;
    session_id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    created_at: string;
    rag_sources: SourceInfo[] | null;
    /** Agent activity that occurred while generating this message. */
    agentSteps?: AgentStep[];
    /** Inference performance stats from the LLM backend. */
    stats?: InferenceStats;
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
    indexing_status?: 'pending' | 'indexing' | 'complete' | 'failed' | 'cancelled' | 'missing';
}

/** A file attached to a message before sending. */
export interface Attachment {
    id: string;
    file: File;
    name: string;
    url: string;       // Object URL for preview, replaced with server URL after upload
    uploading: boolean;
    uploaded: boolean;
    serverUrl?: string; // Server URL after upload completes
    isImage: boolean;
    error?: string;
}

export interface ModelStatus {
    found: boolean;
    downloaded: boolean;
    loaded: boolean;
}

export interface Settings {
    custom_model: string | null;
    model_status: ModelStatus | null;
}

export interface SystemStatus {
    lemonade_running: boolean;
    model_loaded: string | null;
    embedding_model_loaded: boolean;
    disk_space_gb: number;
    memory_available_gb: number;
    initialized: boolean;
    version: string;
    // Extended Lemonade info
    lemonade_version: string | null;
    model_size_gb: number | null;
    model_device: string | null;
    model_context_size: number | null;
    model_labels: string[] | null;
    gpu_name: string | null;
    gpu_vram_gb: number | null;
    tokens_per_second: number | null;
    time_to_first_token: number | null;
    // Device compatibility check
    processor_name: string | null;
    device_supported: boolean;
    // LLM configuration health
    context_size_sufficient: boolean;
    model_downloaded: boolean | null;
    default_model_name: string | null;
    lemonade_url: string | null;
    expected_model_loaded: boolean;
    // Boot-time initialization tracking
    init_state?: 'initializing' | 'ready' | 'degraded';
    init_tasks?: Array<{ name: string; status: string }>;
}

// ── File Browser Types ───────────────────────────────────────────────────

/** A single file or folder entry returned by the browse endpoint. */
export interface FileEntry {
    name: string;
    path: string;
    type: 'file' | 'folder';
    size: number;
    extension: string;
    modified: string;
}

/** A quick-access link (Desktop, Documents, Downloads, etc.). */
export interface QuickLink {
    name: string;
    path: string;
    icon: string;
}

/** Response from the /files/browse endpoint. */
export interface BrowseResponse {
    current_path: string;
    parent_path: string | null;
    entries: FileEntry[];
    quick_links: QuickLink[];
}

/** Response from the /documents/index-folder endpoint. */
export interface IndexFolderResponse {
    indexed: number;
    failed: number;
    documents: Document[];
    errors: string[];
}

// ── MCP Server Types ──────────────────────────────────────────────────────

export interface MCPServerInfo {
    name: string;
    command: string;
    args: string[];
    env: Record<string, string>;
    enabled: boolean;
}

export interface MCPCatalogEntry {
    name: string;
    display_name: string;
    description: string;
    category: string;
    tier: number;
    command: string;
    args: string[];
    env: Record<string, string>;
    requires_config: string[];
}

export interface MCPServerStatus {
    name: string;
    connected: boolean;
    tool_count: number;
    error: string | null;
}

// ── Mobile Access / Tunnel Types ─────────────────────────────────────────

/** Status of the ngrok tunnel for mobile access. */
export interface TunnelStatus {
    active: boolean;
    url: string | null;
    token: string | null;
    startedAt: string | null;
    error: string | null;
    publicIp: string | null;
}

// ── Agent Activity Types ──────────────────────────────────────────────────

/** Structured command output for shell command results. */
export interface CommandOutput {
    command: string;
    stdout: string;
    stderr: string;
    returnCode: number;
    cwd?: string;
    durationSeconds?: number;
    truncated?: boolean;
}

/** A single retrieval chunk from RAG document search. */
export interface RetrievalChunk {
    id: number;
    source?: string;
    sourcePath?: string;
    page?: number | null;
    score?: number | null;
    preview: string;
    content: string;
}

/** A single step in the agent's execution. */
export interface AgentStep {
    id: number;
    type: 'thinking' | 'tool' | 'plan' | 'status' | 'error';
    /** Short label shown in collapsed view. */
    label: string;
    /** Detailed content shown when expanded. */
    detail?: string;
    /** Tool name (for type='tool'). */
    tool?: string;
    /** Tool result summary (for type='tool'). */
    result?: string;
    /** Whether this step completed successfully. */
    success?: boolean;
    /** Whether this step is currently running. */
    active?: boolean;
    /** Plan steps (for type='plan'). */
    planSteps?: string[];
    /** Timestamp when this step started. */
    timestamp: number;
    /** Structured command output (for run_shell_command). */
    commandOutput?: CommandOutput;
    /** Retrieved document chunks (for RAG query tools). */
    retrievalChunks?: RetrievalChunk[];
    /** File list from file search tools. */
    fileList?: {
        files: Array<Record<string, unknown>>;
        total: number;
    };
    /** MCP server name (for MCP tools). */
    mcpServer?: string;
    /** Tool call latency in milliseconds. */
    latencyMs?: number;
}

/** Extended SSE event types for agent communication. */
export type StreamEventType =
    | 'chunk'        // Text content chunk
    | 'done'         // Stream complete
    | 'error'        // Error
    | 'status'       // Agent state change
    | 'step'         // Step progress
    | 'thinking'     // Agent reasoning
    | 'plan'         // Agent plan
    | 'tool_start'   // Tool execution started
    | 'tool_end'     // Tool execution completed
    | 'tool_result'  // Tool result summary
    | 'tool_args'    // Tool arguments detail
    | 'tool_confirm' // Tool requires user confirmation (blocking)
    | 'answer'       // Final answer from agent
    | 'agent_error'  // Agent-level error (non-fatal)
    | 'permission_request' // Tool confirmation request
    | 'mcp_status'   // MCP server connection status update
    | 'agent_created'; // New agent created — triggers agent list refresh

export interface StreamEvent {
    type: StreamEventType;
    content?: string;
    message_id?: number;
    // Agent-specific fields
    status?: string;
    message?: string;
    step?: number;
    total?: number;
    tool?: string;
    summary?: string;
    success?: boolean;
    steps?: string[];
    current_step?: number;
    title?: string;
    detail?: string;
    args?: Record<string, unknown>;
    model?: string;
    elapsed?: number;
    tools_used?: number;
    /** Inference stats from the LLM backend (attached to done events). */
    stats?: InferenceStats;
    /** MCP server statuses (for mcp_status events). */
    servers?: MCPServerStatus[];
    /** Structured command output (for tool_result of run_shell_command). */
    command_output?: {
        command: string;
        stdout: string;
        stderr: string;
        return_code: number;
        cwd?: string;
        duration_seconds?: number;
        truncated?: boolean;
    };
    /** Agent ID of the newly created agent (for agent_created events). */
    agent_id?: string;
    /** Confirmation ID (for tool_confirm events). */
    confirm_id?: string;
    /** Timeout in seconds (for tool_confirm events). */
    timeout_seconds?: number;
    /** MCP server name (for tool_start of MCP tools). */
    mcp_server?: string;
    /** Tool call latency in milliseconds (for tool_result). */
    latency_ms?: number;
    /** Structured result data (for tool_result with search results, file lists, etc.). */
    result_data?: {
        type: string;
        count?: number;
        source_files?: string[];
        chunks?: Array<{
            id: number;
            source?: string;
            sourcePath?: string;
            page?: number | null;
            score?: number | null;
            preview: string;
            content: string;
        }>;
        files?: Array<Record<string, unknown>>;
        total?: number;
    };
}
