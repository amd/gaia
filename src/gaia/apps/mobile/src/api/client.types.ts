// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { AgentInstallStatus } from '@webui/services/api';
import type { IndexProgress } from '@webui/utils/documentLibrary.core';
import type { FollowUpOutcome } from '@webui/utils/messageQueue';
import type {
    ActiveModel,
    ActiveRuns,
    AgentInfo,
    AgentMcpServer,
    ChatPerformance,
    CloudPriceList,
    ConnectorListEntry,
    ContextChoice,
    ContextUsageResult,
    CustomModelRemoved,
    CustomModelReport,
    FeaturedLibrary,
    GpuMemoryStatus,
    LemonadeFound,
    LemonadeTarget,
    LemonadeTargetBody,
    LinkPreview,
    LocalModelStatus,
    MCPServerStatus,
    Message,
    ModelNeed,
    ModelNeedJob,
    PermissionMode,
    PreflightReport,
    ParsedSchedule,
    ProviderModelsResponse,
    ResourceUsage,
    RetryChoice,
    RetryResult,
    Schedule,
    ScheduleResult,
    Session,
    SessionPermissions,
    SetupCheck,
    SetupPlan,
    SetupRunStatus,
    Settings,
    SkillInfo,
    SpendingReport,
    ChatSpending,
    StartScreen,
    StreamEvent,
    SystemStatus,
} from '@webui/types';
import type { FileDiff, TurnChanges, UndoResult, UndoSettings } from '@webui/types/changes';
import type { DiscordStatus } from '@webui/services/messagingApi';
import type { AgentCapabilities, SessionChanges, SessionEnvironment, SessionGit, SessionOutputs, UndoAllResult } from '@webui/types/sessionPanel';
import type { DiscoveryFinding, Goal, GoalStats, InferenceInsight, KnowledgeCreateBody, KnowledgeListParams, KnowledgeSaveResult, KnowledgeUpdateBody, MemoryCleared, MemoryReinitializeResult, MemorySettings, MemorySettingsUpdateResponse, Priority as GoalPriority } from '@webui/services/memoryApi';
import type { SpeechModel, SpeechSettings } from '@webui/services/speechApi';
import type { AboutYouItem, ActivityDay, ConvSession, ConvTurn, EntityInfo, KnowledgeEntry, TemporalItem, ToolStat } from '@webui/utils/memoryFormat.core';
import type {
    AgentUiHealthResponse,
    BrowseResponse,
    CancelStreamResponse,
    ConfirmToolResponse,
    CreateSessionRequest,
    DocumentListResponse,
    DocumentResponse,
    FilePreviewResponse,
    FileSearchResponse,
    MessagesDeleteResponse,
    ModelServerInfo,
    ProviderSummaryList,
    SessionDeleteResponse,
    SessionSearchResponse,
    ToolConfirmRequest,
    UserInputRequest as ApiUserInputRequest,
    UserInputResponse,
} from '@webui/types/api.gen';

export type { AgentInstallStatus } from '@webui/services/api';
export type {
    ActiveModel,
    ActiveRuns,
    AgentInfo,
    AgentMcpServer,
    ChatPerformance,
    CloudPriceList,
    ConnectorListEntry,
    ConnectorRow,
    ContextChoice,
    ContextUsage,
    ContextUsageResult,
    CustomModelRemoved,
    CustomModelReport,
    FeaturedLibrary,
    GpuMemoryStatus,
    LemonadeFound,
    LemonadeTarget,
    LemonadeTargetBody,
    LinkPreview,
    LinkSkill,
    LocalModelStatus,
    MCPServerStatus,
    Message,
    ModelNeed,
    ModelNeedJob,
    PermissionMode,
    PreflightReport,
    ParsedSchedule,
    ProviderId,
    ProviderInfo,
    ProviderModel,
    ProviderModelsResponse,
    ResourceUsage,
    RetryChoice,
    RetryResult,
    Schedule,
    ScheduleResult,
    Session,
    SessionPermissions,
    SetupCheck,
    SetupPlan,
    SetupRunStatus,
    Settings,
    SkillInfo,
    SkillSource,
    SpendingReport,
    ChatSpending,
    StartScreen,
    StreamEvent,
    SystemStatus,
    UnavailableConnectorRow,
} from '@webui/types';
export type { FileDiff, TurnChanges, UndoResult, UndoSettings } from '@webui/types/changes';
export type { ActiveSkill, AgentCapabilities, AgentTool, ChatFileChange, GitRepo, OutputItem, OutputKind, ScopeEntry, SessionChanges, SessionEnvironment, SessionGit, SessionOutputs, SkillOrigin, UndoAllResult } from '@webui/types/sessionPanel';
export type { BrowseResponse, CreateSessionRequest, DocumentListResponse, DocumentResponse, FileEntry, FileSearchResponse, FileSearchResult, ModelServerInfo, QuickLink, ProviderSummary, ProviderSummaryList, SessionSearchResponse, SessionSearchResult, ToolConfirmRequest, FilePreviewResponse } from '@webui/types/api.gen';

/** A photo or file chosen on this phone: where it is, and what it is called. */
export interface PickedFile {
    uri: string;
    name: string;
    mimeType: string | null;
    size: number | null;
}

/** `display` is what the phone shows as the answer; it is never sent to GAIA. */
export type UserInputRequest = ApiUserInputRequest & { display?: string };

export interface SessionList {
    sessions: Session[];
    total: number;
}

export interface FileStatusList {
    files: { path: string; exists?: boolean | null; mtime?: number | null }[];
}

export interface MessageList {
    messages: Message[];
    total: number;
}

/** The `connector.disconnected` event's payload: the provider-side revoke outcome (#2591). */
export interface DisconnectOutcome {
    revoke_supported?: boolean;
    revoked_remotely?: boolean;
    revoke_error?: string | null;
}

export interface ConnectorList {
    connectors: ConnectorListEntry[];
}

export interface AgentList {
    agents: AgentInfo[];
}

export interface AllPermissions {
    default_mode: PermissionMode;
    sessions: SessionPermissions[];
}

/** Namespaced agent ids to the scopes granted to them. */
export interface ConnectorGrants {
    grants: Record<string, string[]>;
}

/** Namespaced agent ids to whether the agent may use the connector's tools. */
export interface ConnectorActivations {
    activations: Record<string, boolean>;
}

/** `invalid` maps each unreadable skill's path to why GAIA skipped it. */
export interface SkillList {
    skills: SkillInfo[];
    invalid: Record<string, string>;
}

export interface FeaturedLibraries {
    libraries: FeaturedLibrary[];
}

export interface AgentMcpList {
    agent_mcps: AgentMcpServer[];
}

export interface ScheduleList {
    schedules: Schedule[];
    total: number;
}

/** POST /api/providers/{id}/connect's answer, as the web's Model settings reads it. */
export interface ProviderConnected {
    remembered: boolean;
    remember_error?: string | null;
    known_models?: boolean;
}

export type { MemoryCleared, MemoryReinitializeResult, MemorySettings, MemorySettingsUpdateResponse } from '@webui/services/memoryApi';
/** GET /api/messaging/discord, typed as Settings > Messaging on the web types it. */
export type { DiscordState, DiscordStatus } from '@webui/services/messagingApi';
export type { DiscoveryFinding, InferenceInsight } from '@webui/services/memoryApi';

/** One event of the profile-setup streams (`stream-discovery`, `stream-inference`), as ui/routers/memory.py documents them. */
export type ProfileStreamEvent =
    | { type: 'log'; message: string }
    | { type: 'finding'; source: string; item: DiscoveryFinding }
    | { type: 'insight'; item: InferenceInsight }
    | { type: 'error'; message: string; source?: string }
    | { type: 'done'; total: number };
/** One day of GET /api/memory/activity, one tool of GET /api/memory/tools, one item of GET /api/memory/upcoming; typed as the web's `MemoryDashboard` types them. */
export type { ActivityDay as MemoryActivityDay, TemporalItem as MemoryUpcomingItem, ToolStat as MemoryToolStat } from '@webui/utils/memoryFormat.core';

export type { Goal, GoalStats, GoalStatus, GoalTask, Priority as GoalPriority } from '@webui/services/memoryApi';

export type { KnowledgeCreateBody, KnowledgeListParams, KnowledgeSaveResult, KnowledgeUpdateBody } from '@webui/services/memoryApi';
export type { AboutYouItem as MemoryKnowledgeItem } from '@webui/utils/memoryFormat.core';
export type { ConvSession as MemoryConversation, ConvTurn as MemoryTurn, EntityInfo as MemoryEntity, KnowledgeEntry as MemoryKnowledgeEntry } from '@webui/utils/memoryFormat.core';

/** The body of POST /api/goals. */
export interface CreateGoalBody {
    title: string;
    description: string;
    priority: GoalPriority;
}

/** One page of GET /api/memory/knowledge; `total` counts every match, not just this page. */
export interface MemoryKnowledgePage {
    items: KnowledgeEntry[];
    total?: number;
}

/** GET /api/memory/embedding-coverage; typed as the web's `MemoryDashboard` types it. */
export interface EmbeddingCoverage {
    total_items: number;
    with_embedding: number;
    without_embedding: number;
    coverage_pct: number;
}

/** GET /api/memory/stats. The web types it inside `MemoryDashboard.tsx`; the generated API types leave it out. */
export interface MemoryStats {
    knowledge: {
        total: number;
        total_retrievals: number;
        by_category: Record<string, number>;
        by_context: Record<string, number>;
        sensitive_count: number;
        entity_count: number;
        avg_confidence: number;
        oldest: string | null;
        newest: string | null;
    };
    conversations: {
        total_turns: number;
        total_sessions: number;
        consolidated_sessions?: number;
        first_session: string | null;
        last_session: string | null;
    };
    tools: {
        total_calls: number;
        unique_tools: number;
        overall_success_rate: number;
        total_errors: number;
    };
    temporal: {
        upcoming_count: number;
        overdue_count: number;
    };
    /** Null when the coverage query failed; `embedding_error` says why. */
    embedding?: EmbeddingCoverage | null;
    embedding_error?: string;
    db_size_bytes: number;
}

export interface ConsolidateMemoryResult {
    status: string;
    consolidated: number;
    extracted_items: number;
}

export interface RebuildEmbeddingsResult {
    backfilled: number;
    total_without: number;
}

export interface ReconcileMemoryResult {
    pairs_checked: number;
    reinforced: number;
    contradicted: number;
    weakened: number;
    neutral: number;
}

export interface RefreshSystemContextResult {
    stored: number;
    skipped: boolean;
    reason?: string;
}

export interface SessionUpdate {
    title?: string;
    title_is_custom?: boolean;
}

export type HealthResponse = AgentUiHealthResponse;
export type CancelChatResult = CancelStreamResponse;
export type ConfirmToolResult = ConfirmToolResponse;
export type DeleteMessagesResult = MessagesDeleteResponse;
export type DeleteSessionResult = SessionDeleteResponse;
export type UserInputResult = UserInputResponse;

export interface DeleteDocumentResult {
    deleted: boolean;
}

export interface CancelIndexingResult {
    cancelled: boolean;
    id: string;
}

export interface DocumentStatus {
    id: string;
    indexing_status: string;
    chunk_count: number;
    is_active: boolean;
    /** Null unless the document is being indexed right now. */
    progress: IndexProgress | null;
}

export type { IndexProgress };

/** What `POST /api/setup/run` takes; the web UI's `SetupRunOptions` (services/setupApi.ts). */
export interface SetupRunOptions {
    acknowledgeLowDisk?: boolean;
    keepTrying?: boolean;
    extraModels?: string[];
    serverOnly?: boolean;
    /** Replace GAIA's older embedded Lemonade without downloading models again. */
    upgradeEmbedded?: boolean;
}

export interface CreateScheduleRequest {
    name: string;
    interval: string;
    prompt: string;
    /** IANA zone the times are in; the device's. */
    timezone?: string;
}

export interface ScheduleResults {
    results: ScheduleResult[];
    total: number;
}

export interface DeleteScheduleResult {
    deleted: boolean;
}

export interface RestartModelResult {
    status: string;
    model: string;
    starting_server: boolean;
}

export interface FollowUpResult {
    delivered: boolean;
}

export type { FollowUpOutcome };

/** What the PC answers an upload with; `url` is the link a message carries so the agent can read the file. */
export interface UploadedFile {
    filename: string;
    original_name: string;
    url: string;
    size: number;
    content_type: string;
    is_image: boolean;
}

export interface RequestOptions {
    signal?: AbortSignal;
}

export type { SpeechModel, SpeechSettings, Voice } from '@webui/services/speechApi';
/** The read-aloud settings `PUT /api/speech/settings` changes; the rest stay as saved. */
export type SpeechSettingsUpdate = Partial<Pick<SpeechSettings, 'voice' | 'speed' | 'auto'>>;

/** Audio the PC made, as bytes; `contentType` is the response's media type. */
export interface PcAudioBytes {
    bytes: Uint8Array<ArrayBuffer>;
    contentType: string;
}

export interface GaiaClient {
    health(options?: RequestOptions): Promise<HealthResponse>;
    listSessions(options?: RequestOptions): Promise<SessionList>;
    getSession(sessionId: string, options?: RequestOptions): Promise<Session>;
    createSession(request?: CreateSessionRequest, options?: RequestOptions): Promise<Session>;
    /** Sends a file to the PC; recordings go to the transcription folder. Rejects with the PC's refusal (type, size, empty). */
    uploadFile(file: Blob, name: string, options?: RequestOptions): Promise<UploadedFile>;
    /** The chat as Markdown, the same text the desktop's Export saves. */
    exportSessionMarkdown(sessionId: string, options?: RequestOptions): Promise<{ content: string }>;
    setSessionPinned(sessionId: string, pinned: boolean, options?: RequestOptions): Promise<Session>;
    /** The file changes each turn of the chat made. */
    listTurnChanges(sessionId: string, options?: RequestOptions): Promise<{ turns: TurnChanges[] }>;
    /** What one turn did to each file. */
    getTurnDiff(sessionId: string, turnId: string, options?: RequestOptions): Promise<{ turn_id: string; files: FileDiff[] }>;
    /** Puts the turn's files back; rejects with a 409 naming a file changed since, unless `force`. */
    undoTurn(sessionId: string, turnId: string, force?: boolean, options?: RequestOptions): Promise<UndoResult>;
    /** Every file the chat changed, across turns, with totals and diffs. */
    getSessionChanges(sessionId: string, options?: RequestOptions): Promise<SessionChanges>;
    /** Puts every file the chat changed back; rejects with 409 `conflict` when one changed since, unless `force`. */
    undoAllChanges(sessionId: string, force?: boolean, options?: RequestOptions): Promise<UndoAllResult>;
    /** The git repositories the chat's files are in, with a drafted message for each. */
    getSessionGit(sessionId: string, options?: RequestOptions): Promise<SessionGit>;
    /** Commits the chat's files in a repo on the PC; runs only when the person confirmed it. */
    commitChanges(sessionId: string, root: string, message: string, options?: RequestOptions): Promise<{ root: string; commit: string; files: string[]; ignored: string[] }>;
    /** Pushes the repo's branch and opens a pull request with `gh` on the PC; runs only when the person confirmed it. */
    createPullRequest(sessionId: string, root: string, title: string, body: string, options?: RequestOptions): Promise<{ root: string; url: string }>;
    /** Deletes the chat and its messages; a turn still running on it is stopped first. */
    deleteSession(sessionId: string, options?: RequestOptions): Promise<DeleteSessionResult>;
    /** Clears the chat's unread mark. */
    markSessionRead(sessionId: string, options?: RequestOptions): Promise<Session>;
    /** The PC's settings, among them `network_policy` (Local only). */
    getSettings(options?: RequestOptions): Promise<Settings>;
    /** Saves the custom model ID (`''` clears it) or the dynamic-tools switch; answers the settings as now saved, with the custom ID's `model_status`. */
    updateSettings(patch: { custom_model?: string; dynamic_tools?: boolean }, options?: RequestOptions): Promise<Settings>;
    /** Turns Local only on or off. The PC alone may change it: 403 from a phone. */
    setNetworkPolicy(policy: Settings['network_policy'], options?: RequestOptions): Promise<Settings>;
    /** How long and how much undo history is kept, and how much is in use. */
    getUndoSettings(options?: RequestOptions): Promise<UndoSettings>;
    /** Saves the undo limits; the PC trims history to fit them at once. */
    updateUndoSettings(retentionDays: number, maxMb: number, options?: RequestOptions): Promise<UndoSettings>;
    /** The model new chats run on and where it runs. */
    getActiveModel(options?: RequestOptions): Promise<ActiveModel>;
    getContextChoice(model: string, options?: RequestOptions): Promise<ContextChoice>;
    /** Remembers a context size for a local model; null returns it to Automatic. */
    setContextChoice(model: string, ctxSize: number | null, options?: RequestOptions): Promise<ContextChoice>;
    /** A cloud provider's list prices, readable before it is connected. */
    /** One provider's chat models; `local` also carries each one's state, size and download. */
    listProviderModels(provider: string, options?: RequestOptions): Promise<ProviderModelsResponse>;
    /** Checks a pasted Hugging Face link; nothing is saved or downloaded. `quant` is a quantization or one of its file names. */
    checkCustomModel(url: string, quant?: string, options?: RequestOptions): Promise<CustomModelReport>;
    /** Adds the model a link names to the list; it downloads on request. */
    addCustomModel(url: string, quant?: string, options?: RequestOptions): Promise<{ report: CustomModelReport }>;
    /** Removes a model the person added, and its download. */
    removeCustomModel(model: string, options?: RequestOptions): Promise<CustomModelRemoved>;
    /** Makes `model` the one new messages use; a cloud model is picked after the person agrees to what leaves the PC. */
    selectModel(model: string, options?: RequestOptions): Promise<ActiveModel>;
    /** The chat's permission mode and the tools it always allows. */
    getPermissions(sessionId: string, options?: RequestOptions): Promise<SessionPermissions>;
    /** Every chat with full access or grants, and the mode new chats start in. */
    listAllPermissions(options?: RequestOptions): Promise<AllPermissions>;
    /** The mode new chats start in. The PC refuses it with a 403 from anywhere but the PC itself. */
    setDefaultPermissionMode(mode: PermissionMode, options?: RequestOptions): Promise<{ default_mode: PermissionMode }>;
    /** Revokes one "always allow" grant, or all of the chat's when `key` is omitted. */
    revokeGrants(sessionId: string, key?: string, options?: RequestOptions): Promise<SessionPermissions>;
    setPermissionMode(sessionId: string, mode: PermissionMode, options?: RequestOptions): Promise<SessionPermissions>;
    /** How full the model's context window is for the chat's next message, or why there is no reading yet. */
    getContextUsage(sessionId: string, options?: RequestOptions): Promise<ContextUsageResult>;
    /** Answers the message again with another model, without touching the chat; keeping it is chooseRetry. A local model loads first, which can take a minute. */
    retryWith(sessionId: string, messageId: number, model: string, options?: RequestOptions): Promise<RetryResult>;
    /** Keeps the original answer or the retry's; the PC saves the retry over the original only for 'retry'. */
    chooseRetry(sessionId: string, messageId: number, keep: 'original' | 'retry', options?: RequestOptions): Promise<RetryChoice>;
    /** Where each answer's time and tokens went, and the model as Lemonade reports it now. */
    getChatPerformance(sessionId: string, options?: RequestOptions): Promise<ChatPerformance>;
    /** What the chat's last finished turn did with the messages added to it; rejects with 409 while a turn still runs. */
    getFollowUpOutcome(sessionId: string, options?: RequestOptions): Promise<FollowUpOutcome>;
    /** Chats whose title or messages match `query`, newest first; `truncated` says the backend capped the list. */
    searchSessions(query: string, includeAutomated: boolean, options?: RequestOptions): Promise<SessionSearchResponse>;
    getMessages(sessionId: string, options?: RequestOptions): Promise<MessageList>;
    /** Removes only this message. */
    deleteMessage(sessionId: string, messageId: number, options?: RequestOptions): Promise<{ deleted: boolean }>;
    /** Renames a chat; the title is pinned against the auto-retitler. */
    updateSession(sessionId: string, update: SessionUpdate, options?: RequestOptions): Promise<Session>;
    /** Flips the chat's private mode (not saved to memory) and returns the chat. */
    toggleSessionPrivate(sessionId: string, options?: RequestOptions): Promise<Session>;
    /** Removes a document from this chat; the library keeps it. */
    detachDocument(sessionId: string, docId: string, options?: RequestOptions): Promise<void>;
    /** The tools and skills of the chat's running agent; rejects with 409 until the agent has started. */
    getCapabilities(sessionId: string, options?: RequestOptions): Promise<AgentCapabilities>;
    /** The folders this chat may touch and whether its web tools are on. */
    getEnvironment(sessionId: string, options?: RequestOptions): Promise<SessionEnvironment>;
    /** Everything the chat produced, newest first. */
    getOutputs(sessionId: string, options?: RequestOptions): Promise<SessionOutputs>;
    /** Gives the chat a folder on the PC; rejects with 400 when the PC refuses it (too broad, missing). */
    addEnvironmentFolder(sessionId: string, path: string, options?: RequestOptions): Promise<SessionEnvironment>;
    /** Takes a folder or file away from the chat; rejects with 409 when the PC refuses. */
    removeEnvironmentFolder(sessionId: string, path: string, options?: RequestOptions): Promise<SessionEnvironment>;
    /** Turns the chat's web tools on or off; rejects with 409 or 502 when the PC can't apply it. */
    setInternet(sessionId: string, on: boolean, options?: RequestOptions): Promise<SessionEnvironment>;
    /** This PC's CPU, memory, GPU, NPU and network use. */
    getResourceUsage(options?: RequestOptions): Promise<ResourceUsage>;
    /** Deletes the message and everything after it, so the exchange can be asked again. */
    deleteMessagesFrom(sessionId: string, messageId: number, options?: RequestOptions): Promise<DeleteMessagesResult>;
    /** Hands a message to the running turn, read at its next step; rejects when the turn can't take it. */
    sendFollowUp(sessionId: string, text: string, options?: RequestOptions): Promise<FollowUpResult>;
    /** Starts a turn and streams it to its `done` or `error` event. */
    sendMessage(sessionId: string, message: string, options?: RequestOptions): AsyncIterable<StreamEvent>;
    /**
     * Streams the chat's running turn: every event so far, then the live ones.
     * Ends at once, with no events, when no turn is running.
     * Aborting only stops this phone's view; the turn goes on on the PC.
     */
    attachToRun(sessionId: string, options?: RequestOptions): AsyncIterable<StreamEvent>;
    /** Asks GAIA to stop the chat's turn; the turn's stream then ends with a cancelled `done`. */
    cancelChat(sessionId: string, options?: RequestOptions): Promise<CancelChatResult>;
    /** Answers a `permission_request`: allow once, always allow this call in this chat, or deny. */
    confirmTool(request: ToolConfirmRequest, options?: RequestOptions): Promise<ConfirmToolResult>;
    /** Answers a `needs_input` question; the turn continues once GAIA has it. */
    sendUserInput(request: UserInputRequest, options?: RequestOptions): Promise<UserInputResult>;
    /**
     * Streams events about the PC's chat list, such as `set_active_session`. Never ends on its own:
     * it throws when the connection drops or goes silent, and the caller reconnects.
     */
    watchSessionEvents(options?: RequestOptions): AsyncIterable<StreamEvent>;
    listConnectors(options?: RequestOptions): Promise<ConnectorList>;
    listConnectorGrants(connectorId: string, options?: RequestOptions): Promise<ConnectorGrants>;
    listConnectorActivations(connectorId: string, options?: RequestOptions): Promise<ConnectorActivations>;
    /** Sets the scopes an agent holds on a connector; the PC answers 400 for a scope past what the connector allows. */
    grantConnectorAgent(connectorId: string, agentId: string, scopes: string[], options?: RequestOptions): Promise<void>;
    /** Removes an agent's grant on a connector. */
    revokeConnectorAgentGrant(connectorId: string, agentId: string, options?: RequestOptions): Promise<void>;
    /** Lets an agent use an MCP server's tools; `scopes` creates the grant when the agent has none. */
    activateConnectorAgent(connectorId: string, agentId: string, scopes?: string[], options?: RequestOptions): Promise<void>;
    /** Hides an MCP server's tools from an agent; its grant stays. */
    deactivateConnectorAgent(connectorId: string, agentId: string, options?: RequestOptions): Promise<void>;
    /** Pauses an MCP server; its credentials and grants stay. Turning it back on is PC-only. */
    disableConnector(connectorId: string, options?: RequestOptions): Promise<void>;
    /**
     * Removes a connector's credentials from the PC. Resolves with what the PC reported about the
     * provider-side revoke, `{}` for a connector with no revoke concept, or null when the PC's
     * event stream could not be read in time.
     */
    disconnectConnector(connectorId: string, options?: RequestOptions): Promise<DisconnectOutcome | null>;
    /** Servers that custom agents declare in their own mcp_servers.json; read-only. */
    listAgentMcps(options?: RequestOptions): Promise<AgentMcpList>;
    /** The agents this PC can run; a PC without the `gaia` agent can't start chats. */
    listAgents(options?: RequestOptions): Promise<AgentList>;
    /** Starts installing an agent on the PC (202); follow it with `getAgentInstallStatus`. PC-only: a phone gets the refusal. */
    installAgent(id: string, options?: RequestOptions): Promise<{ id: string; status: string }>;
    getAgentInstallStatus(id: string, options?: RequestOptions): Promise<AgentInstallStatus>;
    /** The chats with a turn in flight, those queued, and those paused on the user. */
    getActiveRuns(options?: RequestOptions): Promise<ActiveRuns>;
    listSchedules(options?: RequestOptions): Promise<ScheduleList>;
    /** The last `days` days of conversations, tool calls, learned entries and errors, oldest first. */
    getMemoryActivity(days: number, options?: RequestOptions): Promise<ActivityDay[]>;
    /** Per-tool call counts and timings; memory's own tools are not logged. */
    getMemoryTools(options?: RequestOptions): Promise<ToolStat[]>;
    /** Memories due within `days` days, plus the overdue ones. */
    getMemoryUpcoming(days: number, options?: RequestOptions): Promise<TemporalItem[]>;
    getMemoryStats(options?: RequestOptions): Promise<MemoryStats>;
    /** The Discord bridge's state, pairing code and links. The PC answers only itself: a phone gets the 403 PC-only refusal. */
    getDiscordStatus(options?: RequestOptions): Promise<DiscordStatus>;
    /** Every goal with its tasks, newest first. */
    listGoals(options?: RequestOptions): Promise<{ goals: Goal[]; total: number }>;
    /** Goal and task counts by status. */
    getGoalStats(options?: RequestOptions): Promise<GoalStats>;
    /** Why GAIA can't run approved goals (the goal list can't be read); null when it can. */
    getGoalStoreHealth(options?: RequestOptions): Promise<{ error: string | null }>;
    /** Makes a goal GAIA will work on; it starts queued. */
    createGoal(body: CreateGoalBody, options?: RequestOptions): Promise<Goal>;
    /** Accepts a goal GAIA suggested. A paired device's approval is audit-logged on the PC with the device's id. */
    approveGoal(goalId: string, options?: RequestOptions): Promise<Goal>;
    /** Turns down a goal GAIA suggested; also audit-logged. */
    rejectGoal(goalId: string, options?: RequestOptions): Promise<Goal>;
    /** Stops a queued or running goal. */
    cancelGoal(goalId: string, options?: RequestOptions): Promise<Goal>;
    /** Deletes a goal and its tasks. */
    deleteGoal(goalId: string, options?: RequestOptions): Promise<void>;
    /** One page of stored memories; the About you tab asks `about_user` so GAIA's own notes stay out. */
    listMemoryKnowledge(params: KnowledgeListParams, options?: RequestOptions): Promise<MemoryKnowledgePage>;
    /** Every entity with a memory, with its count. */
    listMemoryEntities(options?: RequestOptions): Promise<EntityInfo[]>;
    /** The most recent conversations in memory, newest first. */
    listMemoryConversations(limit: number, options?: RequestOptions): Promise<ConvSession[]>;
    /** Turns that match `query`, across every conversation. */
    searchMemoryConversations(query: string, limit: number, options?: RequestOptions): Promise<ConvTurn[]>;
    /** One conversation's turns, oldest first. */
    getMemoryConversation(sessionId: string, options?: RequestOptions): Promise<ConvTurn[]>;
    /** Saves a memory and embeds it. */
    addMemoryKnowledge(body: KnowledgeCreateBody, options?: RequestOptions): Promise<KnowledgeSaveResult>;
    /** Changes a memory's text or due date; only the fields given are sent. */
    updateMemoryKnowledge(knowledgeId: string, body: KnowledgeUpdateBody, options?: RequestOptions): Promise<KnowledgeSaveResult>;
    /** Forgets a memory. */
    deleteMemoryKnowledge(knowledgeId: string, options?: RequestOptions): Promise<unknown>;
    /** The voice and speed replies are read in, whether every reply is read, and the voices offered. */
    getSpeechSettings(options?: RequestOptions): Promise<SpeechSettings>;
    /** Saves the fields given; answers with all the settings. Shared with the terminal app. */
    updateSpeechSettings(change: SpeechSettingsUpdate, options?: RequestOptions): Promise<SpeechSettings>;
    /** Whether the voice model is on the PC; the first speech request downloads it if not. */
    getSpeechModel(options?: RequestOptions): Promise<SpeechModel>;
    /** Speaks `text` (at most MAX_SPEECH_CHARS) as audio, in the saved voice unless one is given. */
    speak(text: string, voice?: { voice?: string; speed?: number }, options?: RequestOptions): Promise<PcAudioBytes>;
    /** A generated audio card's file, by its `/api/media/audio/<name>` `src`; refuses any other `src`, a reply that isn't audio, and an oversized file. */
    getGeneratedAudio(src: string, options?: RequestOptions): Promise<PcAudioBytes>;
    getMemorySettings(options?: RequestOptions): Promise<MemorySettings>;
    /** Changes the memory switches named in `settings`; answers with all of them, and what the system scan did when discovery was switched on. */
    updateMemorySettings(settings: Partial<MemorySettings>, options?: RequestOptions): Promise<MemorySettingsUpdateResponse>;
    /** Deletes every memory, conversation and tool log; irreversible. Answers with the rows deleted. */
    clearAllMemory(options?: RequestOptions): Promise<MemoryCleared>;
    /** Deletes everything as clearAllMemory does, then re-collects this PC's facts; irreversible. */
    reinitializeMemory(options?: RequestOptions): Promise<MemoryReinitializeResult>;
    getEmbeddingCoverage(options?: RequestOptions): Promise<EmbeddingCoverage>;
    /** Folds finished chats into lasting memory. */
    consolidateMemory(options?: RequestOptions): Promise<ConsolidateMemoryResult>;
    /** Embeds the entries that have no embedding yet. */
    rebuildEmbeddings(options?: RequestOptions): Promise<RebuildEmbeddingsResult>;
    /** Checks pairs of memories against each other for support or contradiction. */
    reconcileMemory(options?: RequestOptions): Promise<ReconcileMemoryResult>;
    rebuildMemoryFts(options?: RequestOptions): Promise<{ status: string }>;
    /** Re-reads this PC's facts into memory; `skipped` when system context collection is off. */
    refreshSystemContext(options?: RequestOptions): Promise<RefreshSystemContextResult>;
    /** Scans the PC and streams what it finds, ending at `done`; nothing is saved until commitMemoryDiscovery. */
    streamMemoryDiscovery(options?: RequestOptions): AsyncIterable<ProfileStreamEvent>;
    /** Asks a model on the PC to describe the person, streamed to `done`; `includeBrowser` adds 30 days of browser history. */
    streamMemoryInference(includeBrowser: boolean, options?: RequestOptions): AsyncIterable<ProfileStreamEvent>;
    /** Saves the findings the person kept. */
    commitMemoryDiscovery(items: DiscoveryFinding[], options?: RequestOptions): Promise<{ stored: number }>;
    /** Saves the insights the person kept, replacing the earlier inferred profile. */
    commitMemoryInference(insights: InferenceInsight[], options?: RequestOptions): Promise<{ stored: number }>;
    /** Every skill GAIA can load, plus the folders it skipped. */
    listSkills(options?: RequestOptions): Promise<SkillList>;
    /** Skill libraries GAIA suggests adding from a link. */
    listFeaturedSkillLibraries(options?: RequestOptions): Promise<FeaturedLibraries>;
    /** Reads and audits the skills behind a link; writes nothing. Adding them is PC-only. */
    previewSkillLink(url: string, options?: RequestOptions): Promise<LinkPreview>;
    /** Creates a schedule; `interval` is the plain-words "when to run", parsed by the PC. 409 when the name is taken. */
    createSchedule(request: CreateScheduleRequest, options?: RequestOptions): Promise<Schedule>;
    /** Reads a plain-words "when to run" without saving it, for the form's live preview. */
    parseSchedule(input: string, timezone: string | undefined, options?: RequestOptions): Promise<ParsedSchedule>;
    /** Pauses or resumes a schedule; answers with the schedule as it now is. */
    updateScheduleStatus(name: string, status: 'active' | 'paused', options?: RequestOptions): Promise<Schedule>;
    /** Runs a schedule once now (409 while a run is in flight); its next scheduled run stays as it was. */
    runSchedule(name: string, options?: RequestOptions): Promise<Schedule>;
    deleteSchedule(name: string, options?: RequestOptions): Promise<DeleteScheduleResult>;
    /** Puts a file from this phone in the library and indexes it; resolves once GAIA has read it or failed to. */
    uploadDocument(file: PickedFile, options?: RequestOptions): Promise<DocumentResponse>;
    /** Indexes a file that is on the PC, as the chat's folders allow; resolves once GAIA has read it or failed to. */
    uploadDocumentByPath(filepath: string, sessionId: string, options?: RequestOptions): Promise<DocumentResponse>;
    /** A folder on the PC, as the chat's folders allow; with no `path` it opens the chat's first folder. 403 with `status: 'os_denied'` for a folder the PC's account can't read. */
    browseFiles(sessionId: string, path: string | undefined, options?: RequestOptions): Promise<BrowseResponse>;
    /** Looks for files by name in the chat's folders; `fileTypes` is a comma list of extensions. */
    searchFiles(sessionId: string, query: string, fileTypes: string | undefined, limit: number, options?: RequestOptions): Promise<FileSearchResponse>;
    /** Attaches a library document to a chat, so the chat searches it. */
    attachDocument(sessionId: string, docId: string, options?: RequestOptions): Promise<void>;
    /** A schedule's most recent runs, newest first. */
    listScheduleResults(name: string, limit: number, options?: RequestOptions): Promise<ScheduleResults>;
    /** The library's documents; a chat searches only the ones attached to it. */
    listDocuments(options?: RequestOptions): Promise<DocumentListResponse>;
    /** Stops a document's indexing; rejects with 404 when none is running. */
    cancelDocumentIndexing(docId: string, options?: RequestOptions): Promise<CancelIndexingResult>;
    /** Takes a document out of the library; the file on the PC is untouched. */
    deleteDocument(docId: string, options?: RequestOptions): Promise<DeleteDocumentResult>;
    /** Where a document's indexing has got, with its progress while it runs. */
    getDocumentStatus(docId: string, options?: RequestOptions): Promise<DocumentStatus>;
    /** Indexes a document again; rejects with the reason when it fails once more. */
    reindexDocument(docId: string, options?: RequestOptions): Promise<DocumentResponse>;
    /** A PC file's first lines (or its columns, or just its details) as the chat's folders allow; rejects with 403 for a file this chat may not read. */
    getFilePreview(path: string, sessionId: string, options?: RequestOptions): Promise<FilePreviewResponse>;
    /** Whether each path still exists on the PC and when it last changed; unchecked outside the PC user's home folder. */
    getFileStatus(paths: string[], options?: RequestOptions): Promise<FileStatusList>;
    /** An image on the PC, as bytes; the chat's id is what lets a phone read it. */
    getImageBytes(path: string, sessionId: string, options?: RequestOptions): Promise<Uint8Array>;
    /** A library document's file, as bytes; the chat's id is what lets a phone read it. */
    getDocumentBytes(docId: string, sessionId: string, options?: RequestOptions): Promise<Uint8Array>;
    getSystemStatus(options?: RequestOptions): Promise<SystemStatus>;
    /** Starts a model's download on the PC (`force` downloads it again); follow it in `getSystemStatus().download_progress`. */
    downloadModel(model: string, options?: { force?: boolean; loadAfter?: boolean } & RequestOptions): Promise<{ status: string; model: string }>;
    /** Loads a model on the PC at the context window chat needs; 409 when a chat is answering, until `force`. */
    loadModel(modelName: string, force: boolean, options?: RequestOptions): Promise<{ status: string; model: string; ctx_size: number }>;
    /** The model server's state, what it runs on and its memory; the pill and its sheet read it. */
    getLocalModelStatus(options?: RequestOptions): Promise<LocalModelStatus>;
    /** Starts the model server if stopped, then reloads the chat model. `confirm` agrees to reload a Lemonade GAIA doesn't own. */
    restartLocalModel(confirm: boolean, options?: RequestOptions): Promise<RestartModelResult>;
    /** Whether a job needs a model that isn't on the PC, and which. */
    getModelNeed(job: ModelNeedJob, options?: RequestOptions): Promise<ModelNeed>;
    /** How this PC's memory is split, the presets and what each unlocks; `settingGb` also reads the manual steps for that size. Read only: changing it is done on the PC. */
    getGpuMemory(settingGb?: number, options?: RequestOptions): Promise<GpuMemoryStatus>;
    /** "I've done this": records the size someone set by hand on the PC, so it reports a restart pending. Changes no setting. */
    recordGpuMemoryPending(settingGb: number, options?: RequestOptions): Promise<GpuMemoryStatus>;
    /** "Keep {gb}": stops reporting a pending GPU memory change. Changes no setting. */
    dismissGpuMemoryPending(options?: RequestOptions): Promise<GpuMemoryStatus>;
    /** Starts one model's download on the PC; follow it with `getSetupStatus`. */
    downloadSetupModel(modelId: string, acknowledgeLowDisk: boolean, options?: RequestOptions): Promise<SetupRunStatus>;
    /** Deletes one downloaded model from the PC's model server; answers the new plan and the space freed. */
    removeSetupModel(modelId: string, options?: RequestOptions): Promise<SetupPlan & { freed_gb: number }>;
    getSetupStatus(options?: RequestOptions): Promise<SetupRunStatus>;
    /** Stops one model's download, or the whole run without a model; finished and partial files are kept. */
    cancelSetup(modelId?: string, options?: RequestOptions): Promise<{ cancelled: boolean }>;
    /** The new-chat screen's cards and working folder; `skills_error` says why the assistant can't list its skills. */
    getStartScreen(options?: RequestOptions): Promise<StartScreen>;
    /** Is GAIA set up on this PC: ready, or the stage and reasons it is not. */
    checkSetup(skipChatModel: boolean, options?: RequestOptions): Promise<SetupCheck>;
    /** This PC's memory, disk, GPU and NPU as setup reads them. */
    getPreflight(options?: RequestOptions): Promise<PreflightReport>;
    /** Every provider and whether it is set up (`configured`) or held off by Local only (`blocked`); reads only, so a paired phone may. */
    listProviderSummaries(options?: RequestOptions): Promise<ProviderSummaryList>;
    /** The model server GAIA uses: whether it answers, and the llama.cpp backends it lists and prefers. */
    getModelServer(options?: RequestOptions): Promise<ModelServerInfo>;
    /** Saves the llama.cpp backend for chat models (`auto` or a backend name); PC-only. */
    setModelServerBackend(backend: string, options?: RequestOptions): Promise<ModelServerInfo>;
    /** Which model server GAIA uses, and whether the PC still has to choose one. */
    getLemonadeTarget(options?: RequestOptions): Promise<LemonadeTarget>;
    /** Switches GAIA to its own model server or to the one at `base_url`; PC-only (403 from a phone). A 422 body carries `status` (`needs_key`, `bad_key`, ...). */
    setLemonadeTarget(body: LemonadeTargetBody, options?: RequestOptions): Promise<LemonadeTarget & { daemon: unknown }>;
    /** Each MCP server GAIA runs: whether it connected and how many tools it offers. */
    getMcpStatus(options?: RequestOptions): Promise<{ servers: MCPServerStatus[] }>;
    /** A model server the PC already runs; `unchecked` lists addresses that took the connection but didn't answer. */
    detectLemonade(options?: RequestOptions): Promise<{ found: LemonadeFound | null; unchecked?: string[] }>;
    /** Registers the provider with the key the PC already holds; the phone never sends a key. */
    connectProvider(provider: string, options?: RequestOptions): Promise<ProviderConnected>;
    /** After the PC's OS key-store unlock prompt was closed: lets GAIA ask once more, on the PC. */
    retryKeyStore(options?: RequestOptions): Promise<{ retrying: boolean }>;
    /** A cloud provider's list prices, ranked; the first row is the model GAIA uses unless another is picked. */
    getCloudPrices(provider: string, options?: RequestOptions): Promise<CloudPriceList>;
    /** This month's cloud spending at list price, the monthly limit and where it stands. */
    getSpending(options?: RequestOptions): Promise<SpendingReport>;
    /** This chat's cloud spending and every chat's since `since` (the local day's start), for `model`. */
    getChatSpending(sessionId: string, since: string, model: string | null, options?: RequestOptions): Promise<ChatSpending>;
    /** Sets the monthly limit in dollars; null removes it. Answers with the new report. */
    setSpendingLimit(limitUsd: number | null, options?: RequestOptions): Promise<SpendingReport>;
    /** Lets cloud chats go on past the limit until the month ends. */
    allowSpendingThisMonth(options?: RequestOptions): Promise<SpendingReport>;
    /** Reads the NPU's setup afresh after a fix on the PC; answers the plan. */
    recheckSetupNpu(options?: RequestOptions): Promise<SetupPlan>;
    /** What setup will use and download, before it starts; `catalog` marks partial downloads. */
    getSetupPlan(options?: RequestOptions): Promise<SetupPlan>;
    /** Saves setup's chat model; a 409 `no_fit` asks to be sent again with `acknowledgeNoFit`. Returns the new plan. */
    chooseSetupChatModel(modelId: string, acknowledgeNoFit: boolean, options?: RequestOptions): Promise<SetupPlan>;
    /** Saves the model that reads images; null goes back to GAIA's choice. Returns the new plan. */
    chooseSetupVisionModel(modelId: string | null, options?: RequestOptions): Promise<SetupPlan>;
    /** Skips one of setup's models, or takes it back. Returns the new plan. */
    skipSetupModel(modelId: string, skipped: boolean, options?: RequestOptions): Promise<SetupPlan>;
    /** The GPU's share of this PC's memory, and whether it can be changed. */
    startSetup(
        skipChatModel: boolean,
        gaiaLemonade?: boolean,
        options?: SetupRunOptions & RequestOptions,
    ): Promise<SetupRunStatus>;
}
