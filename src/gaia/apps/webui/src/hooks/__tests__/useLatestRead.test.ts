// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useLatestRead } from '../useLatestRead';

describe('useLatestRead', () => {
    it('lets a read land when nothing newer has', () => {
        const { result } = renderHook(() => useLatestRead());
        expect(result.current()()).toBe(true);
    });

    it('drops a read that answers after a newer one landed', () => {
        const { result } = renderHook(() => useLatestRead());
        const older = result.current();
        const newer = result.current();
        expect(newer()).toBe(true);
        expect(older()).toBe(false);
    });

    it('still lands a read when newer ones have started but none has answered', () => {
        // Re-reads every two seconds against a server that takes three: each must not retire the last.
        const { result } = renderHook(() => useLatestRead());
        const first = result.current();
        result.current();
        result.current();
        expect(first()).toBe(true);
    });

    it('lands reads in the order they answer when that is also the order they started', () => {
        const { result } = renderHook(() => useLatestRead());
        const a = result.current();
        const b = result.current();
        expect(a()).toBe(true);
        expect(b()).toBe(true);
    });

    it('drops a read that answers after the component is gone', () => {
        const { result, unmount } = renderHook(() => useLatestRead());
        const read = result.current();
        unmount();
        expect(read()).toBe(false);
    });
});
