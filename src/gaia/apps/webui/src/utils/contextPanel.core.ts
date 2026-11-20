// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The context panel's number formatting and wording rules, shared by the Agent UI and the phone app. */

import type { ChatPerformance } from '../types';
import { tEn, type Translate } from '../i18n/translator';

export const exact = (n: number) => n.toLocaleString('en-US');

/** "1.2K". */
export function tokensShort(n: number): string {
    return n < 1000
        ? String(n)
        : n < 1_000_000
            ? `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`
            : `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
}

/** "1.2 s". */
export function secondsShort(s: number): string {
    return s < 0.1 ? (s === 0 ? '0 s' : '<0.1 s') : s < 10 ? `${s.toFixed(1)} s` : `${Math.round(s)} s`;
}

export const pct = (part: number, whole: number) => (whole > 0 ? Math.round((part / whole) * 100) : 0);

/** A share that rounds to nothing but isn't nothing reads "<1", never "0". */
export const pctLabel = (part: number, whole: number) => {
    const p = pct(part, whole);
    return p === 0 && part > 0 ? '<1' : String(p);
};

/** Rounded down, so 99.7% reuse never reads as "100%". */
export const hitRate = (cached: number | null, total: number | null) =>
    total && cached != null ? Math.floor((cached / total) * 100) : null;

/** Below this, time to first token is mostly fixed cost, not reading speed. */
export const MIN_TOKENS_TO_RATE = 256;

/** A cloud provider's name as people know it; an unknown id is shown as given. */
export function cloudProviderName(provider: string): string {
    return provider === 'fireworks' ? 'Fireworks AI' : provider === 'amd' ? 'AMD LLM Gateway' : provider;
}

export function placement(now: ChatPerformance['now'], t: Translate = tEn): string {
    if (now.provider === 'unknown') return t('ctx.unknown');
    if (now.provider === 'cloud') return t('ctx.cloud');
    if (now.provider !== 'local') return t('ctx.cloudVia', { provider: cloudProviderName(now.provider) });
    if (!now.device) return now.loaded === false ? t('ctx.notLoadedNow') : t('ctx.unknown');
    const device = now.device.toUpperCase();
    const backend = now.backend === 'npu' ? 'FLM' : now.backend;
    return backend && backend !== now.device
        ? `${device} · ${backend.charAt(0).toUpperCase()}${backend.slice(1)}`
        : device;
}

/** The newest turn, only when it is the newest answer: a kept "Retry with" has no record, so the last turn is unknown. */
export function latestTurn(perf: ChatPerformance) {
    const last = perf.turns[perf.turns.length - 1] ?? null;
    return last && last.message_id === perf.latest_message_id ? last : null;
}
