// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { IndexProgress } from '../services/api';
import { useT, type Translate } from '../i18n/useT';
import './IndexingProgress.css';

// One digit style in both halves of a bracketed label.
const fmt = (n: number) => n.toLocaleString('en-US');

/** "Reading page 37 of 229", "Embedding 412 of 1,180"; just the count when the total isn't known. */
export function progressLabel(p: IndexProgress, t: Translate): string {
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

/** A bar and a line saying how far indexing has got; indeterminate without a total. */
export function IndexingProgress({ progress }: { progress: IndexProgress }) {
    const t = useT();
    const label = progressLabel(progress, t);
    const pct = progressPercent(progress);
    return (
        // Only drawn while indexing runs, so its sweep is live state.
        <div className="index-progress is-running">
            <div
                className={`index-progress-track${pct == null ? ' is-indeterminate' : ''}`}
                role="progressbar"
                aria-label={label}
                aria-valuemin={pct == null ? undefined : 0}
                aria-valuemax={pct == null ? undefined : 100}
                aria-valuenow={pct ?? undefined}
            >
                <div className="index-progress-fill" style={pct == null ? undefined : { inlineSize: `${pct}%` }} />
            </div>
            <span className="index-progress-label">
                {label}
                {pct != null && <span className="index-progress-pct"> · {pct}%</span>}
            </span>
        </div>
    );
}
