// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Seconds of one silent model phase before the indicator says it is still waiting. */
export const MODEL_WAIT_HINT_S = 45;

export const MODEL_WAIT_HINT =
    'Still waiting on the model — nothing has come back yet. A hosted model that stays silent is retried, then stopped with an error. Press Stop to end it now.';

/** On this PC nothing is retried; a long prompt is simply slow to read. */
export const LOCAL_MODEL_WAIT_HINT =
    'Still waiting on the model — nothing has come back yet. A long prompt can take a minute or more to read on this PC. Press Stop to end it now.';

/** Phases where GAIA is waiting on the model itself, not running a tool or counting its output. */
export function isModelWaitLabel(label: string | null | undefined): boolean {
    if (!label) return true;
    return label === 'Thinking...' || label.startsWith('Reading ') || /— retrying (now|in \d+s)/.test(label);
}
