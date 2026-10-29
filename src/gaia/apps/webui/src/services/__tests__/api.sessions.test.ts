// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Every chat reaches the sidebar, not just the first page (#5254). */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { listSessions, SESSION_PAGE_SIZE } from '../api';

function jsonResponse(body: unknown): Response {
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
}

const chats = (from: number, n: number) =>
    Array.from({ length: n }, (_, i) => ({ id: `s${from + i}`, title: `Chat ${from + i}` }));

describe('listSessions', () => {
    beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
    afterEach(() => vi.unstubAllGlobals());

    it('reads page after page until it has every chat', async () => {
        const total = SESSION_PAGE_SIZE * 2 + 30;
        const fetchMock = fetch as ReturnType<typeof vi.fn>;
        fetchMock.mockImplementation(async (url: string) => {
            const offset = Number(new URL(url, 'http://x').searchParams.get('offset'));
            return jsonResponse({ sessions: chats(offset, Math.min(SESSION_PAGE_SIZE, total - offset)), total });
        });

        const { sessions } = await listSessions();

        expect(sessions).toHaveLength(total);
        expect(fetchMock).toHaveBeenCalledTimes(3);
        expect(String(fetchMock.mock.calls[2][0])).toContain(`limit=${SESSION_PAGE_SIZE}&offset=${SESSION_PAGE_SIZE * 2}`);
    });

    it('keeps one copy of a chat that moved between two pages', async () => {
        const fetchMock = fetch as ReturnType<typeof vi.fn>;
        const first = chats(0, SESSION_PAGE_SIZE);
        fetchMock
            .mockResolvedValueOnce(jsonResponse({ sessions: first, total: SESSION_PAGE_SIZE + 1 }))
            .mockResolvedValueOnce(jsonResponse({ sessions: [first[SESSION_PAGE_SIZE - 1]], total: SESSION_PAGE_SIZE + 1 }));
        const { sessions } = await listSessions();
        expect(sessions).toHaveLength(SESSION_PAGE_SIZE);
    });

    it('makes one request when everything fits a page', async () => {
        const fetchMock = fetch as ReturnType<typeof vi.fn>;
        fetchMock.mockResolvedValue(jsonResponse({ sessions: chats(0, 3), total: 3 }));
        expect((await listSessions()).sessions).toHaveLength(3);
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });
});
