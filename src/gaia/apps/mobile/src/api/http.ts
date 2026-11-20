// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Message, Session, StreamEvent, SystemStatus } from '@webui/types';
import { fromApiMessages } from '@webui/utils/chatStream';

import type { FetchLike, FetchRequestInit, FetchResponseLike } from '@/platform/fetch.types';

import {
    GaiaApiError,
    GaiaAuthError,
    GaiaConfigError,
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
    ActiveRuns,
    CancelChatResult,
    ConfirmToolResult,
    GaiaClient,
    Goal,
    HealthResponse,
    MessageList,
    RequestOptions,
    ScheduleList,
    ScheduleResults,
    SessionList,
    UserInputResult,
} from './client.types';
import {
    ATTACH_HEADER_TIMEOUT_MS,
    ENDPOINTS,
    MESSAGE_PAGE_SIZE,
    QUEUED_IDLE_MS,
    scheduleResultsPath,
    SEND_HEADER_TIMEOUT_MS,
    SESSION_EVENTS_IDLE_MS,
    SESSION_PAGE_SIZE,
    sessionMessagesPath,
    sessionPath,
    STREAM_IDLE_MS,
    UI_HEADERS,
} from './constants';
import { parseSseStream } from './sse';

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
            headers: headers(body !== undefined),
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
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

    async function readJson<T>(res: FetchResponseLike, path: string): Promise<T> {
        const text = await res.text();
        try {
            return JSON.parse(text) as T;
        } catch {
            throw new GaiaApiError(res.status, text.slice(0, 200), 'mobileApp.error.notJson', { path });
        }
    }

    async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
        return readJson<T>(await request('GET', path, undefined, signal), path);
    }

    async function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
        return readJson<T>(await request('POST', path, body, signal), path);
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

        sendMessage(sessionId, message, options) {
            const body = { session_id: sessionId, message, stream: true };
            return streamTurn('POST', ENDPOINTS.chatSend, body, 'send', SEND_HEADER_TIMEOUT_MS, options?.signal);
        },

        attachToRun(sessionId, options) {
            const path = `${ENDPOINTS.chatAttach}?session_id=${encodeURIComponent(sessionId)}`;
            return streamTurn('GET', path, undefined, 'attach', ATTACH_HEADER_TIMEOUT_MS, options?.signal);
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

        getActiveRuns(options) {
            return getJson<ActiveRuns>(ENDPOINTS.chatActive, options?.signal);
        },

        listSchedules(options) {
            return getJson<ScheduleList>(ENDPOINTS.schedules, options?.signal);
        },

        listScheduleResults(name, limit, options) {
            return getJson<ScheduleResults>(scheduleResultsPath(name, limit), options?.signal);
        },

        listGoals(options) {
            return getJson<{ goals: Goal[]; total: number }>(ENDPOINTS.goals, options?.signal);
        },

        getSystemStatus(options) {
            return getJson<SystemStatus>(ENDPOINTS.systemStatus, options?.signal);
        },
    };
}
