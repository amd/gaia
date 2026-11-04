// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { FileDown, Loader2 } from 'lucide-react';
import { useT } from '../i18n/useT';
import * as api from '../services/api';
import { formatBytes } from './setup/format';

/** The id a failed answer names its saved problem report by (`RECORD_ID_RE` in failure_record.py). */
const FAILURE_RECORD_RE = /\bGF-\d{8}-\d{6}-[0-9a-f]{4}\b/;

export function namesFailureRecord(text: string): boolean {
    return FAILURE_RECORD_RE.test(text);
}

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

/** Where each surface puts the report button, and how it styles it. */
export interface ReportClasses {
    root: string;
    button: string;
    hint: string;
    error: string;
}

/**
 * Saves one troubleshooting report: setup's record, GAIA's logs, the saved
 * model-failure records and this PC's details. It is written on this PC and
 * handed to the browser's save; nothing is sent anywhere.
 */
export function TroubleshootingReport({ classes, note }: { classes: ReportClasses; note?: string }) {
    const t = useT();
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState<api.SetupReport | null>(null);
    const [error, setError] = useState<string | null>(null);

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
            // Later, not now: the desktop app's save dialog reads the URL after the click.
            setTimeout(() => URL.revokeObjectURL(url), 60_000);
            setSaved(report);
        } catch (err) {
            setError(t('setup.logs.reportFailed', { error: errorText(err) }));
        } finally {
            setSaving(false);
        }
    }, [t]);

    return (
        <div className={classes.root}>
            <button type="button" className={classes.button} onClick={() => void save()} disabled={saving}>
                {saving ? <Loader2 size={13} className="spin" aria-hidden="true" /> : <FileDown size={13} aria-hidden="true" />}
                <span>{t('setup.logs.report')}</span>
            </button>
            {note && <span className={classes.hint}>{note}</span>}
            {saved && (
                <p className={classes.hint} role="status">
                    {t('setup.logs.reportSaved', { size: formatBytes(saved.size_bytes) })} <bdi dir="ltr">{saved.path}</bdi>
                </p>
            )}
            {error && <p className={classes.error} role="alert">{error}</p>}
        </div>
    );
}
