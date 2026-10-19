// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

const fetchMock = () => fetch as ReturnType<typeof vi.fn>;

describe('chat-turn calls reached through the API client', () => {
    beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it.each([
        ['getContextUsage', () => api.getContextUsage('a b'), 'GET', '/chat/context/a%20b'],
        ['getChatPerformance', () => api.getChatPerformance('a/b'), 'GET', '/chat/performance/a%2Fb'],
        ['sendFollowUp', () => api.sendFollowUp('s', 'hi'), 'POST', '/chat/followup'],
        ['retryWith', () => api.retryWith('s', 3, 'M-GGUF'), 'POST', '/chat/retry-with'],
        ['chooseRetry', () => api.chooseRetry('s', 3, 'retry'), 'POST', '/chat/retry-with/choose'],
    ])('%s sends %s to the right path, escaping the session id', async (_name, call, method, path) => {
        fetchMock().mockResolvedValue(jsonResponse({ ok: true }));
        await call();
        const [url, init] = fetchMock().mock.calls[0];
        expect(String(url).endsWith(path)).toBe(true);
        expect((init as RequestInit).method).toBe(method);
    });

    it('surfaces a failed performance read instead of an empty panel', async () => {
        fetchMock().mockResolvedValue(jsonResponse({ detail: 'Session not found' }, 404));
        await expect(api.getChatPerformance('missing')).rejects.toThrow();
    });
});
