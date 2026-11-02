// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { ChevronRight, FileDown, Loader2 } from 'lucide-react';
import { useT } from '../../i18n/useT';
import * as api from '../../services/api';
import type { SetupLogStage, SetupRunStatus } from '../../types';
import { formatBytes } from './format';

const STAGES: SetupLogStage[] = ['check', 'download', 'test'];

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

/**
 * Each stage's log, collapsed until asked for, and a report to send us.
 * The report carries setup's whole record, so a user who hits a problem has
 * everything we need without a terminal.
 */
export function StageLogs({ run }: { run: SetupRunStatus | null }) {
    const t = useT();
    const [open, setOpen] = useState<Set<SetupLogStage>>(() => new Set());
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState<api.SetupReport | null>(null);
    const [error, setError] = useState<string | null>(null);

    const toggle = (stage: SetupLogStage) => setOpen((s) => {
        const next = new Set(s);
        if (next.has(stage)) next.delete(stage); else next.add(stage);
        return next;
    });

    const save = useCallback(async () => {
        setSaving(true);
        setError(null);
        setSaved(null);
        try {
            const report = await api.createSetupReport();
            const blob = await api.fetchSetupReport(report.name);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = report.name;
            a.click();
            URL.revokeObjectURL(url);
            setSaved(report);
        } catch (err) {
            setError(t('setup.logs.reportFailed', { error: errorText(err) }));
        } finally {
            setSaving(false);
        }
    }, [t]);

    const stages = STAGES.filter((s) => (run?.logs?.[s]?.length ?? 0) > 0);
    return (
        <section className="setup-logs" aria-label={t('setup.logs.title')}>
            {stages.map((stage) => {
                const lines = run?.logs?.[stage] ?? [];
                const shown = open.has(stage);
                return (
                    <div key={stage} className="setup-log">
                        <button type="button" className="setup-log-toggle" onClick={() => toggle(stage)} aria-expanded={shown}>
                            <ChevronRight size={13} className={shown ? 'is-open' : ''} aria-hidden="true" /> {t(`setup.logs.${stage}`)}
                        </button>
                        {shown && <pre className="setup-log-pre" dir="ltr">{lines.join('\n')}</pre>}
                    </div>
                );
            })}
            <div className="setup-report">
                <button type="button" className="btn-secondary setup-report-button" onClick={() => void save()} disabled={saving}>
                    {saving ? <Loader2 size={13} className="spin" aria-hidden="true" /> : <FileDown size={13} aria-hidden="true" />}
                    <span>{t('setup.logs.report')}</span>
                </button>
                <span className="setup-hint">{t('setup.logs.reportNote')}</span>
                {saved && (
                    <p className="setup-hint" role="status">
                        {t('setup.logs.reportSaved', { size: formatBytes(saved.size_bytes) })} <bdi dir="ltr">{saved.path}</bdi>
                    </p>
                )}
                {error && <p className="setup-error" role="alert">{error}</p>}
            </div>
        </section>
    );
}
