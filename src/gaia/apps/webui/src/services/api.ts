// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** API client for GAIA Agent UI backend. */

import type { Session, Message, Document, SystemStatus, Settings, StreamEvent, TunnelStatus, Schedule, ScheduleResult, ParsedSchedule, BrowseResponse, IndexFolderResponse, MCPServerStatus, AgentInfo, SetupCheck, SetupRunStatus, ProvidersResponse, ProviderInfo, ProviderModelsResponse, ActiveModel, ContextChoice, LocalModelStatus, CloudPriceList, SpendingReport, ConnectProviderBody, SkillInfo, PreflightReport, WarmUpStatus, SetupPlan, StartScreen, LemonadeTarget, LemonadeFound, LemonadeTargetBody } from '../types';
import type { ModelServerInfo } from '../types/api.gen';
import type { FileDiff, TurnChanges, UndoResult, UndoSettings } from '../types/changes';
import { getApiBase } from '../utils/apiBase';
import { log } from '../utils/logger';

const API_BASE = getApiBase();

// -- Helpers -------------------------------------------------------------------

/**
 * Pull a human-readable message out of an error response body.
 *
 * FastAPI's `detail` is a plain string for `HTTPException`, but a *list of
 * error objects* for request-validation failures (422) — interpolating one of
 * those into a message renders "[object Object]" in the UI.
 */
export function parseErrorDetail(errorText: string): string {
    try {
        const parsed = JSON.parse(errorText).detail;
        if (Array.isArray(parsed)) {
            return parsed.map((e) => e?.msg ?? JSON.stringify(e)).join('; ') || errorText;
        }
        if (parsed && typeof parsed === 'object' && typeof parsed.message === 'string') return parsed.message;
        if (parsed && typeof parsed !== 'string') return JSON.stringify(parsed);
        return parsed || errorText;
    } catch {
        // Body isn't JSON (proxy HTML, empty response) — the raw text is the useful thing.
        return errorText;
    }
}

/** `detail.status` from a structured error body, e.g. `needs_key`; null otherwise. */
function parseErrorStatus(errorText: string): string | null {
    try {
        const status = JSON.parse(errorText).detail?.status;
        return typeof status === 'string' ? status : null;
    } catch {
        return null;
    }
}

/** A non-2xx answer: the message to show, the backend's own words, and its machine-readable reason. */
export class ApiError extends Error {
    readonly httpStatus: number;
    readonly detail: string;
    readonly reason: string | null;

    constructor(message: string, httpStatus: number, detail: string, reason: string | null) {
        super(message);
        this.name = 'ApiError';
        this.httpStatus = httpStatus;
        this.detail = detail;
        this.reason = reason;
    }
}

function getFriendlyError(status: number, detail: string): string {
    switch (status) {
        case 403: return detail || 'Access denied.';
        // Prefer the backend's detail for 404s — the previous canned string
        // ("The file or folder may have been moved or deleted.") was misleading
        // for upload/indexing flows where the real cause was a malformed
        // filepath, not a missing file. See issue #728.
        case 404: return detail || 'The requested item was not found.';
        case 413: return detail || 'File too large to process.';
        case 500: return 'Server error. Please try again.';
        case 502: return 'Service unavailable. Is the backend running?';
        case 503: return detail || 'Service unavailable.';
        default: return detail || `Request failed (HTTP ${status})`;
    }
}

/**
 * CSRF marker required by the backend on every mutating request.
 *
 * A cross-origin page cannot set a custom header without a CORS
 * preflight, and the backend approves preflights only from its own
 * origins — so this is what stops a page the user visits from driving
 * the Agent UI. Sent on reads too: a few read routes require it, and one
 * unconditional rule is one fewer thing to forget.
 */
export const UI_HEADER = { 'x-gaia-ui': '1', 'x-gaia-client': 'agent-ui' };

const SECRET_FIELD = /key|token|secret|password/i;

/** A copy of a request body safe to log: credential-looking fields are masked. */
export function redactSecrets(body: unknown): unknown {
    if (!body || typeof body !== 'object' || Array.isArray(body)) return body;
    return Object.fromEntries(
        Object.entries(body as Record<string, unknown>).map(([k, v]) =>
            [k, SECRET_FIELD.test(k) && typeof v === 'string' && v ? '***' : v]),
    );
}

/** Fetch wrapper with logging, timing, and error handling. */
export async function apiFetch<T>(
    method: string,
    path: string,
    body?: unknown,
    extraHeaders?: Record<string, string>,
    signal?: AbortSignal,
): Promise<T> {
    const url = `${API_BASE}${path}`;
    const t = log.api.time();

    log.api.info(`${method} ${url}`, body !== undefined ? { body: redactSecrets(body) } : '');

    const baseHeaders: Record<string, string> = body !== undefined
        ? { 'Content-Type': 'application/json' }
        : {};
    const init: RequestInit = {
        method,
        // UI_HEADER first so no caller can drop it; extraHeaders next so
        // Content-Type cannot be accidentally overridden for body requests.
        headers: { ...UI_HEADER, ...extraHeaders, ...baseHeaders },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal,
    };

    let res: Response;
    try {
        res = await fetch(url, init);
    } catch (err) {
        log.api.error(`${method} ${url} - network error`, err);
        throw err;
    }

    if (!res.ok) {
        const errorText = await res.text().catch(() => '');
        log.api.error(`${method} ${url} - HTTP ${res.status}`, { errorText });
        const detail = parseErrorDetail(errorText);
        throw new ApiError(getFriendlyError(res.status, detail), res.status, detail, parseErrorStatus(errorText));
    }

    // Some endpoints (DELETE, fire-and-forget POSTs) intentionally return no
    // body, so there is no JSON to parse. A non-JSON response *with* content,
    // however, means the request never reached the intended handler — e.g. an
    // HTML error page or the SPA index.html served with a 200 because the route
    // wasn't mounted. Surface that loudly instead of silently casting to
    // `undefined`, which turns every such backend regression into an opaque
    // "Cannot destructure ..." at the callsite (#983).
    const contentType = res.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
        const text = await res.text().catch(() => '');
        if (res.status === 204 || text.trim() === '') {
            log.api.timed(`${method} ${url} -> ${res.status} (no body)`, t);
            return undefined as T;
        }
        log.api.error(`${method} ${url} -> ${res.status} (non-JSON body)`, {
            contentType,
            preview: text.slice(0, 200),
        });
        throw new Error(
            `${method} ${path}: expected JSON, got ${contentType || 'no Content-Type'} ` +
            `(HTTP ${res.status}: ${text.slice(0, 120)})`,
        );
    }

    const data = await res.json();
    log.api.timed(`${method} ${url} -> ${res.status}`, t, data);
    return data;
}

// -- System --------------------------------------------------------------------

export async function getSystemStatus(signal?: AbortSignal): Promise<SystemStatus> {
    return apiFetch<SystemStatus>('GET', '/system/status', undefined, undefined, signal);
}

/** Ask the backend to prime a new chat's prompt. Debounced and single-flight there. */
export async function requestWarmUp(): Promise<WarmUpStatus> {
    return apiFetch<WarmUpStatus>('POST', '/system/warm-up');
}

export async function getWarmUp(): Promise<WarmUpStatus> {
    return apiFetch<WarmUpStatus>('GET', '/system/warm-up');
}

/** Which Lemonade GAIA uses, where, and the backend each loaded model runs on. */
export async function getModelServer(): Promise<ModelServerInfo> {
    return apiFetch<ModelServerInfo>('GET', '/system/model-server');
}

/** Save the llama.cpp backend for chat models (`auto` or a name Lemonade offers). */
export async function setModelServerBackend(backend: string): Promise<ModelServerInfo> {
    return apiFetch<ModelServerInfo>('PUT', '/system/model-server/backend', { backend });
}

// -- Settings ------------------------------------------------------------------

export async function getSettings(): Promise<Settings> {
    return apiFetch<Settings>('GET', '/settings');
}

export async function updateSettings(data: Partial<Settings>): Promise<Settings> {
    return apiFetch<Settings>('PUT', '/settings', data);
}

export async function loadModel(modelName: string, ctxSize?: number, force = false): Promise<{ status: string; model: string; ctx_size: number }> {
    return apiFetch('POST', '/system/load-model', { model_name: modelName, ctx_size: ctxSize, force });
}

/**
 * Load a model, asking first when a chat is answering: loading then can cut
 * that answer off. Resolves false when the user chose to wait.
 */
export async function loadModelAskingFirst(
    modelName: string,
    ctxSize?: number,
    confirmLoad: (question: string) => boolean = (q) => window.confirm(q),
): Promise<boolean> {
    try {
        await loadModel(modelName, ctxSize);
        return true;
    } catch (err) {
        if (!(err instanceof ApiError) || err.httpStatus !== 409) throw err;
        if (!confirmLoad(`${err.detail}

Load ${modelName} anyway?`)) return false;
        await loadModel(modelName, ctxSize, true);
        return true;
    }
}

export async function downloadModel(modelName: string, force = false): Promise<{ status: string; model: string }> {
    return apiFetch('POST', '/system/download-model', { model_name: modelName, force });
}

// -- Agents --------------------------------------------------------------------

export async function listAgents(): Promise<{ agents: AgentInfo[]; total: number }> {
    return apiFetch('GET', '/agents');
}

// -- First-run setup (same `gaia init` the TUI runs) ----------------------------

/** Hardware report (memory, disk, GPU, NPU) shown on the setup screen. */
export async function getOnboardingPreflight(): Promise<PreflightReport> {
    return apiFetch<PreflightReport>('GET', '/onboarding/preflight');
}

export async function checkSetup(skipChatModel = false): Promise<SetupCheck> {
    return apiFetch('GET', `/setup/check?skip_chat_model=${skipChatModel}`);
}

export async function startSetup(skipChatModel: boolean): Promise<SetupRunStatus> {
    return apiFetch('POST', '/setup/run', { skip_chat_model: skipChatModel });
}

export async function getSetupStatus(): Promise<SetupRunStatus> {
    return apiFetch('GET', '/setup/status');
}

/** The Lemonade, chat model and download setup will use. Loads nothing. */
export async function getSetupPlan(): Promise<SetupPlan> {
    return apiFetch('GET', '/setup/plan');
}

/** Pick setup's chat model before it starts; returns the new plan. */
export async function chooseSetupChatModel(modelId: string): Promise<SetupPlan> {
    return apiFetch('POST', '/setup/chat-model', { model_id: modelId });
}

/** A new chat's task cards and working folder. */
export async function getStartScreen(): Promise<StartScreen> {
    return apiFetch('GET', '/onboarding/start');
}

// -- Which Lemonade Server GAIA uses ---------------------------------------------

export async function getLemonadeTarget(): Promise<LemonadeTarget> {
    return apiFetch('GET', '/lemonade/target');
}

/** A Lemonade the user already runs on this PC, or null. */
/** `unchecked` lists URLs that accepted a connection but didn't answer in time. */
export async function detectLemonade(): Promise<{ found: LemonadeFound | null; unchecked?: string[] }> {
    return apiFetch('GET', '/lemonade/detect');
}

/**
 * Switch servers. Nothing is saved unless the user's server checks out; a
 * rejection is an `ApiError` whose `reason` says why (`needs_key`, `bad_key`, …).
 */
export async function setLemonadeTarget(body: LemonadeTargetBody): Promise<LemonadeTarget & { daemon: unknown }> {
    try {
        return await apiFetch('POST', '/lemonade/target', body);
    } catch (err) {
        // A 502 here is "saved, but the daemon did not switch" — keep those words.
        if (err instanceof ApiError && err.httpStatus === 502 && err.detail) {
            throw new ApiError(err.detail, err.httpStatus, err.detail, err.reason);
        }
        throw err;
    }
}

// -- AI providers and models (the TUI's /provider) -------------------------------

export async function listProviders(): Promise<ProvidersResponse> {
    return apiFetch('GET', '/providers');
}

export async function getActiveModel(): Promise<ActiveModel> {
    return apiFetch('GET', '/providers/active');
}

/** Register a cloud provider. A blank key keeps the one Lemonade or the OS store already has. */
export async function connectProvider(
    provider: string,
    body: ConnectProviderBody,
): Promise<ProviderInfo & { remembered: boolean; remember_error: string | null }> {
    return apiFetch('POST', `/providers/${encodeURIComponent(provider)}/connect`, body);
}

export async function forgetProviderKey(provider: string): Promise<{ removed: boolean }> {
    return apiFetch('DELETE', `/providers/${encodeURIComponent(provider)}/key`);
}

export async function listProviderModels(provider: string): Promise<ProviderModelsResponse> {
    return apiFetch('GET', `/providers/${encodeURIComponent(provider)}/models`);
}

export async function getContextChoice(model: string): Promise<ContextChoice> {
    return apiFetch('GET', `/providers/local/context?model=${encodeURIComponent(model)}`);
}

/** Remember a context size for a model; null returns it to Automatic. */
export async function setContextChoice(model: string, ctxSize: number | null): Promise<ContextChoice> {
    return apiFetch('PUT', '/providers/local/context', { model, ctx_size: ctxSize });
}

export async function getLocalModelStatus(): Promise<LocalModelStatus> {
    return apiFetch('GET', '/providers/local/status');
}

/** Start the model server if stopped, then reload the chat model. */
export async function restartLocalModel(confirm = false): Promise<{ status: string; model: string; starting_server: boolean }> {
    return apiFetch('POST', '/providers/local/restart', { confirm });
}

export async function selectModel(model: string): Promise<ActiveModel> {
    return apiFetch('POST', '/providers/select', { model });
}

// -- Cloud prices and spending (#5258) ------------------------------------------

export async function getCloudPrices(provider: string): Promise<CloudPriceList> {
    return apiFetch('GET', `/spending/prices/${encodeURIComponent(provider)}`);
}

export async function getSpending(): Promise<SpendingReport> {
    return apiFetch('GET', '/spending');
}

/** Set the monthly limit for cloud chats; null removes it. */
export async function setSpendingLimit(limitUsd: number | null): Promise<SpendingReport> {
    return apiFetch('PUT', '/spending/limit', { limit_usd: limitUsd });
}

/** Let cloud chats go on past the limit until the month ends. */
export async function allowSpendingThisMonth(): Promise<SpendingReport> {
    return apiFetch('POST', '/spending/allow-month');
}

// -- Skills ----------------------------------------------------------------------

export async function listSkills(): Promise<{ skills: SkillInfo[]; invalid: Record<string, string> }> {
    return apiFetch('GET', '/skills');
}

// -- Connections (issue #915): see connectorsApi.ts ----------------------------

export * from './connectorsApi';

// -- Sessions ------------------------------------------------------------------

/** The backend's largest page of chats. */
export const SESSION_PAGE_SIZE = 200;

/** Every chat, newest first, fetched page by page so none past the first page go missing. */
export async function listSessions(): Promise<{ sessions: Session[]; total: number }> {
    const byId = new Map<string, Session>();
    let total = 0;
    for (let offset = 0; ; offset += SESSION_PAGE_SIZE) {
        const page: { sessions: Session[]; total: number } = await apiFetch(
            'GET', `/sessions?limit=${SESSION_PAGE_SIZE}&offset=${offset}`,
        );
        total = page.total;
        // A chat updated between two page reads can shift into both; keep one.
        for (const s of page.sessions) if (!byId.has(s.id)) byId.set(s.id, s);
        if (page.sessions.length < SESSION_PAGE_SIZE || offset + SESSION_PAGE_SIZE >= total) break;
    }
    return { sessions: [...byId.values()], total };
}

export async function createSession(data: Partial<Session> = {}): Promise<Session> {
    return apiFetch('POST', '/sessions', data);
}

export async function updateSession(id: string, data: { title?: string; title_is_custom?: boolean; system_prompt?: string; private?: boolean; agent_type?: string }): Promise<Session> {
    return apiFetch('PUT', `/sessions/${id}`, data);
}

export async function deleteSession(id: string): Promise<void> {
    return apiFetch('DELETE', `/sessions/${id}`);
}

export async function getSession(id: string): Promise<Session> {
    return apiFetch('GET', `/sessions/${id}`);
}

export async function pinSession(id: string, pinned: boolean): Promise<Session> {
    return apiFetch('PUT', `/sessions/${id}/pin`, { pinned });
}

/** Clear the chat's unread mark. */
export async function markSessionRead(id: string): Promise<Session> {
    return apiFetch('POST', `/sessions/${id}/read`);
}

export async function toggleSessionPrivacy(id: string): Promise<Session> {
    return apiFetch('PATCH', `/sessions/${id}/private`);
}

export async function getMessageCount(sessionId: string): Promise<number> {
    const { total } = await apiFetch<{ messages: Message[]; total: number }>(
        'GET', `/sessions/${sessionId}/messages?limit=1&offset=0`,
    );
    return total;
}

export async function getMessages(sessionId: string): Promise<{ messages: Message[]; total: number }> {
    const pageSize = 100;
    const path = `/sessions/${sessionId}/messages`;
    const first = await apiFetch<{ messages: Message[]; total: number }>(
        'GET', `${path}?limit=${pageSize}&offset=0`,
    );
    const messages = [...first.messages];
    // Bound this load to its initial count; a running agent may keep appending.
    // The next refresh retrieves those newer messages.
    const total = first.total;
    while (messages.length < total) {
        const limit = Math.min(pageSize, total - messages.length);
        const page = await apiFetch<{ messages: Message[]; total: number }>(
            'GET', `${path}?limit=${limit}&offset=${messages.length}`,
        );
        if (page.messages.length === 0) {
            throw new Error('Incomplete transcript received. Please reload the conversation.');
        }
        messages.push(...page.messages);
    }
    return { messages, total };
}

export async function exportSession(sessionId: string): Promise<{ content: string }> {
    return apiFetch('GET', `/sessions/${sessionId}/export?format=markdown`);
}

export async function deleteMessage(sessionId: string, messageId: number): Promise<void> {
    return apiFetch('DELETE', `/sessions/${sessionId}/messages/${messageId}`);
}

export async function deleteMessagesFrom(sessionId: string, messageId: number): Promise<{ deleted: boolean; count: number }> {
    return apiFetch('DELETE', `/sessions/${sessionId}/messages/${messageId}/and-below`);
}

// -- Chat (Streaming with Agent Events) ----------------------------------------

export * from './chatTurnApi';

/**
 * Callbacks for agent streaming events.
 *
 * The stream can produce both text chunks (for the response)
 * and agent activity events (steps, tool calls, thinking).
 */
export interface StreamCallbacks {
    /** Text chunk for the response content. */
    onChunk: (event: StreamEvent) => void;
    /** Agent activity event (step, tool, thinking, plan, etc.). */
    onAgentEvent: (event: StreamEvent) => void;
    /** Stream complete with final response. */
    onDone: (event: StreamEvent) => void;
    /** Error occurred. */
    onError: (error: Error) => void;
    /** A new agent was created and registered — refresh the agent list. */
    onAgentCreated?: (event: StreamEvent) => void;
    /** The backend answered the request; a wait shown before this is over. */
    onConnected?: () => void;
}

/** Agent event types that represent activity rather than content. */
const AGENT_EVENT_TYPES = new Set([
    'status', 'step', 'thinking', 'plan',
    'tool_start', 'tool_end', 'tool_result', 'tool_args', 'tool_confirm', 'agent_error',
    'permission_request', 'needs_confirmation', 'needs_input', 'policy_alert',
    'followup_taken', 'followup_returned', 'context_usage',
]);

export function sendMessageStream(
    sessionId: string,
    message: string,
    onChunkOrCallbacks: ((event: StreamEvent) => void) | StreamCallbacks,
    onDone?: (event: StreamEvent) => void,
    onError?: (error: Error) => void,
    agentType?: string,
    device?: string,
): AbortController {
    // Support both old 3-arg style and new callbacks style
    let callbacks: StreamCallbacks;
    if (typeof onChunkOrCallbacks === 'function') {
        callbacks = {
            onChunk: onChunkOrCallbacks,
            onAgentEvent: () => {},  // no-op if using old API
            onDone: onDone || (() => {}),
            onError: onError || ((err) => { log.stream.error('Unhandled stream error', err); }),
        };
    } else {
        callbacks = onChunkOrCallbacks;
    }

    const controller = new AbortController();
    const t = log.stream.time();

    log.stream.info(`Starting SSE stream for session=${sessionId}`, { messageLength: message.length });

    const headers = watchHeaders(controller, callbacks, SEND_HEADER_TIMEOUT_MS, SEND_WAITING_NOTICE_MS);
    fetch(`${API_BASE}/chat/send`, {
        method: 'POST',
        headers: { ...UI_HEADER, 'Content-Type': 'application/json' },
        body: JSON.stringify({
            session_id: sessionId,
            message,
            stream: true,
            ...(agentType ? { agent_type: agentType } : {}),
            ...(device ? { device } : {}),
        }),
        signal: controller.signal,
    })
        .then((res) => {
            headers.received();
            headers.connected();
            return consumeSSEResponse(res, callbacks, t, { silentCloseIsError: true });
        })
        .catch((err) => {
            headers.received();
            if (headers.timedOut()) {
                log.stream.error(`No answer to /chat/send within ${SEND_HEADER_TIMEOUT_MS / 1000}s`);
                callbacks.onError(new StreamTimeoutError('send', SEND_HEADER_TIMEOUT_MS));
            } else if (err.name === 'AbortError') {
                log.stream.warn('Stream aborted by user');
            } else {
                log.stream.error('Stream fetch error', err);
                callbacks.onError(err);
            }
        });

    return controller;
}

/** The backend sends a keepalive every 5 s, so this much silence means the connection is gone. */
export const STREAM_IDLE_MS = 30_000;
/** A turn waiting for the model hears nothing until its place in line changes. */
export const QUEUED_IDLE_MS = 10 * 60_000;
/** The backend may hold a send for 60 s while another chat has the model, then answers 429. */
export const SEND_HEADER_TIMEOUT_MS = 90_000;
/** When a send not yet started by the backend begins showing as waiting. */
export const SEND_WAITING_NOTICE_MS = 3_000;
export const SEND_WAITING_NOTICE_TEXT = 'Waiting for GAIA to start this answer…';
/** An attach replays at once; a slower answer means the backend is stuck or unreachable. */
export const ATTACH_HEADER_TIMEOUT_MS = 30_000;

/** The stream went silent past STREAM_IDLE_MS; the run may still be going server-side. */
export class StreamStalledError extends Error {
    constructor(readonly idleMs: number) {
        super(`The connection to GAIA went silent for ${Math.round(idleMs / 1000)} s.`);
        this.name = 'StreamStalledError';
    }
}

/** The connection broke after the run started (network switch, proxy reset, restart). */
export class StreamDroppedError extends Error {
    constructor(readonly cause: unknown) {
        super(`The connection to GAIA was cut: ${cause instanceof Error ? cause.message : String(cause)}`);
        this.name = 'StreamDroppedError';
    }
}

/** The backend never started answering the request. */
export class StreamTimeoutError extends Error {
    constructor(readonly endpoint: 'send' | 'attach', readonly timeoutMs: number) {
        super(`GAIA did not start answering within ${Math.round(timeoutMs / 1000)} s.`);
        this.name = 'StreamTimeoutError';
    }
}

/** Bound the wait for response headers, and say so when it runs long. */
function watchHeaders(
    controller: AbortController,
    callbacks: StreamCallbacks,
    timeoutMs: number,
    noticeMs?: number,
): { received: () => void; connected: () => void; timedOut: () => boolean } {
    let timedOut = false;
    const notice = noticeMs === undefined ? undefined : setTimeout(() => {
        callbacks.onAgentEvent({
            type: 'status',
            status: 'working',
            phase: 'waiting',
            message: SEND_WAITING_NOTICE_TEXT,
        } as StreamEvent);
    }, noticeMs);
    const limit = setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, timeoutMs);
    let settled = false;
    return {
        received: () => {
            clearTimeout(notice);
            clearTimeout(limit);
        },
        connected: () => {
            if (settled) return;
            settled = true;
            callbacks.onConnected?.();
        },
        timedOut: () => timedOut,
    };
}

/** The next chunk, or StreamStalledError after `idleMs` of silence. */
async function readWithin(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    idleMs: number,
): Promise<ReadableStreamReadResult<Uint8Array>> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stalled = new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new StreamStalledError(idleMs)), idleMs);
    });
    try {
        return await Promise.race([reader.read(), stalled]);
    } finally {
        clearTimeout(timer);
    }
}

/**
 * Read and dispatch an SSE response body against a set of StreamCallbacks.
 * Shared by ``sendMessageStream`` (POST /chat/send) and ``attachToRun``
 * (GET /chat/attach) so both parse events identically.
 */
async function consumeSSEResponse(
    res: Response,
    callbacks: StreamCallbacks,
    t: ReturnType<typeof log.stream.time>,
    { silentCloseIsError = false }: { silentCloseIsError?: boolean } = {},
): Promise<void> {
    log.stream.info(`SSE connection opened -> HTTP ${res.status}`);

    if (!res.ok) {
        const errText = await res.text().catch(() => '');
        log.stream.error(`SSE connection failed: HTTP ${res.status}`, errText);
        // The server's own words when it gave any, never a raw JSON body.
        const detail = parseErrorDetail(errText);
        const message = detail && detail !== 'Internal Server Error' ? detail : getFriendlyError(res.status, detail);
        callbacks.onError(new ApiError(message, res.status, detail, parseErrorStatus(errText)));
        return;
    }

    const reader = res.body?.getReader();
    if (!reader) {
        log.stream.error('No response body reader available');
        callbacks.onError(new Error('No response body'));
        return;
    }

    const decoder = new TextDecoder();
    let buffer = '';
    let doneReceived = false;
    let chunkCount = 0;
    let totalChars = 0;
    let agentEventCount = 0;
    let queued = false;
    // An `error` event already closed the turn through onError.
    let errorReceived = false;
    // A fatal agent_error ends the turn; the closing `done` may never come.
    let fatalAgentError = false;

    try {
        while (true) {
            let next: ReadableStreamReadResult<Uint8Array>;
            try {
                next = await readWithin(reader, queued ? QUEUED_IDLE_MS : STREAM_IDLE_MS);
            } catch (err) {
                if ((err as Error)?.name === 'AbortError') throw err;
                if (!(err instanceof StreamStalledError)) {
                    log.stream.error('SSE connection cut mid-stream', err);
                    if (doneReceived || errorReceived) return;
                    if (fatalAgentError) break;
                    throw new StreamDroppedError(err);
                }
                log.stream.error(`SSE stream silent for ${err.idleMs / 1000}s; dropping the connection`);
                await reader.cancel().catch((cancelErr) => log.stream.warn('Could not cancel the stalled stream', cancelErr));
                // The turn already ended for the caller; a dead connection after that changes nothing.
                if (doneReceived || errorReceived) return;
                if (fatalAgentError) break;
                throw err;
            }
            const { done, value } = next;
            if (done) {
                log.stream.debug('SSE reader done (stream ended)');
                break;
            }

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const raw = line.slice(6).trim();
                    if (!raw) continue;
                    try {
                        const event: StreamEvent = JSON.parse(raw);
                        queued = event.type === 'status' && event.phase === 'queued';

                        if (event.type === 'chunk') {
                            chunkCount++;
                            totalChars += (event.content || '').length;
                            if (chunkCount <= 3 || chunkCount % 50 === 0) {
                                log.stream.debug(`Chunk #${chunkCount} (+${(event.content || '').length} chars)`);
                            }
                            callbacks.onChunk(event);
                        } else if (event.type === 'answer') {
                            // Agent final answer - treat as content
                            callbacks.onChunk(event);
                        } else if (event.type === 'done') {
                            doneReceived = true;
                            log.stream.timed(`Stream complete: ${chunkCount} chunks, ${totalChars} chars, ${agentEventCount} agent events`, t);
                            callbacks.onDone(event);
                        } else if (event.type === 'error') {
                            log.stream.error(`Stream error event:`, event.content);
                            errorReceived = true;
                            callbacks.onError(new Error(event.content || 'Unknown error'));
                        } else if (event.type === 'agent_created') {
                            log.stream.info(`Agent created: ${event.agent_id}`);
                            callbacks.onAgentCreated?.(event);
                        } else if (event.type === 'mcp_status') {
                            // Settings reads the same report from GET /mcp/status; here it is only logged.
                            const servers = event.servers ?? [];
                            log.stream.debug(`MCP status: ${servers.length} server(s)`, servers);
                            for (const s of servers) {
                                if (!s.connected) {
                                    log.stream.warn(`MCP server "${s.name}" is not connected: ${s.error ?? 'no error reported'}`);
                                }
                            }
                        } else if (AGENT_EVENT_TYPES.has(event.type)) {
                            agentEventCount++;
                            if (event.type === 'agent_error' && !(event as { recoverable?: boolean }).recoverable) {
                                fatalAgentError = true;
                            }
                            log.stream.debug(`Agent event: ${event.type}`, event);
                            callbacks.onAgentEvent(event);
                        } else {
                            log.stream.warn(`Unknown SSE event type: ${event.type}`, event);
                        }
                    } catch (parseErr) {
                        log.stream.warn(`Malformed SSE data, skipping`, { raw: raw.slice(0, 100) });
                    }
                }
            }
        }
    } finally {
        // Release the reader to free the underlying connection
        reader.releaseLock();
    }

    // Only signal completion if no explicit done event was received during the stream
    if (!doneReceived) {
        log.stream.timed(`SSE connection closed without done event: ${chunkCount} chunks, ${agentEventCount} agent events`, t);
        // A send that closes having said nothing would otherwise leave the
        // message sitting there with no answer and no error.
        if (silentCloseIsError && chunkCount === 0 && agentEventCount === 0 && !errorReceived) {
            callbacks.onError(new Error(
                'GAIA closed the connection before any answer arrived. Send the message again; ' +
                'if it keeps happening, check the GAIA server log.',
            ));
            return;
        }
        callbacks.onDone({ type: 'done' });
    }
}

/**
 * Re-attach to an in-flight background run and stream its events (#1580).
 *
 * Used when the user revisits a session whose turn is still running. The
 * backend replays everything emitted so far, then streams live events.
 * Returns an AbortController so the caller can detach on unmount — that
 * does NOT cancel the run (it keeps going server-side), it only closes
 * this client's view of it.
 */
export function attachToRun(
    sessionId: string,
    callbacks: StreamCallbacks,
): AbortController {
    const controller = new AbortController();
    const t = log.stream.time();

    log.stream.info(`Attaching to background run for session=${sessionId}`);

    const headers = watchHeaders(controller, callbacks, ATTACH_HEADER_TIMEOUT_MS);
    fetch(`${API_BASE}/chat/attach?session_id=${encodeURIComponent(sessionId)}`, {
        method: 'GET',
        signal: controller.signal,
    })
        .then((res) => {
            headers.received();
            headers.connected();
            // The run ended between the active-runs poll and this attach: its
            // answer is already saved, so close the view like any finished turn.
            if (res.status === 404) {
                log.stream.info(`Run for session=${sessionId} finished before the attach`);
                callbacks.onDone({ type: 'done', content: '' } as StreamEvent);
                return;
            }
            return consumeSSEResponse(res, callbacks, t);
        })
        .catch((err) => {
            headers.received();
            if (headers.timedOut()) {
                log.stream.error(`No answer to /chat/attach within ${ATTACH_HEADER_TIMEOUT_MS / 1000}s`);
                callbacks.onError(new StreamTimeoutError('attach', ATTACH_HEADER_TIMEOUT_MS));
            } else if (err.name === 'AbortError') {
                log.stream.warn('Run attach detached by client');
            } else if (err instanceof TypeError) {
                // The run lives server-side; a failed attach is a connection problem, not a lost run.
                log.stream.error('Run attach could not connect', err);
                callbacks.onError(new StreamDroppedError(err));
            } else {
                log.stream.error('Run attach fetch error', err);
                callbacks.onError(err);
            }
        });

    return controller;
}

// -- Documents -----------------------------------------------------------------

export async function listDocuments(): Promise<{ documents: Document[]; total: number; total_size_bytes: number; total_chunks: number }> {
    return apiFetch('GET', '/documents');
}

export async function uploadDocumentByPath(filepath: string): Promise<Document> {
    return apiFetch('POST', '/documents/upload-path', { filepath });
}

/**
 * Upload a document as a multipart blob for indexing.
 *
 * Used by drag-and-drop in both the Document Library and ChatView.
 * Required for browser-mode users (and modern Electron versions, where
 * File.path is no longer populated for drag-drop) — there's no reliable
 * way to get an absolute filesystem path from a browser File object,
 * so we have to stream the content directly to the server.
 *
 * Errors are mapped through getFriendlyError so 404/413 messages are
 * consistent with the rest of the app.
 */
export async function uploadDocumentBlob(file: File): Promise<Document> {
    const url = `${API_BASE}/documents/upload`;
    const t = log.api.time();
    log.api.info(`POST ${url}`, { fileName: file.name, size: file.size });

    const formData = new FormData();
    formData.append('file', file);

    let res: Response;
    try {
        res = await fetch(url, { method: 'POST', headers: UI_HEADER, body: formData });
    } catch (err) {
        log.api.error(`POST ${url} - network error`, err);
        throw err;
    }

    if (!res.ok) {
        const errorText = await res.text().catch(() => '');
        log.api.error(`POST ${url} - HTTP ${res.status}`, { errorText });
        throw new Error(getFriendlyError(res.status, parseErrorDetail(errorText)));
    }

    const data = await res.json();
    log.api.timed(`POST ${url} -> ${res.status}`, t, data);
    return data;
}

export async function deleteDocument(id: string): Promise<void> {
    return apiFetch('DELETE', `/documents/${id}`);
}

export async function getDocumentStatus(id: string): Promise<{
    id: string;
    indexing_status: string;
    chunk_count: number;
    is_active: boolean;
}> {
    return apiFetch('GET', `/documents/${id}/status`);
}

export async function cancelIndexing(id: string): Promise<{ cancelled: boolean; id: string }> {
    return apiFetch('POST', `/documents/${id}/cancel`);
}

export async function reindexDocument(id: string): Promise<Document> {
    return apiFetch('POST', `/documents/${id}/reindex`);
}

export async function attachDocument(sessionId: string, documentId: string): Promise<void> {
    return apiFetch('POST', `/sessions/${sessionId}/documents`, { document_id: documentId });
}

export async function detachDocument(sessionId: string, documentId: string): Promise<void> {
    return apiFetch('DELETE', `/sessions/${sessionId}/documents/${documentId}`);
}

// -- File Browser -----------------------------------------------------------------

export async function browseFiles(path?: string): Promise<BrowseResponse> {
    const params = path ? `?path=${encodeURIComponent(path)}` : '';
    return apiFetch('GET', `/files/browse${params}`);
}

export async function indexFolder(folderPath: string, recursive: boolean = true): Promise<IndexFolderResponse> {
    return apiFetch('POST', '/documents/index-folder', { folder_path: folderPath, recursive });
}

export async function openFileOrFolder(path: string, reveal: boolean = true): Promise<{ status: string; path: string }> {
    return apiFetch('POST', '/files/open', { path, reveal });
}

/** Open a file in its default app. The server refuses scripts and programs. */
export async function openFileInApp(path: string): Promise<{ status: string; path: string }> {
    return apiFetch('POST', '/files/open', { path, launch: true });
}

/** Whether each path still exists and when it last changed (unchecked outside home). */
export async function getFileStatus(paths: string[]): Promise<{
    files: Array<{ path: string; exists?: boolean | null; mtime?: number | null }>;
}> {
    return apiFetch('POST', '/files/status', { paths });
}

/** Upload a file (image/document) to the server. */
export async function uploadFile(file: File): Promise<{
    filename: string;
    original_name: string;
    url: string;
    size: number;
    content_type: string;
    is_image: boolean;
}> {
    const url = `${API_BASE}/files/upload`;
    const t = log.api.time();
    log.api.info(`POST ${url}`, { fileName: file.name, size: file.size });

    const formData = new FormData();
    formData.append('file', file);

    const res = await fetch(url, {
        method: 'POST',
        headers: UI_HEADER,
        body: formData,
    });

    if (!res.ok) {
        const errorText = await res.text().catch(() => 'Upload failed');
        log.api.error(`POST ${url} - HTTP ${res.status}`, { errorText });
        throw new Error(`Upload failed: ${errorText}`);
    }

    const data = await res.json();
    log.api.timed(`POST ${url} -> ${res.status}`, t, data);
    return data;
}

// -- File Search & Preview ----------------------------------------------------------

export async function searchFiles(query: string, fileTypes?: string, maxResults?: number): Promise<{
    results: Array<{ name: string; path: string; size: number; size_display: string; extension: string; modified: string; directory: string }>;
    total: number;
    query: string;
    searched_locations: string[];
    truncated: boolean;
}> {
    const params = new URLSearchParams({ query });
    if (fileTypes) params.set('file_types', fileTypes);
    if (maxResults) params.set('max_results', String(maxResults));
    return apiFetch('GET', `/files/search?${params}`);
}

export async function previewFile(path: string, lines?: number): Promise<{
    path: string;
    name: string;
    size: number;
    size_display: string;
    extension: string;
    modified: string;
    is_text: boolean;
    preview_lines: string[];
    total_lines: number | null;
    columns: string[] | null;
    row_count: number | null;
}> {
    const params = new URLSearchParams({ path });
    if (lines) params.set('lines', String(lines));
    return apiFetch('GET', `/files/preview?${params}`);
}

// -- Mobile Access / Tunnel -------------------------------------------------------

export async function startTunnel(): Promise<TunnelStatus> {
    return apiFetch('POST', '/tunnel/start');
}

export async function stopTunnel(): Promise<{ active: boolean }> {
    return apiFetch('POST', '/tunnel/stop');
}

export async function getTunnelStatus(): Promise<TunnelStatus> {
    return apiFetch('GET', '/tunnel/status');
}

// -- MCP Server Management -------------------------------------------------------

// MCP server configuration moved to the connectors framework (#927):
//   - Catalog tiles  → POST /api/connectors/{id}/configure
//   - Custom servers → CLI `gaia connectors mcp add` today; UI in #977
//   - Catalog list   → GET /api/connectors/catalog (filter by type='mcp_server')
// Only read-only runtime endpoints remain:

export async function getMCPRuntimeStatus(): Promise<{ servers: MCPServerStatus[] }> {
    return apiFetch('GET', '/mcp/status');
}

// -- Schedules -----------------------------------------------------------------

export async function listSchedules(): Promise<{ schedules: Schedule[]; total: number }> {
    return apiFetch('GET', '/schedules');
}

/** The user's IANA time zone, so "daily at 9am" means 9am where they are. */
export function browserTimeZone(): string | undefined {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
}

export async function createSchedule(name: string, interval: string, prompt: string): Promise<Schedule> {
    return apiFetch('POST', '/schedules', { name, interval, prompt, timezone: browserTimeZone() });
}

export async function updateSchedule(name: string, status: string): Promise<Schedule> {
    return apiFetch('PUT', `/schedules/${encodeURIComponent(name)}`, { status });
}

export async function deleteSchedule(name: string): Promise<void> {
    return apiFetch('DELETE', `/schedules/${encodeURIComponent(name)}`);
}

export async function getScheduleResults(name: string, limit: number = 20): Promise<{ results: ScheduleResult[]; total: number }> {
    return apiFetch('GET', `/schedules/${encodeURIComponent(name)}/results?limit=${limit}`);
}

export async function parseScheduleInput(input: string): Promise<ParsedSchedule> {
    return apiFetch('POST', '/schedules/parse', { input, timezone: browserTimeZone() });
}

// -- Undo a turn's file changes (#5225) --------------------------------------

export async function listTurnChanges(sessionId: string): Promise<{ turns: TurnChanges[] }> {
    return apiFetch('GET', `/sessions/${encodeURIComponent(sessionId)}/changes`);
}

export async function getTurnDiff(sessionId: string, turnId: string): Promise<{ turn_id: string; files: FileDiff[] }> {
    return apiFetch('GET', `/sessions/${encodeURIComponent(sessionId)}/changes/${encodeURIComponent(turnId)}/diff`);
}

export async function undoTurn(sessionId: string, turnId: string, force = false): Promise<UndoResult> {
    return apiFetch('POST', `/sessions/${encodeURIComponent(sessionId)}/changes/${encodeURIComponent(turnId)}/undo`, { force });
}

export async function getUndoSettings(): Promise<UndoSettings> {
    return apiFetch('GET', '/settings/undo');
}

export async function updateUndoSettings(retentionDays: number, maxMb: number): Promise<UndoSettings> {
    return apiFetch('PUT', '/settings/undo', { retention_days: retentionDays, max_mb: maxMb });
}
