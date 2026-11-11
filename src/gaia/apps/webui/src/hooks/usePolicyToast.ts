// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';

/** How long the "blocked by policy" toast stays on screen. */
export const POLICY_TOAST_DURATION_MS = 5200;

export interface PolicyToast {
    tool: string;
    receiptId?: string;
}

export interface UsePolicyToastResult {
    policyToast: PolicyToast | null;
    /** Show a toast; a toast already on screen is replaced and the timer restarts. */
    showPolicyToast: (toast: PolicyToast) => void;
    /** Hide the toast now and cancel its pending auto-hide. */
    dismissPolicyToast: () => void;
}

/** Transient policy-block toast that hides itself after `durationMs`. */
export function usePolicyToast(durationMs: number = POLICY_TOAST_DURATION_MS): UsePolicyToastResult {
    const [policyToast, setPolicyToast] = useState<PolicyToast | null>(null);
    const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const clearTimer = useCallback(() => {
        if (timerRef.current) {
            clearTimeout(timerRef.current);
            timerRef.current = null;
        }
    }, []);

    const dismissPolicyToast = useCallback(() => {
        clearTimer();
        setPolicyToast(null);
    }, [clearTimer]);

    const showPolicyToast = useCallback((toast: PolicyToast) => {
        clearTimer();
        setPolicyToast(toast);
        timerRef.current = setTimeout(() => {
            timerRef.current = null;
            setPolicyToast(null);
        }, durationMs);
    }, [clearTimer, durationMs]);

    useEffect(() => clearTimer, [clearTimer]);

    return { policyToast, showPolicyToast, dismissPolicyToast };
}
