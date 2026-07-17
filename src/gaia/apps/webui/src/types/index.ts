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
    private?: boolean;
    agent_type?: string;
    /** Device used for this session (cpu / gpu / npu). */
    device?: string;
    /** Mail provider for email-triage sessions ("google" | "microsoft"). */
    mail_provider?: string;
}

/** Per-device configuration for an agent (CPU / GPU / NPU). */
export interface DeviceConfig {
    device: 'cpu' | 'gpu' | 'npu';
    model: string;
    recipe: string;
    backend: string;
    verified: boolean;
    ctx_size: number;
}

/** Selectable model size for an agent (issue #1162): "full" vs "lite" (~4B). */
export interface ModelTier {
    name: string;        // "full" | "lite"
    label: string;       // e.g. "Lite (~4B)"
    models: string[];    // preferred models; empty = agent's own default
    min_memory_gb?: number | null;
    default?: boolean;
}

export interface AgentInfo {
    id: string;
    name: string;
    description: string;
    source: string;
    conversation_starters: string[];
    models: string[];
    /** Minimum recommended free RAM in GB for this agent. Null = no declared requirement. */
    min_memory_gb?: number | null;
    /**
     * Connection requirements declared by the agent's REQUIRED_CONNECTORS
     * (issue #915). The Settings → Connections page renders these so the
     * user can grant scopes per agent.
     */
    required_connections?: ConnectorRequirement[];
    /**
     * True when the agent loads MCP servers dynamically at runtime (e.g. the
     * chat agent) and gates their tools through the activation ledger. The
     * Settings → Connectors "Active for" panel lists such agents as
     * activatable for `mcp_server` connectors even without a matching
     * `required_connections` entry.
     */
    consumes_mcp_servers?: boolean;
    /**
     * Opaque grant-ledger key. Built-ins are `builtin:<id>`, custom agents
     * are `custom:<sha256-prefix>:<id>`, installed agents are
     * `installed:<id>`, and native agents are `native:<id>`. Pass this to
     * the grants endpoint.
     */
    namespaced_agent_id?: string;
    /** Agent Hub metadata — used to render rich discovery cards. */
    category?: string;
    tags?: string[];
    icon?: string;
    tools_count?: number;
    language?: string;
    /** Per-device model/backend/recipe configurations declared by the agent. */
    device_configs?: DeviceConfig[];
    /**
     * Model-size tiers (issue #1162). Agents that support a "full" vs "lite"
     * (~4B) model selection declare both; the card renders a size selector
     * instead of shipping a separate "… Lite" card. Empty/undefined = single
     * model size.
     */
    model_tiers?: ModelTier[];

    // ── Agent Hub catalog/install fields (issue #1097) ──────────────────────
    // Populated by GET /api/agents/catalog (#1096). Locally-registered agents
    // returned by GET /api/agents leave these undefined; the Hub merges catalog
    // data in by id so installed cards can show versions and update badges.

    /**
     * Lifecycle status from the catalog. ``installed`` = present locally,
     * ``available`` = downloadable from the Hub, ``update_available`` = a newer
     * version exists than the installed one, ``installing`` = an install is in
     * flight. Undefined for local-only agents (treated as ``installed``).
     */
    status?: AgentCardState;
    /** Installed version (semver), when known. */
    version?: string;
    /** Latest version offered by the catalog — set when newer than ``version``. */
    latest_version?: string;
    /** Per-agent compatibility verdict from the backend's system check. */
    compatibility?: AgentCompatibility;
    /** Download size of the agent package in bytes (Available cards). */
    download_size_bytes?: number;
    /** Trust tier: AMD-verified, community-published, or experimental opt-in. */
    security_tier?: 'verified' | 'community' | 'experimental';
    /**
     * True when installing this agent needs an explicit native-trust opt-in —
     * a non-verified native (C++) package that runs unsandboxed. The Hub shows
     * a "Trust & Install" confirmation before sending ``trust_native``.
     */
    requires_trust?: boolean;
    /** Optional remote avatar image URL from the catalog. */
    avatar_url?: string;
    /** True when the publisher has deprecated this agent. */
    deprecated?: boolean;
    /** Public URL of the eval scorecard markdown; absent when none was published. */
    eval_scorecard_url?: string;
    /** Aggregate eval score (0–100) from the latest published scorecard; absent when none. */
    eval_score?: number;
}

/** Derived card state for the Agent Hub (issue #1097). */
export type AgentCardState =
    | 'installed'
    | 'available'
    | 'update_available'
    | 'installing';

/**
 * Per-agent compatibility verdict (issue #1096/#1097).
 *
 * ``level`` drives the green/yellow/red indicator: ``compatible`` (green),
 * ``warning`` (yellow — runnable but a requirement is marginal), and
 * ``incompatible`` (red — Install is disabled). ``reasons`` explains any
 * non-green verdict for the tooltip.
 */
export interface AgentCompatibility {
    level: 'compatible' | 'warning' | 'incompatible';
    reasons?: string[];
}

/**
 * Wire-level install state machine from the backend (issue #1096), distinct
 * from the derived card-state enum. Polled via GET /api/agents/{id}/install-status.
 */
export type AgentInstallState =
    | 'downloading'
    | 'verifying'
    | 'installing'
    | 'installed'
    | 'failed';

/** Install progress snapshot returned by the install-status endpoint. */
export interface InstallStatus {
    agent_id: string;
    state: AgentInstallState;
    /** 0–100 overall progress. */
    progress: number;
    /** Populated only when ``state === 'failed'``. */
    error?: string | null;
}

/**
 * Response from GET /api/agents/catalog (issue #1096).
 *
 * ``offline`` is ``true`` when the backend served a cached copy because R2 was
 * unreachable — the UI surfaces a "Showing cached catalog" banner rather than
 * silently presenting stale data as fresh (CLAUDE.md: no silent fallbacks).
 */
export interface AgentCatalogResponse {
    agents: AgentInfo[];
    offline: boolean;
    /** ISO timestamp the cached catalog was last refreshed (when offline). */
    cached_at?: string | null;
}

export interface DiskAgentInfo {
    id: string;
    name: string;
    registered: boolean;
    registered_agent_id?: string | null;
    source?: string | null;
}

/**
 * Issue #915 — declarative scope claim on an agent.
 */
export interface ConnectorRequirement {
    connector_id: string;
    scopes: string[];
    reason: string;
}

/**
 * Connector row returned by GET /api/connectors (new framework, T-8b).
 * Merges ConnectorSpec fields with live state.
 */
export interface ConnectorRow {
    id: string;
    display_name: string;
    icon: string | null;
    category: string;
    tier: string;
    type: 'oauth_pkce' | 'mcp_server' | string;
    description: string;
    product_url: string | null;
    /**
     * GAIA documentation URL — what the AgentUI's "Learn more" link
     * points at. Tells users where to obtain client credentials, API
     * tokens, and any other setup specifics. ``null`` means the
     * connector hasn't shipped a docs page yet; the UI falls back to
     * ``product_url`` in that case.
     */
    docs_url: string | null;
    configured: boolean;
    /**
     * ``false`` when the connector cannot be instantiated as configured —
     * for example, an ``oauth_pkce`` provider whose required environment
     * variables (``GAIA_GOOGLE_CLIENT_ID`` etc.) aren't set. The UI uses
     * this to disable the Connect button up-front instead of letting the
     * user click and see a raw 503 error inline.
     */
    configurable: boolean;
    /**
     * Human-readable explanation of why ``configurable`` is ``false``.
     * Populated only when ``configurable === false``; null otherwise.
     */
    config_error: string | null;
    /**
     * Whether the connector is currently enabled (#1004).
     *
     * Meaningful only for ``type === 'mcp_server'`` — when ``false``, the
     * connector retains its credentials and per-agent grants but is
     * suppressed from agent tool lists. The backend defaults this to
     * ``true`` for OAuth tiles and for not-yet-configured MCP tiles, so
     * the UI never renders a "Disabled" pill where the concept doesn't
     * apply.
     */
    enabled: boolean;
    account_id: string | null;
    scopes: string[];
    /**
     * Per-agent MCP-tool-visibility activation snapshot (issue #1005).
     * Keys are namespaced agent ids (``builtin:chat``,
     * ``custom:<hash>:<id>``, …), values are ``true`` when the agent is
     * explicitly activated. Absence means inactive — activations are
     * opt-in. Populated only for ``type === 'mcp_server'`` connectors;
     * OAuth connectors always return ``{}`` because activation writes
     * are rejected for them at the API layer.
     */
    activations: Record<string, boolean>;
    last_tested_at: string | null;
    mcp_env_keys: string[];
    default_scopes: string[];
    available_scopes: string[];
    /**
     * First-time setup fields the user fills in to provide OAuth-app
     * client credentials (e.g. Google Cloud Console client_id +
     * client_secret). When ``configurable`` is ``false`` and this list
     * is non-empty, the UI renders the form inline; submitting it
     * stores the credentials in the OS keyring and triggers the OAuth
     * browser flow. Empty for connectors that don't require user-side
     * provider credentials.
     */
    oauth_setup_fields: ConnectorConfigField[];
}

/**
 * One field in a connector's first-time setup form. Mirrors
 * ``gaia.connectors.spec.ConfigField`` on the backend.
 */
export interface ConnectorConfigField {
    key: string;
    label: string;
    kind: 'text' | 'secret' | 'url' | 'email' | 'select' | 'bool' | 'textarea';
    required: boolean;
    placeholder: string;
    help_md: string;
}

/**
 * One MCP server entry declared by a custom Python agent (#1020).
 * Read-only — controlled by the agent's local mcp_servers.json.
 */
export interface AgentMcpServer {
    agent_id: string;
    agent_name: string;
    config_path: string;
    server_name: string;
    command: string;
    args: string[];
    disabled: boolean;
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
    /** Structured cards emitted via tool_result.render during this turn
     *  (issue #2108). Rendered by RenderCard above the markdown content. */
    cards?: RenderCardData[];
}

/** One card instance transferred onto a finalized Message (issue #2108). */
export interface RenderCardData {
    render: string;
    data: unknown;
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
    last_error?: string | null;
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
    /** Persisted context window size override (tokens). null = use default 32768. */
    context_size: number | null;
    /** Beta dynamic tool loader (#1798). Effective value: GAIA_DYNAMIC_TOOLS wins when set, else the persisted setting. */
    dynamic_tools: boolean;
    /** True when GAIA_DYNAMIC_TOOLS locks the value — the toggle reflects the effective value and disables. */
    dynamic_tools_locked: boolean;
}

/** Status of the GAIA Agent UI MCP server (exposes UI tools to Claude Code etc.). */
export interface AgentMCPServerStatus {
    running: boolean;
    port: number;
    pid: number | null;
    url: string | null;
}

/**
 * Live download progress for the default model. Mirrors the backend's
 * ``DownloadProgress`` schema; populated from Lemonade's ``POST /v1/pull``
 * SSE stream and surfaced via ``GET /api/system/status``.
 */
export interface DownloadProgress {
    state: 'starting' | 'downloading' | 'complete' | 'error';
    model_name: string;
    /** Overall percent across the full multi-file download (0–100). */
    percent: number;
    /** Current file being fetched (null between files / on terminal events). */
    file: string | null;
    file_index: number;
    total_files: number;
    /** Cumulative bytes pulled across every file in this download. */
    downloaded_bytes: number;
    /** Sum of every file's ``bytes_total`` in this download. */
    total_bytes: number;
    /** Populated only when state === 'error'. */
    message: string | null;
}

export interface SystemStatus {
    lemonade_running: boolean;
    model_loaded: string | null;
    embedding_model_loaded: boolean;
    disk_space_gb: number;
    memory_available_gb: number | null;
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
    /**
     * Catalog-reported size of ``default_model_name`` (GB). Used by the
     * "model not downloaded" banner so the size hint stays in sync with
     * the actual default — replaces the previously hard-coded "~25 GB".
     */
    default_model_size_gb: number | null;
    lemonade_url: string | null;
    expected_model_loaded: boolean;
    /** Live progress while a model pull is in flight. ``null`` otherwise. */
    download_progress: DownloadProgress | null;
    // Boot-time initialization tracking
    init_state?: 'initializing' | 'ready' | 'degraded';
    init_tasks?: Array<{ name: string; status: string }>;
    /** Devices detected on this system (e.g. ['cpu', 'gpu', 'npu']). */
    detected_devices?: string[];
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

export interface MCPServerStatus {
    name: string;
    connected: boolean;
    tool_count: number;
    error: string | null;
}

// ── Scheduled Tasks ──────────────────────────────────────────────────────

/** A recurring scheduled task. */
export interface Schedule {
    id: string;
    name: string;
    interval_seconds: number;
    prompt: string;
    status: 'active' | 'paused' | 'cancelled';
    created_at: string | null;
    last_run_at: string | null;
    next_run_at: string | null;
    last_result: string | null;
    run_count: number;
    error_count: number;
    session_id: string | null;
}

/** A single execution result for a scheduled task. */
export interface ScheduleResult {
    id: string;
    task_id: string;
    executed_at: string;
    result: string | null;
    error: string | null;
}

/** Parsed result from natural language schedule input. */
export interface ParsedSchedule {
    interval_seconds: number;
    time_of_day: string | null;
    start_hour: number | null;
    end_hour: number | null;
    days_of_week: number[] | null;
    description: string;
    next_run_at: string | null;
    valid: boolean;
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
    type: 'thinking' | 'tool' | 'plan' | 'status' | 'error' | 'policy_alert' | 'needs_confirmation';
    /** Short label shown in collapsed view. */
    label: string;
    /** Detailed content shown when expanded. */
    detail?: string;
    /** Tool name (for type='tool'). */
    tool?: string;
    /** Governance decision (for type='policy_alert'). */
    decision?: string;
    /** Governance policy reason (for type='policy_alert'). */
    reason?: string;
    /** Governance rule IDs (for type='policy_alert'). */
    ruleIds?: string[];
    /** Governance policy version (for type='policy_alert'). */
    policyVersion?: string;
    /** Governance receipt ID (for type='policy_alert'). */
    receiptId?: string;
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
    /** Action name requiring confirmation (for type='needs_confirmation'). */
    action?: string;
    /** Human-readable summary of the pending action (for type='needs_confirmation'). */
    summary?: string;
    /** Card render key (issue #2108/#2109) — populated on hydrated tool_result
     *  steps so `message.cards` can be re-derived after a session reload. */
    render?: string;
    /** Card payload for `render`, mirroring `StreamEvent.data`. */
    data?: unknown;
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
    | 'policy_alert' // Governance policy blocked a tool
    | 'needs_confirmation' // Sidecar agent action awaits user confirmation
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
    /** Governance decision (for policy_alert). */
    decision?: string;
    /** Governance policy reason (for policy_alert). */
    reason?: string;
    /** Governance rule IDs (for policy_alert). */
    rule_ids?: string[];
    /** Governance policy version (for policy_alert). */
    policy_version?: string;
    /** Governance receipt ID (for policy_alert). */
    receipt_id?: string;
    /** Action name awaiting confirmation (for needs_confirmation). */
    action?: string;
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
    /**
     * Card render key (issue #2108, additive on `tool_result`). Non-empty
     * string means the frontend should mount a registered card via
     * RenderCard against `data`. Absent on every other event type.
     */
    render?: string;
    /** Card payload for `render` (issue #2108, additive on `tool_result`). */
    data?: unknown;
}
