// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { getUpdaterBridge, useUpdateStatus } from '../../hooks/useUpdateStatus';
import type { GaiaUpdaterBridge, UpdateState } from '../../hooks/useUpdateStatus';
import { tEn, useT, type Translate } from '../../i18n/useT';
import { log } from '../../utils/logger';
import { VersionPicker } from '../VersionPicker';
import { SettingsDisclosure } from './SettingsDisclosure';

/** "0.25.2-rc.3" is a release candidate; "0.25.2" is not. */
export function isReleaseCandidate(version: string | null): boolean {
    return /^\d+\.\d+\.\d+-/.test(version ?? '');
}

/** One line saying where updates stand. */
export function describeUpdateStatus(s: UpdateState, t: Translate = tEn): string {
    const version = s.version ?? '';
    switch (s.status) {
        case 'checking':
            return t('settings.updates.checking');
        case 'available':
            return t('settings.updates.available', { version });
        case 'downloading':
            return t('settings.updates.downloading', { version, percent: s.progress });
        case 'downloaded':
            return t('settings.updates.ready', { version });
        case 'error':
            return t('settings.updates.failed', { reason: s.error ?? t.bare('settings.advanced.unknown') });
        case 'no-channel':
            return s.error ?? t('settings.updates.noChannel');
        case 'disabled':
            return t('settings.updates.disabled');
        default:
            return t('settings.updates.current');
    }
}

/**
 * Settings → General → Updates. Auto-update is off by default while GAIA
 * changes fast: a found update is offered here and in the corner chip, never
 * installed unasked. "Install a specific version" pins the app to that one.
 * Shown only in the desktop app, which owns updating.
 */
export function UpdatesSection() {
    const t = useT();
    const update = useUpdateStatus();
    const bridge = getUpdaterBridge();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showPicker, setShowPicker] = useState(false);

    if (!bridge) return null;

    async function run(what: string, call: (b: GaiaUpdaterBridge) => Promise<unknown>) {
        if (!bridge) return;
        setBusy(true);
        setError(null);
        try {
            await call(bridge);
        } catch (err) {
            log.system.error(`${what} failed`, err);
            setError(t('settings.updates.actionFailed', { what, reason: err instanceof Error ? err.message : String(err) }));
        } finally {
            setBusy(false);
        }
    }

    const disabled = update.status === 'disabled';
    const working = busy || update.status === 'checking' || update.status === 'downloading';

    const line = `${t('settings.updates.running', { version: update.currentVersion ?? t.bare('settings.advanced.unknown') })} ${describeUpdateStatus(update, t)}`;
    const needsYou = ['available', 'downloaded', 'error'].includes(update.status) || !!update.pinnedVersion || !!update.configError || !!error;

    return (
        <SettingsDisclosure title={t('settings.updates.title')} hint={line} forceOpen={needsYou}>
            <div className="settings-field" role="group" aria-labelledby="updates-label">
                <span className="settings-label" id="updates-label">{t('settings.updates.title')}</span>
                <p className="visually-hidden" role="status">{line}</p>

                <label className="setting-row">
                    <span>{t('settings.updates.auto')}</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            role="switch"
                            checked={update.autoUpdate}
                            aria-describedby="updates-auto-hint"
                            aria-label={t('settings.updates.auto')}
                            aria-busy={busy}
                            disabled={disabled}
                            onChange={() => {
                                if (!busy) void run(t.bare('settings.updates.savingAuto'), (b) => b.setAutoUpdate(!update.autoUpdate));
                            }}
                        />
                        <span className="toggle-track" />
                    </span>
                </label>
                <p className="settings-hint" id="updates-auto-hint">
                    {t('settings.updates.autoHint')}
                </p>

                <label className="setting-row">
                    <span>{t('settings.updates.rc')}</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            role="switch"
                            checked={Boolean(update.includePrereleases)}
                            aria-describedby="updates-rc-hint"
                            aria-label={t('settings.updates.rc')}
                            aria-busy={busy}
                            disabled={disabled}
                            onChange={() => {
                                if (!busy) {
                                    void run(t.bare('settings.updates.savingRc'), (b) =>
                                        b.setIncludePrereleases(!update.includePrereleases));
                                }
                            }}
                        />
                        <span className="toggle-track" />
                    </span>
                </label>
                <p className="settings-hint" id="updates-rc-hint">
                    {t('settings.updates.rcHint')}
                    {isReleaseCandidate(update.currentVersion) && ` ${t('settings.updates.rcCurrent')}`}
                </p>

                {update.pinnedVersion && (
                    <p className="settings-hint">
                        {t('settings.updates.pinned', { version: update.pinnedVersion })}{' '}
                        <button
                            type="button"
                            className="btn-secondary"
                            disabled={busy}
                            onClick={() => void run(t.bare('settings.updates.resuming'), (b) => b.resumeUpdates())}
                        >
                            {t('settings.updates.unpin')}
                        </button>
                    </p>
                )}

                <div className="setting-actions">
                    {update.status === 'available' && !update.pinnedVersion && (
                        <button
                            type="button"
                            className="btn-primary"
                            disabled={busy}
                            onClick={() => void run(t.bare('settings.updates.fetching'), (b) => b.downloadUpdate())}
                        >
                            {t('settings.updates.updateTo', { version: update.version ?? '' })}
                        </button>
                    )}
                    {update.status === 'downloaded' && (
                        <button
                            type="button"
                            className="btn-primary"
                            disabled={busy}
                            onClick={() => void run(t.bare('settings.updates.restarting'), (b) => b.restart())}
                        >
                            {t('settings.updates.restart')}
                        </button>
                    )}
                    <button
                        type="button"
                        className="btn-secondary"
                        disabled={working || disabled}
                        onClick={() => void run(t.bare('settings.updates.check'), (b) => b.check())}
                    >
                        {t('settings.updates.check')}
                    </button>
                    <button
                        type="button"
                        className="btn-secondary"
                        disabled={working || disabled}
                        onClick={() => setShowPicker(true)}
                    >
                        {t('settings.updates.pick')}
                    </button>
                </div>

                {update.configError && <p className="settings-error" role="alert">{update.configError}</p>}
                {error && <p className="settings-error" role="alert">{error}</p>}
                {showPicker && <VersionPicker onClose={() => setShowPicker(false)} />}
            </div>
        </SettingsDisclosure>
    );
}
