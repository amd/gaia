// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, detectLemonade, getLemonadeTarget, redactSecrets, setLemonadeTarget } from '../api';

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

const fetchMock = () => fetch as ReturnType<typeof vi.fn>;

function call(i = 0): { url: string; init: RequestInit } {
    const [url, init] = fetchMock().mock.calls[i];
    return { url: String(url), init: init as RequestInit };
}

describe('Lemonade target API', () => {
    beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('reads the target and detects with the UI header', async () => {
        fetchMock().mockImplementation(async () => jsonResponse({ found: null }));
        await getLemonadeTarget();
        await detectLemonade();

        expect(call(0).url).toMatch(/\/api\/lemonade\/target$/);
        expect(call(1).url).toMatch(/\/api\/lemonade\/detect$/);
        for (const i of [0, 1]) {
            expect(call(i).init.method).toBe('GET');
            expect(call(i).init.headers).toMatchObject({ 'x-gaia-ui': '1' });
        }
    });

    it('posts mode, URL and key as JSON with the UI header', async () => {
        fetchMock().mockResolvedValue(jsonResponse({ mode: 'external', daemon: null }));
        await setLemonadeTarget({ mode: 'external', base_url: 'http://localhost:13305', api_key: 'sk-secret' });

        const { url, init } = call();
        expect(url).toMatch(/\/api\/lemonade\/target$/);
        expect(init.method).toBe('POST');
        expect(init.headers).toMatchObject({ 'x-gaia-ui': '1', 'Content-Type': 'application/json' });
        expect(JSON.parse(init.body as string)).toEqual({ mode: 'external', base_url: 'http://localhost:13305', api_key: 'sk-secret' });
    });

    it("turns a 422 into the server's sentence plus its reason", async () => {
        const message = 'The Lemonade Server at http://localhost:13305/api/v1 needs an API key. Enter the key it was started with (its LEMONADE_API_KEY).';
        fetchMock().mockResolvedValue(jsonResponse({ detail: { message, status: 'needs_key' } }, 422));

        const err = await setLemonadeTarget({ mode: 'external', base_url: 'http://localhost:13305' }).catch((e) => e);
        expect(err).toBeInstanceOf(ApiError);
        expect(err.message).toBe(message);
        expect(err.reason).toBe('needs_key');
        expect(err.httpStatus).toBe(422);
    });

    it('keeps the "saved, but the daemon did not switch" words on a 502', async () => {
        const detail = 'Saved, but the GAIA daemon did not switch: timed out';
        fetchMock().mockResolvedValue(jsonResponse({ detail }, 502));
        await expect(setLemonadeTarget({ mode: 'gaia' })).rejects.toThrow(detail);
    });
});

describe('redactSecrets', () => {
    it('masks credential fields before a request body is logged', () => {
        expect(redactSecrets({ mode: 'external', base_url: 'http://x', api_key: 's3cret' }))
            .toEqual({ mode: 'external', base_url: 'http://x', api_key: '***' });
        expect(redactSecrets({ token: 't', api_key: '' })).toEqual({ token: '***', api_key: '' });
        expect(redactSecrets(undefined)).toBeUndefined();
    });
});
