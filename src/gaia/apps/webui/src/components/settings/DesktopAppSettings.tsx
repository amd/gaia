// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { ref, type MessageKey } from '../../i18n/registry';
import { useT } from '../../i18n/useT';
import type { TraySettings } from '../../types/agent';
import { isMacPlatform } from '../../utils/platform';
import { SettingsDisclosure } from './SettingsDisclosure';

const TRAY = ref(isMacPlatform() ? 'settings.desktop.menuBar' : 'settings.desktop.tray');

const TOGGLES: Array<{ key: keyof TraySettings; label: MessageKey; hint: MessageKey }> = [
    { key: 'minimizeToTray', label: 'settings.desktop.keepRunning', hint: 'settings.desktop.keepRunningHint' },
    { key: 'startOnLogin', label: 'settings.desktop.startOnLogin', hint: 'settings.desktop.startOnLoginHint' },
    { key: 'startMinimized', label: 'settings.desktop.startMinimized', hint: 'settings.desktop.startMinimizedHint' },
];

/** Desktop-app settings, saved by the tray. Shown only in the desktop app. */
export function DesktopAppSettings() {
    const t = useT();
    const tray = window.gaiaAPI?.tray;
    const [settings, setSettings] = useState<TraySettings | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState<keyof TraySettings | null>(null);

    useEffect(() => {
        if (!tray) return;
        tray.getConfig()
            .then((cfg) => setSettings(cfg.tray))
            .catch((err) => setError(t('settings.desktop.readFailed', { reason: err instanceof Error ? err.message : String(err) })));
    }, [tray, t]);

    const toggle = useCallback(async (key: keyof TraySettings) => {
        if (!tray || !settings || saving !== null) return;
        setSaving(key);
        setError(null);
        try {
            const saved = await tray.setConfig({ tray: { [key]: !settings[key] } });
            setSettings(saved.tray);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setSaving(null);
        }
    }, [tray, settings, saving]);

    if (!tray) return null;

    return (
        <SettingsDisclosure title={t('settings.desktop.title')}>
            <div className="settings-field" role="group" aria-labelledby="desktop-app-label">
                <span className="settings-label" id="desktop-app-label">{t('settings.desktop.title')}</span>
                {TOGGLES.map(({ key, label, hint }) => (
                    <div key={key}>
                        <label className="setting-row">
                            <span>{t(label, { tray: TRAY })}</span>
                            <span className="toggle-switch">
                                <input
                                    type="checkbox"
                                    role="switch"
                                    checked={!!settings?.[key]}
                                    aria-describedby={`desktop-${key}-hint`}
                                    onChange={() => void toggle(key)}
                                    // The switch being saved stays focusable; disabling it would drop keyboard focus.
                                    disabled={!settings || (saving !== null && saving !== key)}
                                    aria-busy={saving === key}
                                    aria-label={t(label, { tray: TRAY })}
                                />
                                <span className="toggle-track" />
                            </span>
                        </label>
                        <p className="settings-hint" id={`desktop-${key}-hint`}>{t(hint, { tray: TRAY })}</p>
                    </div>
                ))}
                {error && <p className="settings-error" role="alert">{error}</p>}
            </div>
        </SettingsDisclosure>
    );
}
