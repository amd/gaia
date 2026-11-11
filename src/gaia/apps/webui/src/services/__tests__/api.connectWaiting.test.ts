// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, connectProviderWaitingForOs } from '../api';

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

const fetchMock = () => fetch as ReturnType<typeof vi.fn>;
const ASKING = { detail: 'macOS is asking GAIA for keychain access to read your saved Fireworks AI key. Look for a system dialog and click Always Allow; this updates once you answer.' };
const CONNECTED = { id: 'fireworks', models_discovered: true, remembered: false, remember_error: null };

describe('connectProviderWaitingForOs', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.stubGlobal('fetch', vi.fn());
    });
    afterEach(() => {
        vi.useRealTimers();
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('says the OS is asking, then connects once the user answers', async () => {
        fetchMock()
            .mockResolvedValueOnce(jsonResponse(ASKING, 409))
            .mockResolvedValueOnce(jsonResponse(CONNECTED));
        const waiting = vi.fn();
        const done = connectProviderWaitingForOs('fireworks', {}, waiting);
        await vi.advanceTimersByTimeAsync(2000);
        expect(await done).toMatchObject({ models_discovered: true });
        expect(waiting).toHaveBeenCalledWith(ASKING.detail);
        expect(fetchMock()).toHaveBeenCalledTimes(2);
    });

    it('fails at once on any other error', async () => {
        fetchMock().mockResolvedValueOnce(jsonResponse({ detail: 'bad key' }, 401));
        const waiting = vi.fn();
        await expect(connectProviderWaitingForOs('fireworks', {}, waiting)).rejects.toBeInstanceOf(ApiError);
        expect(waiting).not.toHaveBeenCalled();
    });

    it('stops asking when told to', async () => {
        fetchMock().mockResolvedValue(jsonResponse(ASKING, 409));
        await expect(connectProviderWaitingForOs('fireworks', {}, vi.fn(), () => true)).rejects.toBeInstanceOf(ApiError);
        expect(fetchMock()).toHaveBeenCalledTimes(1);
    });
});
