// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * First-run onboarding wizard state machine (#1726, #1727).
 *
 * Pure navigation logic: the ordered steps, the current position, and the
 * guard that stops a step being left before its work is done (a hardware
 * blocker on pre-flight, an unfinished model download). Data fetching lives in
 * the step components; this hook only decides *where* we are and *whether we
 * may move*. Keeping it side-effect-free makes the gating trivially testable.
 */

import { useCallback, useMemo, useState } from 'react';

/** Ordered wizard steps. ``done`` is the terminal confirmation screen. */
export const ONBOARDING_STEPS = [
    'welcome',
    'preflight',
    'download',
    'connector',
    'done',
] as const;

export type OnboardingStep = (typeof ONBOARDING_STEPS)[number];

export interface UseOnboarding {
    /** The step currently shown. */
    step: OnboardingStep;
    /** Zero-based index of the current step. */
    index: number;
    /** Total number of steps. */
    total: number;
    isFirst: boolean;
    isLast: boolean;
    /**
     * Whether leaving the current step forward is currently blocked. A guarded
     * step disables the primary "Next" action; ``skip()`` bypasses it (the
     * explicit power-user / optional-step escape) so a guard never traps the
     * user, it only stops a *silent* advance past unfinished work.
     */
    guarded: boolean;
    /** Mark the current step as guarded (true) or clear the guard (false). */
    setGuard: (blocked: boolean) => void;
    /**
     * Advance to the next step. Returns ``false`` (a no-op) when the current
     * step is guarded — the caller should keep the Next button disabled in
     * that case rather than relying on this return.
     */
    next: () => boolean;
    /** Go back one step (clears any guard on the step we land on). */
    back: () => boolean;
    /** Jump to an explicit step, ignoring guards (used by "Re-scan" etc.). */
    goTo: (step: OnboardingStep) => void;
    /** Advance past an optional/guarded step deliberately (records nothing). */
    skip: () => void;
}

export function useOnboarding(initial: OnboardingStep = 'welcome'): UseOnboarding {
    const [index, setIndex] = useState<number>(() =>
        Math.max(0, ONBOARDING_STEPS.indexOf(initial)),
    );
    // Guard is keyed by step so navigating back and forth doesn't leak a guard
    // set on a different step.
    const [guards, setGuards] = useState<Partial<Record<OnboardingStep, boolean>>>({});

    const step = ONBOARDING_STEPS[index];
    const guarded = !!guards[step];

    const setGuard = useCallback(
        (blocked: boolean) => {
            setGuards((prev) => (prev[step] === blocked ? prev : { ...prev, [step]: blocked }));
        },
        [step],
    );

    const next = useCallback((): boolean => {
        if (guards[step]) return false;
        let moved = false;
        setIndex((i) => {
            if (i < ONBOARDING_STEPS.length - 1) {
                moved = true;
                return i + 1;
            }
            return i;
        });
        return moved;
    }, [guards, step]);

    const skip = useCallback(() => {
        setIndex((i) => Math.min(i + 1, ONBOARDING_STEPS.length - 1));
    }, []);

    const back = useCallback((): boolean => {
        let moved = false;
        setIndex((i) => {
            if (i > 0) {
                moved = true;
                return i - 1;
            }
            return i;
        });
        return moved;
    }, []);

    const goTo = useCallback((target: OnboardingStep) => {
        const i = ONBOARDING_STEPS.indexOf(target);
        if (i >= 0) setIndex(i);
    }, []);

    return useMemo(
        () => ({
            step,
            index,
            total: ONBOARDING_STEPS.length,
            isFirst: index === 0,
            isLast: index === ONBOARDING_STEPS.length - 1,
            guarded,
            setGuard,
            next,
            back,
            goTo,
            skip,
        }),
        [step, index, guarded, setGuard, next, back, goTo, skip],
    );
}
