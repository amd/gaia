// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useFollowScroll } from '../useFollowScroll';

let resize: (() => void) | null = null;

class StubResizeObserver {
    constructor(cb: () => void) { resize = cb; }
    observe() {}
    disconnect() { resize = null; }
}

function makeList(clientHeight: number, scrollHeight: number, scrollTop: number) {
    const el = document.createElement('div');
    let height = clientHeight;
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => height });
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => scrollHeight });
    el.scrollTop = scrollTop;
    return { el, setHeight: (h: number) => { height = h; } };
}

describe('useFollowScroll', () => {
    beforeEach(() => { vi.stubGlobal('ResizeObserver', StubResizeObserver); });
    afterEach(() => { vi.unstubAllGlobals(); resize = null; });

    it('keeps the newest row visible when a card in the composer shrinks the list', () => {
        const { el, setHeight } = makeList(500, 1500, 1000);
        renderHook(() => useFollowScroll({ current: el }, { current: true }, () => {}));

        setHeight(300);
        resize?.();

        expect(el.scrollTop).toBe(1500);
    });

    it('leaves a reader who scrolled up where they are', () => {
        const { el, setHeight } = makeList(500, 1500, 200);
        renderHook(() => useFollowScroll({ current: el }, { current: false }, () => {}));

        setHeight(300);
        resize?.();

        expect(el.scrollTop).toBe(200);
    });

    it('does not jump when the list grows', () => {
        const { el, setHeight } = makeList(300, 1500, 1200);
        renderHook(() => useFollowScroll({ current: el }, { current: true }, () => {}));
        el.scrollTop = 1100;

        setHeight(500);
        resize?.();

        expect(el.scrollTop).toBe(1100);
    });
});
