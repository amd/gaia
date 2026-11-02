// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { IndexProgress } from '../services/api';
import { useT } from '../i18n/useT';
import { progressLabel, timeLeftLabel } from '../utils/indexingLabels';
import './IndexingProgress.css';

export { progressLabel };

/** Whole percent done, or null when there is no total to measure against. */
export function progressPercent(p: IndexProgress): number | null {
    if (p.phase === 'queued') return 0;
    if (p.total == null || p.total <= 0) return null;
    return Math.min(100, Math.floor((p.done / p.total) * 100));
}

/** The bar itself; indeterminate when `percent` is null. */
export function ProgressBar({ percent, label }: { percent: number | null; label: string }) {
    return (
        <div
            className={`index-progress-track${percent == null ? ' is-indeterminate' : ''}`}
            role="progressbar"
            aria-label={label}
            aria-valuemin={percent == null ? undefined : 0}
            aria-valuemax={percent == null ? undefined : 100}
            aria-valuenow={percent ?? undefined}
        >
            <div className="index-progress-fill" style={percent == null ? undefined : { inlineSize: `${percent}%` }} />
        </div>
    );
}

/** A bar and a line saying how far indexing has got; indeterminate without a total. */
export function IndexingProgress({ progress }: { progress: IndexProgress }) {
    const t = useT();
    const label = progressLabel(progress, t);
    const pct = progressPercent(progress);
    const running = progress.phase !== 'queued';
    return (
        // Only drawn while indexing runs, so its sweep is live state.
        <div className="index-progress is-running">
            <ProgressBar percent={pct} label={label} />
            <span className="index-progress-label">
                <span>
                    {label}
                    {running && pct != null && <span className="index-progress-pct"> · {pct}%</span>}
                </span>
                {running && progress.estimate && (
                    <span className="index-progress-eta">{timeLeftLabel(progress.seconds_left, progress.estimate, t)}</span>
                )}
            </span>
        </div>
    );
}
