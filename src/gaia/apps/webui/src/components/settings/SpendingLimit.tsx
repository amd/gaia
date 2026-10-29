// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import { formatTotal } from './cloudPrice';
import type { SpendingReport } from '../../types';

/** This month's cloud spending at list price, and an optional monthly limit. */
export function SpendingLimit() {
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
            : <p className="settings-hint"><Loader2 size={13} className="spin" /> Loading this month&rsquo;s spending…</p>;
    }

    const limit = report.limit_usd;
    const parsed = draft.trim() === '' ? null : Number(draft);
    const draftValid = parsed === null || (Number.isFinite(parsed) && parsed > 0);
    const dirty = parsed !== limit;

    return (
        <section className="settings-field spending" aria-label="Cloud spending">
            <span className="settings-label">Cloud spending this month</span>
            <p className="spending-total">
                <strong>{formatTotal(report.total_usd)}</strong>
                {limit != null && <> of your {formatTotal(limit)} limit</>}
                <span className="settings-hint"> · {report.turns} cloud {report.turns === 1 ? 'chat turn' : 'chat turns'}, at list price</span>
            </p>
            {report.unpriced_turns > 0 && (
                <p className="settings-hint">
                    Plus {report.unpriced_turns} {report.unpriced_turns === 1 ? 'turn' : 'turns'} on models with an unknown
                    price, not included above.
                </p>
            )}
            {report.state === 'near' && (
                <p className="spending-warning" role="status">
                    <AlertTriangle size={13} aria-hidden="true" /> Close to your limit. GAIA stops before a cloud chat once
                    it&rsquo;s reached.
                </p>
            )}
            {report.state === 'reached' && (
                <div className="spending-warning" role="alert">
                    <p>
                        <AlertTriangle size={13} aria-hidden="true" /> You&rsquo;ve reached your limit, so cloud chats are
                        stopped until next month. Models on this PC still work.
                    </p>
                    <button type="button" className="btn-secondary" disabled={busy} onClick={() => void apply(api.allowSpendingThisMonth)}>
                        Allow more this month
                    </button>
                </div>
            )}
            {report.state === 'allowed_over' && (
                <p className="settings-hint">Over your limit; you allowed more for this month.</p>
            )}
            <form
                className="settings-row"
                onSubmit={(e) => { e.preventDefault(); if (draftValid && dirty) void apply(() => api.setSpendingLimit(parsed)); }}
            >
                <label className="spending-limit-label" htmlFor="spend-limit">Monthly limit ($)</label>
                <input
                    id="spend-limit"
                    className="settings-input"
                    type="number"
                    min="0.01"
                    step="0.01"
                    inputMode="decimal"
                    placeholder="No limit"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    aria-invalid={!draftValid}
                />
                <button type="submit" className="btn-secondary" disabled={busy || !draftValid || !dirty}>
                    {busy ? <Loader2 size={13} className="spin" /> : null} Save
                </button>
                {limit != null && (
                    <button type="button" className="btn-secondary" disabled={busy} onClick={() => void apply(() => api.setSpendingLimit(null))}>
                        Remove limit
                    </button>
                )}
            </form>
            <span className="settings-hint">
                Counts cloud chats in GAIA&rsquo;s Agent UI at list price, without cache discounts. Your provider&rsquo;s bill
                is the final word. Turns that fail or are stopped before a reply aren&rsquo;t counted. Models with an unknown price, like the AMD LLM Gateway&rsquo;s, add nothing to the total,
                but are stopped too once the limit is reached.
            </span>
            {error && <p className="settings-error" role="alert">{error}</p>}
        </section>
    );
}
