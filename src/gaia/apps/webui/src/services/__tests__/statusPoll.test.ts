// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', async (orig) => ({ ...(await orig<typeof import('../api')>()), getSystemStatus: vi.fn() }));

import * as api from '../api';
import { createStatusPoller } from '../statusPoll';

const mocked = vi.mocked(api);

afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
});

describe('createStatusPoller', () => {
    it('cancels the request in flight when stopped, and reports nothing for it', async () => {
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
        let signal: AbortSignal | undefined;
        mocked.getSystemStatus.mockImplementation((s?: AbortSignal) => {
            signal = s;
            return new Promise((_resolve, reject) => {
                s?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
            });
        });
        const onOutcome = vi.fn();
        const stop = createStatusPoller(onOutcome).start();
        await vi.advanceTimersByTimeAsync(1_000);
        stop();
        await vi.advanceTimersByTimeAsync(0);
        expect(signal?.aborted).toBe(true);
        expect(onOutcome).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(120_000);
        expect(mocked.getSystemStatus).toHaveBeenCalledTimes(1);
    });

    it.each([500, 503])('tells a backend that answers HTTP %i apart from one that cannot be reached', async (code) => {
        mocked.getSystemStatus.mockRejectedValue(new api.ApiError(`HTTP ${code}`, code, 'boom', null));
        const outcomes: unknown[] = [];
        await createStatusPoller((o) => outcomes.push(o)).check();
        expect(outcomes[0]).toMatchObject({ kind: 'error', httpStatus: code });
    });

    // The status handler never raises these; a dev proxy, reverse proxy or tunnel does.
    it('reads a gateway HTTP 502 as an unreachable backend', async () => {
        mocked.getSystemStatus.mockRejectedValue(new api.ApiError('HTTP 502', 502, 'Bad Gateway', null));
        const outcomes: unknown[] = [];
        await createStatusPoller((o) => outcomes.push(o)).check();
        expect(outcomes[0]).toMatchObject({ kind: 'unreachable' });
    });

    it('reads a gateway HTTP 504 as a slow backend, not a lost one', async () => {
        mocked.getSystemStatus.mockRejectedValue(new api.ApiError('HTTP 504', 504, 'Gateway Timeout', null));
        const outcomes: unknown[] = [];
        await createStatusPoller((o) => outcomes.push(o)).check();
        expect(outcomes[0]).toMatchObject({ kind: 'slow' });
    });

    it('shares the request in flight when Check again is pressed during a slow probe', async () => {
        let answer!: (v: never) => void;
        mocked.getSystemStatus.mockImplementation(() => new Promise((r) => { answer = r as never; }));
        const onOutcome = vi.fn();
        const poller = createStatusPoller(onOutcome);
        const first = poller.check();
        const second = poller.check();
        expect(mocked.getSystemStatus).toHaveBeenCalledTimes(1);
        answer({ lemonade_running: true } as never);
        await Promise.all([first, second]);
        expect(onOutcome).toHaveBeenCalledTimes(1);
    });

    it('starts a fresh request when restarted right after a stop, as StrictMode does', async () => {
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
        const signals: AbortSignal[] = [];
        mocked.getSystemStatus.mockImplementation((s?: AbortSignal) => {
            if (s) signals.push(s);
            return new Promise(() => {});
        });
        const poller = createStatusPoller(vi.fn());
        const stop = poller.start();
        stop();
        poller.start();
        await vi.advanceTimersByTimeAsync(0);
        expect(mocked.getSystemStatus).toHaveBeenCalledTimes(2);
        expect(signals[1].aborted).toBe(false);
    });

    it('keeps polling when handling an outcome throws, and reports it', async () => {
        mocked.getSystemStatus.mockResolvedValue({ lemonade_running: true } as never);
        const onOutcome = vi.fn(() => { throw new Error('render failed'); });
        const poller = createStatusPoller(onOutcome);
        await expect(poller.check()).resolves.toBeUndefined();
        await expect(poller.check()).resolves.toBeUndefined();
        expect(onOutcome).toHaveBeenCalledTimes(2);
    });
});
