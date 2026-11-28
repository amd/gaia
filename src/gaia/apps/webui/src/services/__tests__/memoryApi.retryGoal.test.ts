// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { retryGoal } from '../memoryApi';

describe('retryGoal', () => {
    beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('uses the guarded retry route, not the any-state status route', async () => {
        const mockFetch = fetch as ReturnType<typeof vi.fn>;
        mockFetch.mockResolvedValue(
            new Response(JSON.stringify({ id: 'g/1', status: 'queued' }), {
                status: 200,
                headers: { 'content-type': 'application/json' },
            }),
        );
        await retryGoal('g/1');
        const [url, init] = mockFetch.mock.calls[0];
        expect(String(url)).toMatch(/\/goals\/g%2F1\/retry$/);
        expect(init.method).toBe('PUT');
        expect(init.body).toBeUndefined();
    });
});
