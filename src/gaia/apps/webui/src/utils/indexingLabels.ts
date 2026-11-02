// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { IndexProgress } from '../services/api';
import type { Translate } from '../i18n/useT';
import type { StreamEvent } from '../types';

// One digit style in both halves of a bracketed label.
export const fmt = (n: number) => n.toLocaleString('en-US');

/** "Reading page 37 of 229", "Embedding 412 of 1,180"; just the count when the total isn't known. */
export function progressLabel(p: IndexProgress, t: Translate): string {
    if (p.phase === 'queued') return t('docs.progress.queued');
    const done = fmt(p.done);
    if (p.phase === 'reading') {
        return p.total != null
            ? t('docs.progress.reading', { done, total: fmt(p.total) })
            : t('docs.progress.readingCount', { done });
    }
    return p.total != null
        ? t('docs.progress.embedding', { done, total: fmt(p.total) })
        : t('docs.progress.embeddingCount', { done });
}

/** Minutes worth saying: exact under 20, then to the nearest five. */
function roundedMinutes(seconds: number): number {
    const minutes = Math.max(1, Math.round(seconds / 60));
    return minutes < 20 ? minutes : 5 * Math.round(minutes / 5);
}

/**
 * The time left in words, or what stands in for it. Seconds are noise, so the
 * number is rounded harder the further off it is; a stall never shows a number.
 */
export function timeLeftLabel(seconds: number | null | undefined, estimate: string | undefined, t: Translate): string {
    if (estimate === 'stalled') return t('docs.eta.stalled');
    if (estimate === 'waiting') return t('docs.eta.waiting');
    const atLeast = estimate === 'at_least';
    if (seconds == null || (estimate !== 'ready' && !atLeast)) return t('docs.eta.estimating');
    if (seconds < 60) return atLeast ? t('docs.eta.estimating') : t('docs.eta.lessThanMinute');
    const minutes = roundedMinutes(seconds);
    if (minutes < 60) return t.plural(atLeast ? 'docs.eta.atLeastMinutes' : 'docs.eta.aboutMinutes', minutes);
    // Nearest ten minutes, then nearest hour once it is hours away.
    const tens = minutes >= 300 ? 60 * Math.round(minutes / 60) : 10 * Math.round(minutes / 10);
    const hours = Math.floor(tens / 60);
    const rest = tens % 60;
    if (rest === 0) return t.plural(atLeast ? 'docs.eta.atLeastHours' : 'docs.eta.aboutHours', hours);
    return t(atLeast ? 'docs.eta.atLeastHoursMinutes' : 'docs.eta.aboutHoursMinutes', { hours, minutes: rest });
}

/** File names in one line, with the chosen language's comma. */
export const joinNames = (names: string[], t: Translate) => names.join(t.locale === 'ar' ? '، ' : ', ');

/** A status event's line in the chosen language, where the backend says what it means. */
export function statusEventLabel(event: StreamEvent, t: Translate): string | undefined {
    const { notice, index } = event;
    if (notice) {
        const names = joinNames(notice.names, t);
        return t(notice.kind === 'still_indexing' ? 'chat.indexing.step' : 'chat.notIndexed.step', { names });
    }
    if (index) {
        const line = `${index.name}: ${progressLabel(index, t)}`;
        return index.estimate ? `${line} · ${timeLeftLabel(index.seconds_left, index.estimate, t)}` : line;
    }
    return event.message;
}
