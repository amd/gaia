// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { ref, type MessageKey } from '../../i18n/registry';
import { useT } from '../../i18n/useT';
import type { TrayHost, TraySettings } from '../../types/agent';
import { isMacPlatform } from '../../utils/platform';
import { SettingsDisclosure } from './SettingsDisclosure';

const TRAY = ref(isMacPlatform() ? 'settings.desktop.menuBar' : 'settings.desktop.tray');

const TOGGLES: Array<{ key: keyof TraySettings; label: MessageKey; hint: MessageKey }> = [
    { key: 'minimizeToTray', label: 'settings.desktop.keepRunning', hint: 'settings.desktop.keepRunningHint' },
    { key: 'startOnLogin', label: 'settings.desktop.startOnLogin', hint: 'settings.desktop.startOnLoginHint' },
    { key: 'startMinimized', label: 'settings.desktop.startMinimized', hint: 'settings.desktop.startMinimizedHint' },
];

/** Toggles that only work with a tray icon to bring the window back. */
const NEEDS_TRAY: ReadonlySet<keyof TraySettings> = new Set(['startMinimized']);

/** Hints that change where there is no tray: closing then minimizes to the dock. */
const NO_TRAY_HINT: Partial<Record<keyof TraySettings, MessageKey>> = {
    minimizeToTray: 'settings.desktop.keepRunningNoTray',
};

/** Why those toggles are off, or null while they work (or the check hasn't finished). */
function noTrayHint(host: TrayHost | null): MessageKey | null {
    if (host?.reason === 'no-host') return 'settings.desktop.noTray';
    if (host?.reason === 'check-failed') return 'settings.desktop.noTrayCheckFailed';
    return null;
}

/** Desktop-app settings, saved by the tray. Shown only in the desktop app. */
export function DesktopAppSettings() {
    const t = useT();
    const tray = window.gaiaAPI?.tray;
    const [settings, setSettings] = useState<TraySettings | null>(null);
    const [host, setHost] = useState<TrayHost | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState<keyof TraySettings | null>(null);

    useEffect(() => {
        if (!tray) return;
        tray.getConfig()
            .then((cfg) => {
                setSettings(cfg.tray);
                setHost(cfg.host);
            })
            .catch((err) => setError(t('settings.desktop.readFailed', { reason: err instanceof Error ? err.message : String(err) })));
    }, [tray, t]);

    const toggle = useCallback(async (key: keyof TraySettings) => {
        if (!tray || !settings || saving !== null) return;
        setSaving(key);
        setError(null);
        try {
            const saved = await tray.setConfig({ tray: { [key]: !settings[key] } });
            setSettings(saved.tray);
            setHost(saved.host);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setSaving(null);
        }
    }, [tray, settings, saving]);

    if (!tray) return null;
    const noTray = noTrayHint(host);

    return (
        <SettingsDisclosure title={t('settings.desktop.title')}>
            <div className="settings-field" role="group" aria-labelledby="desktop-app-label">
                <span className="settings-label" id="desktop-app-label">{t('settings.desktop.title')}</span>
                {TOGGLES.map(({ key, label, hint }) => {
                    const blockedBy = NEEDS_TRAY.has(key) ? noTray : null;
                    const shownHint = (noTray && NO_TRAY_HINT[key]) || hint;
                    return (
                        <div key={key}>
                            <label className="setting-row">
                                <span>{t(label, { tray: TRAY })}</span>
                                <span className="toggle-switch">
                                    <input
                                        id={`desktop-${key}`}
                                        type="checkbox"
                                        role="switch"
                                        checked={!blockedBy && !!settings?.[key]}
                                        aria-describedby={`desktop-${key}-hint`}
                                        onChange={() => void toggle(key)}
                                        // The switch being saved stays focusable; disabling it would drop keyboard focus.
                                        disabled={!!blockedBy || !settings || (saving !== null && saving !== key)}
                                        aria-busy={saving === key}
                                        aria-label={t(label, { tray: TRAY })}
                                    />
                                    <span className="toggle-track" />
                                </span>
                            </label>
                            <p className="settings-hint" id={`desktop-${key}-hint`}>
                                {blockedBy ? t(blockedBy) : t(shownHint, { tray: TRAY })}
                            </p>
                        </div>
                    );
                })}
                {error && <p className="settings-error" role="alert">{error}</p>}
            </div>
        </SettingsDisclosure>
    );
}
