// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useOnboarding, ONBOARDING_STEPS } from '../useOnboarding';

describe('useOnboarding state machine', () => {
    it('starts on welcome', () => {
        const { result } = renderHook(() => useOnboarding());
        expect(result.current.step).toBe('welcome');
        expect(result.current.index).toBe(0);
        expect(result.current.isFirst).toBe(true);
        expect(result.current.isLast).toBe(false);
        expect(result.current.total).toBe(ONBOARDING_STEPS.length);
    });

    it('advances forward through every step', () => {
        const { result } = renderHook(() => useOnboarding());
        for (let i = 1; i < ONBOARDING_STEPS.length; i++) {
            act(() => { result.current.next(); });
            expect(result.current.step).toBe(ONBOARDING_STEPS[i]);
        }
        expect(result.current.isLast).toBe(true);
    });

    it('does not advance past the last step', () => {
        const { result } = renderHook(() => useOnboarding('done'));
        act(() => {
            const moved = result.current.next();
            expect(moved).toBe(false);
        });
        expect(result.current.step).toBe('done');
    });

    it('goes back and clamps at the first step', () => {
        const { result } = renderHook(() => useOnboarding('preflight'));
        act(() => { result.current.back(); });
        expect(result.current.step).toBe('welcome');
        act(() => {
            const moved = result.current.back();
            expect(moved).toBe(false);
        });
        expect(result.current.step).toBe('welcome');
    });

    it('blocks next() while the current step is guarded', () => {
        const { result } = renderHook(() => useOnboarding('preflight'));
        act(() => { result.current.setGuard(true); });
        expect(result.current.guarded).toBe(true);
        act(() => {
            const moved = result.current.next();
            expect(moved).toBe(false);
        });
        expect(result.current.step).toBe('preflight');

        act(() => { result.current.setGuard(false); });
        act(() => { result.current.next(); });
        expect(result.current.step).toBe('download');
    });

    it('skip() bypasses a guard (optional-step escape)', () => {
        const { result } = renderHook(() => useOnboarding('download'));
        act(() => { result.current.setGuard(true); });
        act(() => { result.current.skip(); });
        expect(result.current.step).toBe('connector');
    });

    it('guard is scoped per step — moving back to a clean step is not guarded', () => {
        const { result } = renderHook(() => useOnboarding('preflight'));
        act(() => { result.current.setGuard(true); });
        // skip past the guarded step, then walk back to it
        act(() => { result.current.skip(); }); // -> download
        expect(result.current.guarded).toBe(false); // download has no guard
        act(() => { result.current.back(); }); // -> preflight
        expect(result.current.guarded).toBe(true); // its guard persists
    });

    it('goTo jumps to an explicit step ignoring guards', () => {
        const { result } = renderHook(() => useOnboarding('welcome'));
        act(() => { result.current.goTo('connector'); });
        expect(result.current.step).toBe('connector');
        act(() => { result.current.goTo('preflight'); });
        expect(result.current.step).toBe('preflight');
    });
});
