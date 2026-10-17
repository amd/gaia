// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setVisibleInterval } from '../visibleInterval';

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
