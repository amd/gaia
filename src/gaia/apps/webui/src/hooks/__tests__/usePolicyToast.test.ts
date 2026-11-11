// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { POLICY_TOAST_DURATION_MS, usePolicyToast } from '../usePolicyToast';

describe('usePolicyToast', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });
    afterEach(() => {
        vi.useRealTimers();
    });

    it('starts with no toast', () => {
        const { result } = renderHook(() => usePolicyToast());

        expect(result.current.policyToast).toBeNull();
    });

    it('shows the toast, then hides it after the default duration', () => {
        const { result } = renderHook(() => usePolicyToast());

        act(() => result.current.showPolicyToast({ tool: 'shell', receiptId: 'r1' }));
        expect(result.current.policyToast).toEqual({ tool: 'shell', receiptId: 'r1' });

        act(() => { vi.advanceTimersByTime(POLICY_TOAST_DURATION_MS - 1); });
        expect(result.current.policyToast).not.toBeNull();

        act(() => { vi.advanceTimersByTime(1); });
        expect(result.current.policyToast).toBeNull();
    });

    it('honours a custom duration', () => {
        const { result } = renderHook(() => usePolicyToast(1000));

        act(() => result.current.showPolicyToast({ tool: 'shell' }));
        act(() => { vi.advanceTimersByTime(1000); });

        expect(result.current.policyToast).toBeNull();
    });

    it('replaces the toast and restarts the timer when a new one arrives', () => {
        const { result } = renderHook(() => usePolicyToast(1000));

        act(() => result.current.showPolicyToast({ tool: 'first' }));
        act(() => { vi.advanceTimersByTime(800); });
        act(() => result.current.showPolicyToast({ tool: 'second' }));

        // The first toast's timer would have fired here.
        act(() => { vi.advanceTimersByTime(300); });
        expect(result.current.policyToast).toEqual({ tool: 'second' });

        act(() => { vi.advanceTimersByTime(700); });
        expect(result.current.policyToast).toBeNull();
    });

    it('dismiss hides the toast and cancels the pending timer', () => {
        const { result } = renderHook(() => usePolicyToast(1000));

        act(() => result.current.showPolicyToast({ tool: 'shell' }));
        act(() => result.current.dismissPolicyToast());

        expect(result.current.policyToast).toBeNull();
        expect(vi.getTimerCount()).toBe(0);
    });

    it('a dismissed timer does not hide a toast shown afterwards', () => {
        const { result } = renderHook(() => usePolicyToast(1000));

        act(() => result.current.showPolicyToast({ tool: 'first' }));
        act(() => { vi.advanceTimersByTime(900); });
        act(() => result.current.dismissPolicyToast());
        act(() => result.current.showPolicyToast({ tool: 'second' }));
        act(() => { vi.advanceTimersByTime(200); });

        expect(result.current.policyToast).toEqual({ tool: 'second' });
    });

    it('clears the pending timer on unmount', () => {
        const { result, unmount } = renderHook(() => usePolicyToast(1000));

        act(() => result.current.showPolicyToast({ tool: 'shell' }));
        expect(vi.getTimerCount()).toBe(1);

        unmount();

        expect(vi.getTimerCount()).toBe(0);
    });
});
