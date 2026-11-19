// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Loader2 } from 'lucide-react';
import { useT } from '../../i18n/useT';
import type { SetupPlan, SetupRunStatus } from '../../types';

interface Props {
    run: SetupRunStatus;
    /** The parts the run sets up; without them only `idle` and the download's percent show. */
    plan?: SetupPlan | null;
    /** What to say when no part is reported active yet. */
    idle: string;
    /** False where the screen already has a progress bar, so one screen shows one. */
    bar?: boolean;
}

/**
 * A run with no model rows yet: the part being set up, how far its download
 * is when the run reports it, and how many parts are ready. Nothing estimated.
 */
export function RunActivity({ run, plan, idle, bar = true }: Props) {
    const t = useT();
    const parts = plan?.components ?? [];
    const active = parts.find((c) => run.components?.[c.id] === 'active');
    const ready = parts.filter((c) => run.components?.[c.id] === 'done' || (run.components?.[c.id] == null && c.installed)).length;
    const percent = run.percent != null ? Math.max(0, Math.min(100, Math.round(run.percent))) : null;
    const label = active ? t('setup.download.now', { name: t(`setup.component.${active.id}`) }) : idle;
    return (
        <div className="setup-activity">
            <p className="setup-hint">
                <Loader2 size={13} className="spin" aria-hidden="true" />{' '}
                <span role="status">{label}</span>
                {percent != null && <> · <bdi dir="ltr">{percent}%</bdi></>}
                {parts.length > 0 && <> · {t('setup.download.partsReady', { done: String(ready), total: String(parts.length) })}</>}
            </p>
            {bar && percent != null && <progress className="setup-progress" max={100} value={percent} aria-label={label} />}
        </div>
    );
}
