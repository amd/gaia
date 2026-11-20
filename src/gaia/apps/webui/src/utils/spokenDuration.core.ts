// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { MessageKey, Say } from '../i18n/registry';
import { tEn } from '../i18n/translator';

/** Marks a number with its unit, so the renderer can keep it left to right. */
export type Mark = (text: string) => string;
const plain: Mark = (text) => text;

/** Swaps a key for a surface's own wording of it (the phone's "your PC", Arabic unit letters). */
export type Variant = (key: MessageKey) => MessageKey;
export const same: Variant = (key) => key;

/** `40200` -> "40 s", `400` -> "under a second", `95000` -> "1 min 35 s". */
export function spokenDuration(ms: number, say: Say = tEn.bare, mark: Mark = plain, variant: Variant = same): string {
    if (ms < 1000) return say('answer.duration.underSecond');
    const total = Math.round(ms / 1000);
    if (total < 60) return mark(say(variant('answer.duration.sec'), { n: total }));
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return mark(secs ? say(variant('answer.duration.minSec'), { m: mins, s: secs }) : say(variant('answer.duration.min'), { n: mins }));
}
