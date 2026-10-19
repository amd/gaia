// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import type { TraySettings } from '../../types/agent';

const isMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform);
const TRAY = isMac ? 'menu bar' : 'tray';

const TOGGLES: Array<{ key: keyof TraySettings; label: string; hint: string }> = [
    {
        key: 'minimizeToTray',
        label: 'Keep running when the window closes',
        hint: `GAIA stays in the ${TRAY} with your model loaded. Quit from the ${TRAY} menu.`,
    },
    {
        key: 'startOnLogin',
        label: 'Start GAIA when you sign in',
        hint: 'Your first chat of the day starts without waiting for GAIA to open.',
    },
    {
        key: 'startMinimized',
        label: `Start in the ${TRAY}`,
        hint: `GAIA opens without its window. Use the ${TRAY} icon to show it.`,
    },
];

/** Desktop-app settings, saved by the tray. Shown only in the desktop app. */
export function DesktopAppSettings() {
    const tray = window.gaiaAPI?.tray;
    const [settings, setSettings] = useState<TraySettings | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState<keyof TraySettings | null>(null);

    useEffect(() => {
        if (!tray) return;
        tray.getConfig()
            .then((cfg) => setSettings(cfg.tray))
            .catch((err) => setError(`Couldn't read the desktop settings: ${err instanceof Error ? err.message : String(err)}`));
    }, [tray]);

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
        <div className="settings-field" role="group" aria-labelledby="desktop-app-label">
            <span className="settings-label" id="desktop-app-label">Desktop app</span>
            {TOGGLES.map(({ key, label, hint }) => (
                <div key={key}>
                    <label className="setting-row">
                        <span>{label}</span>
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
                                aria-label={label}
                            />
                            <span className="toggle-track" />
                        </span>
                    </label>
                    <p className="settings-hint" id={`desktop-${key}-hint`}>{hint}</p>
                </div>
            ))}
            {error && <p className="settings-error" role="alert">{error}</p>}
        </div>
    );
}
