// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Types shared across the GAIA Agent UI frontend. */

import type { TurnSummaryResponse } from './api.gen';

export interface Session {
    id: string;
    title: string;
    /** True when the title was explicitly set (rename/API) and is pinned
     *  against auto-retitling (#2165). */
    title_is_custom?: boolean;
    created_at: string;
    updated_at: string;
    model: string;
    /** The model a turn actually runs (custom override, agent preference, device). */
    effective_model?: string | null;
    system_prompt: string | null;
    message_count: number;
    document_ids: string[];
    private?: boolean;
    agent_type?: string;
    /** Device used for this session (cpu / gpu / npu). */
    device?: string;
    /** Mail provider for email-triage sessions ("google" | "microsoft"). */
    mail_provider?: string | null;
    /** Whether chat leaves this machine; null/absent when the backend can't tell. */
    inference_remote?: boolean | null;
    /** Backend id answering chat, e.g. "lemonade", "fireworks". */
    inference_provider?: string | null;
    /** Human name for that backend, e.g. "Fireworks AI". */
    inference_provider_name?: string | null;
    /** One sentence on where chat runs and what is sent there. */
    inference_description?: string | null;
    /** Pinned to the top of the chat list. */
    pinned?: boolean;
    /** A turn finished since the chat was last opened. */
    unread?: boolean;
    /** How the last turn ended; null before the first. */
    last_run_status?: 'done' | 'failed' | 'stopped' | null;
    /** Who started the chat: a person, the eval runner, or a schedule. */
    kind?: SessionKind;
}

export type SessionKind = 'chat' | 'eval' | 'scheduled';

/** What a chat's running turn is paused on. */
export type AwaitingUser = 'permission' | 'question';

/** GET /api/chat/active: chats with a turn in flight. */
export interface ActiveRuns {
    session_ids: string[];
    /** Chats waiting for the model, first to run next first. */
    queued: string[];
    awaiting_user: Record<string, AwaitingUser>;
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

/**
 * An agent as ``GET /api/agents`` (``gaia.ui.models.AgentInfo``) describes it.
 * Settings → Connectors uses it to grant scopes.
 *
 * Every field must be emitted by that response: declaring one nothing sends
 * reads as ``undefined`` forever at runtime (#2970, #3842), so
 * ``tests/unit/test_webui_agent_info_contract.py`` fails the build on it.
 */
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
     * (issue #915). The Settings → Connectors page renders these so the
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
     * Whether the provider supports the RFC 8628 device-code flow (#1275) —
     * a short code entered at a URL, no browser redirect and no per-user Azure
     * app registration. When ``true`` the tile offers a "Sign in with a code"
     * option alongside the browser Connect button (Microsoft today).
     */
    supports_device_code?: boolean;
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
    /**
     * Which OAuth *client* (app credentials) the provider resolves (#2104).
     * ``source`` is ``'keyring'`` for credentials saved via Settings /
     * `gaia connectors configure`, ``'env'`` for GAIA_*_CLIENT_ID, or
     * ``null`` when no client is configured. The client secret value is
     * never included — only whether one is stored. ``null``/absent for
     * ``mcp_server`` connectors.
     */
    oauth_client?: {
        source: 'keyring' | 'env' | null;
        client_id: string | null;
        has_secret: boolean;
    } | null;
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
    /** The agent's own step count for the turn. */
    steps?: number | null;
    /** The model that wrote the answer; absent on answers saved before it was recorded. */
    model?: string | null;
    /** Set when the answer came from Retry with…: how long its model took, loading included. */
    seconds?: number | null;
    /** True only for an answer kept from Retry with…. */
    retried?: boolean | null;
    /** Where the turn was answered: "on your GPU", "via Fireworks AI". */
    place?: string | null;
}

/** Estimated tokens the next model call holds, by kind (GET /api/chat/context). */
export interface ContextUsage {
    window: number;
    reply_reserve: number;
    system: number;
    tools: number;
    documents: number;
    history: number;
    query: number;
    used: number;
    history_budget: number;
    history_used: number;
    turns_in_view: number;
    turns_hidden: number;
    /** Oldest turns the next overflow drops, by their question. */
    at_risk: string[];
    at_risk_tokens: number;
    /** Parts of `system` the skills and memory fragments take. */
    skills?: number;
    memory?: number;
    /** Part of `tools` the tool schemas take; the rest is replayed tool activity. */
    tool_definitions?: number;
    /** Between turns only: whether the counts came from the model's own tokenizer. */
    tokenizer?: { exact: true; model: string } | { exact: false; reason: string };
    /** Between turns only: the panel's categories; their tokens add up to `used`. */
    categories?: Array<{ key: string; label: string; tokens: number }>;
    tool_loading?: { loaded: string[]; on_demand: string[] };
    model?: string | null;
    /** A cloud model's own window from the catalog, when larger than what GAIA admits. */
    model_window?: number | null;
    /** What the agent moved out of the window to keep going; absent when it reports nothing. */
    context_state?: ContextState | null;
}

/** The agent's own account of context it put away or summarized (``context_state()``). */
export interface ContextState {
    /** Tool results already used, replaced by a re-read handle. */
    put_away?: { results: number; tokens: number } | null;
    /** Older turns folded into a rolling summary; originals stay searchable. */
    summarized?: { turns: number; tokens: number; summary_tokens: number } | null;
}

/** GET /api/chat/performance/{id}: one turn's measured performance. Null = not measured. */
export interface TurnPerformance {
    message_id: number;
    created_at: string;
    model: string | null;
    time: {
        wall_seconds: number;
        model_seconds: number | null;
        gaia_seconds: number | null;
        tool_seconds: number;
        waiting_seconds: number;
        own_seconds: number | null;
    };
    first_token_seconds: number | null;
    /** `server`: the model server's own timing. `client`: measured by GAIA on the stream. */
    first_token_source?: 'server' | 'client' | null;
    prefill_tokens_per_second: number | null;
    /** `client` means derived from GAIA's first-token time, so it includes network time. */
    prefill_source?: 'server' | 'client' | null;
    generation_tokens_per_second: number | null;
    generation_source?: 'server' | 'client' | null;
    model_calls: number;
    tokens_in: number | null;
    tokens_new: number | null;
    tokens_cached: number | null;
    tokens_out: number | null;
    /** The last request's whole templated prompt, as the server counted it. */
    last_prompt_tokens: number | null;
    tool_calls: number;
    /** All tool time the step timer measured, the same total as the time split. */
    tool_seconds_total?: number;
    /** A null name is tool time measured outside any recorded step. */
    tools: Array<{ name: string | null; seconds: number | null }>;
    loads: Array<{ model: string | null; seconds: number; replaced?: string[]; during?: 'request' }>;
    cost: { kind: 'local' | 'estimate' | 'unknown'; usd: number | null };
    /** Why a value is unknown, keyed by field. */
    unknown?: Partial<Record<'first_token_seconds' | 'prefill_tokens_per_second' | 'generation_tokens_per_second' | 'tokens' | 'tokens_cached' | 'time', string>>;
}

export interface ChatPerformance {
    /** The newest answer's id; the last turn is unknown when `turns` has no record of it. */
    latest_message_id: number | null;
    turns: TurnPerformance[];
    totals: {
        turns: number;
        wall_seconds: number | null;
        model_seconds: number | null;
        gaia_seconds: number | null;
        tokens_in: number | null;
        tokens_cached: number | null;
        tokens_out: number | null;
        tool_calls: number;
        loads: number;
        load_seconds: number | null;
        usd: number;
        unpriced_turns: number;
    };
    /** The model as Lemonade reports it now. */
    now: {
        model: string | null;
        /** `local`, a cloud provider name, `cloud`, or `unknown`. */
        provider: string;
        loaded: boolean | null;
        device: string | null;
        backend: string | null;
        ctx_size: number | null;
        weights_gb: number | null;
        kv_cache_gb: number | null;
        free_gb: number | null;
        error: string | null;
    };
}

/** A retried answer from another model, held until one is kept. */
export interface RetryResult {
    message_id: number;
    model: string;
    content: string;
    stats: {
        model: string;
        retried: true;
        seconds: number;
        load_seconds: number;
        input_tokens: number;
        output_tokens: number;
        tokens_per_second: number;
        time_to_first_token: number;
    };
}

/** GET /api/chat/context: the meter's numbers, or why there are none yet. */
export type ContextUsageResult = ({ available: true } & ContextUsage) | { available: false; reason: string };

/** POST /api/chat/retry-with/choose: which answer the chat now continues from. */
export type RetryChoice = { kept: 'original' } | {
    kept: 'retry';
    content: string;
    /** The retry's numbers over the turn's saved ones, so `steps` and `place` carry over. */
    stats: RetryResult['stats'] & Pick<InferenceStats, 'steps' | 'place'>;
};

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
    /** Set when the user stopped this turn. 'unconfirmed' means the server
     *  never acknowledged the stop, so the client closed the turn itself. */
    stopState?: 'stopped' | 'unconfirmed';
    /** Error the server reported while a stopped turn was closing. */
    stopError?: string;
    /** Set when this turn pushed older turns out of the model's view. */
    historyNotice?: string;
    /** Sources, files and data flow of the turn, for the side rail (#5222). */
    turnSummary?: TurnSummary;
}

/** What one turn used, wrote, and sent off this PC (`done.turn_summary`). */
export type TurnSummary = TurnSummaryResponse;

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
    /** Set when retrying can't help until the file is fixed: corrupted, encrypted, empty (no text), empty_file (nothing to index) or unsupported_workbook (.xls or password-protected). */
    error_kind?: string | null;
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
    /** Background agent behaviour; a legacy stored "autonomous" is reported as "goal_driven". */
    agent_mode: 'manual' | 'goal_driven';
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

/** Whether a new chat's first turn will find its prompt already read. */
export interface WarmUpStatus {
    state: 'idle' | 'warming' | 'ready' | 'failed' | 'skipped';
    elapsed_seconds?: number | null;
    prompt_tokens?: number | null;
    cached_tokens?: number | null;
    error?: string | null;
    /** The model the warm-up loads, once it is resolved. */
    model?: string | null;
    /** Where that model runs; null when Lemonade did not say. */
    device?: 'npu' | 'gpu' | 'cpu' | null;
}

export interface SystemStatus {
    lemonade_running: boolean;
    /** Present when the Lemonade health/catalog query failed. */
    lemonade_error?: string | null;
    /** How the probe ended: nothing answered ("down"), connected but no answer in time ("timed_out"), an error answer ("error"), or "ok". */
    lemonade_probe?: 'ok' | 'down' | 'timed_out' | 'error' | null;
    /** How to start Lemonade on this host, resolved server-side. */
    start_instruction?: string | null;
    /** Shell command, or null on hosts started from a GUI (tray / app). */
    start_command?: string | null;
    model_loaded: string | null;
    embedding_model_loaded: boolean;
    disk_space_gb: number;
    memory_available_gb: number | null;
    initialized: boolean;
    /** Why the desktop app's first-run `gaia init` failed; null when it did not. */
    setup_error?: string | null;
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
    processor_name: string | null;
    // LLM configuration health
    context_size_sufficient: boolean;
    model_downloaded: boolean | null;
    default_model_name: string | null;
    /** A cloud model: nothing to download, load or resize on this PC. */
    default_model_is_cloud?: boolean;
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
    /** Priming of a new chat's prompt; never blocks sending. */
    warm_up?: WarmUpStatus | null;
    /** Devices detected on this system (e.g. ['cpu', 'gpu', 'npu']). */
    detected_devices?: string[];
    /** Active profile from ``~/.gaia/config.json`` (e.g. "chat", "npu"). */
    active_profile: string;
    /** Why ``~/.gaia/config.json`` could not be loaded; null when it loaded. */
    config_error?: string | null;
    /** One message per status probe that failed; the matching fields stay unknown. */
    probe_warnings?: string[];
    /** The folder that holds the chat database. */
    data_dir?: string | null;
}

/**
 * Hardware report from GET /api/onboarding/preflight, shown on the setup
 * screen. ``compatible`` is false only when there is a hard blocker
 * (e.g. not enough disk for the model download).
 */
export interface PreflightReport {
    os: string | null;
    detected_platform: string | null;
    ram_gb: number | null;
    disk_free_gb: number | null;
    /** true/false when probed; null when hardware couldn't be verified. */
    npu_detected: boolean | null;
    gpu_name: string | null;
    gpu_vram_gb: number | null;
    lemonade_running: boolean;
    /** Present when the Lemonade system-info query failed. */
    lemonade_error?: string | null;
    tier: 'full' | 'standard' | 'light' | 'insufficient' | 'unknown' | string;
    recommended_profile: string;
    recommended_model: string;
    required_disk_gb: number;
    required_memory_gb: number;
    compatible: boolean;
    blockers: string[];
    warnings: string[];
}

// ── File Browser Types ───────────────────────────────────────────────────

/** A single file or folder entry returned by the browse endpoint. */
export interface FileEntry {
    name: string;
    path: string;
    type: 'file' | 'folder';
    size: number;
    extension: string | null;
    modified: string | null;
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
    /** Files in the folder of a type GAIA doesn't index. */
    skipped?: number;
    documents: Document[];
    errors: string[];
}

// ── MCP Server Types ──────────────────────────────────────────────────────

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
    /** Parsed natural-language schedule as JSON; null for plain-interval schedules. */
    schedule_config: string | null;
    /** When it runs, e.g. "Weekdays at 8:00 AM" — the same words the form preview shows. */
    description: string;
    /** The last run's error; null when it succeeded. */
    last_error: string | null;
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
    /** IANA zone the times are in. */
    timezone?: string | null;
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
    /** The device behind the latest authenticated remote request. */
    device?: { name: string; lastSeen: string } | null;
}

// ── Agent Activity Types ──────────────────────────────────────────────────

/** Structured command output for shell command results. */
export interface CommandOutput {
    command: string;
    stdout: string;
    stderr: string;
    returnCode: number;
    /** Why a non-zero exit is an answer, not a failure (grep's 1: no match). */
    expectedExit?: string | null;
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
    type: 'thinking' | 'tool' | 'plan' | 'status' | 'error' | 'policy_alert';
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
    /** One plain line per plan step, parallel to planSteps (for type='plan'). */
    planNarrations?: string[];
    /** Zero-based index of the plan step being executed (for type='plan'). */
    currentStep?: number;
    /** What the tool call does, in plain words (for type='tool'). */
    narration?: string;
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
    /** The connector's display name, e.g. "Git" (for MCP tools). */
    mcpServerLabel?: string;
    /** The server's own tool name, e.g. "git_log" (for MCP tools). */
    mcpTool?: string;
    /** The text an MCP tool returned. */
    mcpOutput?: McpOutput;
    /** Tool call latency in milliseconds. */
    latencyMs?: number;
}

/** The text an MCP tool returned, capped by the server. */
export interface McpOutput {
    text: string;
    truncated?: boolean;
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
    | 'answer'       // Final answer from agent
    | 'agent_error'  // Agent-level error (non-fatal)
    | 'permission_request' // Tool confirmation request
    | 'needs_confirmation' // Stateless confirmation card (email /query, #2109) — informational, non-blocking
    | 'needs_input' // Mid-run question (#2595) — answerable; the run blocks until POST /chat/user-input
    | 'policy_alert' // Governance policy blocked a tool
    | 'mcp_status'   // MCP server connection status update
    | 'agent_created' // New agent created — triggers agent list refresh
    | 'followup_taken'    // The running turn took a message sent mid-turn
    | 'followup_returned' // The turn ended without taking these mid-turn messages
    | 'context_usage';    // What this turn's model call holds, by kind

export interface StreamEvent {
    type: StreamEventType;
    content?: string;
    message_id?: number;
    /** On `done`: the user's Stop ended this run (absent when it completed normally). */
    cancelled?: boolean;
    /** On `followup_taken`: the message the running turn took. */
    text?: string;
    /** On `followup_returned`: messages the turn ended without taking. */
    texts?: string[];
    // Agent-specific fields
    status?: string;
    message?: string;
    /** Model phase on a status event: reading, loading_model, reasoning, tool_call, … */
    phase?: string;
    step?: number;
    total?: number;
    tool?: string;
    summary?: string;
    success?: boolean;
    steps?: string[];
    /** One plain line per plan step, parallel to `steps` (for plan events). */
    step_narrations?: string[];
    current_step?: number;
    /** What a tool call does, in plain words (for tool_start and tool_args). */
    narration?: string;
    title?: string;
    detail?: string;
    args?: Record<string, unknown>;
    model?: string;
    elapsed?: number;
    tools_used?: number;
    /** Inference stats from the LLM backend (attached to done events). */
    stats?: InferenceStats;
    /** Per-turn sources, files and data flow (attached to done events). */
    turn_summary?: TurnSummary;
    /** MCP server statuses (for mcp_status events). */
    servers?: MCPServerStatus[];
    /** Structured command output (for tool_result of run_shell_command). */
    command_output?: {
        command: string;
        stdout: string;
        stderr: string;
        return_code: number;
        expected_exit?: string | null;
        cwd?: string;
        duration_seconds?: number;
        truncated?: boolean;
    };
    /** Agent ID of the newly created agent (for agent_created events). */
    agent_id?: string;
    /** Confirmation ID (for permission_request events). */
    confirm_id?: string;
    /** What "always allow" would grant for this call; absent means it is not offered. */
    always_scope?: string;
    /** `always_scope` in plain words, e.g. "editing notes.txt" (for permission_request events). */
    always_label?: string;
    /** The tool in plain words, e.g. "writing file" (for permission_request events). */
    tool_label?: string;
    /** What the call does: read, write, execute, or destructive (for permission_request events). */
    risk?: string;
    /** Machine tool name a confirmation is about (for needs_confirmation events). */
    action?: string;
    /** Question id to echo back on POST /chat/user-input (for needs_input events). */
    request_id?: string;
    /** The question text (for needs_input events). */
    question?: string;
    /** 2-4 mutually-exclusive answer options (for needs_input events). */
    options?: Array<{ value: string; label: string; description: string }>;
    /** Whether a free-text answer is also accepted (for needs_input events). */
    allow_free_text?: boolean;
    /** Whether the answer must never be echoed into visible history (for needs_input events). */
    sensitive?: boolean;
    /** Timeout in seconds (for tool_confirm events). */
    timeout_seconds?: number;
    /** MCP server name (for tool_start of MCP tools). */
    mcp_server?: string;
    /** The connector's display name (for tool_start of MCP tools). */
    mcp_server_label?: string;
    /** The server's own tool name (for tool_start of MCP tools). */
    mcp_tool?: string;
    /** The text an MCP tool returned (for tool_result of MCP tools). */
    mcp_output?: McpOutput;
    /** Tool call latency in milliseconds (for tool_result). */
    latency_ms?: number;
    /** agent_error the agent recovers from by retrying, rather than a turn-ending one. */
    recoverable?: boolean;
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

// ── First-run setup (GET /api/setup/*) ────────────────────────────────────

export type SetupStepStatus = 'pending' | 'active' | 'done' | 'failed' | 'cancelled';

export interface SetupStep {
    key: 'server' | 'models' | 'finish';
    label: string;
    detail: string;
    status?: SetupStepStatus;
}

/** `gaia init --check --json`: ready, or the stage and reasons it is not. */
export interface SetupCheck {
    ready: boolean;
    stage?: 'setup' | 'server' | 'load' | string;
    reasons?: string[];
    models?: Array<{ id: string; role: string; size_gb: number | null; loaded: boolean; error: string | null }>;
    steps: SetupStep[];
    /** The steps for a cloud provider, which downloads only the embedding model. */
    cloud_steps?: SetupStep[];
}

/** A failed setup with a known fix: the user's Lemonade Server isn't answering. */
export interface SetupProblem {
    kind: 'server_unreachable';
    /** One plain sentence: what is wrong. */
    message: string;
    /** How to start that server, or null when this PC has none to start. */
    start: string | null;
    /** Setup can switch to GAIA's own server instead. */
    use_gaia: boolean;
    /** Set when it can't switch from here (LEMONADE_BASE_URL), and what does. */
    switch_hint: string | null;
}

export interface SetupRunStatus {
    state: 'idle' | 'running' | 'verifying' | 'ready' | 'failed' | 'cancelled';
    steps?: SetupStep[];
    text?: string;
    percent?: number | null;
    /** How much of the current download is done, e.g. "1.20 GB of 5.60 GB". */
    size?: string | null;
    error?: string | null;
    problem?: SetupProblem | null;
    command?: string;
    log_tail?: string[];
    skip_chat_model?: boolean;
    /** The run is switching to GAIA's own server; a retry must keep switching. */
    gaia_lemonade?: boolean;
    /** The chat model setup chose, once it has said. */
    chat_model?: string | null;
}

/** GET /api/setup/plan: what setup will use and download, before it starts. */
export interface SetupPlan {
    /** `url` is null while GAIA's own server isn't running: it has no address yet. */
    lemonade: { mode: 'gaia' | 'external'; url: string | null; reachable: boolean };
    chat_model: { id: string; user_set: boolean; size_gb: number | null; downloaded: boolean | null };
    recommended_model: string | null;
    /** GAIA's own server isn't running yet, so the final pick comes once it is. */
    decided_after_server: boolean;
    memory_gb: number | null;
    memory_source: string | null;
    options: Array<{ id: string; size_gb: number | null; fits: boolean | null; reason: string | null }>;
    models: Array<{ id: string; role: 'chat' | 'embedding'; size_gb: number | null }>;
    download_gb: number | null;
}

/** A task a new chat offers, from what is set up on this PC. */
export interface StartCard {
    /** The skill that sharpens this task when installed; also the card's id. */
    skill: string;
    title: string;
    note: string;
    prompt: string;
    /** The prompt ends where the user types the subject, so it fills the box instead of sending. */
    needs_input: boolean;
}

/** GET /api/onboarding/start */
export interface StartScreen {
    cards: StartCard[];
    working_folder: { path: string; name: string; gaia_folder: boolean };
    /** Why there are no cards: the agent couldn't list its skills. */
    skills_error?: string | null;
}

// ── Which Lemonade Server GAIA uses (/api/lemonade/*) ─────────────────────

export type LemonadeMode = 'gaia' | 'external';

/** GET /api/lemonade/target: the server GAIA uses now and who chose it. */
export interface LemonadeTarget {
    mode: LemonadeMode;
    /** null until the user (or `gaia init`) has chosen. */
    saved_mode: LemonadeMode | null;
    /** `env` means LEMONADE_BASE_URL chose it, so the UI cannot change it. */
    source: 'env' | 'config' | 'gaia';
    base_url: string | null;
    saved_base_url: string | null;
    api_key_stored: boolean;
    gaia_installed: boolean;
    /** Nobody has chosen yet: offer a Lemonade found running. */
    offer: boolean;
    what_gaia_does: string;
}

export type LemonadeProbeStatus = 'ok' | 'needs_key' | 'bad_key' | 'too_old' | 'not_lemonade' | 'unreachable';

/** A Lemonade the user already runs, from GET /api/lemonade/detect. */
export interface LemonadeFound {
    url: string;
    status: LemonadeProbeStatus;
    version: string | null;
    minimum: string | null;
    message: string;
}

export interface LemonadeTargetBody {
    mode: LemonadeMode;
    base_url?: string;
    api_key?: string;
}

// ── AI providers (GET /api/providers) ─────────────────────────────────────

export type ProviderId = 'local' | 'fireworks' | 'amd';

export interface ProviderInfo {
    id: ProviderId;
    name: string;
    remote: boolean;
    privacy_notice: string;
    registered?: boolean;
    /** Where Lemonade's key comes from; null when none is configured. */
    key_source?: 'environment' | 'lemonade' | 'stored' | null;
    /**
     * `waiting_for_os_permission` while the OS holds the saved-key read for the
     * user's permission (a macOS keychain dialog): `key_source` is then unknown,
     * not absent, and `key_message` says what to do.
     */
    key_state?: 'read' | 'waiting_for_os_permission';
    key_message?: string | null;
    models_discovered?: boolean;
    env_var?: string;
    base_url?: string | null;
    error?: string;
    /** AMD gateway only: whether its host answers from this network. */
    network?: ProviderNetwork;
}

export interface ProviderNetwork {
    /** Null when this PC can't tell: Lemonade runs elsewhere, or a proxy carries the traffic. */
    reachable: boolean | null;
    /** Why it can't be reached; null when it can. */
    detail: string | null;
}

export interface ActiveModel {
    provider: ProviderId;
    /** Null only when an unreadable config hides the default; `config_error` says why. */
    model: string | null;
    display_name?: string | null;
    label: string | null;
    remote: boolean;
    is_default: boolean;
    /** Local model on disk; null for a cloud model or while the server starts. */
    downloaded?: boolean | null;
    config_error?: string | null;
    /** Where a new chat would be answered, as the backend resolves it; null when it can't tell. */
    inference_remote?: boolean | null;
    inference_description?: string | null;
    /** `pending` while the model server is still starting; ask again. */
    restore?: { status: 'restored' | 'failed' | 'none' | 'pending'; message: string | null };
}

export interface ProvidersResponse {
    providers: ProviderInfo[];
    active: ActiveModel;
    lemonade_error: string | null;
}

export interface ProviderModel {
    id: string;
    context_length: number | null;
    downloaded: boolean;
    labels: string[];
    rank: number | null;
    note: string | null;
    evidence: string | null;
    // Local models only (GET /api/providers/local/models).
    display_name?: string;
    recipe?: string | null;
    state?: LocalModelState;
    placement?: ModelPlacement;
    size_gb?: number | null;
    /** Fits this PC's memory; null when GAIA can't tell. */
    fits?: boolean | null;
    available?: boolean;
    unavailable_reason?: string | null;
    /** Median tokens/s of its recent turns on this PC. */
    tokens_per_second?: number | null;
    /** An in-flight (or just-finished) download of this model. */
    download_progress?: DownloadProgress | null;
    /** List price per million tokens; null when unknown (never shown as $0). Cloud models only. */
    price?: ModelPrice | null;
}

export type LocalModelState = 'loaded' | 'downloaded' | 'not_downloaded';

/** Where a local model runs: Lemonade's report once loaded, its recipe's target before. */
export interface ModelPlacement {
    device: string | null;
    backend: string | null;
    /** "GPU · Vulkan", "NPU · FLM", "CPU". */
    label: string | null;
    source: 'reported' | 'expected' | null;
    /** Set when the reported device is not the expected one. */
    warning: string | null;
}

export interface ProviderModelsResponse {
    provider: string;
    models: ProviderModel[];
    memory_gb?: number | null;
    memory_source?: string | null;
    capacity_error?: string | null;
}

/** GET/PUT /api/providers/local/context. */
export interface ContextChoice {
    model: string;
    display_name: string;
    chosen: number | null;
    automatic: number | null;
    min: number | null;
    max_fit: number | null;
    options: number[];
    loaded_ctx: number | null;
    memory_source: string;
    note: string | null;
    /** On PUT for a loaded model: whether GAIA started reloading it at the new size. */
    reload?: { started: boolean; reason: 'busy' | 'warm_up' | 'already_loaded' | 'restarting' | 'not_gaias_server' | null } | null;
}

/** GET /api/providers/local/status — the sidebar pill's popover. */
export interface LocalModelStatus {
    state: 'loaded' | 'warming' | 'idle' | 'stopped';
    active_model: string | null;
    active_display_name: string | null;
    active_remote: boolean;
    model: string | null;
    display_name: string | null;
    placement: ModelPlacement | null;
    ctx_size: number | null;
    memory: { source: string; total_gb: number; free_gb: number | null; model_gb: number | null } | null;
    memory_error: string | null;
    /** One plain line; `error_detail` carries the raw cause. */
    error: string | null;
    error_detail?: string | null;
    base_url: string | null;
    /** `gaia` for GAIA's own Lemonade; GAIA asks before reloading on `user` or `remote`. */
    owner: 'gaia' | 'user' | 'remote' | null;
    /** The loaded model is answering a request, or GAIA is warming it up for a new chat. */
    busy: boolean;
    busy_reason?: 'warm_up' | 'request' | null;
    restarting: boolean;
    restart_error: string | null;
}

/** USD per million tokens, from GAIA's one rate card. */
export interface ModelPrice {
    input_per_million: number;
    cached_input_per_million: number;
    output_per_million: number;
    source: string;
}

/** GET /api/spending/prices/{provider}: prices before connecting. */
export interface CloudPriceList {
    provider: string;
    models: Array<{ model: string; name: string; rank: number | null; note: string | null; price: ModelPrice | null }>;
    source: string | null;
    note: string | null;
}

/** GET /api/spending: this month's cloud chats at list price, and the limit. */
export interface SpendingReport {
    month: string;
    total_usd: number;
    turns: number;
    /** Turns on a model with no known price, counted apart rather than as $0. */
    unpriced_turns: number;
    limit_usd: number | null;
    state: 'no_limit' | 'ok' | 'near' | 'reached' | 'allowed_over';
    by_model: Array<{ model: string; provider: string; turns: number; usd: number; unpriced_turns: number }>;
    price_source: string;
}

export interface ConnectProviderBody {
    api_key?: string;
    base_url?: string;
    auth_header_name?: string;
    auth_header_prefix?: string;
}

// ── Tool permissions (GET /api/chat/permissions) ─────────────────────────

export type PermissionMode = 'ask' | 'full_access';

export interface SessionPermissions {
    session_id: string;
    mode: PermissionMode;
    grants: Array<{ key: string; label: string }>;
}

// ── Skills (GET /api/skills) ──────────────────────────────────────────────

/** Which discovery root a skill was found in. */
export type SkillSource = 'agent-bundled' | 'user' | 'claude-import';

export interface SkillInfo {
    name: string;
    description: string;
    /** Omitted by older Hub sidecars; false means a selectable task skill. */
    always_on?: boolean;
    version: string | null;
    tier: string | null;
    tools: string[];
    path: string;
    /** Absent when an older GAIA agent answered the listing. */
    source?: SkillSource | null;
}
