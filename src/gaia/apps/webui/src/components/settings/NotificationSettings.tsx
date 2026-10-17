// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import {
    useAlertSettings,
    type AlertEvent,
    type AlertMode,
} from '../../stores/notificationStore';

const EVENTS: Array<{ id: AlertEvent; label: string }> = [
    { id: 'needs_approval', label: 'Needs your approval' },
    { id: 'question', label: 'Has a question' },
    { id: 'finished', label: 'Finished' },
];

const MODES: Array<{ value: AlertMode; label: string }> = [
    { value: 'banner', label: 'Banner' },
    { value: 'badge', label: 'Badge' },
    { value: 'off', label: 'Off' },
];

const BATTERY_FLOORS = [0, 10, 20, 30, 50];

function browserPermission(): NotificationPermission | 'unsupported' {
    return typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
}

/** When a chat alerts you, and how; plus keep-awake (#5226). */
export function NotificationSettings() {
    const settings = useAlertSettings();
    const isDesktop = !!window.gaiaAPI;
    const [permission, setPermission] = useState(browserPermission);
    const wantsBanner = Object.values(settings.modes).includes('banner');

    const askPermission = () => {
        Notification.requestPermission().then(setPermission).catch((err) => {
            console.error('[alerts] Notification permission request failed', err);
            setPermission(browserPermission());
        });
    };

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">Notifications</h2>
            <p className="settings-pane-lede">
                When a chat you aren't looking at needs you or finishes. A banner also counts on the badge.
            </p>
            {EVENTS.map((e) => (
                <div className="setting-row" key={e.id}>
                    <span id={`alert-${e.id}`}>{e.label}</span>
                    <div className="segmented" role="radiogroup" aria-labelledby={`alert-${e.id}`}>
                        {MODES.map((m) => (
                            <button
                                key={m.value}
                                type="button"
                                role="radio"
                                aria-checked={settings.modes[e.id] === m.value}
                                className={settings.modes[e.id] === m.value ? 'is-active' : ''}
                                onClick={() => settings.setMode(e.id, m.value)}
                            >
                                {m.label}
                            </button>
                        ))}
                    </div>
                </div>
            ))}
            {!isDesktop && wantsBanner && permission !== 'granted' && (
                permission === 'default' ? (
                    <button type="button" className="btn-secondary" onClick={askPermission}>
                        Allow banners in this browser
                    </button>
                ) : (
                    <p className="settings-error" role="alert">
                        {permission === 'unsupported'
                            ? "This browser can't show banners; alerts appear in the chat list only."
                            : 'This browser blocks banners from GAIA. Allow notifications for this site in the browser’s site settings.'}
                    </p>
                )
            )}
            <label className="setting-row">
                <span>Play a sound</span>
                <span className="toggle-switch">
                    <input
                        type="checkbox"
                        checked={settings.sound}
                        onChange={() => settings.update({ sound: !settings.sound })}
                        aria-label="Play a sound"
                    />
                    <span className="toggle-track" />
                </span>
            </label>
            {isDesktop && (
                <label className="setting-row">
                    <span>Flash the taskbar when GAIA isn&apos;t focused</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            checked={settings.flash}
                            onChange={() => settings.update({ flash: !settings.flash })}
                            aria-label="Flash the taskbar when GAIA isn't focused"
                        />
                        <span className="toggle-track" />
                    </span>
                </label>
            )}

            <h3 className="settings-label">Keep awake</h3>
            {isDesktop ? (
                <>
                    <label className="setting-row">
                        <span>Keep the PC awake while GAIA works</span>
                        <span className="toggle-switch">
                            <input
                                type="checkbox"
                                checked={settings.keepAwake}
                                onChange={() => settings.update({ keepAwake: !settings.keepAwake })}
                                aria-label="Keep the PC awake while GAIA works"
                            />
                            <span className="toggle-track" />
                        </span>
                    </label>
                    <div className="setting-row">
                        <label htmlFor="battery-floor">On battery, let it sleep below</label>
                        <select
                            id="battery-floor"
                            value={settings.batteryFloor}
                            disabled={!settings.keepAwake}
                            onChange={(ev) => settings.update({ batteryFloor: Number(ev.target.value) })}
                        >
                            {BATTERY_FLOORS.map((f) => (
                                <option key={f} value={f}>{f === 0 ? 'Never' : `${f}%`}</option>
                            ))}
                        </select>
                    </div>
                    <p className="settings-hint">The screen can still turn off; only sleep is held off.</p>
                </>
            ) : (
                <p className="settings-hint">Keep-awake needs the GAIA desktop app.</p>
            )}
        </div>
    );
}
