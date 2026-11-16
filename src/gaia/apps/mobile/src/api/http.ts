// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Message, ResourceUsage, Session, StartScreen, StreamEvent, SystemStatus } from '@webui/types';
import { fromApiMessages } from '@webui/utils/chatStream';
import { isMediaFile } from '@webui/utils/mediaFiles';

import type { FetchLike, FetchRequestInit, FetchResponseLike } from '@/platform/fetch.types';
import { formFile } from '@/platform/formFile';

import {
    GaiaApiError,
    GaiaAuthError,
    GaiaConfigError,
    GaiaError,
    GaiaNetworkError,
    GaiaPinMismatchError,
    GaiaStreamDroppedError,
    GaiaStreamError,
    GaiaStreamTimeoutError,
    parseErrorBody,
    parseErrorDetail,
    type StreamEndpoint,
} from './errors';
import type {
    ActiveModel,
    ActiveRuns,
    AgentCapabilities,
    AgentInstallStatus,
    AgentList,
    AgentMcpList,
    CancelChatResult,
    CancelIndexingResult,
    ChatPerformance,
    CloudPriceList,
    ConfirmToolResult,
    ContextChoice,
    ConnectorActivations,
    AllPermissions,
    ConnectorGrants,
    ConnectorList,
    DisconnectOutcome,
    ContextUsageResult,
    CustomModelRemoved,
    CustomModelReport,
    ConsolidateMemoryResult,
    DeleteDocumentResult,
    DeleteMessagesResult,
    DeleteScheduleResult,
    Goal,
    GoalStats,
    BrowseResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatus,
    EmbeddingCoverage,
    FilePreviewResponse,
    FileSearchResponse,
    FileStatusList,
    FollowUpOutcome,
    FollowUpResult,
    DeleteSessionResult,
    DiscordStatus,
    FeaturedLibraries,
    GaiaClient,
    GpuMemoryStatus,
    HealthResponse,
    LemonadeFound,
    LemonadeTarget,
    LinkPreview,
    LocalModelStatus,
    KnowledgeSaveResult,
    MemoryActivityDay,
    MemoryCleared,
    MemoryConversation,
    MemoryEntity,
    MemoryKnowledgePage,
    MemoryReinitializeResult,
    MemorySettings,
    MemorySettingsUpdateResponse,
    MemoryStats,
    PcAudioBytes,
    MemoryToolStat,
    MCPServerStatus,
    MemoryTurn,
    MemoryUpcomingItem,
    ProfileStreamEvent,
    MessageList,
    ModelNeed,
    ModelServerInfo,
    PreflightReport,
    ProviderConnected,
    ProviderSummaryList,
    RetryChoice,
    RetryResult,
    RestartModelResult,
    RequestOptions,
    ParsedSchedule,
    PermissionMode,
    ProviderModelsResponse,
    RebuildEmbeddingsResult,
    ReconcileMemoryResult,
    RefreshSystemContextResult,
    Schedule,
    ScheduleList,
    ScheduleResults,
    SessionChanges,
    SessionEnvironment,
    SessionGit,
    SessionList,
    SessionOutputs,
    SessionPermissions,
    SessionSearchResponse,
    SetupCheck,
    SetupPlan,
    SetupRunStatus,
    Settings,
    SkillList,
    SpeechModel,
    SpeechSettings,
    SpendingReport,
    ChatSpending,
    FileDiff,
    TurnChanges,
    UndoAllResult,
    UndoResult,
    UndoSettings,
    UploadedFile,
    UserInputResult,
} from './client.types';
import {
    agentInstallStatusPath,
    ATTACH_HEADER_TIMEOUT_MS,
    connectorActivationPath,
    connectorActivationsPath,
    connectorDisablePath,
    connectorGrantPath,
    connectorGrantsPath,
    CONNECTOR_REVOKE_OUTCOME_MS,
    connectorPath,
    chatSpendingPath,
    cloudPricesPath,
    documentFilePath,
    documentUploadPathPath,
    filesBrowsePath,
    filesSearchPath,
    documentPath,
    goalPath,
    ENDPOINTS,
    filePath,
    generatedAudioPath,
    GENERATED_AUDIO_TYPES,
    chatContextPath,
    chatPermissionGrantsPath,
    chatPerformancePath,
    chatPermissionsPath,
    providerConnectPath,
    localCustomModelPath,
    providerModelsPath,
    followupOutcomePath,
    MAX_GENERATED_AUDIO_BYTES,
    MESSAGE_PAGE_SIZE,
    QUEUED_IDLE_MS,
    memoryActivityPath,
    memoryConversationPath,
    memoryConversationsPath,
    memoryConversationsSearchPath,
    memoryKnowledgeItemPath,
    memoryKnowledgePath,
    memoryStreamInferencePath,
    memoryUpcomingPath,
    PROFILE_STREAM_IDLE_MS,
    schedulePath,
    scheduleResultsPath,
    scheduleRunPath,
    SEND_HEADER_TIMEOUT_MS,
    SESSION_EVENTS_IDLE_MS,
    SESSION_PAGE_SIZE,
    sessionCapabilitiesPath,
    sessionOutputsPath,
    sessionDocumentPath,
    sessionDocumentsPath,
    sessionEnvironmentPath,
    sessionMessagePath,
    sessionMessagesAndBelowPath,
    sessionMessagesPath,
    sessionPath,
    sessionChangesDiffPath,
    sessionChangesPartPath,
    sessionChangesPath,
    sessionChangesUndoPath,
    sessionExportPath,
    sessionPinPath,
    sessionPrivatePath,
    sessionReadPath,
    setupNeedsPath,
    STREAM_IDLE_MS,
    UI_HEADERS,
} from './constants';
import { parseSseStream } from './sse';

/** A body sent as it is, not as JSON: a file, or a multipart form. */
class RawBody {
    constructor(readonly body: Blob | FormData) {}
}

export interface HttpClientConfig {
    baseUrl: string;
    token?: string | null;
    fetch: FetchLike;
}

interface HeaderLimit {
    endpoint: StreamEndpoint;
    ms: number;
    abort: () => void;
}

/** The address without trailing slashes; GaiaConfigError when it isn't an http(s) URL. */
export function normalizeBaseUrl(baseUrl: string): string {
    let url: URL;
    try {
        url = new URL(baseUrl);
    } catch (cause) {
        throw new GaiaConfigError('mobileApp.error.badAddress', { address: baseUrl }, { cause });
    }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
        throw new GaiaConfigError('mobileApp.error.badScheme', { address: baseUrl });
    }
    return baseUrl.replace(/\/+$/, '');
}

// A non-recoverable agent_error ends the turn; the closing `done` may never come.
function endsTurn(event: StreamEvent): boolean {
    return event.type === 'done' || event.type === 'error' || (event.type === 'agent_error' && event.recoverable !== true);
}

// A turn waiting in line for the model only speaks when its place changes.
function idleLimit(last: StreamEvent | undefined): number {
    return last?.type === 'status' && last.phase === 'queued' ? QUEUED_IDLE_MS : STREAM_IDLE_MS;
}

function within<T>(limit: HeaderLimit, pending: Promise<T>): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const expired = new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
            limit.abort();
            reject(new GaiaStreamTimeoutError(limit.endpoint, limit.ms));
        }, limit.ms);
    });
    return Promise.race([pending, expired]).finally(() => clearTimeout(timer));
}

export function createHttpClient(config: HttpClientConfig): GaiaClient {
    const base = normalizeBaseUrl(config.baseUrl);
    const { token, fetch } = config;

    function headers(hasBody: boolean): Record<string, string> {
        return {
            ...UI_HEADERS,
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            ...(hasBody ? { 'Content-Type': 'application/json' } : {}),
        };
    }

    async function request(
        method: string,
        path: string,
        body: unknown,
        signal?: AbortSignal,
        headerLimit?: HeaderLimit,
    ): Promise<FetchResponseLike> {
        const url = `${base}${path}`;
        const init: FetchRequestInit = {
            method,
            headers: headers(body !== undefined && !(body instanceof RawBody)),
            ...(body === undefined ? {} : { body: body instanceof RawBody ? body.body : JSON.stringify(body) }),
            ...(signal ? { signal } : {}),
        };
        let res: FetchResponseLike;
        try {
            const pending = fetch(url, init);
            res = await (headerLimit ? within(headerLimit, pending) : pending);
        } catch (err) {
            if (err instanceof GaiaStreamTimeoutError || err instanceof GaiaPinMismatchError || signal?.aborted) throw err;
            throw new GaiaNetworkError(url, err);
        }
        if (!res.ok) {
            const errorText = await res.text().catch(() => '');
            const detail = parseErrorDetail(errorText);
            throw res.status === 401 ? new GaiaAuthError(detail) : new GaiaApiError(res.status, detail, undefined, undefined, parseErrorBody(errorText));
        }
        return res;
    }

    /** The PC's connector events, or null when the stream can't be opened. */
    async function openConnectorEvents(signal: AbortSignal): Promise<AsyncGenerator<StreamEvent> | null> {
        try {
            const res = await request('GET', ENDPOINTS.connectorsEvents, undefined, signal);
            return res.body ? parseSseStream(res.body) : null;
        } catch {
            return null;
        }
    }

    /** The disconnect event's payload for this connector, or null when none arrives in time. */
    async function readDisconnectOutcome(events: AsyncGenerator<StreamEvent>, connectorId: string): Promise<DisconnectOutcome | null> {
        const read = (async () => {
            for await (const event of events) {
                const { type, payload } = event as unknown as { type: string; payload?: Record<string, unknown> };
                if (type === 'connector.disconnected' && payload?.connector_id === connectorId) return payload as DisconnectOutcome;
            }
            return null;
        })().catch(() => null);
        let timer: ReturnType<typeof setTimeout> | undefined;
        const late = new Promise<null>((resolve) => {
            timer = setTimeout(() => resolve(null), CONNECTOR_REVOKE_OUTCOME_MS);
        });
        try {
            return await Promise.race([read, late]);
        } finally {
            clearTimeout(timer);
        }
    }

    async function readJson<T>(res: FetchResponseLike, path: string): Promise<T> {
        const text = await res.text();
        try {
            return JSON.parse(text) as T;
        } catch {
            throw new GaiaApiError(res.status, text.slice(0, 200), 'mobileApp.error.notJson', { path });
        }
    }

    async function drainBytes(res: FetchResponseLike, limit?: { bytes: number; tooLarge: () => GaiaError }): Promise<Uint8Array<ArrayBuffer>> {
        if (!res.body) throw new GaiaStreamError('mobileApp.error.noBody');
        const reader = res.body.getReader();
        const chunks: Uint8Array[] = [];
        let size = 0;
        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            chunks.push(value);
            size += value.length;
            if (limit && size > limit.bytes) {
                await reader.cancel();
                throw limit.tooLarge();
            }
        }
        const out = new Uint8Array(size);
        let at = 0;
        for (const chunk of chunks) {
            out.set(chunk, at);
            at += chunk.length;
        }
        return out;
    }

    async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
        return readJson<T>(await request('GET', path, undefined, signal), path);
    }

    async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
        return readJson<T>(await request('POST', path, body, signal), path);
    }

    async function patchJson<T>(path: string, signal?: AbortSignal): Promise<T> {
        return readJson<T>(await request('PATCH', path, undefined, signal), path);
    }

    async function deleteJson<T>(path: string, signal?: AbortSignal): Promise<T> {
        return readJson<T>(await request('DELETE', path, undefined, signal), path);
    }

    async function putJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
        return readJson<T>(await request('PUT', path, body, signal), path);
    }

    // The whole body, drained from the stream: a file on the PC is read by this client only, never by a URL handed to the system.
    async function readBytes(path: string, signal?: AbortSignal): Promise<Uint8Array> {
        return drainBytes(await request('GET', path, undefined, signal));
    }

    async function* streamTurn(
        method: 'GET' | 'POST',
        path: string,
        body: unknown,
        endpoint: StreamEndpoint,
        headerMs: number,
        signal?: AbortSignal,
    ): AsyncGenerator<StreamEvent> {
        // One controller drops the connection whether the caller left, the headers or the stream timed out.
        const connection = new AbortController();
        const drop = () => connection.abort();
        if (signal?.aborted) drop();
        else signal?.addEventListener('abort', drop);
        let ended = false;
        try {
            let res: FetchResponseLike;
            try {
                res = await request(method, path, body, connection.signal, { endpoint, ms: headerMs, abort: drop });
            } catch (err) {
                // No turn is running: its answer, if it had one, is already saved.
                if (endpoint === 'attach' && err instanceof GaiaApiError && err.status === 404) return;
                throw err;
            }
            if (!res.body) {
                throw new GaiaStreamError('mobileApp.error.noBody');
            }
            try {
                for await (const event of parseSseStream(res.body, idleLimit)) {
                    if (endsTurn(event)) ended = true;
                    yield event;
                }
            } catch (err) {
                if (signal?.aborted) throw err;
                // The turn already ended; a connection that dies or goes quiet after that changes nothing.
                if (ended) return;
                if (err instanceof GaiaStreamError) throw err;
                throw new GaiaStreamDroppedError(err);
            }
            if (!ended) {
                throw new GaiaStreamError('mobileApp.error.streamClosed');
            }
        } finally {
            signal?.removeEventListener('abort', drop);
            // Closes a connection left open; after a body read to its end this does nothing.
            drop();
        }
    }

    /** A profile-setup stream, to its `done` event; one that closes before it is an error, as the web's onerror treats it. */
    async function* streamProfile(path: string, signal?: AbortSignal): AsyncGenerator<ProfileStreamEvent> {
        const connection = new AbortController();
        const drop = () => connection.abort();
        if (signal?.aborted) drop();
        else signal?.addEventListener('abort', drop);
        try {
            const res = await request('GET', path, undefined, connection.signal, { endpoint: 'attach', ms: ATTACH_HEADER_TIMEOUT_MS, abort: drop });
            if (!res.body) throw new GaiaStreamError('mobileApp.error.noBody');
            for await (const event of parseSseStream(res.body, () => PROFILE_STREAM_IDLE_MS)) {
                yield event as unknown as ProfileStreamEvent;
                if (event.type === 'done') return;
            }
            throw new GaiaStreamError('mobileApp.error.streamClosed');
        } finally {
            signal?.removeEventListener('abort', drop);
            drop();
        }
    }

    return {
        health(options?: RequestOptions) {
            return getJson<HealthResponse>(ENDPOINTS.health, options?.signal);
        },

        async listSessions(options?: RequestOptions): Promise<SessionList> {
            const byId = new Map<string, Session>();
            let total = 0;
            for (let offset = 0; ; offset += SESSION_PAGE_SIZE) {
                const page = await getJson<SessionList>(
                    `${ENDPOINTS.sessions}?limit=${SESSION_PAGE_SIZE}&offset=${offset}`,
                    options?.signal,
                );
                total = page.total;
                // A chat updated between two page reads can shift into both.
                for (const s of page.sessions) if (!byId.has(s.id)) byId.set(s.id, s);
                if (page.sessions.length < SESSION_PAGE_SIZE || offset + SESSION_PAGE_SIZE >= total) break;
            }
            return { sessions: [...byId.values()], total };
        },

        getSession(sessionId, options) {
            return getJson<Session>(sessionPath(sessionId), options?.signal);
        },

        createSession(request = {}, options) {
            return postJson<Session>(ENDPOINTS.sessions, request, options?.signal);
        },

        async uploadFile(file, name, options) {
            const media = isMediaFile(name);
            const path = media ? `${ENDPOINTS.filesUploadMedia}?name=${encodeURIComponent(name)}` : ENDPOINTS.filesUpload;
            let body: Blob | FormData = file;
            if (!media) {
                // A file on this phone keeps the name it was picked with, not its cache path's.
                const uri = (file as { uri?: unknown }).uri;
                body = new FormData();
                body.append('file', typeof uri === 'string' ? await formFile(uri, name, file.type || null) : file, name);
            }
            return readJson<UploadedFile>(await request('POST', path, new RawBody(body), options?.signal), path);
        },

        exportSessionMarkdown(sessionId, options) {
            return getJson<{ content: string }>(`${sessionExportPath(sessionId)}?format=markdown`, options?.signal);
        },

        setSessionPinned(sessionId, pinned, options) {
            return putJson<Session>(sessionPinPath(sessionId), { pinned }, options?.signal);
        },

        listTurnChanges(sessionId, options) {
            return getJson<{ turns: TurnChanges[] }>(sessionChangesPath(sessionId), options?.signal);
        },

        getTurnDiff(sessionId, turnId, options) {
            return getJson<{ turn_id: string; files: FileDiff[] }>(sessionChangesDiffPath(sessionId, turnId), options?.signal);
        },

        undoTurn(sessionId, turnId, force = false, options) {
            return postJson<UndoResult>(sessionChangesUndoPath(sessionId, turnId), { force }, options?.signal);
        },

        getSessionChanges(sessionId, options) {
            return getJson<SessionChanges>(sessionChangesPartPath(sessionId, 'total'), options?.signal);
        },

        undoAllChanges(sessionId, force = false, options) {
            return postJson<UndoAllResult>(sessionChangesPartPath(sessionId, 'undoAll'), { force }, options?.signal);
        },

        getSessionGit(sessionId, options) {
            return getJson<SessionGit>(sessionChangesPartPath(sessionId, 'git'), options?.signal);
        },

        commitChanges(sessionId, root, message, options) {
            return postJson<{ root: string; commit: string; files: string[]; ignored: string[] }>(
                sessionChangesPartPath(sessionId, 'gitCommit'),
                { root, message, confirmed: true },
                options?.signal,
            );
        },

        createPullRequest(sessionId, root, title, body, options) {
            return postJson<{ root: string; url: string }>(sessionChangesPartPath(sessionId, 'gitPr'), { root, title, body, confirmed: true }, options?.signal);
        },

        deleteSession(sessionId, options) {
            return deleteJson<DeleteSessionResult>(sessionPath(sessionId), options?.signal);
        },

        markSessionRead(sessionId, options) {
            return postJson<Session>(sessionReadPath(sessionId), undefined, options?.signal);
        },

        getSettings(options) {
            return getJson<Settings>(ENDPOINTS.settings, options?.signal);
        },

        updateSettings(patch, options) {
            return putJson<Settings>(ENDPOINTS.settings, patch, options?.signal);
        },

        setNetworkPolicy(policy, options) {
            return putJson<Settings>(ENDPOINTS.settings, { network_policy: policy }, options?.signal);
        },

        getDiscordStatus(options) {
            return getJson<DiscordStatus>(ENDPOINTS.messagingDiscord, options?.signal);
        },

        getUndoSettings(options) {
            return getJson<UndoSettings>(ENDPOINTS.settingsUndo, options?.signal);
        },

        updateUndoSettings(retentionDays, maxMb, options) {
            return putJson<UndoSettings>(ENDPOINTS.settingsUndo, { retention_days: retentionDays, max_mb: maxMb }, options?.signal);
        },

        getContextChoice(model, options) {
            return getJson<ContextChoice>(`${ENDPOINTS.localModelContext}?model=${encodeURIComponent(model)}`, options?.signal);
        },

        setContextChoice(model, ctxSize, options) {
            return putJson<ContextChoice>(ENDPOINTS.localModelContext, { model, ctx_size: ctxSize }, options?.signal);
        },

        getCloudPrices(provider, options) {
            return getJson<CloudPriceList>(cloudPricesPath(provider), options?.signal);
        },

        getSpending(options) {
            return getJson<SpendingReport>(ENDPOINTS.spending, options?.signal);
        },

        getChatSpending(sessionId, since, model, options) {
            return getJson<ChatSpending>(chatSpendingPath(sessionId, since, model), options?.signal);
        },

        setSpendingLimit(limitUsd, options) {
            return putJson<SpendingReport>(ENDPOINTS.spendingLimit, { limit_usd: limitUsd }, options?.signal);
        },

        allowSpendingThisMonth(options) {
            return postJson<SpendingReport>(ENDPOINTS.spendingAllowMonth, undefined, options?.signal);
        },

        getActiveModel(options) {
            return getJson<ActiveModel>(ENDPOINTS.providersActive, options?.signal);
        },

        listProviderModels(provider, options) {
            return getJson<ProviderModelsResponse>(providerModelsPath(provider), options?.signal);
        },

        checkCustomModel(url, quant, options) {
            return postJson<CustomModelReport>(ENDPOINTS.localCustomModelCheck, { url, quant: quant || null }, options?.signal);
        },

        addCustomModel(url, quant, options) {
            return postJson<{ report: CustomModelReport }>(ENDPOINTS.localCustomModel, { url, quant: quant || null }, options?.signal);
        },

        removeCustomModel(model, options) {
            return deleteJson<CustomModelRemoved>(localCustomModelPath(model), options?.signal);
        },

        selectModel(model, options) {
            return postJson<ActiveModel>(ENDPOINTS.providersSelect, { model }, options?.signal);
        },

        getPermissions(sessionId, options) {
            return getJson<SessionPermissions>(chatPermissionsPath(sessionId), options?.signal);
        },

        listAllPermissions(options) {
            return getJson<AllPermissions>(ENDPOINTS.chatPermissionsDefault, options?.signal);
        },

        setDefaultPermissionMode(mode, options) {
            return putJson<{ default_mode: PermissionMode }>(ENDPOINTS.chatPermissionsDefault, { mode }, options?.signal);
        },

        revokeGrants(sessionId, key, options) {
            return deleteJson<SessionPermissions>(chatPermissionGrantsPath(sessionId, key), options?.signal);
        },

        setPermissionMode(sessionId, mode, options) {
            return putJson<SessionPermissions>(chatPermissionsPath(sessionId), { mode }, options?.signal);
        },

        getContextUsage(sessionId, options) {
            return getJson<ContextUsageResult>(chatContextPath(sessionId), options?.signal);
        },

        retryWith(sessionId, messageId, model, options) {
            return postJson<RetryResult>(ENDPOINTS.chatRetryWith, { session_id: sessionId, message_id: messageId, model }, options?.signal);
        },

        chooseRetry(sessionId, messageId, keep, options) {
            return postJson<RetryChoice>(ENDPOINTS.chatRetryWithChoose, { session_id: sessionId, message_id: messageId, keep }, options?.signal);
        },

        getChatPerformance(sessionId, options) {
            return getJson<ChatPerformance>(chatPerformancePath(sessionId), options?.signal);
        },

        getFollowUpOutcome(sessionId, options) {
            return getJson<FollowUpOutcome>(followupOutcomePath(sessionId), options?.signal);
        },

        searchSessions(query: string, includeAutomated: boolean, options?: RequestOptions): Promise<SessionSearchResponse> {
            const params = new URLSearchParams({ q: query, include_automated: String(includeAutomated) });
            return getJson<SessionSearchResponse>(`${ENDPOINTS.sessionSearch}?${params}`, options?.signal);
        },

        async getMessages(sessionId: string, options?: RequestOptions): Promise<MessageList> {
            const path = sessionMessagesPath(sessionId);
            const first = await getJson<MessageList>(`${path}?limit=${MESSAGE_PAGE_SIZE}&offset=0`, options?.signal);
            const messages: Message[] = [...first.messages];
            // Bounded to the first page's total: a running agent may keep appending.
            while (messages.length < first.total) {
                const limit = Math.min(MESSAGE_PAGE_SIZE, first.total - messages.length);
                const page = await getJson<MessageList>(`${path}?limit=${limit}&offset=${messages.length}`, options?.signal);
                if (page.messages.length === 0) {
                    throw new GaiaApiError(200, 'empty page', 'mobileApp.error.incompleteTranscript');
                }
                messages.push(...page.messages);
            }
            return { messages: fromApiMessages(messages), total: first.total };
        },

        sendFollowUp(sessionId, text, options) {
            return postJson<FollowUpResult>(ENDPOINTS.chatFollowup, { session_id: sessionId, text }, options?.signal);
        },

        updateSession(sessionId, update, options) {
            return putJson<Session>(sessionPath(sessionId), update, options?.signal);
        },

        toggleSessionPrivate(sessionId, options) {
            return patchJson<Session>(sessionPrivatePath(sessionId), options?.signal);
        },

        async detachDocument(sessionId, docId, options) {
            await request('DELETE', sessionDocumentPath(sessionId, docId), undefined, options?.signal);
        },

        getCapabilities(sessionId, options) {
            return getJson<AgentCapabilities>(sessionCapabilitiesPath(sessionId), options?.signal);
        },

        getOutputs(sessionId, options) {
            return getJson<SessionOutputs>(sessionOutputsPath(sessionId), options?.signal);
        },

        getEnvironment(sessionId, options) {
            return getJson<SessionEnvironment>(sessionEnvironmentPath(sessionId), options?.signal);
        },

        addEnvironmentFolder(sessionId, path, options) {
            return postJson<SessionEnvironment>(sessionEnvironmentPath(sessionId, 'folders'), { path }, options?.signal);
        },

        removeEnvironmentFolder(sessionId, path, options) {
            return postJson<SessionEnvironment>(sessionEnvironmentPath(sessionId, 'folderRemove'), { path }, options?.signal);
        },

        setInternet(sessionId, on, options) {
            return putJson<SessionEnvironment>(sessionEnvironmentPath(sessionId, 'internet'), { on }, options?.signal);
        },

        getResourceUsage(options) {
            return getJson<ResourceUsage>(ENDPOINTS.systemResources, options?.signal);
        },

        sendMessage(sessionId, message, options) {
            const body = { session_id: sessionId, message, stream: true };
            return streamTurn('POST', ENDPOINTS.chatSend, body, 'send', SEND_HEADER_TIMEOUT_MS, options?.signal);
        },

        attachToRun(sessionId, options) {
            const path = `${ENDPOINTS.chatAttach}?session_id=${encodeURIComponent(sessionId)}`;
            return streamTurn('GET', path, undefined, 'attach', ATTACH_HEADER_TIMEOUT_MS, options?.signal);
        },

        streamMemoryDiscovery(options) {
            return streamProfile(ENDPOINTS.memoryStreamDiscovery, options?.signal);
        },

        streamMemoryInference(includeBrowser, options) {
            return streamProfile(memoryStreamInferencePath(includeBrowser), options?.signal);
        },

        commitMemoryDiscovery(items, options) {
            return postJson<{ stored: number }>(ENDPOINTS.memoryCommitDiscovery, { items }, options?.signal);
        },

        commitMemoryInference(insights, options) {
            return postJson<{ stored: number }>(ENDPOINTS.memoryCommitInference, { insights }, options?.signal);
        },

        async *watchSessionEvents(options) {
            const connection = new AbortController();
            const drop = () => connection.abort();
            if (options?.signal?.aborted) drop();
            else options?.signal?.addEventListener('abort', drop);
            try {
                const res = await request('GET', ENDPOINTS.sessionEvents, undefined, connection.signal, {
                    endpoint: 'attach',
                    ms: ATTACH_HEADER_TIMEOUT_MS,
                    abort: drop,
                });
                if (!res.body) throw new GaiaStreamError('mobileApp.error.noBody');
                yield* parseSseStream(res.body, () => SESSION_EVENTS_IDLE_MS);
                throw new GaiaStreamError('mobileApp.error.streamClosed');
            } finally {
                options?.signal?.removeEventListener('abort', drop);
                drop();
            }
        },

        deleteMessage(sessionId, messageId, options) {
            return deleteJson<{ deleted: boolean }>(sessionMessagePath(sessionId, messageId), options?.signal);
        },

        deleteMessagesFrom(sessionId, messageId, options) {
            return deleteJson<DeleteMessagesResult>(sessionMessagesAndBelowPath(sessionId, messageId), options?.signal);
        },

        cancelChat(sessionId, options) {
            return postJson<CancelChatResult>(ENDPOINTS.chatCancel, { session_id: sessionId }, options?.signal);
        },

        confirmTool(request, options) {
            const body = {
                session_id: request.session_id,
                approved: request.approved,
                always: request.always ?? false,
                ...(request.confirm_id ? { confirm_id: request.confirm_id } : {}),
            };
            return postJson<ConfirmToolResult>(ENDPOINTS.chatConfirmTool, body, options?.signal);
        },

        sendUserInput(request, options) {
            const body = { session_id: request.session_id, request_id: request.request_id, value: request.value };
            return postJson<UserInputResult>(ENDPOINTS.chatUserInput, body, options?.signal);
        },

        listAgents(options) {
            return getJson<AgentList>(ENDPOINTS.agents, options?.signal);
        },

        listConnectors(options) {
            return getJson<ConnectorList>(ENDPOINTS.connectors, options?.signal);
        },

        listConnectorGrants(connectorId, options) {
            return getJson<ConnectorGrants>(connectorGrantsPath(connectorId), options?.signal);
        },

        listConnectorActivations(connectorId, options) {
            return getJson<ConnectorActivations>(connectorActivationsPath(connectorId), options?.signal);
        },

        async grantConnectorAgent(connectorId, agentId, scopes, options) {
            await request('PUT', connectorGrantPath(connectorId, agentId), { scopes }, options?.signal);
        },

        async revokeConnectorAgentGrant(connectorId, agentId, options) {
            await request('DELETE', connectorGrantPath(connectorId, agentId), undefined, options?.signal);
        },

        async activateConnectorAgent(connectorId, agentId, scopes, options) {
            await request('PUT', connectorActivationPath(connectorId, agentId), scopes ? { scopes } : {}, options?.signal);
        },

        async deactivateConnectorAgent(connectorId, agentId, options) {
            await request('DELETE', connectorActivationPath(connectorId, agentId), undefined, options?.signal);
        },

        async disableConnector(connectorId, options) {
            await request('POST', connectorDisablePath(connectorId), {}, options?.signal);
        },

        async disconnectConnector(connectorId, options) {
            // The revoke outcome only travels on the events stream, so listen before deleting.
            const listen = new AbortController();
            const stop = () => listen.abort();
            options?.signal?.addEventListener('abort', stop);
            try {
                const events = await openConnectorEvents(listen.signal);
                await request('DELETE', connectorPath(connectorId), undefined, options?.signal);
                return events ? await readDisconnectOutcome(events, connectorId) : null;
            } finally {
                options?.signal?.removeEventListener('abort', stop);
                stop();
            }
        },

        listAgentMcps(options) {
            return getJson<AgentMcpList>(ENDPOINTS.connectorsAgentMcps, options?.signal);
        },

        installAgent(id, options) {
            return postJson<{ id: string; status: string }>(ENDPOINTS.agentsInstall, { id }, options?.signal);
        },

        getAgentInstallStatus(id, options) {
            return getJson<AgentInstallStatus>(agentInstallStatusPath(id), options?.signal);
        },

        listSkills(options) {
            return getJson<SkillList>(ENDPOINTS.skills, options?.signal);
        },

        listFeaturedSkillLibraries(options) {
            return getJson<FeaturedLibraries>(ENDPOINTS.skillsFeatured, options?.signal);
        },

        previewSkillLink(url, options) {
            return postJson<LinkPreview>(ENDPOINTS.skillsLinkPreview, { url }, options?.signal);
        },

        getActiveRuns(options) {
            return getJson<ActiveRuns>(ENDPOINTS.chatActive, options?.signal);
        },

        listSchedules(options) {
            return getJson<ScheduleList>(ENDPOINTS.schedules, options?.signal);
        },

        createSchedule(request, options) {
            return postJson<Schedule>(ENDPOINTS.schedules, request, options?.signal);
        },

        parseSchedule(input, timezone, options) {
            return postJson<ParsedSchedule>(ENDPOINTS.scheduleParse, { input, timezone }, options?.signal);
        },

        updateScheduleStatus(name, status, options) {
            return putJson<Schedule>(schedulePath(name), { status }, options?.signal);
        },

        runSchedule(name, options) {
            return postJson<Schedule>(scheduleRunPath(name), undefined, options?.signal);
        },

        listScheduleResults(name, limit, options) {
            return getJson<ScheduleResults>(scheduleResultsPath(name, limit), options?.signal);
        },

        getMemoryActivity(days, options) {
            return getJson<MemoryActivityDay[]>(memoryActivityPath(days), options?.signal);
        },

        getMemoryTools(options) {
            return getJson<MemoryToolStat[]>(ENDPOINTS.memoryTools, options?.signal);
        },

        listMemoryEntities(options) {
            return getJson<MemoryEntity[]>(ENDPOINTS.memoryEntities, options?.signal);
        },

        listMemoryConversations(limit, options) {
            return getJson<MemoryConversation[]>(memoryConversationsPath(limit), options?.signal);
        },

        searchMemoryConversations(query, limit, options) {
            return getJson<MemoryTurn[]>(memoryConversationsSearchPath(query, limit), options?.signal);
        },

        getMemoryConversation(sessionId, options) {
            return getJson<MemoryTurn[]>(memoryConversationPath(sessionId), options?.signal);
        },

        getMemoryUpcoming(days, options) {
            return getJson<MemoryUpcomingItem[]>(memoryUpcomingPath(days), options?.signal);
        },

        listMemoryKnowledge(params, options) {
            return getJson<MemoryKnowledgePage>(memoryKnowledgePath(params), options?.signal);
        },

        addMemoryKnowledge(body, options) {
            return postJson<KnowledgeSaveResult>(ENDPOINTS.memoryKnowledge, body, options?.signal);
        },

        updateMemoryKnowledge(knowledgeId, body, options) {
            return putJson<KnowledgeSaveResult>(memoryKnowledgeItemPath(knowledgeId), body, options?.signal);
        },

        deleteMemoryKnowledge(knowledgeId, options) {
            return deleteJson<unknown>(memoryKnowledgeItemPath(knowledgeId), options?.signal);
        },

        listGoals(options) {
            return getJson<{ goals: Goal[]; total: number }>(ENDPOINTS.goals, options?.signal);
        },

        getGoalStats(options) {
            return getJson<GoalStats>(ENDPOINTS.goalsStats, options?.signal);
        },

        getGoalStoreHealth(options) {
            return getJson<{ error: string | null }>(ENDPOINTS.goalsHealth, options?.signal);
        },

        createGoal(body, options) {
            return postJson<Goal>(ENDPOINTS.goals, body, options?.signal);
        },

        approveGoal(goalId, options) {
            return putJson<Goal>(goalPath(goalId, 'approve'), undefined, options?.signal);
        },

        rejectGoal(goalId, options) {
            return putJson<Goal>(goalPath(goalId, 'reject'), undefined, options?.signal);
        },

        cancelGoal(goalId, options) {
            return putJson<Goal>(goalPath(goalId, 'cancel'), undefined, options?.signal);
        },

        async deleteGoal(goalId, options) {
            // The PC answers 204 with no body.
            await request('DELETE', goalPath(goalId), undefined, options?.signal);
        },

        getMemoryStats(options) {
            return getJson<MemoryStats>(ENDPOINTS.memoryStats, options?.signal);
        },

        getSpeechSettings(options) {
            return getJson<SpeechSettings>(ENDPOINTS.speechSettings, options?.signal);
        },

        updateSpeechSettings(change, options) {
            return putJson<SpeechSettings>(ENDPOINTS.speechSettings, change, options?.signal);
        },

        getSpeechModel(options) {
            return getJson<SpeechModel>(ENDPOINTS.speechModel, options?.signal);
        },

        async speak(text, voice, options): Promise<PcAudioBytes> {
            const res = await request('POST', ENDPOINTS.speech, { text, voice: voice?.voice, speed: voice?.speed }, options?.signal);
            return { bytes: await drainBytes(res), contentType: res.headers.get('content-type') ?? '' };
        },

        async getGeneratedAudio(src, options): Promise<PcAudioBytes> {
            const path = generatedAudioPath(src);
            if (path === null) throw new GaiaConfigError('render.invalidPayload', { render: 'audio' });
            const res = await request('GET', path, undefined, options?.signal);
            const contentType = (res.headers.get('content-type') ?? '').split(';')[0]!.trim().toLowerCase();
            if (!GENERATED_AUDIO_TYPES.includes(contentType)) {
                await res.body?.cancel();
                throw new GaiaStreamError('mobileApp.error.notAudio', { type: contentType || '-' });
            }
            const limit = { bytes: MAX_GENERATED_AUDIO_BYTES, tooLarge: () => new GaiaStreamError('mobileApp.error.audioTooLarge', { size: `${MAX_GENERATED_AUDIO_BYTES / (1024 * 1024)} MB` }) };
            return { bytes: await drainBytes(res, limit), contentType };
        },

        getMemorySettings(options) {
            return getJson<MemorySettings>(ENDPOINTS.memorySettings, options?.signal);
        },

        updateMemorySettings(settings, options) {
            return putJson<MemorySettingsUpdateResponse>(ENDPOINTS.memorySettings, settings, options?.signal);
        },

        clearAllMemory(options) {
            return deleteJson<MemoryCleared>(ENDPOINTS.memoryAll, options?.signal);
        },

        reinitializeMemory(options) {
            return postJson<MemoryReinitializeResult>(ENDPOINTS.memoryReinitialize, undefined, options?.signal);
        },

        getEmbeddingCoverage(options) {
            return getJson<EmbeddingCoverage>(ENDPOINTS.memoryEmbeddingCoverage, options?.signal);
        },

        consolidateMemory(options) {
            return postJson<ConsolidateMemoryResult>(ENDPOINTS.memoryConsolidate, undefined, options?.signal);
        },

        rebuildEmbeddings(options) {
            return postJson<RebuildEmbeddingsResult>(ENDPOINTS.memoryRebuildEmbeddings, undefined, options?.signal);
        },

        reconcileMemory(options) {
            return postJson<ReconcileMemoryResult>(ENDPOINTS.memoryReconcile, undefined, options?.signal);
        },

        rebuildMemoryFts(options) {
            return postJson<{ status: string }>(ENDPOINTS.memoryRebuildFts, undefined, options?.signal);
        },

        refreshSystemContext(options) {
            return postJson<RefreshSystemContextResult>(ENDPOINTS.memoryRefreshSystemContext, undefined, options?.signal);
        },

        deleteSchedule(name, options) {
            return deleteJson<DeleteScheduleResult>(schedulePath(name), options?.signal);
        },

        async uploadDocument(file, options) {
            // The multipart boundary header is the fetch's to set.
            const form = new FormData();
            form.append('file', await formFile(file.uri, file.name, file.mimeType), file.name);
            return readJson<DocumentResponse>(await request('POST', ENDPOINTS.documentUpload, new RawBody(form), options?.signal), ENDPOINTS.documentUpload);
        },

        uploadDocumentByPath(filepath, sessionId, options) {
            return postJson<DocumentResponse>(documentUploadPathPath(sessionId), { filepath }, options?.signal);
        },

        browseFiles(sessionId, path, options) {
            return getJson<BrowseResponse>(filesBrowsePath(sessionId, path), options?.signal);
        },

        searchFiles(sessionId, query, fileTypes, limit, options) {
            return getJson<FileSearchResponse>(filesSearchPath(sessionId, query, fileTypes, limit), options?.signal);
        },

        async attachDocument(sessionId, docId, options) {
            await postJson<unknown>(sessionDocumentsPath(sessionId), { document_id: docId }, options?.signal);
        },

        listDocuments(options) {
            return getJson<DocumentListResponse>(ENDPOINTS.documents, options?.signal);
        },

        cancelDocumentIndexing(docId, options) {
            return postJson<CancelIndexingResult>(documentPath(docId, 'documentCancel'), undefined, options?.signal);
        },

        deleteDocument(docId, options) {
            return deleteJson<DeleteDocumentResult>(documentPath(docId), options?.signal);
        },

        getFileStatus(paths, options) {
            return postJson<FileStatusList>(ENDPOINTS.filesStatus, { paths }, options?.signal);
        },

        getFilePreview(path, sessionId, options) {
            return getJson<FilePreviewResponse>(filePath('filesPreview', path, sessionId), options?.signal);
        },

        getImageBytes(path, sessionId, options) {
            return readBytes(filePath('filesImage', path, sessionId), options?.signal);
        },

        getDocumentBytes(docId, sessionId, options) {
            return readBytes(documentFilePath(docId, sessionId), options?.signal);
        },

        getDocumentStatus(docId, options) {
            return getJson<DocumentStatus>(documentPath(docId, 'documentStatus'), options?.signal);
        },

        reindexDocument(docId, options) {
            return postJson<DocumentResponse>(documentPath(docId, 'documentReindex'), undefined, options?.signal);
        },

        getSystemStatus(options) {
            return getJson<SystemStatus>(ENDPOINTS.systemStatus, options?.signal);
        },

        downloadModel(model, options) {
            return postJson<{ status: string; model: string }>(
                ENDPOINTS.systemDownloadModel,
                { model_name: model, force: options?.force ?? false, load_after: options?.loadAfter ?? false },
                options?.signal,
            );
        },

        loadModel(modelName, force, options) {
            return postJson<{ status: string; model: string; ctx_size: number }>(
                ENDPOINTS.systemLoadModel,
                { model_name: modelName, force },
                options?.signal,
            );
        },

        getLocalModelStatus(options) {
            return getJson<LocalModelStatus>(ENDPOINTS.localModelStatus, options?.signal);
        },

        restartLocalModel(confirm, options) {
            return postJson<RestartModelResult>(ENDPOINTS.localModelRestart, { confirm }, options?.signal);
        },

        getGpuMemory(settingGb, options) {
            const query = settingGb == null ? '' : `?setting_gb=${encodeURIComponent(String(settingGb))}`;
            return getJson<GpuMemoryStatus>(`${ENDPOINTS.gpuMemory}${query}`, options?.signal);
        },

        recordGpuMemoryPending(settingGb, options) {
            return postJson<GpuMemoryStatus>(ENDPOINTS.gpuMemoryPending, { setting_gb: settingGb }, options?.signal);
        },

        dismissGpuMemoryPending(options) {
            return deleteJson<GpuMemoryStatus>(ENDPOINTS.gpuMemoryPending, options?.signal);
        },

        getModelNeed(job, options) {
            return getJson<ModelNeed>(setupNeedsPath(job), options?.signal);
        },

        downloadSetupModel(modelId, acknowledgeLowDisk, options) {
            return postJson<SetupRunStatus>(
                ENDPOINTS.setupModelDownload,
                { model_id: modelId, acknowledge_low_disk: acknowledgeLowDisk, keep_trying: false },
                options?.signal,
            );
        },

        removeSetupModel(modelId, options) {
            return postJson<SetupPlan & { freed_gb: number }>(ENDPOINTS.setupModelRemove, { model_id: modelId }, options?.signal);
        },

        getSetupStatus(options) {
            return getJson<SetupRunStatus>(ENDPOINTS.setupStatus, options?.signal);
        },

        cancelSetup(modelId, options) {
            return postJson<{ cancelled: boolean }>(ENDPOINTS.setupCancel, { model_id: modelId ?? null }, options?.signal);
        },

        getStartScreen(options) {
            return getJson<StartScreen>(ENDPOINTS.onboardingStart, options?.signal);
        },

        checkSetup(skipChatModel, options) {
            return getJson<SetupCheck>(`${ENDPOINTS.setupCheck}?skip_chat_model=${skipChatModel}`, options?.signal);
        },

        listProviderSummaries(options) {
            return getJson<ProviderSummaryList>(ENDPOINTS.providersList, options?.signal);
        },

        getModelServer(options) {
            return getJson<ModelServerInfo>(ENDPOINTS.modelServer, options?.signal);
        },

        setModelServerBackend(backend, options) {
            return putJson<ModelServerInfo>(ENDPOINTS.modelServerBackend, { backend }, options?.signal);
        },

        getPreflight(options) {
            return getJson<PreflightReport>(ENDPOINTS.onboardingPreflight, options?.signal);
        },

        getLemonadeTarget(options) {
            return getJson<LemonadeTarget>(ENDPOINTS.lemonadeTarget, options?.signal);
        },

        setLemonadeTarget(body, options) {
            return postJson<LemonadeTarget & { daemon: unknown }>(ENDPOINTS.lemonadeTarget, body, options?.signal);
        },

        getMcpStatus(options) {
            return getJson<{ servers: MCPServerStatus[] }>(ENDPOINTS.mcpStatus, options?.signal);
        },

        detectLemonade(options) {
            return getJson<{ found: LemonadeFound | null; unchecked?: string[] }>(ENDPOINTS.lemonadeDetect, options?.signal);
        },

        connectProvider(provider, options) {
            // No key travels from the phone: a blank body keeps the key the PC already holds.
            return postJson<ProviderConnected>(providerConnectPath(provider), {}, options?.signal);
        },

        retryKeyStore(options) {
            return postJson<{ retrying: boolean }>(ENDPOINTS.providersKeyStoreRetry, undefined, options?.signal);
        },

        recheckSetupNpu(options) {
            return postJson<SetupPlan>(ENDPOINTS.setupNpuRecheck, undefined, options?.signal);
        },

        getSetupPlan(options) {
            return getJson<SetupPlan>(ENDPOINTS.setupPlan, options?.signal);
        },

        chooseSetupChatModel(modelId, acknowledgeNoFit, options) {
            return postJson<SetupPlan>(
                ENDPOINTS.setupChatModel,
                { model_id: modelId, acknowledge_no_fit: acknowledgeNoFit },
                options?.signal,
            );
        },

        chooseSetupVisionModel(modelId, options) {
            return postJson<SetupPlan>(ENDPOINTS.setupVisionModel, { model_id: modelId }, options?.signal);
        },

        skipSetupModel(modelId, skipped, options) {
            return postJson<SetupPlan>(ENDPOINTS.setupModelSkip, { model_id: modelId, skipped }, options?.signal);
        },

        startSetup(skipChatModel, gaiaLemonade = false, options = {}) {
            return postJson<SetupRunStatus>(
                ENDPOINTS.setupRun,
                {
                    skip_chat_model: skipChatModel,
                    gaia_lemonade: gaiaLemonade,
                    acknowledge_low_disk: !!options.acknowledgeLowDisk,
                    keep_trying: !!options.keepTrying,
                    extra_models: options.extraModels ?? [],
                    server_only: !!options.serverOnly,
                    upgrade_embedded: !!options.upgradeEmbedded,
                },
                options.signal,
            );
        },
    };
}
