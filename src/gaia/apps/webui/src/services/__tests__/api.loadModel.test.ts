// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { loadModelAskingFirst } from '../api';

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

const fetchMock = () => fetch as ReturnType<typeof vi.fn>;
const sentBodies = () => fetchMock().mock.calls.map(([, init]) => JSON.parse(String((init as RequestInit).body)));
const BUSY = { detail: '1 chat(s) are answering right now.' };

describe('loadModelAskingFirst', () => {
    beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('loads straight away when no chat is answering', async () => {
        fetchMock().mockResolvedValue(jsonResponse({ status: 'loading' }, 202));
        const ask = vi.fn();
        expect(await loadModelAskingFirst('M-GGUF', 65536, ask)).toBe(true);
        expect(ask).not.toHaveBeenCalled();
        expect(sentBodies()).toEqual([{ model_name: 'M-GGUF', ctx_size: 65536, force: false }]);
    });

    it('asks while a chat answers, and loads with force on yes', async () => {
        fetchMock()
            .mockResolvedValueOnce(jsonResponse(BUSY, 409))
            .mockResolvedValueOnce(jsonResponse({ status: 'loading' }, 202));
        const ask = vi.fn((_question: string) => true);
        expect(await loadModelAskingFirst('M-GGUF', undefined, ask)).toBe(true);
        expect(ask.mock.calls[0][0]).toContain('answering');
        expect(sentBodies()[1].force).toBe(true);
    });

    it('sends nothing more when the user chooses to wait', async () => {
        fetchMock().mockResolvedValueOnce(jsonResponse(BUSY, 409));
        expect(await loadModelAskingFirst('M-GGUF', undefined, () => false)).toBe(false);
        expect(fetchMock()).toHaveBeenCalledTimes(1);
    });
});
