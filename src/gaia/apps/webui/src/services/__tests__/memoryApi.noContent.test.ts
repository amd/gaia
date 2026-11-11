// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deleteGoal } from '../memoryApi';

describe('memoryApi on a 204 reply', () => {
    beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('resolves even when the empty reply is labelled JSON', async () => {
        // v0.25.1's DELETE /api/goals/{id} sent exactly this, and the page showed an error.
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            new Response(null, { status: 204, headers: { 'content-type': 'application/json' } }),
        );
        await expect(deleteGoal('g1')).resolves.toBeUndefined();
    });
});
