// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Seconds of one silent model phase before the indicator says it is still waiting. */
export const MODEL_WAIT_HINT_S = 45;

/** Phases where GAIA is waiting on the model itself, not running a tool or counting its output. */
export function isModelWaitLabel(label: string | null | undefined): boolean {
    if (!label) return true;
    return label === 'Thinking...' || label.startsWith('Reading ') || /— retrying (now|in \d+s)/.test(label);
}
