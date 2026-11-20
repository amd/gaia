// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { createRecordingFetch, jsonResponse, streamResponse, textResponse } from '@/test-support/fakeFetch';

import type { GaiaClient, StreamEvent } from './client.types';
import { SEND_HEADER_TIMEOUT_MS } from './constants';
import {
    GaiaApiError,
    GaiaAuthError,
    GaiaConfigError,
    GaiaNetworkError,
    GaiaStreamError,
    GaiaStreamTimeoutError,
} from './errors';
import { createHttpClient } from './http';

const BASE = 'http://192.168.1.20:4200';
const UI_HEADERS = { 'X-Gaia-UI': '1', 'X-Gaia-Client': 'agent-ui' };

async function collect(events: AsyncIterable<StreamEvent>): Promise<StreamEvent[]> {
    const out: StreamEvent[] = [];
    for await (const e of events) out.push(e);
    return out;
}

describe('createHttpClient requests', () => {
    it('health: GET /api/health with the UI headers and no body', async () => {
        const { fetch, calls } = createRecordingFetch(() => jsonResponse({ status: 'ok', service: 'gaia-agent-ui' }));
        const result = await createHttpClient({ baseUrl: BASE, token: null, fetch }).health();
        expect(result.status).toBe('ok');
        expect(calls).toEqual([
            { url: `${BASE}/api/health`, method: 'GET', headers: UI_HEADERS, body: undefined, signal: undefined },
        ]);
    });

    it('sends Bearer only when a token is set', async () => {
        const { fetch, calls } = createRecordingFetch(() => jsonResponse({ status: 'ok', service: 's' }));
        await createHttpClient({ baseUrl: BASE, token: 'tok-1', fetch }).health();
        await createHttpClient({ baseUrl: BASE, token: '', fetch }).health();
        await createHttpClient({ baseUrl: BASE, fetch }).health();
        expect(calls[0]!.headers).toEqual({ ...UI_HEADERS, Authorization: 'Bearer tok-1' });
        expect(calls[1]!.headers).toEqual(UI_HEADERS);
        expect(calls[2]!.headers).toEqual(UI_HEADERS);
    });

    it('trims a trailing slash from the base URL', async () => {
        const { fetch, calls } = createRecordingFetch(() => jsonResponse({ status: 'ok', service: 's' }));
        await createHttpClient({ baseUrl: `${BASE}/`, fetch }).health();
        expect(calls[0]!.url).toBe(`${BASE}/api/health`);
    });

    it('listSessions: pages of 200 until the total is read, one copy of each chat', async () => {
        const mk = (id: string) => ({ id, title: id });
        const first = Array.from({ length: 200 }, (_, i) => mk(`s${i}`));
        const second = [mk('s199'), mk('s200')];
        const { fetch, calls } = createRecordingFetch((call) =>
            call.url.endsWith('offset=0')
                ? jsonResponse({ sessions: first, total: 202 })
                : jsonResponse({ sessions: second, total: 202 }),
        );
        const result = await createHttpClient({ baseUrl: BASE, token: 't', fetch }).listSessions();
        expect(calls.map((c) => [c.method, c.url])).toEqual([
            ['GET', `${BASE}/api/sessions?limit=200&offset=0`],
            ['GET', `${BASE}/api/sessions?limit=200&offset=200`],
        ]);
        expect(calls.every((c) => c.body === undefined && c.headers.Authorization === 'Bearer t')).toBe(true);
        expect(result.sessions).toHaveLength(201);
    });

    it('getMessages: encodes the id, pages of 100, bounded by the first total', async () => {
        const mk = (n: number) => ({ id: n, content: `m${n}` });
        const { fetch, calls } = createRecordingFetch((call) =>
            call.url.endsWith('limit=100&offset=0')
                ? jsonResponse({ messages: Array.from({ length: 100 }, (_, i) => mk(i)), total: 130 })
                : jsonResponse({ messages: Array.from({ length: 30 }, (_, i) => mk(100 + i)), total: 131 }),
        );
        const result = await createHttpClient({ baseUrl: BASE, fetch }).getMessages('a b/c');
        expect(calls.map((c) => c.url)).toEqual([
            `${BASE}/api/sessions/a%20b%2Fc/messages?limit=100&offset=0`,
            `${BASE}/api/sessions/a%20b%2Fc/messages?limit=30&offset=100`,
        ]);
        expect(result.messages).toHaveLength(130);
    });

    it('getMessages raises when a page comes back empty before the total', async () => {
        let n = 0;
        const { fetch } = createRecordingFetch(() =>
            jsonResponse(n++ === 0 ? { messages: [{ id: 1 }], total: 5 } : { messages: [], total: 5 }),
        );
        await expect(createHttpClient({ baseUrl: BASE, fetch }).getMessages('s')).rejects.toBeInstanceOf(GaiaApiError);
    });

    it('sendMessage: POST /api/chat/send with the JSON body and Content-Type', async () => {
        const { fetch, calls } = createRecordingFetch(() =>
            streamResponse(['data: {"type":"chunk","content":"hi"}\n\n', 'data: {"type":"done"}\n\n']),
        );
        const controller = new AbortController();
        const events = await collect(
            createHttpClient({ baseUrl: BASE, token: 'tok', fetch }).sendMessage('sid', 'hello', {
                signal: controller.signal,
            }),
        );
        expect(events.map((e) => e.type)).toEqual(['chunk', 'done']);
        expect(calls).toHaveLength(1);
        const call = calls[0]!;
        expect(call.url).toBe(`${BASE}/api/chat/send`);
        expect(call.method).toBe('POST');
        expect(call.headers).toEqual({
            ...UI_HEADERS,
            Authorization: 'Bearer tok',
            'Content-Type': 'application/json',
        });
        expect(JSON.parse(call.body!)).toEqual({ session_id: 'sid', message: 'hello', stream: true });
    });

    const JSON_HEADERS = { ...UI_HEADERS, 'Content-Type': 'application/json' };
    it.each<[string, (c: GaiaClient) => Promise<unknown>, string, string, Record<string, string>, unknown]>([
        ['getSession', (c) => c.getSession('a b'), 'GET', '/api/sessions/a%20b', UI_HEADERS, undefined],
        ['createSession', (c) => c.createSession({ title: 'Trip' }), 'POST', '/api/sessions', JSON_HEADERS, { title: 'Trip' }],
        ['createSession with no fields', (c) => c.createSession(), 'POST', '/api/sessions', JSON_HEADERS, {}],
        ['cancelChat', (c) => c.cancelChat('s1'), 'POST', '/api/chat/cancel', JSON_HEADERS, { session_id: 's1' }],
        [
            'confirmTool, allow once',
            (c) => c.confirmTool({ session_id: 's1', approved: true, confirm_id: 'c1' }),
            'POST',
            '/api/chat/confirm-tool',
            JSON_HEADERS,
            { session_id: 's1', approved: true, always: false, confirm_id: 'c1' },
        ],
        [
            'confirmTool, always allow, no prompt id',
            (c) => c.confirmTool({ session_id: 's1', approved: true, always: true }),
            'POST',
            '/api/chat/confirm-tool',
            JSON_HEADERS,
            { session_id: 's1', approved: true, always: true },
        ],
        [
            'sendUserInput',
            (c) => c.sendUserInput({ session_id: 's1', request_id: 'r1', value: 'Paris' }),
            'POST',
            '/api/chat/user-input',
            JSON_HEADERS,
            { session_id: 's1', request_id: 'r1', value: 'Paris' },
        ],
        ['getSystemStatus', (c) => c.getSystemStatus(), 'GET', '/api/system/status', UI_HEADERS, undefined],
        ['getActiveRuns', (c) => c.getActiveRuns(), 'GET', '/api/chat/active', UI_HEADERS, undefined],
        ['listSchedules', (c) => c.listSchedules(), 'GET', '/api/schedules', UI_HEADERS, undefined],
        ['listScheduleResults', (c) => c.listScheduleResults('daily/news', 20), 'GET', '/api/schedules/daily%2Fnews/results?limit=20', UI_HEADERS, undefined],
        ['listGoals', (c) => c.listGoals(), 'GET', '/api/goals', UI_HEADERS, undefined],
    ])('%s: %s', async (_name, call, method, path, headers, body) => {
        const { fetch, calls } = createRecordingFetch(() => jsonResponse({ status: 'ok' }));
        await call(createHttpClient({ baseUrl: BASE, fetch }));
        expect(calls).toHaveLength(1);
        const sent = calls[0]!;
        expect([sent.method, sent.url, sent.headers]).toEqual([method, `${BASE}${path}`, headers]);
        expect(sent.body === undefined ? undefined : JSON.parse(sent.body)).toEqual(body);
    });

    it('attachToRun: GET /api/chat/attach for the chat; no turn running ends with no events', async () => {
        const { fetch, calls } = createRecordingFetch(() => jsonResponse({ detail: 'No active run for this session.' }, 404));
        const events = await collect(createHttpClient({ baseUrl: BASE, fetch }).attachToRun('a b'));
        expect(events).toEqual([]);
        expect(calls.map((c) => [c.method, c.url, c.headers, c.body])).toEqual([
            ['GET', `${BASE}/api/chat/attach?session_id=a%20b`, UI_HEADERS, undefined],
        ]);
    });
});

describe('createHttpClient stream connections', () => {
    afterEach(() => jest.useRealTimers());

    it('a send GAIA never answers raises GaiaStreamTimeoutError and drops the request', async () => {
        jest.useFakeTimers({ doNotFake: ['nextTick', 'queueMicrotask'] });
        const { fetch, calls } = createRecordingFetch(() => new Promise<never>(() => {}));
        const outcome = collect(createHttpClient({ baseUrl: BASE, fetch }).sendMessage('s', 'm')).catch((e) => e);
        await jest.advanceTimersByTimeAsync(SEND_HEADER_TIMEOUT_MS);
        const err = await outcome;
        expect(err).toBeInstanceOf(GaiaStreamTimeoutError);
        expect([err.endpoint, err.params]).toEqual(['send', { seconds: SEND_HEADER_TIMEOUT_MS / 1000 }]);
        expect(calls[0]!.signal?.aborted).toBe(true);
    });

    it("the caller's abort drops the request and comes back as the abort, not a network error", async () => {
        const aborted = new Error('aborted');
        const { fetch, calls } = createRecordingFetch(
            (call) => new Promise<never>((_, reject) => call.signal?.addEventListener('abort', () => reject(aborted))),
        );
        const controller = new AbortController();
        const outcome = collect(createHttpClient({ baseUrl: BASE, fetch }).attachToRun('s', { signal: controller.signal }));
        controller.abort();
        await expect(outcome).rejects.toBe(aborted);
        expect(calls[0]!.signal?.aborted).toBe(true);
    });
});

describe('createHttpClient failures', () => {
    it('401 raises GaiaAuthError, a GaiaApiError', async () => {
        const { fetch } = createRecordingFetch(() => jsonResponse({ detail: 'bad token' }, 401));
        const err = await createHttpClient({ baseUrl: BASE, token: 'x', fetch }).listSessions().catch((e) => e);
        expect(err).toBeInstanceOf(GaiaAuthError);
        expect(err).toBeInstanceOf(GaiaApiError);
        expect(err.status).toBe(401);
        expect(err.detail).toBe('bad token');
    });

    it('500 with a JSON detail raises GaiaApiError carrying it', async () => {
        const { fetch } = createRecordingFetch(() => jsonResponse({ detail: 'database is locked' }, 500));
        const err = await createHttpClient({ baseUrl: BASE, fetch }).health().catch((e) => e);
        expect(err).toBeInstanceOf(GaiaApiError);
        expect(err).not.toBeInstanceOf(GaiaAuthError);
        expect(err.status).toBe(500);
        expect(err.detail).toBe('database is locked');
    });

    it('422 validation errors are joined, and a non-JSON body is kept as text', async () => {
        const a = createRecordingFetch(() => jsonResponse({ detail: [{ msg: 'too short' }, { msg: 'bad id' }] }, 422));
        const e1 = await createHttpClient({ baseUrl: BASE, fetch: a.fetch }).health().catch((e) => e);
        expect(e1.detail).toBe('too short; bad id');
        const b = createRecordingFetch(() => textResponse('<html>Bad gateway</html>', 502));
        const e2 = await createHttpClient({ baseUrl: BASE, fetch: b.fetch }).health().catch((e) => e);
        expect(e2.detail).toBe('<html>Bad gateway</html>');
    });

    it('a failed send raises before any event, with the server detail', async () => {
        const { fetch } = createRecordingFetch(() => jsonResponse({ detail: 'busy' }, 409));
        const err = await collect(createHttpClient({ baseUrl: BASE, fetch }).sendMessage('s', 'm')).catch((e) => e);
        expect(err).toBeInstanceOf(GaiaApiError);
        expect(err.detail).toBe('busy');
    });

    it('a network error raises GaiaNetworkError with the cause', async () => {
        const boom = new TypeError('Network request failed');
        const { fetch } = createRecordingFetch(() => {
            throw boom;
        });
        const err = await createHttpClient({ baseUrl: BASE, fetch }).health().catch((e) => e);
        expect(err).toBeInstanceOf(GaiaNetworkError);
        expect(err.cause).toBe(boom);
        expect(err.params).toEqual({ url: `${BASE}/api/health`, reason: 'Network request failed' });
    });

    it('a response that is not JSON raises instead of returning garbage', async () => {
        const { fetch } = createRecordingFetch(() => textResponse('<html>index</html>', 200));
        await expect(createHttpClient({ baseUrl: BASE, fetch }).health()).rejects.toBeInstanceOf(GaiaApiError);
    });

    it('a stream that ends without done raises', async () => {
        const { fetch } = createRecordingFetch(() => streamResponse(['data: {"type":"chunk","content":"a"}\n\n']));
        const seen: string[] = [];
        const run = async () => {
            for await (const e of createHttpClient({ baseUrl: BASE, fetch }).sendMessage('s', 'm')) seen.push(e.type);
        };
        await expect(run()).rejects.toBeInstanceOf(GaiaStreamError);
        expect(seen).toEqual(['chunk']);
    });

    it('a malformed event raises mid-stream', async () => {
        const { fetch } = createRecordingFetch(() => streamResponse(['data: oops\n\n']));
        const run = async () => collect(createHttpClient({ baseUrl: BASE, fetch }).sendMessage('s', 'm'));
        await expect(run()).rejects.toBeInstanceOf(GaiaStreamError);
    });

    it.each(['ftp://pc:4200', 'pc:4200', 'not a url', ''])('refuses the base URL %p at construction', (baseUrl) => {
        const { fetch } = createRecordingFetch(() => jsonResponse({}));
        expect(() => createHttpClient({ baseUrl, fetch })).toThrow(GaiaConfigError);
    });
});
