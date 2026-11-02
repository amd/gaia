// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { useT } from '../../i18n/useT';
import { formatTotal } from './cloudPrice';
import type { SpendingReport } from '../../types';

/** This month's cloud spending at list price, and an optional monthly limit. */
export function SpendingLimit() {
    const t = useT();
    const [report, setReport] = useState<SpendingReport | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [draft, setDraft] = useState('');
    const [busy, setBusy] = useState(false);

    const apply = useCallback(async (call: () => Promise<SpendingReport>) => {
        setBusy(true);
        setError(null);
        try {
            const next = await call();
            setReport(next);
            setDraft(next.limit_usd != null ? String(next.limit_usd) : '');
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(false);
        }
    }, []);

    useEffect(() => { void apply(api.getSpending); }, [apply]);

    if (!report) {
        return error
            ? <p className="settings-error" role="alert">{error}</p>
            : <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.spend.loading')}</p>;
    }

    const limit = report.limit_usd;
    const parsed = draft.trim() === '' ? null : Number(draft);
    const draftValid = parsed === null || (Number.isFinite(parsed) && parsed > 0);
    const dirty = parsed !== limit;

    return (
        <section className="settings-field spending" aria-label={t('settings.spend.aria')}>
            <span className="settings-label">{t('settings.spend.title')}</span>
            <p className="spending-total">
                <bdi dir="ltr"><strong>{formatTotal(report.total_usd)}</strong></bdi>
                {limit != null && <> {t('settings.spend.ofLimit', { limit: formatTotal(limit) })}</>}
                <span className="settings-hint"> · {t.plural('settings.spend.turns', report.turns)}</span>
            </p>
            {report.unpriced_turns > 0 && (
                <p className="settings-hint">
                    {t.plural('settings.spend.unpriced', report.unpriced_turns)}
                </p>
            )}
            {report.state === 'near' && (
                <p className="spending-warning" role="status">
                    <AlertTriangle size={13} aria-hidden="true" /> {t('settings.spend.near')}
                </p>
            )}
            {report.state === 'reached' && (
                <div className="spending-warning" role="alert">
                    <p>
                        <AlertTriangle size={13} aria-hidden="true" /> {t('settings.spend.reached')}
                    </p>
                    <button type="button" className="btn-secondary" disabled={busy} onClick={() => void apply(api.allowSpendingThisMonth)}>
                        {t('settings.spend.allow')}
                    </button>
                </div>
            )}
            {report.state === 'allowed_over' && (
                <p className="settings-hint">{t('settings.spend.allowed')}</p>
            )}
            <form
                className="settings-row"
                onSubmit={(e) => { e.preventDefault(); if (draftValid && dirty) void apply(() => api.setSpendingLimit(parsed)); }}
            >
                <label className="spending-limit-label" htmlFor="spend-limit">{t('settings.spend.limit')}</label>
                <input
                    id="spend-limit"
                    className="settings-input"
                    type="number"
                    min="0.01"
                    step="0.01"
                    inputMode="decimal"
                    placeholder={t.bare('settings.spend.noLimit')}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    aria-invalid={!draftValid}
                />
                <button type="submit" className="btn-secondary" disabled={busy || !draftValid || !dirty}>
                    {busy ? <Loader2 size={13} className="spin" /> : null} {t('settings.advanced.save')}
                </button>
                {limit != null && (
                    <button type="button" className="btn-secondary" disabled={busy} onClick={() => void apply(() => api.setSpendingLimit(null))}>
                        {t('settings.spend.removeLimit')}
                    </button>
                )}
            </form>
            <span className="settings-hint">
                {t('settings.spend.how')}
            </span>
            {error && <p className="settings-error" role="alert">{error}</p>}
        </section>
    );
}
