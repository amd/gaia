// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { AlertCircle, Check, Loader2 } from 'lucide-react';
import { useT } from '../../i18n/useT';

export interface RailStep {
    id: string;
    label: string;
    status: 'done' | 'current' | 'active' | 'failed' | 'pending';
    /** A Customize step, shown under the step it belongs to. */
    sub?: boolean;
    /** Go back to it. Only steps already passed can be reopened. */
    onOpen?: () => void;
}

/** The steps of setup, always in view. */
export function SetupRail({ steps }: { steps: RailStep[] }) {
    const t = useT();
    return (
        <nav className="setup-rail" aria-label={t('setup.rail.label')}>
            <p className="setup-rail-title">{t('setup.title')}</p>
            <ol className="setup-rail-list">
                {steps.map((s, i) => {
                    const icon = s.status === 'done' ? <Check size={13} aria-hidden="true" />
                        : s.status === 'active' ? <Loader2 size={13} className="spin" aria-hidden="true" />
                            : s.status === 'failed' ? <AlertCircle size={13} aria-hidden="true" />
                                : i + 1;
                    const body = <><span className="setup-rail-num">{icon}</span><span>{s.label}</span></>;
                    const cls = `setup-rail-step is-${s.status}${s.sub ? ' is-sub' : ''}`;
                    const current = s.status === 'current' || s.status === 'active' ? 'step' : undefined;
                    return (
                        <li key={s.id}>
                            {s.onOpen
                                ? <button type="button" className={cls} onClick={s.onOpen}>{body}</button>
                                : <span className={cls} aria-current={current}>{body}</span>}
                        </li>
                    );
                })}
            </ol>
        </nav>
    );
}
