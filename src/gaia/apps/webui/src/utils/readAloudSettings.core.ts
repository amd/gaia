// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Voice } from '../services/speechApi';

/** Speeds offered, as percent of normal; the server accepts any in its range. */
export const SPEEDS = [50, 75, 100, 125, 150, 175, 200];

/** The voice picker's groups, in order; each has a `readAloud.group.<accent>.<gender>` label. */
export const VOICE_GROUPS: Array<{ accent: Voice['accent']; gender: Voice['gender'] }> = [
    { accent: 'american', gender: 'female' },
    { accent: 'american', gender: 'male' },
    { accent: 'british', gender: 'female' },
    { accent: 'british', gender: 'male' },
];

/** The read-aloud key the Settings preview plays under. */
export const PREVIEW_ID = 'settings:preview';
// Spoken, not shown, and every Kokoro voice speaks English, so it is not translated.
export const PREVIEW_TEXT = "Hello, I'm GAIA. This is how I'll read my replies to you.";

/** The speeds to list: a saved speed that isn't one of SPEEDS is added in order. */
export function speedChoices(saved: number): number[] {
    return SPEEDS.includes(saved) ? SPEEDS : [...SPEEDS, saved].sort((a, b) => a - b);
}
