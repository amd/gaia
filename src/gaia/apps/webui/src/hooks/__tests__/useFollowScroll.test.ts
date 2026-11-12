// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useFollowScroll } from '../useFollowScroll';

let resized: (() => void) | null = null;

class FakeResizeObserver {
    constructor(cb: () => void) { resized = cb; }
    observe() {}
    disconnect() { resized = null; }
}

function makeList(): HTMLDivElement {
    const el = document.createElement('div');
    Object.defineProperty(el, 'scrollHeight', { value: 8812, configurable: true });
    el.scrollTop = 8110;
    return el;
}

describe('useFollowScroll', () => {
    beforeEach(() => { vi.stubGlobal('ResizeObserver', FakeResizeObserver); });
    afterEach(() => { vi.unstubAllGlobals(); resized = null; });

    it('re-pins to the end when the list shrinks while following', () => {
        const el = makeList();
        renderHook(() => useFollowScroll({ current: el }, { current: true }, () => {}));

        resized?.();

        expect(el.scrollTop).toBe(8812);
    });

    it('leaves a reader who scrolled up where they are', () => {
        const el = makeList();
        renderHook(() => useFollowScroll({ current: el }, { current: false }, () => {}));

        resized?.();

        expect(el.scrollTop).toBe(8110);
    });

    it('stops watching on unmount', () => {
        const el = makeList();
        const { unmount } = renderHook(() => useFollowScroll({ current: el }, { current: true }, () => {}));

        unmount();

        expect(resized).toBeNull();
    });
});
