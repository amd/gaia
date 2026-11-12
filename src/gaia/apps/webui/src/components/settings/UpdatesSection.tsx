// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { getUpdaterBridge, useUpdateStatus } from '../../hooks/useUpdateStatus';
import type { GaiaUpdaterBridge, UpdateState } from '../../hooks/useUpdateStatus';
import { useT, type Translate } from '../../i18n/useT';
import { ref, type MessageKey } from '../../i18n/registry';
import { log } from '../../utils/logger';
import { updateErrorReason } from '../../hooks/updateError';
import { VersionPicker } from '../VersionPicker';
import { SettingsDisclosure } from './SettingsDisclosure';

const RELEASES_PAGE = 'https://github.com/amd/gaia/releases';

/** "0.25.2-rc.3" is a release candidate; "0.25.2" is not. */
export function isReleaseCandidate(version: string | null): boolean {
    return /^\d+\.\d+\.\d+-/.test(version ?? '');
}

/** One line saying where updates stand. */
export function describeUpdateStatus(s: UpdateState, t: Translate): string {
    const version = s.version ?? '';
    if (s.readFailed) return t('update.status.readFailed');
    switch (s.status) {
        case 'checking':
            return t('update.status.checking');
        case 'available':
            return t('update.status.available', { version });
        case 'downloading':
            if (s.preparing) return t('update.status.preparing', { version });
            return t('update.status.downloading', { version, pct: s.progress });
        case 'downloaded':
            return t('update.status.downloaded', { version });
        case 'error':
            return t('update.status.error', { reason: s.error ?? t('update.status.unknownError') });
        case 'no-channel':
            return s.error ?? t('update.status.noChannel');
        case 'disabled':
            return t('update.status.disabled');
        default:
            return t('update.status.upToDate');
    }
}

/**
 * Settings → General → Updates. Auto-update is off by default while GAIA
 * changes fast: a found update is offered here and in the corner chip, never
 * installed unasked. "Install a specific version" pins the app to that one.
 * Shown only in the desktop app, which owns updating.
 */
export function UpdatesSection() {
    const update = useUpdateStatus();
    const t = useT();
    const bridge = getUpdaterBridge();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showPicker, setShowPicker] = useState(false);

    if (!bridge) return null;

    async function run(what: MessageKey, call: (b: GaiaUpdaterBridge) => Promise<unknown>) {
        if (!bridge) return;
        setBusy(true);
        setError(null);
        try {
            await call(bridge);
        } catch (err) {
            log.system.error(`${what} failed`, err);
            setError(t('update.settings.failed', { action: ref(what), reason: updateErrorReason(err) }));
        } finally {
            setBusy(false);
        }
    }

    const disabled = update.status === 'disabled';
    const working = busy || update.status === 'checking' || update.status === 'downloading';

    // An unread status has no version to report, so the line skips "Running GAIA unknown".
    const line = update.readFailed
        ? describeUpdateStatus(update, t)
        : `${t('update.settings.running', { version: update.currentVersion ?? t('update.settings.unknownVersion') })} ${describeUpdateStatus(update, t)}`;
    const needsYou = ['available', 'downloaded', 'error'].includes(update.status)
        || !!update.pinnedVersion || !!update.installFailed || !!update.configError || !!update.readFailed || !!error;

    return (
        <SettingsDisclosure title={t('update.settings.title')} titleId="updates-label" hint={line} forceOpen={needsYou}>
            <div className="settings-field" role="group" aria-labelledby="updates-label">
                <p className="visually-hidden" role="status">{line}</p>

                {!update.manualInstall && (
                    <>
                        <label className="setting-row">
                            <span>{t('update.settings.auto')}</span>
                            <span className="toggle-switch">
                                <input
                                    type="checkbox"
                                    role="switch"
                                    checked={update.autoUpdate}
                                    aria-describedby="updates-auto-hint"
                                    aria-label={t('update.settings.auto')}
                                    aria-busy={busy}
                                    disabled={disabled}
                                    onChange={() => {
                                        if (!busy) void run('update.action.saveAuto', (b) => b.setAutoUpdate(!update.autoUpdate));
                                    }}
                                />
                                <span className="toggle-track" />
                            </span>
                        </label>
                        <p className="settings-hint" id="updates-auto-hint">
                            {t('update.settings.autoHint')}
                        </p>
                    </>
                )}

                <label className="setting-row">
                    <span>{t('update.settings.rc')}</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            role="switch"
                            checked={Boolean(update.includePrereleases)}
                            aria-describedby="updates-rc-hint"
                            aria-label={t('update.settings.rc')}
                            aria-busy={busy}
                            disabled={disabled}
                            onChange={() => {
                                if (!busy) {
                                    void run('update.action.saveRc', (b) =>
                                        b.setIncludePrereleases(!update.includePrereleases));
                                }
                            }}
                        />
                        <span className="toggle-track" />
                    </span>
                </label>
                <p className="settings-hint" id="updates-rc-hint">
                    {t('update.settings.rcHint')}
                    {isReleaseCandidate(update.currentVersion) && ` ${t('update.settings.rcHintRunning')}`}
                </p>

                {update.pinnedVersion && (
                    <p className="settings-hint">
                        {t('update.settings.pinned', { version: update.pinnedVersion })}{' '}
                        <button
                            type="button"
                            className="btn-secondary"
                            disabled={busy}
                            onClick={() => void run('update.action.resume', (b) => b.resumeUpdates())}
                        >
                            {t('update.settings.unpin')}
                        </button>
                    </p>
                )}

                <div className="setting-actions">
                    {update.status === 'available' && update.manualInstall && (
                        <a
                            className="btn-primary update-download-link"
                            href={update.downloadUrl ?? RELEASES_PAGE}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-describedby="updates-manual-hint"
                        >
                            {t('update.settings.downloadVersion', { version: update.version ?? '' })}
                        </a>
                    )}
                    {update.status === 'available' && !update.manualInstall && !update.pinnedVersion && (
                        <button
                            type="button"
                            className="btn-primary"
                            disabled={busy}
                            onClick={() => void run('update.action.download', (b) => b.downloadUpdate())}
                        >
                            {t('update.settings.updateTo', { version: update.version ?? '' })}
                        </button>
                    )}
                    {update.status === 'downloaded' && (
                        <button
                            type="button"
                            className="btn-primary"
                            disabled={busy}
                            onClick={() => void run('update.action.restart', (b) => b.restart())}
                        >
                            {t('update.settings.restart')}
                        </button>
                    )}
                    {update.readFailed ? (
                        <button
                            type="button"
                            className="btn-secondary"
                            disabled={busy}
                            onClick={async () => {
                                setBusy(true);
                                try {
                                    await update.reload();
                                } finally {
                                    setBusy(false);
                                }
                            }}
                        >
                            {t('update.settings.retryRead')}
                        </button>
                    ) : (
                        <button
                            type="button"
                            className="btn-secondary"
                            disabled={working || disabled}
                            onClick={() => void run('update.action.check', (b) => b.check())}
                        >
                            {t('update.settings.check')}
                        </button>
                    )}
                    <button
                        type="button"
                        className="btn-secondary"
                        disabled={working || disabled}
                        onClick={() => setShowPicker(true)}
                    >
                        {t('update.settings.pick')}
                    </button>
                    {update.previousVersion && (
                        <button
                            type="button"
                            className="btn-secondary"
                            aria-describedby="updates-back-hint"
                            disabled={working || disabled}
                            onClick={() => void run('update.action.goBack', (b) => b.revertUpdate())}
                        >
                            {t('update.settings.goBack', { version: update.previousVersion })}
                        </button>
                    )}
                </div>
                {update.status === 'available' && update.manualInstall && (
                    <p className="settings-hint" id="updates-manual-hint">{t('update.settings.manualHint')}</p>
                )}
                {update.previousVersion && (
                    <p className="settings-hint" id="updates-back-hint">{t('update.settings.goBackHint')}</p>
                )}

                {update.installFailed && (
                    <p className="settings-error" role="alert">
                        {t('update.settings.installFailed', { ...update.installFailed, url: RELEASES_PAGE })}
                    </p>
                )}
                {update.lastCheckError && (
                    <p className="settings-hint">{t('update.settings.lastCheckError', { reason: update.lastCheckError })}</p>
                )}
                {update.configError && <p className="settings-error" role="alert">{update.configError}</p>}
                {error && <p className="settings-error" role="alert">{error}</p>}
                {showPicker && <VersionPicker onClose={() => setShowPicker(false)} manualInstall={update.manualInstall} />}
            </div>
        </SettingsDisclosure>
    );
}
