// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { Download, RefreshCw, AlertTriangle, X } from 'lucide-react';
import { getUpdaterBridge, useUpdateStatus } from '../hooks/useUpdateStatus';
import { useT } from '../i18n/useT';
import { log } from '../utils/logger';
import './UpdateIndicator.css';

/**
 * Small, subtle indicator shown in the corner of the main view when an
 * app update is available, downloading, ready, or failed. Hidden in the
 * `idle`, `checking`, and `disabled` states so it never distracts from
 * normal use. Wires up to the Electron main process via `gaiaUpdater`
 * (see preload.cjs + services/auto-updater.cjs). Phase F.
 *
 * Auto-update is off by default, so an available update is offered here with
 * an "Update now" button rather than downloaded behind the user's back.
 */
export function UpdateIndicator() {
    const t = useT();
    const update = useUpdateStatus();
    const [dismissed, setDismissed] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);

    const act = useCallback((what: 'downloadUpdate' | 'restart') => {
        const updater = getUpdaterBridge();
        if (!updater) return;
        setActionError(null);
        updater[what]().catch((err: unknown) => {
            const msg = err instanceof Error ? err.message : String(err);
            log.system.error(`Update ${what} failed`, err);
            setActionError(msg);
        });
    }, []);

    // States where we deliberately render nothing — keeps the UI calm.
    if (dismissed) return null;
    if (
        update.status === 'idle' ||
        update.status === 'checking' ||
        update.status === 'disabled' ||
        update.status === 'no-channel'
    ) {
        return null;
    }

    if (update.status === 'downloading') {
        const pct = Math.max(0, Math.min(100, update.progress));
        return (
            <div
                className="update-chip update-chip--downloading"
                role="status"
                aria-live="polite"
                title={t.bare('update.downloadingTitle', { version: update.version ?? '' })}
            >
                <Download size={13} className="update-chip__icon" />
                <span className="update-chip__label">
                    {t.bare('update.downloading', { percent: pct })}
                </span>
            </div>
        );
    }

    if (update.status === 'available') {
        return (
            <div
                className="update-chip update-chip--available"
                role="status"
                title={actionError || update.releaseNotes || t.bare('update.newVersion')}
            >
                <Download size={13} className="update-chip__icon" />
                <span className="update-chip__label">
                    {actionError
                        ? t.bare('update.failed')
                        : t.bare('update.available', { version: update.version ?? '' })}
                </span>
                {!update.pinnedVersion && (
                    <button
                        type="button"
                        className="update-chip__action"
                        onClick={() => act('downloadUpdate')}
                    >
                        {t.bare('update.now')}
                    </button>
                )}
                <button
                    type="button"
                    className="update-chip__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label={t.bare('update.dismiss')}
                >
                    <X size={11} />
                </button>
            </div>
        );
    }

    if (update.status === 'downloaded') {
        return (
            <div
                className="update-chip update-chip--ready"
                role="status"
                aria-live="polite"
            >
                <RefreshCw size={13} className="update-chip__icon" />
                <button
                    type="button"
                    className="update-chip__action"
                    onClick={() => act('restart')}
                    title={actionError || t.bare('update.restartTitle')}
                >
                    {actionError ? t.bare('update.restartFailed') : t.bare('update.ready')}
                </button>
            </div>
        );
    }

    if (update.status === 'error') {
        return (
            <div
                className="update-chip update-chip--error"
                role="status"
                title={update.error || t.bare('update.checkFailed')}
            >
                <AlertTriangle size={13} className="update-chip__icon" />
                <span className="update-chip__label">{t.bare('update.checkFailed')}</span>
                <button
                    type="button"
                    className="update-chip__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label={t.bare('update.dismissError')}
                >
                    <X size={11} />
                </button>
            </div>
        );
    }

    return null;
}
