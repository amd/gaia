// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
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

const PERCENT = /\d+(\.\d+)?\s*%/;

/** A redrawn progress bar prints a line per redraw: keep only the last of each run. */
export function collapseProgress(lines: string[]): string[] {
    const out: string[] = [];
    let last: string | null = null;
    for (const line of lines) {
        // Same text before the percent is the same bar; a new file or shard keeps its own line.
        const at = line.search(PERCENT);
        const shape = at >= 0 ? line.slice(0, at) : null;
        if (shape !== null && shape === last) out[out.length - 1] = line;
        else out.push(line);
        last = shape;
    }
    return out;
}

/**
 * Each stage's log and a report to send us, behind one quiet "Having trouble?"
 * that opens by itself when a step fails. The report carries setup's whole
 * record, so a user who hits a problem has everything we need without a terminal.
 */
export function StageLogs({ run, before = {}, failed = false }: { run: SetupRunStatus | null; before?: Partial<Record<SetupLogStage, string[]>>; failed?: boolean }) {
    const t = useT();
    const [help, setHelp] = useState(failed);
    const [open, setOpen] = useState<Set<SetupLogStage>>(() => new Set());
    useEffect(() => {
        if (failed) setHelp(true);
    }, [failed]);

    const toggle = (stage: SetupLogStage) => setOpen((s) => {
        const next = new Set(s);
        if (next.has(stage)) next.delete(stage); else next.add(stage);
        return next;
    });

    // What the screen saw before the run, then what the run printed.
    const linesOf = (stage: SetupLogStage) => collapseProgress([...(before[stage] ?? []), ...(run?.logs?.[stage] ?? [])]);
    const stages = STAGES.filter((s) => linesOf(s).length > 0);
    return (
        <section className="setup-logs" aria-label={t('setup.logs.title')}>
            <button type="button" className="setup-log-toggle setup-help-toggle" onClick={() => setHelp((h) => !h)} aria-expanded={help} aria-controls="setup-help">
                <ChevronRight size={13} className={help ? 'is-open' : ''} aria-hidden="true" /> {t('setup.logs.help')}
            </button>
            {help && (
                <div id="setup-help" className="setup-help">
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
                </div>
            )}
        </section>
    );
}
