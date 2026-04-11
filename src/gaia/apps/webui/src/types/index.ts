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
    | 'mcp_status';  // MCP server connection status update

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
    /** Confirmation ID (for tool_confirm events). */
    confirm_id?: string;
    /** Timeout in seconds (for tool_confirm events). */
    timeout_seconds?: number;
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

// ── Pipeline Template Types ──────────────────────────────────────────────

export interface RoutingRule {
    condition: string;
    route_to: string;
    priority: number;
    loop_back: boolean;
    guidance?: string;
}

export interface PipelineTemplate {
    name: string;
    description: string;
    quality_threshold: number;
    max_iterations: number;
    agent_categories: Record<string, string[]>;
    routing_rules: RoutingRule[];
    quality_weights: Record<string, number>;
}

export interface TemplateListResponse {
    templates: PipelineTemplate[];
    total: number;
}

export interface TemplateValidateResponse {
    valid: boolean;
    errors: string[];
    warnings: string[];
}

export interface TemplateCreateRequest {
    name: string;
    description?: string;
    quality_threshold?: number;
    max_iterations?: number;
    agent_categories?: Record<string, string[]>;
    routing_rules?: RoutingRule[];
    quality_weights?: Record<string, number>;
}

export interface TemplateUpdateRequest {
    description?: string;
    quality_threshold?: number;
    max_iterations?: number;
    agent_categories?: Record<string, string[]>;
    routing_rules?: RoutingRule[];
    quality_weights?: Record<string, number>;
}

// ── Pipeline Metrics Types ───────────────────────────────────────────────

export interface PhaseTiming {
    phase_name: string;
    started_at?: string;
    ended_at?: string;
    duration_seconds: number;
    token_count: number;
    ttft?: number;
    tps: number;
}

export interface LoopMetrics {
    loop_id: string;
    phase_name: string;
    iteration_count: number;
    quality_scores: number[];
    average_quality?: number;
    max_quality?: number;
    defects_by_type: Record<string, number>;
    started_at?: string;
    ended_at?: string;
}

export interface StateTransition {
    from_state: string;
    to_state: string;
    timestamp: string;
    reason: string;
    metadata: Record<string, unknown>;
}

export interface AgentSelection {
    phase: string;
    agent_id: string;
    reason: string;
    alternatives: string[];
    timestamp: string;
}

export interface PipelineMetricsSnapshot {
    pipeline_id: string;
    phase_timings: Record<string, PhaseTiming>;
    loop_metrics: Record<string, LoopMetrics>;
    state_transitions: StateTransition[];
    agent_selections: AgentSelection[];
    quality_scores: [string, string, number][];
    defects_by_type: Record<string, number>;
}

export interface PipelineMetricsSummary {
    pipeline_id: string;
    total_duration_seconds: number;
    total_tokens: number;
    avg_tps: number;
    avg_ttft: number;
    total_loops: number;
    total_iterations: number;
    total_defects: number;
    avg_quality_score: number;
    max_quality_score: number;
    min_quality_score: number;
}

export interface PipelineMetricsResponse {
    success: boolean;
    pipeline_id: string;
    summary: PipelineMetricsSummary;
    phase_breakdown: Record<string, PhaseTiming>;
    loop_metrics: Record<string, LoopMetrics>;
    state_transitions: StateTransition[];
    defects_by_type: Record<string, number>;
    agent_selections: AgentSelection[];
}

export interface MetricHistoryPoint {
    timestamp: string;
    loop_id: string;
    phase: string;
    metric_type: string;
    value: number;
    metadata: Record<string, unknown>;
}

export interface PipelineMetricsHistory {
    pipeline_id: string;
    metric_type?: string;
    start_time?: string;
    end_time?: string;
    total_points: number;
    history: MetricHistoryPoint[];
}

export interface AggregateMetricStatistics {
    metric_type: string;
    count: number;
    mean: number;
    median: number;
    std_dev: number;
    min_value: number;
    max_value: number;
    trend: string;
    percentiles: Record<string, number>;
}

export interface PipelineAggregateMetrics {
    success: boolean;
    total_pipelines: number;
    time_range: { start?: string; end?: string };
    metric_statistics: Record<string, AggregateMetricStatistics>;
    overall_health: number;
    recommendations: string[];
}

// ── Pipeline Execution Types ─────────────────────────────────────────────

/** Pipeline execution status */
export type PipelineStatus =
    | 'initial'
    | 'starting'
    | 'running'
    | 'completed'
    | 'failed'
    | 'blocked';

/** Pipeline stage identifier */
export type PipelineStage =
    | 'domain_analysis'
    | 'workflow_modeling'
    | 'loom_building'
    | 'gap_detection'
    | 'pipeline_execution';

/** Pipeline event delivered via SSE */
export interface PipelineEvent {
    type: StreamEventType;
    pipeline_id?: string;
    status?: string;
    message?: string;
    step?: number;
    total?: number;
    content?: string;
    tool?: string;
    summary?: string;
    result?: Record<string, unknown>;
    elapsed?: number;
}

/** Request to execute a pipeline from the Agent UI */
export interface PipelineRunRequest {
    session_id: string;
    task_description: string;
    template_name?: string;
    auto_spawn?: boolean;
    stream?: boolean;
}

/** Response from non-streaming pipeline execution */
export interface PipelineRunResponse {
    pipeline_id: string;
    status: string;
    message: string;
}

/** Track of a single pipeline execution in the UI */
export interface PipelineExecution {
    id: string;
    sessionId: string;
    taskDescription: string;
    status: PipelineStatus;
    currentStage?: PipelineStage;
    startTime: number;
    endTime?: number;
    events: PipelineEvent[];
    result?: Record<string, unknown>;
    error?: string;
}
