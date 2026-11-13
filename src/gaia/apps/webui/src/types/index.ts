// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Types shared across the GAIA Agent UI frontend. */

import type { TurnSummaryResponse } from './api.gen';
import type { SetupNpuProblem } from './setup';

export * from './setup';
export * from './stream';

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
    /** Label, example and help for each ``mcp_env_keys`` entry. */
    config_fields?: ConnectorConfigField[];
    /** The service's page that creates what the user pastes back (a pre-filled Slack app). */
    setup_url?: string | null;
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

/** A catalog connector whose live state the backend failed to read; its summary raised. */
export interface UnavailableConnectorRow {
    id: string;
    display_name: string;
    error: 'unavailable';
    /** Set when the shared activations file can't be read; `path` names it. */
    reason?: 'activations_unreadable';
    path?: string;
}

/** One entry of GET /api/connectors. */
export type ConnectorListEntry = ConnectorRow | UnavailableConnectorRow;

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
    /** The value must start with this, e.g. `xoxb-` for a Slack bot token. */
    prefix?: string;
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
    /** `unreported`: the model is priced, but its usage never came back (a stopped answer). */
    cost: { kind: 'local' | 'estimate' | 'unknown' | 'unreported'; usd: number | null };
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
        tool_calls: number | null;
        loads: number;
        load_seconds: number | null;
        usd: number | null;
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
    /** Client-side: this reply stands in for a turn that failed before answering. */
    failed?: boolean;
    /** Client-side: the raw error, to recognize the server's saved copy of this failure. */
    failureText?: string;
    /** Client-side: the cause a failed turn named (`done.failure`), for its fix. */
    failure?: string;
    /** Set when this turn pushed older turns out of the model's view. */
    historyNotice?: string;
    /** Sources, files and data flow of the turn, for the side rail (#5222). */
    turnSummary?: TurnSummary;
    /** Notices from adding indexed files to the document library. */
    libraryNotices?: string[];
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
    /** A recording, which GAIA transcribes rather than reads. */
    isMedia?: boolean;
    /** Upload progress of a recording, 0 to 100. */
    progress?: number;
    error?: string;
}

export interface ModelStatus {
    found: boolean;
    downloaded: boolean;
    loaded: boolean;
    /** The model list was read, so `found` false means the ID isn't in it. */
    checked: boolean;
}

export interface Settings {
    custom_model: string | null;
    model_status: ModelStatus | null;
    /** Persisted context window size override (tokens). null = use default 32768. */
    context_size: number | null;
    /** Dynamic tool loading. Effective value: GAIA_DYNAMIC_TOOLS wins when set, else config.json. */
    dynamic_tools: boolean;
    /** True when GAIA_DYNAMIC_TOOLS locks the value — the toggle reflects the effective value and disables. */
    dynamic_tools_locked: boolean;
    /** Background agent behaviour; a legacy stored "autonomous" is migrated to "goal_driven". */
    agent_mode: 'manual' | 'goal_driven';
    /** Shared network policy controlling cloud AI use. */
    network_policy: 'cloud_allowed' | 'local_ai_only';
    policy_warnings?: string[];
    policy_notices?: Array<{ code: string; severity: 'info' | 'warning'; url?: string; reason?: string }>;
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
    /** Set on 'error', and on 'starting' while a backend installs first. */
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
    /** Why a chat can't be given this folder; absent when it can. */
    folder_refusal?: string | null;
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

/** When a schedule runs, as data the UI words in its own language. */
export interface ScheduleDescription {
    /** every: count + unit, optional days; window: that plus start/end hour; days: named days, optional time; daily: a time. */
    kind: 'every' | 'window' | 'days' | 'daily';
    count: number | null;
    unit: 'second' | 'minute' | 'hour' | 'day' | 'week' | null;
    /** 0 = Monday … 6 = Sunday. */
    days: number[] | null;
    /** "HH:MM", 24-hour. */
    time: string | null;
    start_hour: number | null;
    end_hour: number | null;
}

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
    /** When it runs in English, e.g. "Weekdays at 8:00 AM"; the UI words `description_parts` instead. */
    description: string;
    description_parts: ScheduleDescription;
    /** The last run's error; null when it succeeded. */
    last_error: string | null;
    /** A run is in flight right now, timed or Run now. */
    running?: boolean;
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
    /** Null when the input was rejected; `description` then says what to fix. */
    description_parts?: ScheduleDescription | null;
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
    /** `ngrok_not_installed` when the failure is a one-time setup step. */
    reason?: 'ngrok_not_installed' | null;
    /** This OS's ngrok install commands, set only with that reason. */
    installCommands?: string[];
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
    type: 'thinking' | 'commentary' | 'tool' | 'plan' | 'status' | 'error' | 'policy_alert';
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
    /** The user said no to this call, so it never ran (for type='tool'). */
    declined?: boolean;
    /** Nobody answered its permission prompt in time, so it never ran (for type='tool'). */
    timedOut?: boolean;
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
    /** Card key the tool's result drew, persisted for reload (for type='tool'). */
    render?: string;
    /** That card's payload. */
    data?: unknown;
    /** A model the call needed and didn't find, so the row offers it (for type='tool'). */
    needsModel?: 'vision';
}

/** The text an MCP tool returned, capped by the server. */
export interface McpOutput {
    text: string;
    truncated?: boolean;
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
    /** The saved key's last four characters; null when there is none to read. */
    key_hint?: string | null;
    /**
     * `waiting_for_os_permission` while the OS holds the saved-key read for the
     * user's permission (a macOS keychain dialog): `key_source` is then unknown,
     * not absent, and `key_message` says what to do. `store_locked` once the
     * user closed the store's unlock prompt: GAIA holds off asking for five
     * minutes, or until Try again.
     */
    key_state?: 'read' | 'waiting_for_os_permission' | 'store_locked';
    key_message?: string | null;
    models_discovered?: boolean;
    /** The provider's model list is down, so GAIA offers models it already knows. */
    known_models?: boolean;
    env_var?: string;
    /** An environment key GAIA handed Lemonade itself, so a typed key can replace it. */
    key_from_gaia_env?: boolean;
    base_url?: string | null;
    error?: string;
    /** AMD gateway only: whether its host answers from this network. */
    network?: ProviderNetwork;
    /** Local only blocks cloud providers without hiding the policy state. */
    blocked?: boolean;
    blocked_reason?: string | null;
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
    /** Who answers a cloud model, including one GAIA has no provider tab for; null when local. */
    provider_name?: string | null;
    is_default: boolean;
    /** Local model on disk; null for a cloud model or while the server starts. */
    downloaded?: boolean | null;
    config_error?: string | null;
    /** Where a new chat would be answered, as the backend resolves it; null when it can't tell. */
    inference_remote?: boolean | null;
    inference_description?: string | null;
    /** `pending` while the model server is still starting; ask again. */
    /** `retry`: a failed cloud restore the backend checks again on the next read. */
    restore?: { status: 'restored' | 'failed' | 'none' | 'pending'; message: string | null; retry?: boolean };
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
    /** What stops this NPU model on this PC's NPU (Linux setup), in place of the reason. */
    npu_problems?: SetupNpuProblem[];
    /** Median tokens/s of its recent turns on this PC. */
    tokens_per_second?: number | null;
    /** An in-flight (or just-finished) download of this model. */
    download_progress?: DownloadProgress | null;
    /** List price per million tokens; null when unknown (never shown as $0). Cloud models only. */
    price?: ModelPrice | null;
    /** Added by the user from a Hugging Face link, so it can be removed. */
    custom?: boolean;
    /** The link it was added from. */
    source_url?: string | null;
}

/** One check run on a pasted Hugging Face link. */
export interface CustomModelCheck {
    id: 'link' | 'repo' | 'gguf' | 'chat' | 'architecture' | 'fit' | 'new';
    label: string;
    ok: boolean;
    detail: string;
}

/** POST /api/providers/local/custom/check — what a pasted link would add. */
export interface CustomModelReport {
    ok: boolean;
    url: string;
    checks: CustomModelCheck[];
    error: string | null;
    warnings: string[];
    /** Every quantization, by its first file (two can share a quant name). */
    variants: { quant: string; file: string; size_gb: number; fits: boolean; reason: string | null }[];
    repo?: string;
    model_id?: string;
    display_name?: string;
    quant?: string;
    /** The chosen quantization's first file. */
    file?: string;
    checkpoint?: string;
    size_gb?: number;
    architecture?: string | null;
    context_length?: number | null;
    vision?: boolean;
    tool_calling?: boolean;
    llamacpp_version?: string;
}

/** DELETE /api/providers/local/custom/{id}. */
export interface CustomModelRemoved {
    removed: string;
    display_name: string;
    /** Config keys that named the model and were cleared: default_model, last_model. */
    cleared: string[];
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
    blocked?: boolean;
    blocked_reason?: string | null;
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
    /** Turns on a priced model whose usage never came back, such as a stopped answer. */
    unreported_turns: number;
    limit_usd: number | null;
    state: 'no_limit' | 'ok' | 'near' | 'reached' | 'allowed_over';
    by_model: Array<{ model: string; provider: string; turns: number; usd: number; unpriced_turns: number; unreported_turns: number }>;
    price_source: string;
}

/** GET /api/spending/chat/{session_id}: this chat's and today's cloud answers at list price. */
export interface ChatSpending {
    /** The model asked about has a known cloud price; only then is spending shown. */
    paid: boolean;
    model: string | null;
    chat: {
        usd: number;
        answers: number;
        /** Cloud answers with no price, such as a stopped one, counted apart rather than as $0. */
        unpriced_answers: number;
        items: Array<{ message_id: number; created_at: string; model: string | null; kind: string; usd: number | null }>;
    };
    today: { usd: number; answers: number; unpriced_answers: number; chats: number; since: string };
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
export type SkillSource = 'agent-bundled' | 'user' | 'claude-project' | 'claude-import';

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

// ── Resource usage (GET /api/system/resources) ────────────────────────────

/** One CPU, memory, GPU, NPU or network reading; histories are oldest first, one per interval. */
export interface CpuUsage {
    percent: number;
    ghz: number | null;
    history: number[];
}

export interface MemoryUsage {
    used_bytes: number;
    total_bytes: number;
    percent: number;
    history: number[];
}

export interface DeviceUsage {
    id: string;
    name: string;
    kind: 'gpu' | 'npu';
    percent: number;
    memory_used_bytes: number | null;
    history: number[];
}

export interface NetworkUsage {
    id: string;
    /** The connection's name, such as "Ethernet 3". */
    name: string;
    /** The adapter's hardware name. */
    description: string;
    kind: 'ethernet' | 'wifi' | 'network';
    send_bps: number;
    recv_bps: number;
    send_history: number[];
    recv_history: number[];
}

export interface ResourceUsage {
    interval_ms: number;
    /** False until the first sample is in. */
    ready: boolean;
    cpu?: CpuUsage | null;
    memory?: MemoryUsage | null;
    devices?: DeviceUsage[];
    networks?: NetworkUsage[];
    /** Why GPUs and NPUs can't be read, when CPU and memory still can. */
    devices_error?: string | null;
    /** Why network adapters can't be read, when CPU and memory still can. */
    networks_error?: string | null;
    /** Why NPUs can't be read, where they're read apart from GPUs (Linux). */
    npus_error?: string | null;
}

/** A library the Skills panel offers as a one-click add. */
export interface FeaturedLibrary {
    id: string;
    name: string;
    url: string;
    description: string;
}

/** One permission a skill from a link declares, e.g. `network:read` and what it lets the skill do. */
export interface LinkPermission {
    grant: string;
    text: string;
}

/** One skill found behind a pasted link (POST /api/skills/link/preview). */
export interface LinkSkill {
    name: string;
    description: string;
    /** Folder of the skill inside the library. */
    path: string;
    /** Ships tools.py or scripts/ — code that would run on this PC. */
    has_code: boolean;
    /** What the skill declares it may do: the raw grant and a plain-language sentence. */
    permissions: LinkPermission[];
    /** The permissions could not be read, so an empty list does not mean none. */
    permissions_unreadable: boolean;
    /** Security-audit verdict: ALLOW, REVIEW, or BLOCK. */
    verdict: string;
    findings: string[];
    installed: boolean;
    /** Installed from this same link before; adding it again updates it to the latest version. */
    update: boolean;
    /** Why this skill can't be added. */
    error: string | null;
}

export interface LinkPreview {
    link: string;
    source: string;
    skills: LinkSkill[];
    /** What was audited; sent back on add so exactly this version installs. */
    pin: string;
    warning: string;
}

export interface LinkAddResult {
    added: Array<{ name: string; updated: boolean; verdict: string; has_code: boolean; findings: string[] }>;
    failed: Record<string, string>;
}
