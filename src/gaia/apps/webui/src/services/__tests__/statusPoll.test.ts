// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api');

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
});
