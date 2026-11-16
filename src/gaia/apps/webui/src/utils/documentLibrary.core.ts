// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Library formatting shared by the web UI and the phone app; no DOM. */

import { isolate, type MessageKey, type PluralKey } from '../i18n/registry';
import { dateLocale, type Translate } from '../i18n/translator';

/** How far a running index has got: PDF pages read, then chunks embedded. */
export interface IndexProgress {
    /** `starting`: waiting for GAIA's model server to start, at launch. */
    phase: 'starting' | 'reading' | 'embedding';
    done: number;
    /** Null when the count isn't known upfront (e.g. pages of a non-PDF). */
    total: number | null;
}

/** How often an indexing document's status (and progress) is asked for. */
export const INDEX_POLL_MS = 1000;

/** How long a removed document can be put back before it is deleted. */
export const REMOVE_UNDO_MS = 6000;

const FAILURE_HINT_KEYS: Record<string, MessageKey> = {
    corrupted: 'docs.failureHint.corrupted',
    encrypted: 'docs.failureHint.encrypted',
    empty: 'docs.failureHint.empty',
    empty_file: 'docs.failureHint.emptyFile',
    unsupported_workbook: 'docs.failureHint.unsupportedWorkbook',
};

/** What to do about a failure retrying can't fix until the file changes (the backend's error kind); undefined for any other. */
export function failureHint(t: Translate, kind: string | null | undefined): string | undefined {
    const key = kind ? FAILURE_HINT_KEYS[kind] : undefined;
    return key ? t(key) : undefined;
}

/** Whether a document's indexing is still running. */
export function isIndexing(status: string | undefined): boolean {
    return status === 'indexing' || status === 'pending';
}

/** Split a stored indexing error into a one-line summary and the rest. */
export function splitFailureReason(lastError: string): { summary: string; more: string } {
    const text = lastError.replace(/^RAG indexing failed for [^:]+:\s*/, '').trim();
    const [first, ...rest] = text.split('\n');
    return { summary: first.trim(), more: rest.join('\n').trim() };
}

/** Elapsed time since `startMs` as "42s" or "3m 5s". */
export function elapsed(startMs: number, now: number = Date.now()): string {
    const sec = Math.floor((now - startMs) / 1000);
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    const rem = sec % 60;
    return `${min}m ${rem}s`;
}

// One digit style in both halves of a bracketed label.
const fmt = (n: number) => n.toLocaleString('en-US');

/** "Reading page 37 of 229", "Embedding 412 of 1,180"; just the count when the total isn't known. */
export function progressLabel(p: IndexProgress, t: Translate): string {
    if (p.phase === 'starting') return t('docs.progress.starting');
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

/** Whole percent done, or null when there is no total to measure against. */
export function progressPercent(p: IndexProgress): number | null {
    if (p.total == null || p.total <= 0) return null;
    return Math.min(100, Math.floor((p.done / p.total) * 100));
}

/** "3 documents" with the count as its own left-to-right island, so Arabic keeps its colon and "·" in place. */
export function countText(t: Translate, base: PluralKey, n: number): string {
    return t.plural(base, n, { n: isolate(String(n), 'ltr') });
}

/** Format an ISO timestamp as a relative or absolute local time string. */
export function formatTimestamp(isoString: string, t: Translate, now: Date = new Date()): string {
    try {
        const date = new Date(isoString);
        if (isNaN(date.getTime())) return '';
        const diffMs = now.getTime() - date.getTime();
        const diffSec = Math.floor(diffMs / 1000);
        const diffMin = Math.floor(diffSec / 60);
        const diffHour = Math.floor(diffMin / 60);
        const diffDay = Math.floor(diffHour / 24);

        if (diffSec < 60) return t('time.justNow');
        if (diffMin < 60) return t('time.minutesAgo', { n: diffMin });
        if (diffHour < 24) return t('time.hoursAgo', { n: diffHour });
        if (diffDay === 1) return t('time.yesterday');
        if (diffDay < 7) return t('time.daysAgo', { n: diffDay });

        // Older than a week: show date + time
        return date.toLocaleDateString(dateLocale(t), {
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
        });
    } catch {
        return '';
    }
}

/** Format an ISO timestamp as a full local date/time for tooltips. */
export function formatFullTimestamp(isoString: string, t: Translate): string {
    try {
        const date = new Date(isoString);
        if (isNaN(date.getTime())) return '';
        return date.toLocaleString(dateLocale(t), {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
            second: '2-digit',
        });
    } catch {
        return '';
    }
}
