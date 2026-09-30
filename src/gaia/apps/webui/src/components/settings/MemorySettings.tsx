// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as memoryApi from '../../services/memoryApi';
import { useChatStore } from '../../stores/chatStore';

/** Memory on/off and the way into the Memory dashboard. */
export function MemorySettings() {
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
            <h2 className="settings-pane-title">Memory</h2>
            <p className="settings-pane-lede">
                GAIA remembers facts, preferences and past conversations across chats, on this PC only.
                Private chats (the eye icon in a chat) are never remembered.
            </p>
            <label className="setting-row">
                <span>Remember across chats</span>
                <span className="toggle-switch">
                    <input
                        type="checkbox"
                        checked={!!settings?.memory_enabled}
                        onChange={() => void toggle()}
                        disabled={!settings || saving}
                        aria-label="Remember across chats"
                    />
                    <span className="toggle-track" />
                </span>
            </label>
            {error && <p className="settings-error" role="alert">{error}</p>}
            <button
                type="button"
                className="btn-secondary"
                onClick={() => { closeSettings(); setShowMemoryDashboard(true); }}
            >
                Open the memory dashboard
            </button>
            <p className="settings-hint">Search, edit and forget what GAIA remembers.</p>
        </div>
    );
}
