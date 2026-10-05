// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useChatStore } from '../../stores/chatStore';
import type { ThemePreference } from '../../utils/theme';

const THEMES: Array<{ value: ThemePreference; label: string }> = [
    { value: 'light', label: 'Light' },
    { value: 'dark', label: 'Dark' },
    { value: 'system', label: 'Match system' },
];

const SHORTCUTS: Array<[string, string]> = [
    ['Enter', 'Send'],
    ['Shift+Enter', 'New line'],
    ['Esc', 'Stop the answer'],
    ['Ctrl+Shift+O', 'New chat'],
    ['Ctrl+K', 'Search chats'],
    ['Ctrl+B', 'Show or hide the sidebar'],
    ['Ctrl+,', 'Settings'],
];

export function GeneralSettings() {
    const pref = useChatStore((s) => s.themePreference);
    const setPref = useChatStore((s) => s.setThemePreference);

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">General</h2>
            <div className="settings-field">
                <span className="settings-label" id="appearance-label">Appearance</span>
                <div className="segmented" role="radiogroup" aria-labelledby="appearance-label">
                    {THEMES.map((t) => (
                        <button
                            key={t.value}
                            type="button"
                            role="radio"
                            aria-checked={pref === t.value}
                            className={pref === t.value ? 'is-active' : ''}
                            onClick={() => setPref(t.value)}
                        >
                            {t.label}
                        </button>
                    ))}
                </div>
            </div>
            <div className="settings-field">
                <span className="settings-label">Keyboard shortcuts</span>
                <dl className="shortcut-list">
                    {SHORTCUTS.map(([keys, what]) => (
                        <div key={keys} className="shortcut-row">
                            <dt><kbd>{keys}</kbd></dt>
                            <dd>{what}</dd>
                        </div>
                    ))}
                </dl>
            </div>
        </div>
    );
}
