// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { IndexProgress } from '../services/api';
import { useT } from '../i18n/useT';
import { progressLabel, progressPercent } from '../utils/documentLibrary.core';
import './IndexingProgress.css';

export { progressLabel, progressPercent };

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
