// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as memoryApi from '../../services/memoryApi';
import { useChatStore } from '../../stores/chatStore';
import { useT } from '../../i18n/useT';

/** Memory on/off and the way into the Memory dashboard. */
export function MemorySettings() {
    const t = useT();
    const setShowMemoryDashboard = useChatStore((s) => s.setShowMemoryDashboard);
    const closeSettings = useChatStore((s) => s.closeSettings);
    const [settings, setSettings] = useState<memoryApi.MemorySettings | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        memoryApi.getMemorySettings()
            .then(setSettings)
            .catch((err) => setError(err instanceof Error ? err.message : String(err)));
    }, []);

    const toggle = useCallback(async () => {
        if (!settings) return;
        setSaving(true);
        setError(null);
        try {
            setSettings(await memoryApi.updateMemorySettings({ memory_enabled: !settings.memory_enabled }));
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setSaving(false);
        }
    }, [settings]);

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.memory')}</h2>
            <p className="settings-pane-lede">{t('settings.memory.lede')}</p>
            <section className="settings-card" aria-label={t('settings.section.memory')}>
                <label className="setting-row">
                    <span>{t('settings.memory.remember')}</span>
                    <span className="toggle-switch">
                        <input
                            type="checkbox"
                            checked={!!settings?.memory_enabled}
                            onChange={() => void toggle()}
                            disabled={!settings || saving}
                            aria-label={t('settings.memory.remember')}
                        />
                        <span className="toggle-track" />
                    </span>
                </label>
                {error && <p className="settings-error" role="alert">{error}</p>}
                <div className="settings-row settings-row-between">
                    <span className="settings-hint">{t('settings.memory.dashboardHint')}</span>
                    <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => { closeSettings(); setShowMemoryDashboard(true); }}
                    >
                        {t('settings.memory.dashboard')}
                    </button>
                </div>
            </section>
        </div>
    );
}
