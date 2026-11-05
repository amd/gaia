// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { connectHomeAssistant, disconnectHomeAssistant, getHomeAssistant, testHomeAssistant } from '../api';

const json = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

const call = (i = 0) => {
    const [url, init] = vi.mocked(fetch).mock.calls[i] as [string, RequestInit];
    return { url, init, headers: init.headers as Record<string, string> };
};

beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
afterEach(() => { vi.unstubAllGlobals(); });

describe('Home Assistant API', () => {
    it('connect posts the address and token with the UI header', async () => {
        vi.mocked(fetch).mockResolvedValue(json(200, {
            connected: true, url: 'http://ha.local:8123', active: true, enabled: true, tool_count: 28, detail: 'Connected: 28 Home Assistant tools.',
        }));
        const r = await connectHomeAssistant('http://ha.local:8123', 'tok');
        const { url, init, headers } = call();
        expect(url).toMatch(/\/api\/home-assistant$/);
        expect(init.method).toBe('POST');
        expect(JSON.parse(String(init.body))).toEqual({ url: 'http://ha.local:8123', token: 'tok' });
        expect(headers['x-gaia-ui']).toBe('1');
        expect(r.tool_count).toBe(28);
    });

    it("a 400's message is the backend detail, word for word", async () => {
        const detail = 'Home Assistant has no MCP Server integration. Add "Model Context Protocol Server" under Settings › Devices & services.';
        vi.mocked(fetch).mockResolvedValue(json(400, { detail }));
        await expect(connectHomeAssistant('http://ha.local:8123', 'tok')).rejects.toThrow(detail);
    });

    it('status, test and disconnect hit their routes', async () => {
        vi.mocked(fetch)
            .mockResolvedValueOnce(json(200, { connected: false, url: null, active: false }))
            .mockResolvedValueOnce(json(200, { ok: true, detail: 'ok' }))
            .mockResolvedValueOnce(json(200, { connected: false, url: null, active: false, detail: 'forgot' }));
        await getHomeAssistant();
        await testHomeAssistant();
        await disconnectHomeAssistant();
        expect([0, 1, 2].map((i) => `${call(i).init.method} ${call(i).url.replace(/^.*\/api/, '')}`)).toEqual([
            'GET /home-assistant', 'POST /home-assistant/test', 'DELETE /home-assistant',
        ]);
        expect(call(1).headers['x-gaia-ui']).toBe('1');
        expect(call(2).headers['x-gaia-ui']).toBe('1');
    });
});
