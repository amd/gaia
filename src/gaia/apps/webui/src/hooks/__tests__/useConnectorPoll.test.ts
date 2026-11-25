// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useConnectorPoll } from '../useConnectorPoll';

let state: DocumentVisibilityState = 'visible';

function show(next: DocumentVisibilityState) {
    state = next;
    act(() => { document.dispatchEvent(new Event('visibilitychange')); });
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

describe('useConnectorPoll', () => {
    it('re-reads every 4 s while shown, not at all while hidden, and once on return', () => {
        const refresh = vi.fn();
        renderHook(() => useConnectorPoll(true, refresh));
        act(() => { vi.advanceTimersByTime(8000); });
        expect(refresh).toHaveBeenCalledTimes(2);

        show('hidden');
        act(() => { vi.advanceTimersByTime(60_000); });
        expect(refresh).toHaveBeenCalledTimes(2);

        show('visible');
        expect(refresh).toHaveBeenCalledTimes(3);
    });

    it('gives up after ten minutes on screen', () => {
        const refresh = vi.fn();
        renderHook(() => useConnectorPoll(true, refresh));
        act(() => { vi.advanceTimersByTime(10 * 60_000); });
        expect(refresh).toHaveBeenCalledTimes(150);
        act(() => { vi.advanceTimersByTime(60_000); });
        show('hidden');
        show('visible');
        expect(refresh).toHaveBeenCalledTimes(150);
    });

    it('stops on unmount', () => {
        const refresh = vi.fn();
        const { unmount } = renderHook(() => useConnectorPoll(true, refresh));
        unmount();
        act(() => { vi.advanceTimersByTime(60_000); });
        show('hidden');
        show('visible');
        expect(refresh).not.toHaveBeenCalled();
    });
});
