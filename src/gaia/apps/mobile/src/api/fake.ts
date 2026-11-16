// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type {
    ActiveModel,
    CloudPriceList,
    ContextChoice,
    CustomModelReport,
    AgentCapabilities,
    AgentInfo,
    AgentInstallStatus,
    AgentMcpServer,
    MCPServerStatus,
    ChatPerformance,
    ConnectorListEntry,
    DisconnectOutcome,
    ContextUsageResult,
    ConsolidateMemoryResult,
    CreateSessionRequest,
    DiscordStatus,
    BrowseResponse,
    EmbeddingCoverage,
    FilePreviewResponse,
    FileSearchResponse,
    FollowUpOutcome,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatus,
    GaiaClient,
    GpuMemoryStatus,
    HealthResponse,
    LemonadeFound,
    LemonadeTarget,
    LemonadeTargetBody,
    LocalModelStatus,
    KnowledgeCreateBody,
    KnowledgeUpdateBody,
    KnowledgeListParams,
    MemoryActivityDay,
    MemoryCleared,
    MemoryConversation,
    MemoryKnowledgeEntry,
    MemoryKnowledgeItem,
    MemoryReinitializeResult,
    MemoryTurn,
    MemorySettings,
    MemoryStats,
    MemoryToolStat,
    MemoryUpcomingItem,
    ProfileStreamEvent,
    Message,
    MessageList,
    ModelNeed,
    ModelServerInfo,
    PreflightReport,
    ProviderSummaryList,
    RetryChoice,
    RetryResult,
    Goal,
    GoalStats,
    ParsedSchedule,
    AllPermissions,
    PermissionMode,
    PickedFile,
    ProviderModelsResponse,
    RebuildEmbeddingsResult,
    ReconcileMemoryResult,
    RefreshSystemContextResult,
    Schedule,
    ScheduleResult,
    ResourceUsage,
    Session,
    FileStatusList,
    SessionChanges,
    SessionEnvironment,
    SessionGit,
    SessionOutputs,
    SessionList,
    SessionPermissions,
    SessionSearchResponse,
    SessionSearchResult,
    SetupCheck,
    SetupPlan,
    SetupRunStatus,
    Settings,
    SkillList,
    SpeechModel,
    SpeechSettings,
    FeaturedLibraries,
    LinkPreview,
    SpendingReport,
    ChatSpending,
    StartScreen,
    StreamEvent,
    SystemStatus,
    ToolConfirmRequest,
    FileDiff,
    TurnChanges,
    UndoResult,
    UndoSettings,
    UserInputRequest,
} from './client.types';
import { foldText, splitMatch } from '@webui/utils/chatSearch';
import { FLAGSHIP_AGENT_ID } from '@webui/utils/newTask';

import { generatedAudioPath } from './constants';
import { GaiaApiError, GaiaConfigError } from './errors';

function cloneNested<T>(source: Record<string, Record<string, T>> | undefined): Record<string, Record<string, T>> {
    return Object.fromEntries(Object.entries(source ?? {}).map(([key, inner]) => [key, { ...inner }]));
}

const EMPTY_PERFORMANCE: ChatPerformance = {
    latest_message_id: null,
    turns: [],
    totals: {
        turns: 0,
        wall_seconds: null,
        model_seconds: null,
        gaia_seconds: null,
        tokens_in: null,
        tokens_cached: null,
        tokens_out: null,
        tool_calls: null,
        loads: 0,
        load_seconds: null,
        usd: null,
        unpriced_turns: 0,
    },
    now: { model: null, provider: 'unknown', loaded: null, device: null, backend: null, ctx_size: null, weights_gb: null, kv_cache_gb: null, free_gb: null, error: null },
};

export interface FakeClientData {
    sessions?: Session[];
    schedules?: Schedule[];
    /** Past runs by schedule name, newest first. */
    scheduleResults?: Record<string, ScheduleResult[]>;
    messages?: Record<string, Message[]>;
    /** What every send streams back; a function takes the sent text and streams what a test scripts. */
    replies?: StreamEvent[] | ((message: string) => AsyncIterable<StreamEvent>);
    /** What attachToRun replays, by chat; a chat not listed has no turn running. */
    running?: Record<string, StreamEvent[]>;
    connectors?: ConnectorListEntry[];
    agents?: AgentInfo[];
    /** Grants by connector id, then namespaced agent id. */
    grants?: Record<string, Record<string, string[]>>;
    /** Activations by connector id, then namespaced agent id. */
    activations?: Record<string, Record<string, boolean>>;
    agentMcps?: AgentMcpServer[];
    /** What each poll of getAgentInstallStatus answers, in turn; the last one repeats. */
    installStatuses?: AgentInstallStatus[];
    /** Makes uploadFile reject, as a refused file does. */
    uploadError?: GaiaApiError;
    /** What getActiveModel answers, and what selectModel last picked. */
    activeModel?: ActiveModel;
    /** What listProviderModels answers, by provider. */
    providerModels?: Record<string, ProviderModelsResponse>;
    /** What getContextChoice answers, by model; setContextChoice updates it. */
    contexts?: Record<string, ContextChoice>;
    /** Makes downloadModel fail with this, as the PC's 403 does for a phone. */
    modelDownloadError?: GaiaApiError;
    /** What checkCustomModel answers, by link. */
    customReports?: Record<string, CustomModelReport>;
    /** Makes selectModel fail with this. */
    selectError?: GaiaApiError;
    /** What getPermissions answers, by chat; a chat not listed asks first. */
    permissions?: Record<string, PermissionMode>;
    /** The "always allow" grants by chat. */
    permissionGrants?: Record<string, { key: string; label: string }[]>;
    /** The mode new chats start in. */
    defaultPermissionMode?: PermissionMode;
    /** Answers the default-mode PUT with the 403 the PC gives a phone. */
    defaultModePcOnly?: boolean;
    /** Answers downloadModel and loadModel with that error, as a PC that refuses the phone does. */
    modelActionError?: GaiaApiError;
    /** What getContextUsage answers. */
    contextUsage?: ContextUsageResult;
    /** What retryWith answers; an error is thrown as it is. */
    retryResult?: RetryResult | Error;
    /** What chooseRetry answers for a kept retry. */
    retryChoice?: RetryChoice;
    /** What getChatPerformance answers. */
    chatPerformance?: ChatPerformance;
    /** What getFollowUpOutcome answers; a function may throw to script a 409. */
    followUpOutcome?: FollowUpOutcome | (() => FollowUpOutcome);
    /** Makes sendFollowUp reject, as a turn that can't take the message does. */
    followUpRefusal?: GaiaApiError;
    status?: SystemStatus;
    /** What getLocalModelStatus answers; restarting it records the call. */
    localModel?: LocalModelStatus;
    /** What getGpuMemory answers (the same status whatever size is asked for); an Error is thrown. */
    gpuMemory?: GpuMemoryStatus | Error;
    /** What getModelNeed answers, by job; a job not listed needs nothing. */
    modelNeeds?: Partial<Record<ModelNeed['job'], ModelNeed>>;
    /** What getSetupStatus answers. */
    setupStatus?: SetupRunStatus;
    skills?: SkillList;
    featuredLibraries?: FeaturedLibraries;
    linkPreview?: (url: string) => LinkPreview;
    resources?: ResourceUsage;
    start?: StartScreen;
    /** What a disconnect reports about the provider-side revoke; `{}` when unset. */
    disconnectOutcome?: DisconnectOutcome | null;
    /** What watchSessionEvents streams before it ends; the real stream never ends on its own. */
    sessionEvents?: unknown[];
    /** Most chats a search returns before it says `truncated`. */
    searchLimit?: number;
    /** Makes every search fail with this message. */
    searchError?: string;
    /** Each check answers the next entry (the last one repeats); an Error entry is thrown. */
    setupChecks?: (SetupCheck | Error)[];
    /** Each status read answers the next entry (the last one repeats); defaults to idle. */
    setupStatuses?: (SetupRunStatus | Error)[];
    setupPlan?: SetupPlan;
    modelServer?: ModelServerInfo;
    providers?: ProviderSummaryList;
    /** What getSpending answers; setting the limit or allowing the month changes it. */
    spending?: SpendingReport;
    /** What getChatSpending answers. */
    chatSpending?: ChatSpending;
    /** What getCloudPrices answers, by provider. */
    cloudPrices?: Record<string, CloudPriceList>;
    /** What recheckSetupNpu answers; an Error is thrown. */
    npuRecheck?: SetupPlan | Error;
    /** What getPreflight answers; an Error is thrown. */
    preflight?: PreflightReport | Error;
    /** What getLemonadeTarget answers; an Error is thrown. */
    lemonadeTarget?: LemonadeTarget | Error;
    /** Thrown by setModelServerBackend and setLemonadeTarget, as the PC's 403 is. */
    serverChangeError?: Error;
    /** What getMcpStatus answers. */
    mcpServers?: MCPServerStatus[];
    /** What detectLemonade answers; an Error is thrown. */
    lemonadeDetect?: { found: LemonadeFound | null; unchecked?: string[] } | Error;
    documents?: DocumentListResponse;
    /** Makes every upload fail with this error. */
    documentUploadError?: Error;
    /** The state an uploaded document comes back in; defaults to complete. */
    uploadStatus?: DocumentResponse['indexing_status'];
    /** What getDocumentStatus answers, by document; one not listed reports its row's status. */
    documentStatuses?: Record<string, Partial<DocumentStatus>>;
    /** Makes a library action on a document reject with this message, by action. */
    documentFailures?: Partial<Record<DocumentAction, string>>;
    /** What browseFiles answers, by path ('' for no path); `'denied'` is the PC refusing the folder, and one not listed is a 404. */
    fileFolders?: Record<string, BrowseResponse | 'denied'>;
    /** What searchFiles answers, whatever the query. */
    fileSearch?: FileSearchResponse;
    /** What getFilePreview answers, by path; a path not listed is a 404. */
    filePreviews?: Record<string, FilePreviewResponse>;
    /** What getImageBytes and getDocumentBytes answer, by path or document id; one not listed is a 404. */
    fileBytes?: Record<string, Uint8Array>;
    /** What getCapabilities answers, by chat; a chat not listed has no agent running (409). */
    capabilities?: Record<string, AgentCapabilities>;
    /** What getOutputs answers, by chat; a chat not listed has produced nothing. */
    outputs?: Record<string, SessionOutputs>;
    /** What getFileStatus answers, by path; a path not listed is reported as unchecked. */
    fileStatus?: Record<string, FileStatusList['files'][number]>;
    /** What getEnvironment answers, by chat; removing a folder and the internet switch change it. A chat not listed is a 404. */
    environments?: Record<string, SessionEnvironment>;
    memorySettings?: MemorySettings;
    /** What getSpeechSettings answers; updateSpeechSettings changes it. */
    speechSettings?: SpeechSettings;
    /** What getSpeechModel answers; defaults to downloaded. */
    speechModel?: SpeechModel;
    /** Thrown by speak. */
    speechError?: Error;
    /** What listGoals answers; approve, reject, cancel and delete change it. */
    goals?: Goal[] | Error;
    /** What getGoalStoreHealth answers. */
    goalStoreError?: string | null;
    /** What getSettings answers; setNetworkPolicy changes it. */
    settings?: Settings;
    /** Thrown by setNetworkPolicy, as the PC's 403 is. */
    networkPolicyError?: Error;
    /** What listTurnChanges answers, by chat. */
    turnChanges?: Record<string, TurnChanges[]>;
    /** What getSessionChanges answers, by chat; a chat not listed has changed nothing. Undo all empties it. */
    sessionChanges?: Record<string, SessionChanges>;
    /** What getSessionGit answers, by chat; a chat not listed is in no repository. */
    sessionGit?: Record<string, SessionGit>;
    /** What getTurnDiff answers, by turn. */
    turnDiffs?: Record<string, FileDiff[]>;
    /** Answers undoTurn with this unless forced, as the PC does for a file changed since. */
    undoConflict?: GaiaApiError;
    /** What getUndoSettings answers; updateUndoSettings changes it. */
    undoSettings?: UndoSettings;
    /** What getDiscordStatus answers; an Error is thrown (a phone gets the PC's 403). */
    discordStatus?: DiscordStatus | Error;
    /** Thrown by deleteSession for these chat ids. */
    deleteSessionErrors?: Record<string, Error>;
    /** Thrown by updateMemorySettings. */
    memorySettingsError?: Error;
    /** Each stats read answers the next entry (the last one repeats); an Error entry is thrown. */
    memoryStats?: (MemoryStats | Error)[];
    embeddingCoverage?: EmbeddingCoverage;
    /** What the activity read answers for a range in days; an Error is thrown. */
    memoryActivity?: (days: number) => MemoryActivityDay[] | Error;
    memoryTools?: MemoryToolStat[] | Error;
    /** The memories the browsers list; an Error makes the list fail. Adds, edits and forgets change it. Fields a row leaves out read as a fresh, unused memory. */
    memoryKnowledge?: (MemoryKnowledgeItem & Partial<MemoryKnowledgeEntry>)[] | Error;
    /** The conversations in memory, newest first, with each one's turns; an Error makes the reads fail. */
    memoryConversations?: { sessions: MemoryConversation[]; turns: Record<string, MemoryTurn[]> } | Error;
    memoryUpcoming?: MemoryUpcomingItem[] | Error;
    /** What each profile-setup stream sends; `hang` leaves it open after its events until the caller stops it, `error` throws once they are read. */
    profileStreams?: { discovery?: ProfileScript; inference?: ProfileScript };
    /** Thrown by both profile commits. */
    profileCommitError?: Error;
    /** What each maintenance POST answers; an Error makes it fail. Unlisted ones answer a zero result. */
    memoryActionResults?: {
        consolidate?: ConsolidateMemoryResult | Error;
        rebuildEmbeddings?: RebuildEmbeddingsResult | Error;
        reconcile?: ReconcileMemoryResult | Error;
        rebuildFts?: Error;
        refreshSystemContext?: RefreshSystemContextResult | Error;
    };
}

export interface ProfileScript {
    events: ProfileStreamEvent[];
    hang?: boolean;
    error?: Error;
}

export type DocumentAction = 'cancel' | 'delete' | 'reindex';

export type KnowledgeCall =
    | { action: 'add'; body: KnowledgeCreateBody }
    | { action: 'update'; id: string; body: KnowledgeUpdateBody }
    | { action: 'delete'; id: string };

export type GoalCall =
    | { action: 'create'; title: string; description: string; priority: string }
    | { action: 'approve' | 'reject' | 'cancel' | 'delete'; id: string };

export interface FakeGaiaClient extends GaiaClient {
    /** Each goal create, approve, reject, cancel and delete, in order. */
    readonly goalCalls: GoalCall[];
    readonly sent: { sessionId: string; message: string }[];
    readonly created: CreateSessionRequest[];
    readonly cancelled: string[];
    readonly deleted: { sessionId: string; messageId: number }[];
    readonly pinned: { sessionId: string; pinned: boolean }[];
    /** Each undoTurn call, in order. */
    readonly undone: { sessionId: string; turnId: string; force: boolean }[];
    readonly deletedSessions: string[];
    readonly read: string[];
    readonly deletedOne: { sessionId: string; messageId: number }[];
    readonly confirmations: ToolConfirmRequest[];
    readonly answers: UserInputRequest[];
    readonly scheduleActions: { action: 'pause' | 'resume' | 'run' | 'delete'; name: string }[];
    readonly followUps: { sessionId: string; text: string }[];
    readonly restarts: { confirm: boolean }[];
    readonly selected: string[];
    readonly customAdds: { url: string; quant?: string }[];
    /** Every retryWith asked, in order. */
    readonly retries: { sessionId: string; messageId: number; model: string }[];
    /** Every chooseRetry asked, in order. */
    readonly kept: { sessionId: string; messageId: number; keep: 'original' | 'retry' }[];
    readonly customRemoved: string[];
    readonly contextSets: { model: string; ctxSize: number | null }[];
    /** How often memory was wiped (clears) and wiped then re-collected (reinitializes). */
    readonly memoryWipes: { clears: number; reinitializes: number };
    /** The monthly limits saved (null removes it), and how often the month was allowed. */
    readonly spendingLimitSets: (number | null)[];
    readonly spendingAllows: { count: number };
    /** The files uploadFile took, in order. */
    readonly uploads: { name: string; size: number }[];
    readonly downloads: { modelId: string; acknowledgeLowDisk: boolean }[];
    readonly modelDownloads: { modelName: string; force: boolean; loadAfter: boolean }[];
    readonly modelLoads: { modelName: string; force: boolean }[];
    readonly removals: string[];
    readonly backendSets: string[];
    /** The text each speak call was given, in order. */
    readonly spoken: string[];
    readonly targetSets: LemonadeTargetBody[];
    /** The setup model choices saved, in order. */
    readonly setupChoices: { kind: 'chat' | 'vision' | 'skip'; modelId: string | null; flag?: boolean }[];
    /** The providers connectProvider was asked for, in order. */
    readonly connects: string[];
    /** How many times recheckSetupNpu was asked. */
    readonly npuRechecks: number;
    /** How many times retryKeyStore was asked. */
    readonly keyStoreRetries: number;
    readonly documentActions: { action: DocumentAction; docId: string }[];
    readonly setupStarts: { skipChatModel: boolean; gaiaLemonade: boolean; upgradeEmbedded: boolean; serverOnly: boolean }[];
    readonly detached: { sessionId: string; docId: string }[];
    readonly installs: string[];
    readonly uploaded: PickedFile[];
    /** The PC paths indexed with uploadDocumentByPath, with the chat that was named. */
    readonly uploadedPaths: { filepath: string; sessionId: string }[];
    readonly attached: { sessionId: string; docId: string }[];
    readonly capabilityReads: string[];
    /** Each folder removal and internet switch, in order. */
    /** Each commit and pull request the phone confirmed, in order. */
    readonly gitActions: { sessionId: string; kind: 'commit' | 'pr'; root: string; text: string }[];
    readonly environmentChanges: ({ sessionId: string; add: string } | { sessionId: string; remove: string } | { sessionId: string; internet: boolean })[];
    /** Each memory maintenance POST, in order. */
    readonly memoryActions: string[];
    /** Each memory add, edit and forget, in order. */
    readonly knowledgeCalls: KnowledgeCall[];
    /** The params of each knowledge list read, in order. */
    readonly knowledgeReads: KnowledgeListParams[];
    /** Each conversation search's query, in order. */
    readonly conversationSearches: string[];
    /** The range of each activity read, in order. */
    readonly memoryActivityReads: number[];
    /** Each profile stream opened (with the browser-history choice for inference), and whether the caller has since stopped it. */
    readonly profileStreamOpens: { kind: 'discovery' | 'inference'; includeBrowser?: boolean; stopped: boolean }[];
    /** Each profile commit, in order. */
    readonly profileCommits: { kind: 'discovery' | 'inference'; items: unknown[] }[];
    /** How many times the stats were read. */
    readonly memoryStatsReads: number;
}

export function createFakeClient(data: FakeClientData = {}): FakeGaiaClient {
    const sessions = [...(data.sessions ?? [])];
    const messages = data.messages ?? {};
    const replies = data.replies ?? [{ type: 'done' } as StreamEvent];
    const running = data.running ?? {};
    const sent: FakeGaiaClient['sent'] = [];
    const created: CreateSessionRequest[] = [];
    const cancelled: string[] = [];
    const deleted: FakeGaiaClient['deleted'] = [];
    const pinned: FakeGaiaClient['pinned'] = [];
    const undone: FakeGaiaClient['undone'] = [];
    const deletedSessions: string[] = [];
    const read: string[] = [];
    const deletedOne: FakeGaiaClient['deletedOne'] = [];
    const confirmations: ToolConfirmRequest[] = [];
    const answers: UserInputRequest[] = [];
    const grants: Record<string, Record<string, string[]>> = cloneNested(data.grants);
    const activations: Record<string, Record<string, boolean>> = cloneNested(data.activations);
    const restarts: FakeGaiaClient['restarts'] = [];
    const selected: string[] = [];
    const customAdds: FakeGaiaClient['customAdds'] = [];
    const retries: FakeGaiaClient['retries'] = [];
    const kept: FakeGaiaClient['kept'] = [];
    const customRemoved: string[] = [];
    const contextSets: FakeGaiaClient['contextSets'] = [];
    const memoryWipes = { clears: 0, reinitializes: 0 };
    const spendingLimitSets: FakeGaiaClient['spendingLimitSets'] = [];
    const spendingAllows = { count: 0 };
    const uploads: FakeGaiaClient['uploads'] = [];
    const downloads: FakeGaiaClient['downloads'] = [];
    const modelDownloads: FakeGaiaClient['modelDownloads'] = [];
    const modelLoads: FakeGaiaClient['modelLoads'] = [];
    const removals: FakeGaiaClient['removals'] = [];
    const schedules = [...(data.schedules ?? [])];
    const scheduleActions: FakeGaiaClient['scheduleActions'] = [];
    const documentActions: FakeGaiaClient['documentActions'] = [];
    const library: DocumentListResponse = data.documents ?? { documents: [], total: 0, total_chunks: 0, total_size_bytes: 0 };

    function findDocument(docId: string, action: DocumentAction): DocumentResponse {
        documentActions.push({ action, docId });
        const failure = data.documentFailures?.[action];
        if (failure) throw new GaiaApiError(500, failure);
        const doc = library.documents.find((d) => d.id === docId);
        if (!doc) throw new GaiaApiError(404, 'Document not found');
        return doc;
    }

    function permissionsOf(sessionId: string): SessionPermissions {
        return { session_id: sessionId, mode: data.permissions?.[sessionId] ?? 'ask', grants: [...(data.permissionGrants?.[sessionId] ?? [])] };
    }

    function findSchedule(name: string): Schedule {
        const schedule = schedules.find((s) => s.name === name);
        if (!schedule) throw new GaiaApiError(404, `Schedule '${name}' not found`);
        return schedule;
    }
    const followUps: FakeGaiaClient['followUps'] = [];
    const setupStarts: FakeGaiaClient['setupStarts'] = [];
    const backendSets: string[] = [];
    const spoken: string[] = [];
    const targetSets: LemonadeTargetBody[] = [];
    const setupChoices: FakeGaiaClient['setupChoices'] = [];
    const connects: string[] = [];
    let npuRechecks = 0;
    let keyStoreRetries = 0;
    let checkAt = 0;
    let statusAt = 0;
    const nextOf = <T,>(list: (T | Error)[], at: number): T => {
        const item = list[Math.min(at, list.length - 1)]!;
        if (item instanceof Error) throw item;
        return item;
    };
    const detached: FakeGaiaClient['detached'] = [];
    const installs: string[] = [];
    let polls = 0;
    const uploaded: PickedFile[] = [];
    const uploadedPaths: { filepath: string; sessionId: string }[] = [];
    const attached: FakeGaiaClient['attached'] = [];
    const capabilityReads: string[] = [];
    const environmentChanges: FakeGaiaClient['environmentChanges'] = [];
    const gitActions: FakeGaiaClient['gitActions'] = [];
    const memoryActions: string[] = [];
    let memoryStatsReads = 0;
    const memoryActivityReads: number[] = [];
    const knowledgeCalls: KnowledgeCall[] = [];
    const knowledgeReads: KnowledgeListParams[] = [];
    const conversationSearches: string[] = [];
    const profileStreamOpens: FakeGaiaClient['profileStreamOpens'] = [];
    const profileCommits: FakeGaiaClient['profileCommits'] = [];
    async function* profileStream(kind: 'discovery' | 'inference', includeBrowser: boolean | undefined, signal?: AbortSignal): AsyncGenerator<ProfileStreamEvent> {
        const open = { kind, ...(includeBrowser === undefined ? {} : { includeBrowser }), stopped: false };
        profileStreamOpens.push(open);
        signal?.addEventListener('abort', () => (open.stopped = true));
        const script = data.profileStreams?.[kind];
        if (!script) throw new GaiaApiError(503, 'This fake client was given no profile stream.');
        yield* script.events;
        if (script.error) throw script.error;
        if (script.hang) await new Promise<void>((resolve) => (signal?.aborted ? resolve() : signal?.addEventListener('abort', () => resolve())));
    }
    const goalCalls: GoalCall[] = [];
    let goals = data.goals instanceof Error ? [] : [...(data.goals ?? [])];
    const goalById = (id: string): Goal => {
        const goal = goals.find((g) => g.id === id);
        if (!goal) throw new GaiaApiError(404, 'Goal not found');
        return goal;
    };
    const setGoal = (id: string, status: Goal['status']): Goal => {
        const goal = goalById(id);
        const next = { ...goal, status };
        goals = goals.map((g) => (g.id === id ? next : g));
        return next;
    };
    let knowledge = data.memoryKnowledge instanceof Error ? [] : [...(data.memoryKnowledge ?? [])];
    let knowledgeAdds = 0;
    function memoryAction<T>(name: string, result: T | Error): T {
        memoryActions.push(name);
        if (result instanceof Error) throw result;
        return result;
    }

    function findSession(sessionId: string): Session {
        const session = sessions.find((s) => s.id === sessionId);
        if (!session) throw new GaiaApiError(404, 'Session not found');
        return session;
    }

    // Replaces the object, so a refetch sees a changed row.
    function patch(sessionId: string, changes: Partial<Session>): Session {
        const at = sessions.findIndex((s) => s.id === sessionId);
        if (at < 0) throw new GaiaApiError(404, 'Session not found');
        sessions[at] = { ...sessions[at]!, ...changes };
        return sessions[at]!;
    }

    return {
        sent,
        created,
        cancelled,
        deleted,
        pinned,
        undone,
        deletedSessions,
        read,
        deletedOne,
        confirmations,
        answers,
        scheduleActions,
        documentActions,
        followUps,
        restarts,
        selected,
        customAdds,
        retries,
        kept,
        customRemoved,
        contextSets,
        memoryWipes,
        spendingLimitSets,
        spendingAllows,
        uploads,
        downloads,
        modelDownloads,
        modelLoads,
        removals,
        setupStarts,
        backendSets,
        spoken,
        targetSets,
        setupChoices,
        connects,
        get npuRechecks() {
            return npuRechecks;
        },
        get keyStoreRetries() {
            return keyStoreRetries;
        },
        detached,
        installs,
        uploaded,
        uploadedPaths,
        attached,
        capabilityReads,
        environmentChanges,
        gitActions,
        memoryActions,
        memoryActivityReads,
        profileStreamOpens,
        profileCommits,
        knowledgeCalls,
        knowledgeReads,
        conversationSearches,
        goalCalls,
        async listGoals() {
            if (data.goals instanceof Error) throw data.goals;
            return { goals, total: goals.length };
        },
        async getGoalStats(): Promise<GoalStats> {
            if (data.goals instanceof Error) throw data.goals;
            const count: GoalStats = { goals: {}, tasks: {} };
            for (const goal of goals) count.goals[goal.status] = (count.goals[goal.status] ?? 0) + 1;
            return count;
        },
        async getGoalStoreHealth() {
            return { error: data.goalStoreError ?? null };
        },
        async createGoal(body) {
            goalCalls.push({ action: 'create', ...body });
            const now = new Date().toISOString();
            const goal: Goal = {
                id: `goal-${goals.length + 1}`,
                ...body,
                status: 'queued',
                source: 'user',
                mode_required: 'goal_driven',
                approved_for_auto: true,
                progress_notes: '',
                created_at: now,
                updated_at: now,
                tasks: [],
            };
            goals = [goal, ...goals];
            return goal;
        },
        async approveGoal(id) {
            goalCalls.push({ action: 'approve', id });
            return setGoal(id, 'queued');
        },
        async rejectGoal(id) {
            goalCalls.push({ action: 'reject', id });
            return setGoal(id, 'rejected');
        },
        async cancelGoal(id) {
            goalCalls.push({ action: 'cancel', id });
            return setGoal(id, 'cancelled');
        },
        async deleteGoal(id) {
            goalCalls.push({ action: 'delete', id });
            goalById(id);
            goals = goals.filter((g) => g.id !== id);
        },
        get memoryStatsReads() {
            return memoryStatsReads;
        },
        async health(): Promise<HealthResponse> {
            return {
                status: 'ok',
                service: 'gaia-agent-ui',
                stats: { sessions: sessions.length, messages: 0, documents: 0, total_chunks: 0, total_size_bytes: 0 },
            };
        },
        async listSessions(): Promise<SessionList> {
            return { sessions: [...sessions], total: sessions.length };
        },
        async getSession(sessionId) {
            return findSession(sessionId);
        },
        async createSession(request = {}) {
            created.push(request);
            const now = new Date().toISOString();
            const session: Session = {
                id: `fake-${created.length}`,
                title: request.title ?? '',
                created_at: now,
                updated_at: now,
                model: request.model ?? '',
                system_prompt: request.system_prompt ?? null,
                message_count: 0,
                document_ids: request.document_ids ?? [],
            };
            sessions.unshift(session);
            return session;
        },
        async uploadFile(file, name) {
            if (data.uploadError) throw data.uploadError;
            uploads.push({ name, size: file.size });
            return { filename: name, original_name: name, url: `/api/files/uploads/${name}`, size: file.size, content_type: file.type, is_image: file.type.startsWith('image/') };
        },
        async getContextChoice(model) {
            const choice = data.contexts?.[model];
            if (!choice) throw new GaiaApiError(503, `This fake client was given no context sizes for ${model}.`);
            return choice;
        },
        async setContextChoice(model, ctxSize) {
            const choice = data.contexts?.[model];
            if (!choice) throw new GaiaApiError(503, `This fake client was given no context sizes for ${model}.`);
            contextSets.push({ model, ctxSize });
            const next = { ...choice, chosen: ctxSize, reload: { started: false, reason: 'already_loaded' as const } };
            data.contexts![model] = next;
            return next;
        },
        async getCloudPrices(provider) {
            const list = data.cloudPrices?.[provider];
            if (!list) throw new GaiaApiError(503, `This fake client was given no prices for ${provider}.`);
            return list;
        },
        async getSpending() {
            if (!data.spending) throw new GaiaApiError(503, 'This fake client was given no spending report.');
            return data.spending;
        },
        async getChatSpending() {
            if (!data.chatSpending) throw new GaiaApiError(503, 'This fake client was given no chat spending.');
            return data.chatSpending;
        },
        async setSpendingLimit(limitUsd) {
            if (!data.spending) throw new GaiaApiError(503, 'This fake client was given no spending report.');
            if (limitUsd !== null && !(limitUsd > 0)) throw new GaiaApiError(422, 'The limit must be above zero.');
            spendingLimitSets.push(limitUsd);
            data.spending = { ...data.spending, limit_usd: limitUsd, state: limitUsd === null ? 'no_limit' : 'ok' };
            return data.spending;
        },
        async allowSpendingThisMonth() {
            if (!data.spending) throw new GaiaApiError(503, 'This fake client was given no spending report.');
            spendingAllows.count += 1;
            data.spending = { ...data.spending, state: 'allowed_over' };
            return data.spending;
        },
        async getActiveModel() {
            if (!data.activeModel) throw new GaiaApiError(503, 'This fake client was given no active model.');
            return data.activeModel;
        },
        async listProviderModels(provider) {
            return data.providerModels?.[provider] ?? { provider, models: [] };
        },
        async checkCustomModel(url) {
            const report = data.customReports?.[url];
            if (!report) throw new GaiaApiError(422, 'This fake client was given no report for that link.');
            return report;
        },
        async addCustomModel(url, quant) {
            const report = data.customReports?.[url];
            if (!report) throw new GaiaApiError(422, 'This fake client was given no report for that link.');
            customAdds.push({ url, quant });
            return { report };
        },
        async removeCustomModel(model) {
            customRemoved.push(model);
            return { removed: model, display_name: model, cleared: [] };
        },
        async selectModel(model) {
            if (data.selectError) throw data.selectError;
            selected.push(model);
            if (!data.activeModel) throw new GaiaApiError(503, 'This fake client was given no active model.');
            data.activeModel = { ...data.activeModel, model };
            return data.activeModel;
        },
        async getPermissions(sessionId): Promise<SessionPermissions> {
            return permissionsOf(sessionId);
        },
        async listAllPermissions(): Promise<AllPermissions> {
            const ids = new Set([...Object.keys(data.permissions ?? {}), ...Object.keys(data.permissionGrants ?? {})]);
            const sessions = [...ids].map(permissionsOf).filter((p) => p.mode !== 'ask' || p.grants.length > 0);
            return { default_mode: data.defaultPermissionMode ?? 'ask', sessions };
        },
        async setDefaultPermissionMode(mode) {
            if (data.defaultModePcOnly) throw new GaiaApiError(403, 'Change this on the PC running GAIA.');
            data.defaultPermissionMode = mode;
            return { default_mode: mode };
        },
        async revokeGrants(sessionId, key): Promise<SessionPermissions> {
            const held = data.permissionGrants?.[sessionId] ?? [];
            if (key !== undefined && !held.some((g) => g.key === key)) throw new GaiaApiError(404, `No grant '${key}' in this chat.`);
            data.permissionGrants = { ...data.permissionGrants, [sessionId]: key === undefined ? [] : held.filter((g) => g.key !== key) };
            return permissionsOf(sessionId);
        },
        async setPermissionMode(sessionId, mode): Promise<SessionPermissions> {
            data.permissions = { ...data.permissions, [sessionId]: mode };
            return permissionsOf(sessionId);
        },
        async getContextUsage() {
            return data.contextUsage ?? { available: false, reason: 'Measured once GAIA has answered in this chat.' };
        },
        async retryWith(sessionId, messageId, model) {
            retries.push({ sessionId, messageId, model });
            if (!data.retryResult) throw new GaiaApiError(503, 'This fake client was given no retry result.');
            if (data.retryResult instanceof Error) throw data.retryResult;
            return data.retryResult;
        },
        async chooseRetry(sessionId, messageId, keep) {
            kept.push({ sessionId, messageId, keep });
            return keep === 'original' || !data.retryChoice ? { kept: 'original' } : data.retryChoice;
        },
        async getChatPerformance() {
            return data.chatPerformance ?? EMPTY_PERFORMANCE;
        },
        async getFollowUpOutcome() {
            const outcome = data.followUpOutcome ?? { taken: [], returned: [] };
            return typeof outcome === 'function' ? outcome() : outcome;
        },
        async searchSessions(query): Promise<SessionSearchResponse> {
            if (data.searchError) throw new GaiaApiError(500, data.searchError);
            const needle = foldText(query);
            const results: SessionSearchResult[] = [];
            for (const s of sessions) {
                if (foldText(s.title).includes(needle)) {
                    results.push({ session_id: s.id, title: s.title, field: 'title', snippet: splitMatch(s.title, query)! });
                    continue;
                }
                for (const m of messages[s.id] ?? []) {
                    const snippet = splitMatch(m.content, query);
                    if (snippet) {
                        results.push({ session_id: s.id, title: s.title, field: 'message', snippet });
                        break;
                    }
                }
            }
            const limit = data.searchLimit ?? Infinity;
            return { query, results: results.slice(0, limit), truncated: results.length > limit };
        },
        async exportSessionMarkdown(sessionId) {
            const session = findSession(sessionId);
            return { content: `# ${session.title}\n` };
        },
        async setSessionPinned(sessionId, value) {
            findSession(sessionId);
            pinned.push({ sessionId, pinned: value });
            return patch(sessionId, { pinned: value });
        },
        async listTurnChanges(sessionId) {
            return { turns: data.turnChanges?.[sessionId] ?? [] };
        },
        async getSessionChanges(sessionId) {
            return data.sessionChanges?.[sessionId] ?? { files: [], totals: { files: 0, additions: 0, deletions: 0, uncounted: 0 }, turns: 0, pending: false, untracked: [], incomplete: false };
        },
        async undoAllChanges(sessionId, force = false) {
            undone.push({ sessionId, turnId: '*', force });
            if (data.undoConflict && !force) throw data.undoConflict;
            const found = data.sessionChanges?.[sessionId];
            if (!found) throw new GaiaApiError(404, 'Nothing to undo in this chat.');
            data.sessionChanges = { ...data.sessionChanges, [sessionId]: { ...found, files: [], totals: { files: 0, additions: 0, deletions: 0, uncounted: 0 } } };
            return {
                turn_ids: [],
                restored: found.files.filter((f) => f.change !== 'created').map((f) => f.path),
                removed: found.files.filter((f) => f.change === 'created').map((f) => f.path),
                kept_folders: [],
                untracked: [],
                incomplete: false,
            };
        },
        async getSessionGit(sessionId) {
            return data.sessionGit?.[sessionId] ?? { available: false, repos: [] };
        },
        async commitChanges(sessionId, root, message) {
            gitActions.push({ sessionId, kind: 'commit', root, text: message });
            const repo = data.sessionGit?.[sessionId]?.repos.find((r) => r.root === root);
            if (!repo) throw new GaiaApiError(404, 'No such repository.');
            return { root, commit: 'a1b2c3d', files: repo.uncommitted, ignored: repo.ignored };
        },
        async createPullRequest(sessionId, root, title) {
            gitActions.push({ sessionId, kind: 'pr', root, text: title });
            return { root, url: 'https://github.com/example/repo/pull/7' };
        },
        async getTurnDiff(_sessionId, turnId) {
            return { turn_id: turnId, files: data.turnDiffs?.[turnId] ?? [] };
        },
        async undoTurn(sessionId, turnId, force = false): Promise<UndoResult> {
            undone.push({ sessionId, turnId, force });
            if (data.undoConflict && !force) throw data.undoConflict;
            const turn = data.turnChanges?.[sessionId]?.find((t) => t.turn_id === turnId);
            if (!turn) throw new GaiaApiError(404, `No turn '${turnId}' in this chat.`);
            data.turnChanges = {
                ...data.turnChanges,
                [sessionId]: data.turnChanges![sessionId]!.map((t) => (t === turn ? { ...t, restored_at: new Date().toISOString() } : t)),
            };
            const restored = turn.files.filter((f) => f.change !== 'created').map((f) => f.path);
            const removed = turn.files.filter((f) => f.change === 'created').map((f) => f.path);
            return { turn_id: turnId, restored, removed, kept_folders: [], untracked: [], incomplete: false };
        },
        async deleteSession(sessionId) {
            findSession(sessionId);
            if (data.deleteSessionErrors?.[sessionId]) throw data.deleteSessionErrors[sessionId];
            deletedSessions.push(sessionId);
            sessions.splice(sessions.findIndex((s) => s.id === sessionId), 1);
            return { deleted: true };
        },
        async markSessionRead(sessionId) {
            findSession(sessionId);
            read.push(sessionId);
            return patch(sessionId, { unread: false });
        },
        async getMessages(sessionId): Promise<MessageList> {
            const list = messages[sessionId] ?? [];
            return { messages: list, total: list.length };
        },
        async deleteMessage(sessionId, messageId) {
            deletedOne.push({ sessionId, messageId });
            const list = messages[sessionId] ?? [];
            if (!list.some((m) => m.id === messageId)) throw new GaiaApiError(404, 'Message not found');
            messages[sessionId] = list.filter((m) => m.id !== messageId);
            return { deleted: true };
        },
        async deleteMessagesFrom(sessionId, messageId) {
            deleted.push({ sessionId, messageId });
            const list = messages[sessionId] ?? [];
            const at = list.findIndex((m) => m.id === messageId);
            if (at < 0) throw new GaiaApiError(404, 'Message not found');
            messages[sessionId] = list.slice(0, at);
            return { deleted: true, count: list.length - at };
        },
        async sendFollowUp(sessionId, text) {
            if (data.followUpRefusal) throw data.followUpRefusal;
            followUps.push({ sessionId, text });
            return { delivered: true };
        },
        async updateSession(sessionId, update) {
            const session = findSession(sessionId);
            if (update.title !== undefined) session.title = update.title;
            if (update.title_is_custom !== undefined) session.title_is_custom = update.title_is_custom;
            return { ...session };
        },
        async toggleSessionPrivate(sessionId) {
            const session = findSession(sessionId);
            session.private = !session.private;
            return { ...session };
        },
        async detachDocument(sessionId, docId) {
            const session = findSession(sessionId);
            detached.push({ sessionId, docId });
            session.document_ids = session.document_ids.filter((id) => id !== docId);
        },
        async getResourceUsage() {
            if (!data.resources) throw new GaiaApiError(503, 'This fake client was given no resource usage.');
            return data.resources;
        },
        async *sendMessage(sessionId, message) {
            sent.push({ sessionId, message });
            yield* typeof replies === 'function' ? replies(message) : replies;
        },
        async *attachToRun(sessionId) {
            yield* running[sessionId] ?? [];
        },
        async cancelChat(sessionId) {
            cancelled.push(sessionId);
            return { status: 'ok', cancelled: true };
        },
        async confirmTool(request) {
            confirmations.push(request);
            return { status: 'ok', approved: request.approved, granted: null };
        },
        async sendUserInput(request) {
            answers.push(request);
            return { status: 'ok', request_id: request.request_id };
        },
        async *watchSessionEvents() {
            yield* (data.sessionEvents ?? []) as StreamEvent[];
        },
        async listConnectors() {
            return { connectors: [...(data.connectors ?? [])] };
        },
        async disableConnector(connectorId) {
            data.connectors = (data.connectors ?? []).map((c) => (c.id === connectorId && !('error' in c) ? { ...c, enabled: false } : c));
        },
        async disconnectConnector(connectorId) {
            data.connectors = (data.connectors ?? []).map((c) =>
                c.id === connectorId && !('error' in c) ? { ...c, configured: false, enabled: true, account_id: null } : c,
            );
            return data.disconnectOutcome ?? {};
        },
        async listConnectorGrants(connectorId) {
            return { grants: { ...grants[connectorId] } };
        },
        async listConnectorActivations(connectorId) {
            return { activations: { ...activations[connectorId] } };
        },
        async grantConnectorAgent(connectorId, agentId, scopes) {
            grants[connectorId] = { ...grants[connectorId], [agentId]: [...scopes] };
        },
        async revokeConnectorAgentGrant(connectorId, agentId) {
            const { [agentId]: _gone, ...rest } = grants[connectorId] ?? {};
            grants[connectorId] = rest;
        },
        async activateConnectorAgent(connectorId, agentId, scopes) {
            if (scopes && !grants[connectorId]?.[agentId]) {
                grants[connectorId] = { ...grants[connectorId], [agentId]: [...scopes] };
            }
            activations[connectorId] = { ...activations[connectorId], [agentId]: true };
        },
        async deactivateConnectorAgent(connectorId, agentId) {
            const { [agentId]: _gone, ...rest } = activations[connectorId] ?? {};
            activations[connectorId] = rest;
        },
        async listAgentMcps() {
            return { agent_mcps: [...(data.agentMcps ?? [])] };
        },
        async listAgents() {
            const agents = data.agents ?? [
                { id: FLAGSHIP_AGENT_ID, name: 'GAIA', description: '', source: 'builtin', conversation_starters: [], models: [] },
            ];
            return { agents: [...agents] };
        },
        async installAgent(id) {
            installs.push(id);
            return { id, status: 'queued' };
        },
        async getAgentInstallStatus(id) {
            const statuses = data.installStatuses;
            if (!statuses?.length) throw new GaiaApiError(404, `No install status for '${id}'.`);
            return statuses[Math.min(polls++, statuses.length - 1)]!;
        },
        async listSkills() {
            return { skills: [...(data.skills?.skills ?? [])], invalid: { ...(data.skills?.invalid ?? {}) } };
        },
        async listFeaturedSkillLibraries() {
            return { libraries: [...(data.featuredLibraries?.libraries ?? [])] };
        },
        async previewSkillLink(url) {
            if (!data.linkPreview) throw new GaiaApiError(400, 'This fake client was given no link preview.');
            return data.linkPreview(url);
        },
        async getMemoryActivity(days) {
            memoryActivityReads.push(days);
            if (!data.memoryActivity) throw new GaiaApiError(503, 'This fake client was given no memory activity.');
            const rows = data.memoryActivity(days);
            if (rows instanceof Error) throw rows;
            return rows;
        },
        async getMemoryTools() {
            if (!data.memoryTools) throw new GaiaApiError(503, 'This fake client was given no memory tool stats.');
            if (data.memoryTools instanceof Error) throw data.memoryTools;
            return data.memoryTools;
        },
        async getMemoryUpcoming() {
            if (!data.memoryUpcoming) throw new GaiaApiError(503, 'This fake client was given no upcoming items.');
            if (data.memoryUpcoming instanceof Error) throw data.memoryUpcoming;
            return data.memoryUpcoming;
        },
        async listMemoryKnowledge(params) {
            if (data.memoryKnowledge instanceof Error) throw data.memoryKnowledge;
            knowledgeReads.push(params);
            const categories = ([] as string[]).concat(params.category ?? []);
            const needle = params.search?.toLowerCase();
            const rows = knowledge
                .filter((item) => params.include_sensitive || !item.sensitive)
                .filter((item) => params.include_superseded || !item.superseded_by)
                .filter((item) => categories.length === 0 || categories.includes(item.category))
                .filter((item) => !params.context || item.context === params.context)
                .filter((item) => !params.entity || item.entity === params.entity)
                .filter((item) => !needle || item.content.toLowerCase().includes(needle))
                .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
            const offset = params.offset ?? 0;
            const items = rows.slice(offset, offset + (params.limit ?? rows.length)).map(
                (item): MemoryKnowledgeEntry => ({
                    confidence: 0.8, metadata: null, use_count: 0, last_used: null, reminded_at: null, superseded_by: null, has_embedding: true, ...item,
                }),
            );
            return { items, total: rows.length };
        },
        async listMemoryEntities() {
            if (data.memoryKnowledge instanceof Error) throw data.memoryKnowledge;
            const counts = new Map<string, { count: number; last: string }>();
            for (const item of knowledge) {
                if (!item.entity) continue;
                const seen = counts.get(item.entity);
                counts.set(item.entity, { count: (seen?.count ?? 0) + 1, last: seen && seen.last > item.updated_at ? seen.last : item.updated_at });
            }
            return [...counts].map(([entity, { count, last }]) => ({ entity, count, last_updated: last }));
        },
        async listMemoryConversations(limit) {
            const conversations = data.memoryConversations;
            if (conversations instanceof Error) throw conversations;
            return (conversations?.sessions ?? []).slice(0, limit);
        },
        async searchMemoryConversations(query, limit) {
            conversationSearches.push(query);
            const conversations = data.memoryConversations;
            if (conversations instanceof Error) throw conversations;
            const needle = query.toLowerCase();
            return Object.values(conversations?.turns ?? {})
                .flat()
                .filter((turn) => turn.content.toLowerCase().includes(needle))
                .slice(0, limit);
        },
        async getMemoryConversation(sessionId) {
            const conversations = data.memoryConversations;
            if (conversations instanceof Error) throw conversations;
            return conversations?.turns[sessionId] ?? [];
        },
        async addMemoryKnowledge(body) {
            knowledgeCalls.push({ action: 'add', body });
            const now = new Date().toISOString();
            const knowledgeId = `fake-knowledge-${++knowledgeAdds}`;
            knowledge.push({
                id: knowledgeId,
                category: body.category ?? 'fact',
                content: body.content,
                domain: body.domain ?? null,
                source: 'user',
                context: body.context ?? 'global',
                entity: body.entity ?? null,
                sensitive: body.sensitive ?? false,
                created_at: now,
                updated_at: now,
                due_at: body.due_at ?? null,
            });
            return { status: 'stored', knowledge_id: knowledgeId };
        },
        async updateMemoryKnowledge(id, body) {
            knowledgeCalls.push({ action: 'update', id, body });
            const at = knowledge.findIndex((item) => item.id === id);
            if (at < 0) throw new GaiaApiError(404, `Knowledge entry ${id} not found`);
            knowledge[at] = { ...knowledge[at]!, ...body, updated_at: new Date().toISOString() } as MemoryKnowledgeItem;
            return { status: 'updated', knowledge_id: id };
        },
        async deleteMemoryKnowledge(id) {
            knowledgeCalls.push({ action: 'delete', id });
            if (!knowledge.some((item) => item.id === id)) throw new GaiaApiError(404, `Knowledge entry ${id} not found`);
            knowledge = knowledge.filter((item) => item.id !== id);
            return { status: 'deleted' };
        },
        async getMemoryStats() {
            if (!data.memoryStats?.length) throw new GaiaApiError(503, 'This fake client was given no memory stats.');
            return nextOf(data.memoryStats, memoryStatsReads++);
        },
        async getSettings() {
            if (!data.settings) throw new GaiaApiError(503, 'This fake client was given no settings.');
            return data.settings;
        },
        async updateSettings(patch) {
            if (!data.settings) throw new GaiaApiError(503, 'This fake client was given no settings.');
            data.settings = {
                ...data.settings,
                ...(patch.custom_model !== undefined ? { custom_model: patch.custom_model || null } : {}),
                ...(patch.dynamic_tools !== undefined ? { dynamic_tools: patch.dynamic_tools } : {}),
            };
            return data.settings;
        },
        async setNetworkPolicy(policy) {
            if (!data.settings) throw new GaiaApiError(503, 'This fake client was given no settings.');
            if (data.networkPolicyError) throw data.networkPolicyError;
            data.settings = { ...data.settings, network_policy: policy };
            return data.settings;
        },
        async getDiscordStatus() {
            if (!data.discordStatus) throw new GaiaApiError(503, 'This fake client was given no Discord status.');
            if (data.discordStatus instanceof Error) throw data.discordStatus;
            return data.discordStatus;
        },
        async getUndoSettings() {
            if (!data.undoSettings) throw new GaiaApiError(503, 'This fake client was given no undo settings.');
            return data.undoSettings;
        },
        async updateUndoSettings(retentionDays, maxMb) {
            if (!data.undoSettings) throw new GaiaApiError(503, 'This fake client was given no undo settings.');
            data.undoSettings = { ...data.undoSettings, retention_days: retentionDays, max_mb: maxMb };
            return data.undoSettings;
        },
        async getSpeechSettings() {
            if (!data.speechSettings) throw new GaiaApiError(503, 'This fake client was given no speech settings.');
            return data.speechSettings;
        },
        async updateSpeechSettings(change) {
            if (!data.speechSettings) throw new GaiaApiError(503, 'This fake client was given no speech settings.');
            data.speechSettings = { ...data.speechSettings, ...change };
            return data.speechSettings;
        },
        async getSpeechModel() {
            return data.speechModel ?? { downloaded: true, size_gb: null };
        },
        async getGeneratedAudio(src) {
            if (generatedAudioPath(src) === null) throw new GaiaConfigError('render.invalidPayload', { render: 'audio' });
            return { bytes: new TextEncoder().encode(src), contentType: 'audio/wav' };
        },
        async speak(text) {
            spoken.push(text);
            if (data.speechError) throw data.speechError;
            return { bytes: new TextEncoder().encode(text), contentType: 'audio/wav' };
        },
        async getMemorySettings() {
            if (!data.memorySettings) throw new GaiaApiError(503, 'This fake client was given no memory settings.');
            return data.memorySettings;
        },
        async updateMemorySettings(settings) {
            if (!data.memorySettings) throw new GaiaApiError(503, 'This fake client was given no memory settings.');
            if (data.memorySettingsError) throw data.memorySettingsError;
            data.memorySettings = { ...data.memorySettings, ...settings };
            return data.memorySettings;
        },
        async clearAllMemory(): Promise<MemoryCleared> {
            memoryWipes.clears += 1;
            const cleared = { knowledge: knowledge.length, tool_history: 0, conversations: 0 };
            knowledge = [];
            return cleared;
        },
        async reinitializeMemory(): Promise<MemoryReinitializeResult> {
            memoryWipes.reinitializes += 1;
            const cleared = { knowledge: knowledge.length, tool_history: 0, conversations: 0 };
            knowledge = [];
            return { cleared, system_context: { stored: 0 } };
        },
        async getEmbeddingCoverage() {
            if (!data.embeddingCoverage) throw new GaiaApiError(503, 'This fake client was given no embedding coverage.');
            return data.embeddingCoverage;
        },
        async consolidateMemory() {
            return memoryAction('consolidate', data.memoryActionResults?.consolidate ?? { status: 'ok', consolidated: 0, extracted_items: 0 });
        },
        async rebuildEmbeddings() {
            return memoryAction('rebuildEmbeddings', data.memoryActionResults?.rebuildEmbeddings ?? { backfilled: 0, total_without: 0 });
        },
        async reconcileMemory() {
            return memoryAction(
                'reconcile',
                data.memoryActionResults?.reconcile ?? { pairs_checked: 0, reinforced: 0, contradicted: 0, weakened: 0, neutral: 0 },
            );
        },
        async rebuildMemoryFts() {
            return memoryAction('rebuildFts', data.memoryActionResults?.rebuildFts ?? { status: 'ok' });
        },
        async refreshSystemContext() {
            return memoryAction('refreshSystemContext', data.memoryActionResults?.refreshSystemContext ?? { stored: 0, skipped: false });
        },
        streamMemoryDiscovery(options) {
            return profileStream('discovery', undefined, options?.signal);
        },
        streamMemoryInference(includeBrowser, options) {
            return profileStream('inference', includeBrowser, options?.signal);
        },
        async commitMemoryDiscovery(items) {
            if (data.profileCommitError) throw data.profileCommitError;
            profileCommits.push({ kind: 'discovery', items });
            return { stored: items.length };
        },
        async commitMemoryInference(insights) {
            if (data.profileCommitError) throw data.profileCommitError;
            profileCommits.push({ kind: 'inference', items: insights });
            return { stored: insights.length };
        },
        async getActiveRuns() {
            return { session_ids: [], queued: [], awaiting_user: {} };
        },
        async listSchedules() {
            return { schedules: [...schedules], total: schedules.length };
        },
        async createSchedule(request) {
            if (schedules.some((s) => s.name === request.name)) throw new GaiaApiError(409, `Schedule '${request.name}' already exists`);
            const schedule: Schedule = {
                id: `schedule-${schedules.length + 1}`,
                name: request.name,
                interval_seconds: 3600,
                prompt: request.prompt,
                status: 'active',
                created_at: new Date().toISOString(),
                last_run_at: null,
                next_run_at: new Date(Date.now() + 3_600_000).toISOString(),
                last_result: null,
                run_count: 0,
                error_count: 0,
                session_id: null,
                schedule_config: null,
                description: request.interval,
                description_parts: { kind: 'every', count: 1, unit: 'hour', days: null, time: null, start_hour: null, end_hour: null },
                last_error: null,
            };
            schedules.push(schedule);
            return { ...schedule };
        },
        async parseSchedule(input): Promise<ParsedSchedule> {
            const valid = /every|daily|\d/i.test(input);
            return {
                interval_seconds: valid ? 3600 : 0,
                time_of_day: null,
                start_hour: null,
                end_hour: null,
                days_of_week: null,
                description: valid ? input : "Couldn't read that schedule",
                description_parts: valid ? { kind: 'every', count: 1, unit: 'hour', days: null, time: null, start_hour: null, end_hour: null } : null,
                next_run_at: valid ? new Date(Date.now() + 3_600_000).toISOString() : null,
                valid,
            };
        },
        async updateScheduleStatus(name, status) {
            const schedule = findSchedule(name);
            scheduleActions.push({ action: status === 'paused' ? 'pause' : 'resume', name });
            Object.assign(schedule, { status });
            return { ...schedule };
        },
        async runSchedule(name) {
            const schedule = findSchedule(name);
            scheduleActions.push({ action: 'run', name });
            if (schedule.running) throw new GaiaApiError(409, `Schedule '${name}' is already running`);
            Object.assign(schedule, { running: true, run_count: schedule.run_count + 1 });
            return { ...schedule };
        },
        async listScheduleResults(name, limit) {
            findSchedule(name);
            const all = data.scheduleResults?.[name] ?? [];
            return { results: all.slice(0, limit), total: all.length };
        },
        async deleteSchedule(name) {
            const schedule = findSchedule(name);
            scheduleActions.push({ action: 'delete', name });
            schedules.splice(schedules.indexOf(schedule), 1);
            return { deleted: true };
        },
        async uploadDocument(file) {
            if (data.documentUploadError) throw data.documentUploadError;
            uploaded.push(file);
            const doc = {
                id: `doc-${uploaded.length}`,
                filename: file.name,
                filepath: `/managed/${file.name}`,
                file_size: file.size ?? 0,
                chunk_count: 1,
                indexed_at: new Date().toISOString(),
                indexing_status: data.uploadStatus ?? 'complete',
            } as DocumentResponse;
            library.documents.push(doc);
            library.total += 1;
            return doc;
        },
        async uploadDocumentByPath(filepath, sessionId) {
            if (data.documentUploadError) throw data.documentUploadError;
            uploadedPaths.push({ filepath, sessionId });
            const name = filepath.split(/[\\/]/).pop() ?? filepath;
            const doc = {
                id: `doc-${uploadedPaths.length}`,
                filename: name,
                filepath,
                file_size: 0,
                chunk_count: 1,
                indexed_at: new Date().toISOString(),
                indexing_status: data.uploadStatus ?? 'complete',
            } as DocumentResponse;
            library.documents.push(doc);
            library.total += 1;
            return doc;
        },
        async browseFiles(_sessionId, path) {
            const folder = data.fileFolders?.[path ?? ''];
            if (folder === 'denied') throw new GaiaApiError(403, 'Access denied', 'mobileApp.error.http', undefined, { status: 'os_denied', message: 'Access denied' });
            if (!folder) throw new GaiaApiError(404, 'Directory not found');
            return folder;
        },
        async searchFiles() {
            if (!data.fileSearch) throw new GaiaApiError(404, 'Search is not set up');
            return data.fileSearch;
        },
        async attachDocument(sessionId, docId) {
            findSession(sessionId);
            attached.push({ sessionId, docId });
        },
        async listDocuments() {
            return { ...library, documents: [...library.documents] };
        },
        async cancelDocumentIndexing(docId) {
            const doc = findDocument(docId, 'cancel');
            doc.indexing_status = 'cancelled';
            return { cancelled: true, id: docId };
        },
        async deleteDocument(docId) {
            const doc = findDocument(docId, 'delete');
            library.documents.splice(library.documents.indexOf(doc), 1);
            return { deleted: true };
        },
        async getFilePreview(path) {
            const preview = data.filePreviews?.[path];
            if (!preview) throw new GaiaApiError(404, 'File not found');
            return preview;
        },
        async getImageBytes(path) {
            const bytes = data.fileBytes?.[path];
            if (!bytes) throw new GaiaApiError(404, 'Image file not found');
            return bytes;
        },
        async getDocumentBytes(docId) {
            const bytes = data.fileBytes?.[docId];
            if (!bytes) throw new GaiaApiError(404, 'Document not found');
            return bytes;
        },
        async getDocumentStatus(docId) {
            const doc = library.documents.find((d) => d.id === docId);
            if (!doc) throw new GaiaApiError(404, 'Document not found');
            return {
                id: docId,
                indexing_status: doc.indexing_status ?? 'complete',
                chunk_count: doc.chunk_count,
                is_active: false,
                progress: null,
                ...data.documentStatuses?.[docId],
            };
        },
        async reindexDocument(docId) {
            const doc = findDocument(docId, 'reindex');
            doc.indexing_status = 'indexing';
            doc.last_error = null;
            doc.error_kind = null;
            return doc;
        },
        async getCapabilities(sessionId) {
            capabilityReads.push(sessionId);
            const found = data.capabilities?.[sessionId];
            if (!found) throw new GaiaApiError(409, "This chat's agent isn't running yet.");
            return found;
        },
        async getOutputs(sessionId) {
            return data.outputs?.[sessionId] ?? { session_id: sessionId, items: [] };
        },
        async getFileStatus(paths) {
            return { files: paths.map((path) => data.fileStatus?.[path] ?? { path }) };
        },
        async getEnvironment(sessionId) {
            const found = data.environments?.[sessionId];
            if (!found) throw new GaiaApiError(404, 'Session not found');
            return found;
        },
        async addEnvironmentFolder(sessionId, path) {
            const env = data.environments?.[sessionId];
            if (!env) throw new GaiaApiError(404, 'Session not found');
            environmentChanges.push({ sessionId, add: path });
            const entry = { path, name: path.split('/').pop() ?? path, kind: 'folder' as const, source: 'added' as const, removable: true };
            const next = { ...env, folders: [...env.folders, entry], removed: env.removed.filter((r) => r !== path) };
            data.environments = { ...data.environments, [sessionId]: next };
            return next;
        },
        async removeEnvironmentFolder(sessionId, path) {
            const env = data.environments?.[sessionId];
            if (!env) throw new GaiaApiError(404, 'Session not found');
            environmentChanges.push({ sessionId, remove: path });
            const next = { ...env, folders: env.folders.filter((f) => f.path !== path), removed: [...env.removed, path] };
            data.environments = { ...data.environments, [sessionId]: next };
            return next;
        },
        async setInternet(sessionId, on) {
            const env = data.environments?.[sessionId];
            if (!env) throw new GaiaApiError(404, 'Session not found');
            environmentChanges.push({ sessionId, internet: on });
            const next = { ...env, internet: on };
            data.environments = { ...data.environments, [sessionId]: next };
            return next;
        },
        async getSystemStatus() {
            if (!data.status) throw new GaiaApiError(503, 'This fake client was given no system status.');
            return data.status;
        },
        async downloadModel(model, options) {
            if (data.modelActionError) throw data.modelActionError;
            if (data.modelDownloadError) throw data.modelDownloadError;
            modelDownloads.push({ modelName: model, force: options?.force ?? false, loadAfter: options?.loadAfter ?? false });
            return { status: 'downloading', model };
        },
        async loadModel(modelName, force) {
            if (data.modelActionError) throw data.modelActionError;
            modelLoads.push({ modelName, force });
            return { status: 'loading', model: modelName, ctx_size: 65536 };
        },
        async getLocalModelStatus() {
            if (!data.localModel) throw new GaiaApiError(503, 'This fake client was given no model status.');
            return data.localModel;
        },
        async restartLocalModel(confirm) {
            restarts.push({ confirm });
            return { status: 'restarting', model: data.localModel?.model ?? '', starting_server: false };
        },
        async getGpuMemory() {
            if (!data.gpuMemory) throw new GaiaApiError(503, 'This fake client was given no GPU memory status.');
            if (data.gpuMemory instanceof Error) throw data.gpuMemory;
            return data.gpuMemory;
        },
        async recordGpuMemoryPending() {
            if (!data.gpuMemory || data.gpuMemory instanceof Error) throw new GaiaApiError(503, 'This fake client was given no GPU memory status.');
            return data.gpuMemory;
        },
        async dismissGpuMemoryPending() {
            if (!data.gpuMemory || data.gpuMemory instanceof Error) throw new GaiaApiError(503, 'This fake client was given no GPU memory status.');
            return data.gpuMemory;
        },
        async getModelNeed(job) {
            return data.modelNeeds?.[job] ?? { job, needed: false, model: null, disk_free_gb: null };
        },
        async downloadSetupModel(modelId, acknowledgeLowDisk) {
            downloads.push({ modelId, acknowledgeLowDisk });
            return data.setupStatus ?? { state: 'running' };
        },
        async removeSetupModel(modelId) {
            removals.push(modelId);
            return { ...(data.setupPlan as SetupPlan), freed_gb: 0 };
        },
        async getSetupStatus() {
            if (data.setupStatuses?.length) return nextOf(data.setupStatuses, statusAt++);
            return data.setupStatus ?? { state: 'idle' };
        },
        async cancelSetup() {
            return { cancelled: true };
        },
        async getStartScreen() {
            if (!data.start) throw new GaiaApiError(503, 'This fake client was given no start screen.');
            return data.start;
        },
        async checkSetup() {
            if (!data.setupChecks?.length) throw new GaiaApiError(503, 'This fake client was given no setup checks.');
            return nextOf(data.setupChecks, checkAt++);
        },
        async listProviderSummaries() {
            if (!data.providers) throw new GaiaApiError(503, 'This fake client was given no providers.');
            return data.providers;
        },
        async getModelServer() {
            if (!data.modelServer) throw new GaiaApiError(503, 'This fake client was given no model server.');
            return data.modelServer;
        },
        async setModelServerBackend(backend) {
            if (data.serverChangeError) throw data.serverChangeError;
            backendSets.push(backend);
            return { ...(data.modelServer as ModelServerInfo), preferred_backend: backend };
        },
        async getPreflight() {
            if (!data.preflight) throw new GaiaApiError(503, 'This fake client was given no preflight.');
            if (data.preflight instanceof Error) throw data.preflight;
            return data.preflight;
        },
        async getLemonadeTarget() {
            if (!data.lemonadeTarget) throw new GaiaApiError(503, 'This fake client was given no model server target.');
            if (data.lemonadeTarget instanceof Error) throw data.lemonadeTarget;
            return data.lemonadeTarget;
        },
        async setLemonadeTarget(body) {
            if (data.serverChangeError) throw data.serverChangeError;
            if (!data.lemonadeTarget || data.lemonadeTarget instanceof Error) throw new GaiaApiError(503, 'This fake client was given no model server target.');
            targetSets.push(body);
            data.lemonadeTarget = { ...data.lemonadeTarget, mode: body.mode, base_url: body.base_url ?? data.lemonadeTarget.base_url, saved_base_url: body.base_url ?? data.lemonadeTarget.saved_base_url };
            return { ...data.lemonadeTarget, daemon: null };
        },
        async getMcpStatus() {
            return { servers: [...(data.mcpServers ?? [])] };
        },
        async detectLemonade() {
            if (!data.lemonadeDetect) return { found: null };
            if (data.lemonadeDetect instanceof Error) throw data.lemonadeDetect;
            return data.lemonadeDetect;
        },
        async connectProvider(provider) {
            connects.push(provider);
            return { remembered: true };
        },
        async retryKeyStore() {
            keyStoreRetries += 1;
            return { retrying: true };
        },
        async recheckSetupNpu() {
            npuRechecks += 1;
            if (data.npuRecheck instanceof Error) throw data.npuRecheck;
            if (!data.npuRecheck) throw new GaiaApiError(503, 'This fake client was given no NPU recheck plan.');
            return data.npuRecheck;
        },
        async getSetupPlan() {
            if (!data.setupPlan) throw new GaiaApiError(503, 'This fake client was given no setup plan.');
            return data.setupPlan;
        },
        async chooseSetupChatModel(modelId, acknowledgeNoFit) {
            setupChoices.push({ kind: 'chat', modelId, flag: acknowledgeNoFit });
            return data.setupPlan as SetupPlan;
        },
        async chooseSetupVisionModel(modelId) {
            setupChoices.push({ kind: 'vision', modelId });
            return data.setupPlan as SetupPlan;
        },
        async skipSetupModel(modelId, skipped) {
            setupChoices.push({ kind: 'skip', modelId, flag: skipped });
            return data.setupPlan as SetupPlan;
        },
        async startSetup(skipChatModel, gaiaLemonade = false, options = {}) {
            setupStarts.push({ skipChatModel, gaiaLemonade, upgradeEmbedded: !!options.upgradeEmbedded, serverOnly: !!options.serverOnly });
            return { state: 'running', upgrade_embedded: !!options.upgradeEmbedded };
        },
    };
}
