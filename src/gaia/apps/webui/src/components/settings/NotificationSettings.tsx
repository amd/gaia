// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import {
    useAlertSettings,
    type AlertEvent,
    type AlertMode,
} from '../../stores/notificationStore';
import type { MessageKey } from '../../i18n/registry';
import { useT } from '../../i18n/useT';

const EVENTS: Array<{ id: AlertEvent; label: MessageKey }> = [
    { id: 'needs_approval', label: 'settings.alerts.approval' },
    { id: 'question', label: 'settings.alerts.question' },
    { id: 'finished', label: 'settings.alerts.finished' },
];

const MODES: Array<{ value: AlertMode; label: MessageKey }> = [
    { value: 'banner', label: 'settings.alerts.banner' },
    { value: 'badge', label: 'settings.alerts.badge' },
    { value: 'off', label: 'settings.alerts.off' },
];

const BATTERY_FLOORS = [0, 10, 20, 30, 50];

function browserPermission(): NotificationPermission | 'unsupported' {
    return typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
}

/** When a chat alerts you, and how; plus keep-awake (#5226). */
export function NotificationSettings() {
    const t = useT();
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
            <h2 className="settings-pane-title">{t('settings.section.notifications')}</h2>
            <p className="settings-pane-lede">{t('settings.alerts.lede')}</p>
            <section className="settings-card" aria-label={t('settings.alerts.when')}>
                {EVENTS.map((e) => (
                    <div className="setting-row" key={e.id}>
                        <span id={`alert-${e.id}`}>{t(e.label)}</span>
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
                                    {t(m.label)}
                                </button>
                            ))}
                        </div>
                    </div>
                ))}
                {!isDesktop && wantsBanner && permission !== 'granted' && (
                    permission === 'default' ? (
                        <button type="button" className="btn-secondary" onClick={askPermission}>
                            {t('settings.alerts.allow')}
                        </button>
                    ) : (
                        <p className="settings-error" role="alert">
                            {permission === 'unsupported'
                                ? t('settings.alerts.unsupported') : t('settings.alerts.blocked')}
                        </p>
                    )
                )}
                <label className="setting-row">
                    <span>{t('settings.alerts.sound')}</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            checked={settings.sound}
                            onChange={() => settings.update({ sound: !settings.sound })}
                            aria-label={t('settings.alerts.sound')}
                        />
                        <span className="toggle-track" />
                    </span>
                </label>
                {isDesktop && (
                    <label className="setting-row">
                        <span>{t('settings.alerts.flash')}</span>
                        <span className="toggle-switch">
                            <input
                                type="checkbox"
                                checked={settings.flash}
                                onChange={() => settings.update({ flash: !settings.flash })}
                                aria-label={t('settings.alerts.flash')}
                            />
                            <span className="toggle-track" />
                        </span>
                    </label>
                )}
            </section>

            <section className="settings-card" aria-labelledby="keep-awake-title">
                <h3 className="settings-label" id="keep-awake-title">{t('settings.alerts.keepAwakeTitle')}</h3>
                {isDesktop ? (
                    <>
                        <label className="setting-row">
                            <span>{t('settings.alerts.keepAwake')}</span>
                            <span className="toggle-switch">
                                <input
                                    type="checkbox"
                                    checked={settings.keepAwake}
                                    onChange={() => settings.update({ keepAwake: !settings.keepAwake })}
                                    aria-label={t('settings.alerts.keepAwake')}
                                />
                                <span className="toggle-track" />
                            </span>
                        </label>
                        <div className="setting-row">
                            <label htmlFor="battery-floor">{t('settings.alerts.battery')}</label>
                            <select
                                id="battery-floor"
                                className="settings-input settings-select-compact"
                                value={settings.batteryFloor}
                                disabled={!settings.keepAwake}
                                onChange={(ev) => settings.update({ batteryFloor: Number(ev.target.value) })}
                            >
                                {BATTERY_FLOORS.map((f) => (
                                    <option key={f} value={f}>{f === 0 ? t.bare('settings.alerts.never') : `${f}%`}</option>
                                ))}
                            </select>
                        </div>
                        <p className="settings-hint">{t('settings.alerts.screen')}</p>
                    </>
                ) : (
                    <p className="settings-hint">{t('settings.alerts.desktopOnly')}</p>
                )}
            </section>
        </div>
    );
}
