// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { getUpdaterBridge, useUpdateStatus } from '../../hooks/useUpdateStatus';
import { updateErrorText } from '../../hooks/updateError';
import { dateLocale, useT } from '../../i18n/useT';
import { log } from '../../utils/logger';

/**
 * Setup failed: offer the older version setup last finished on, so a broken
 * build never leaves the user stuck. Nothing happens until they choose it.
 */
export function GoBackOffer({ className }: { className?: string }) {
    const t = useT();
    const update = useUpdateStatus();
    const [asked, setAsked] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const last = update.lastWorking;
    if (!getUpdaterBridge() || !last) return null;

    const ours = asked || update.version === last.version;
    const downloading = ours && update.status === 'downloading';
    // Downloaded while the request is open means the restart is under way; after it, the user kept GAIA running.
    const restarting = asked && update.status === 'downloaded';
    const waiting = !asked && update.status === 'downloaded' && update.version === last.version;
    const date = new Date(last.at).toLocaleDateString(dateLocale(t), { year: 'numeric', month: 'short', day: 'numeric' });
    const title = t('setup.goBack.title');

    const goBack = () => {
        const updater = getUpdaterBridge();
        if (!updater) return;
        setAsked(true);
        setError(null);
        updater.revertToLastWorking().catch((err: unknown) => {
            log.system.error('Going back to the last working version failed', err);
            setError(t('setup.goBack.failed', { reason: updateErrorText(err, t) }));
        }).finally(() => setAsked(false));
    };

    const restart = () => {
        const updater = getUpdaterBridge();
        if (!updater) return;
        setError(null);
        updater.restart().catch((err: unknown) => {
            log.system.error('Restarting into the last working version failed', err);
            setError(t('setup.goBack.failed', { reason: updateErrorText(err, t) }));
        });
    };

    return (
        <section className={className ? `setup-card ${className}` : 'setup-card'} aria-label={title}>
            <h3 className="mc-job-title">{title}</h3>
            <p>{t('setup.goBack.lead', { version: last.version, date })}</p>
            <p className="setup-hint">{t('setup.goBack.note', { version: last.version, current: update.currentVersion ?? '' })}</p>
            {downloading || restarting ? (
                <p className="setup-hint" role="status">
                    <Loader2 size={13} className="spin" aria-hidden="true" />{' '}
                    {downloading
                        ? t('setup.goBack.downloading', { version: last.version, pct: Math.max(0, Math.min(100, Math.round(update.progress))) })
                        : t('setup.goBack.restarting', { version: last.version })}
                </p>
            ) : waiting ? (
                <>
                    <p className="setup-hint">{t('setup.goBack.ready', { version: last.version })}</p>
                    <div className="setup-row">
                        <button type="button" className="btn-secondary go-back-action" onClick={restart}>
                            {t('setup.goBack.restart', { version: last.version })}
                        </button>
                    </div>
                </>
            ) : (
                <div className="setup-row">
                    <button type="button" className="btn-secondary go-back-action" onClick={goBack} disabled={asked}>
                        {t('setup.goBack.action', { version: last.version })}
                    </button>
                </div>
            )}
            {error && <p className="setup-error" role="alert">{error}</p>}
        </section>
    );
}
