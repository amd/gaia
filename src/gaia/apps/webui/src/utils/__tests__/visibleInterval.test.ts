// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { runWhileVisible, setVisibleInterval } from '../visibleInterval';

let state: DocumentVisibilityState = 'visible';

function show(next: DocumentVisibilityState) {
    state = next;
    document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
    vi.useFakeTimers();
    state = 'visible';
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => state);
});

afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
});

describe('setVisibleInterval', () => {
    it('ticks while shown, skips while hidden, and catches up once shown again', () => {
        const fn = vi.fn();
        const stop = setVisibleInterval(fn, 1000);
        vi.advanceTimersByTime(2000);
        expect(fn).toHaveBeenCalledTimes(2);

        show('hidden');
        vi.advanceTimersByTime(5000);
        expect(fn).toHaveBeenCalledTimes(2);

        show('visible');
        expect(fn).toHaveBeenCalledTimes(3);
        stop();
        vi.advanceTimersByTime(5000);
        show('visible');
        expect(fn).toHaveBeenCalledTimes(3);
    });
});

describe('runWhileVisible', () => {
    it('stops the poll while hidden and restarts it once when shown again', () => {
        const stopPoll = vi.fn();
        const start = vi.fn(() => stopPoll);
        const end = runWhileVisible(start);
        expect(start).toHaveBeenCalledTimes(1);

        show('hidden');
        expect(stopPoll).toHaveBeenCalledTimes(1);
        show('hidden');
        expect(stopPoll).toHaveBeenCalledTimes(1);

        show('visible');
        show('visible');
        expect(start).toHaveBeenCalledTimes(2);

        end();
        expect(stopPoll).toHaveBeenCalledTimes(2);
        show('hidden');
        show('visible');
        expect(start).toHaveBeenCalledTimes(2);
        expect(stopPoll).toHaveBeenCalledTimes(2);
    });

    it('does not start a poll for a window that opens hidden', () => {
        state = 'hidden';
        const start = vi.fn(() => vi.fn());
        const end = runWhileVisible(start);
        expect(start).not.toHaveBeenCalled();
        end();
        show('visible');
        expect(start).not.toHaveBeenCalled();
    });
});
