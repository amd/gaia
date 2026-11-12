// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { useT } from '../../i18n/useT';
import type { SetupLogStage, SetupRunStatus } from '../../types';
import { TroubleshootingReport, type ReportClasses } from '../TroubleshootingReport';
import { FollowLog } from './FollowLog';

const STAGES: SetupLogStage[] = ['check', 'download', 'test'];

const REPORT_CLASSES: ReportClasses = {
    root: 'setup-report',
    button: 'btn-secondary setup-report-button',
    hint: 'setup-hint',
    error: 'setup-error',
};

/**
 * Each stage's log, collapsed until asked for, and a report to send us.
 * The report carries setup's whole record, so a user who hits a problem has
 * everything we need without a terminal.
 */
export function StageLogs({ run, before = {} }: { run: SetupRunStatus | null; before?: Partial<Record<SetupLogStage, string[]>> }) {
    const t = useT();
    const [open, setOpen] = useState<Set<SetupLogStage>>(() => new Set());

    const toggle = (stage: SetupLogStage) => setOpen((s) => {
        const next = new Set(s);
        if (next.has(stage)) next.delete(stage); else next.add(stage);
        return next;
    });

    // What the screen saw before the run, then what the run printed.
    const linesOf = (stage: SetupLogStage) => [...(before[stage] ?? []), ...(run?.logs?.[stage] ?? [])];
    const stages = STAGES.filter((s) => linesOf(s).length > 0);
    return (
        <section className="setup-logs" aria-label={t('setup.logs.title')}>
            {stages.map((stage) => {
                const lines = linesOf(stage);
                const shown = open.has(stage);
                return (
                    <div key={stage} className="setup-log">
                        <button type="button" className="setup-log-toggle" onClick={() => toggle(stage)} aria-expanded={shown}>
                            <ChevronRight size={13} className={shown ? 'is-open' : ''} aria-hidden="true" /> {t(`setup.logs.${stage}`)}
                        </button>
                        {shown && <FollowLog lines={lines} className="setup-log-pre" />}
                    </div>
                );
            })}
            <TroubleshootingReport classes={REPORT_CLASSES} note={t('setup.logs.reportNote')} />
        </section>
    );
}
