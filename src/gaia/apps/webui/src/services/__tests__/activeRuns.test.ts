// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** One shared poll of /api/chat/active that backs off while idle (#5256). */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BUSY_INTERVAL_MS, IDLE_MAX_MS, refreshActiveRuns, startActiveRunsPoll } from '../activeRuns';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../api';

vi.mock('../api');
const mockedApi = vi.mocked(api);

const IDLE = { session_ids: [], queued: [], awaiting_user: {} };
const BUSY = { session_ids: ['a'], queued: [], awaiting_user: {} };

let stop: (() => void) | null = null;

/** Advance the clock and let the poll's promise chain settle. */
async function advance(ms: number) {
    await vi.advanceTimersByTimeAsync(ms);
}

beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    mockedApi.getActiveRuns.mockResolvedValue(IDLE);
    useChatStore.setState({ runningSessionIds: [], queuedSessionIds: [], awaitingBySession: {}, activeRunsAt: 0, isStreaming: false });
});

afterEach(() => {
    stop?.();
    stop = null;
    vi.useRealTimers();
});

describe('active-runs poll', () => {
    it('shares one request between callers', async () => {
        await Promise.all([refreshActiveRuns(), refreshActiveRuns(), refreshActiveRuns()]);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(1);
        expect(useChatStore.getState().activeRunsAt).toBeGreaterThan(0);
    });

    it('backs off while idle, to at most IDLE_MAX_MS', async () => {
        stop = startActiveRunsPoll();
        await advance(0);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(1);
        // 2 s, 4 s, 8 s, then every 15 s.
        await advance(2_000 + 4_000 + 8_000);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(4);
        await advance(IDLE_MAX_MS);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(5);
        // A minute idle costs a handful of calls, not one a second.
        await advance(60_000);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(9);
    });

    it('polls every second while a chat runs', async () => {
        mockedApi.getActiveRuns.mockResolvedValue(BUSY);
        stop = startActiveRunsPoll();
        await advance(0);
        await advance(BUSY_INTERVAL_MS * 5);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(6);
    });

    it('wakes at once when this window starts a turn', async () => {
        stop = startActiveRunsPoll();
        await advance(0);
        await advance(2_000 + 4_000);
        const before = mockedApi.getActiveRuns.mock.calls.length;
        useChatStore.setState({ isStreaming: true });
        await advance(0);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(before + 1);
        await advance(BUSY_INTERVAL_MS);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(before + 2);
    });

    it('keeps polling after a failed request', async () => {
        mockedApi.getActiveRuns.mockRejectedValueOnce(new Error('backend down'));
        stop = startActiveRunsPoll();
        await advance(0);
        await advance(2_000);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(2);
    });

    it('stops cleanly, and a second start replaces the first', async () => {
        const first = startActiveRunsPoll();
        stop = startActiveRunsPoll();
        await advance(0);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(1);
        first();
        stop();
        stop = null;
        await advance(60_000);
        expect(mockedApi.getActiveRuns).toHaveBeenCalledTimes(1);
    });
});
