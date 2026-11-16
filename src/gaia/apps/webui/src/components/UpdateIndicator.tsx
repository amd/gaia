// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useState } from 'react';
import { Download, RefreshCw, AlertTriangle, X } from 'lucide-react';
import { getUpdaterBridge, useUpdateStatus } from '../hooks/useUpdateStatus';
import { updateErrorText } from '../hooks/updateError';
import { useT } from '../i18n/useT';
import { log } from '../utils/logger';
import './UpdateIndicator.css';

const RELEASES_PAGE = 'https://github.com/amd/gaia/releases';

/**
 * Small, subtle indicator shown in the corner of the main view when an
 * app update is available, downloading, ready, or failed. Hidden in the
 * `idle`, `checking`, and `disabled` states so it never distracts from
 * normal use. Wires up to the Electron main process via `gaiaUpdater`
 * (see preload.cjs + services/auto-updater.cjs). Phase F.
 *
 * By default an update downloads by itself and is offered here as a restart;
 * the ✕ says "not now", after which only Settings offers that version.
 */
export function UpdateIndicator() {
    const update = useUpdateStatus();
    const t = useT();
    const [dismissed, setDismissed] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [skipFailed, setSkipFailed] = useState(false);

    const act = useCallback((what: 'downloadUpdate' | 'restart' | 'revertUpdate' | 'skipUpdate') => {
        const updater = getUpdaterBridge();
        if (!updater) return;
        setActionError(null);
        setSkipFailed(false);
        updater[what]().catch((err: unknown) => {
            log.system.error(`Update ${what} failed`, err);
            setActionError(updateErrorText(err, t));
            setSkipFailed(what === 'skipUpdate');
        });
    }, [t]);

    // States where we deliberately render nothing — keeps the UI calm.
    if (dismissed) return null;
    const installFailedDetail = update.installFailed
        ? t('update.chip.installFailedDetail', update.installFailed)
        : null;
    const busy = update.status === 'downloading' || update.status === 'downloaded';
    if (update.startFailed && update.previousVersion && !busy) {
        return (
            <div className="update-chip update-chip--error" role="status">
                <AlertTriangle size={13} className="update-chip__icon" />
                <span className="update-chip__label" title={actionError ?? undefined}>
                    {actionError ? t('update.chip.updateFailed') : t('update.chip.startFailed')}
                </span>
                <button
                    type="button"
                    className="update-chip__action"
                    onClick={() => act('revertUpdate')}
                >
                    {t('update.chip.goBack', { version: update.previousVersion })}
                </button>
                <button
                    type="button"
                    className="update-chip__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label={t('update.chip.dismissError')}
                >
                    <X size={11} />
                </button>
            </div>
        );
    }
    const offered = update.status === 'available' || update.status === 'downloaded';
    // In "auto" the update installs on quit either way, so there is nothing to skip.
    const canSkip = update.manualInstall || update.updateMode !== 'auto';
    // Skipped: Settings still offers this version, the chip doesn't, even while it downloads again.
    if ((offered || update.status === 'downloading') && update.version && update.version === update.skippedVersion) {
      return null;
    }
    if (installFailedDetail && !offered) {
        return (
            <div
                className="update-chip update-chip--error"
                role="status"
                title={installFailedDetail ?? undefined}
            >
                <AlertTriangle size={13} className="update-chip__icon" />
                <span className="update-chip__label">{t('update.chip.installFailed')}</span>
                <button
                    type="button"
                    className="update-chip__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label={t('update.chip.dismissError')}
                >
                    <X size={11} />
                </button>
            </div>
        );
    }
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
                title={t('update.chip.downloadingTitle', { version: update.version ?? '' })}
            >
                <Download size={13} className="update-chip__icon" />
                <span className="update-chip__label">
                    {update.preparing ? t('update.chip.preparing') : t('update.chip.downloading', { pct })}
                </span>
            </div>
        );
    }

    if (update.status === 'available') {
        return (
            <div
                className="update-chip update-chip--available"
                role="status"
                title={
                    update.manualInstall
                        ? t('update.chip.manualTitle', { version: update.version ?? '' })
                        : actionError || installFailedDetail || update.releaseNotes || t('update.chip.availableTitle')
                }
            >
                <Download size={13} className="update-chip__icon" />
                <span className="update-chip__label">
                    {actionError
                        ? t(skipFailed ? 'update.chip.skipFailed' : 'update.chip.updateFailed')
                        : installFailedDetail
                          ? t('update.chip.installFailed')
                          : t('update.chip.available', { version: update.version ?? '' })}
                </span>
                {update.manualInstall ? (
                    <a
                        className="update-chip__action"
                        href={update.downloadUrl ?? RELEASES_PAGE}
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        {t('update.chip.download')}
                    </a>
                ) : !update.pinnedVersion && (
                    <button
                        type="button"
                        className="update-chip__action"
                        onClick={() => act('downloadUpdate')}
                    >
                        {t('update.chip.updateNow')}
                    </button>
                )}
                {canSkip && (
                    <button
                        type="button"
                        className="update-chip__dismiss"
                        onClick={() => act('skipUpdate')}
                        aria-label={t('update.chip.skip')}
                        title={t('update.chip.skip')}
                    >
                        <X size={11} />
                    </button>
                )}
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
                <span className="update-chip__label" title={actionError || installFailedDetail || undefined}>
                    {actionError
                        ? t(skipFailed ? 'update.chip.skipFailed' : 'update.chip.restartFailed')
                        : installFailedDetail
                          ? t('update.chip.installFailedRetry')
                          : t('update.chip.ready', { version: update.version ?? '' })}
                </span>
                <button
                    type="button"
                    className="update-chip__action"
                    onClick={() => act('restart')}
                    title={t('update.chip.restartTitle')}
                >
                    {t('update.chip.restart')}
                </button>
                {canSkip && (
                    <button
                        type="button"
                        className="update-chip__dismiss"
                        onClick={() => act('skipUpdate')}
                        aria-label={t('update.chip.skip')}
                        title={t('update.chip.skip')}
                    >
                        <X size={11} />
                    </button>
                )}
            </div>
        );
    }

    if (update.status === 'error') {
        return (
            <div
                className="update-chip update-chip--error"
                role="status"
                title={update.error || t('update.chip.checkFailed')}
            >
                <AlertTriangle size={13} className="update-chip__icon" />
                <span className="update-chip__label">{t('update.chip.checkFailed')}</span>
                <button
                    type="button"
                    className="update-chip__dismiss"
                    onClick={() => setDismissed(true)}
                    aria-label={t('update.chip.dismissError')}
                >
                    <X size={11} />
                </button>
            </div>
        );
    }

    return null;
}
