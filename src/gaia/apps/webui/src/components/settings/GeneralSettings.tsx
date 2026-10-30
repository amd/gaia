// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useChatStore } from '../../stores/chatStore';
import type { ThemePreference } from '../../utils/theme';
import { DesktopAppSettings } from './DesktopAppSettings';
import { UpdatesSection } from './UpdatesSection';
import { DEFAULT_CUSTOM, PALETTES, type CustomColors } from '../../utils/palette';
import { formatShortcut } from '../../utils/platform';
import { browserPaneApi } from '../../stores/browserPaneStore';

const THEMES: Array<{ value: ThemePreference; label: string }> = [
    { value: 'light', label: 'Light' },
    { value: 'dark', label: 'Dark' },
    { value: 'system', label: 'Match system' },
];

const SHORTCUTS: Array<[string, string]> = [
    ['Enter', 'Send'],
    ['Shift+Enter', 'New line'],
    ['Esc', 'Stop the answer'],
    ['Mod+Shift+O', 'New chat'],
    ['Mod+K', 'Search chats'],
    ['Mod+B', 'Show or hide the sidebar'],
    ['Mod+,', 'Settings'],
];

/** The desktop app also has the browser pane. */
const DESKTOP_SHORTCUTS: Array<[string, string]> = [['Mod+Shift+B', 'Show or hide the browser pane']];

export function GeneralSettings() {
    const pref = useChatStore((s) => s.themePreference);
    const setPref = useChatStore((s) => s.setThemePreference);
    const palette = useChatStore((s) => s.palette);
    const setPalette = useChatStore((s) => s.setPalette);
    const custom = useChatStore((s) => s.customColors);
    const setCustom = useChatStore((s) => s.setCustomColors);
    const pick = (key: keyof CustomColors) => (e: React.ChangeEvent<HTMLInputElement>) =>
        setCustom({ ...custom, [key]: e.target.value.toUpperCase() });

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
            <DesktopAppSettings />
            <UpdatesSection />
            <div className="settings-field">
                <span className="settings-label" id="palette-label">Colors</span>
                <div className="palette-grid" role="radiogroup" aria-labelledby="palette-label">
                    {PALETTES.map((p) => (
                        <button
                            key={p.id}
                            type="button"
                            role="radio"
                            aria-checked={palette === p.id}
                            className={`palette-tile${palette === p.id ? ' is-active' : ''}`}
                            onClick={() => setPalette(p.id)}
                        >
                            <span className="palette-swatch" aria-hidden="true">
                                {p.swatch.map((c) => <i key={c} style={{ background: c }} />)}
                            </span>
                            {p.name}
                        </button>
                    ))}
                    <button
                        type="button"
                        role="radio"
                        aria-checked={palette === 'custom'}
                        className={`palette-tile${palette === 'custom' ? ' is-active' : ''}`}
                        onClick={() => setPalette('custom')}
                    >
                        <span className="palette-swatch" aria-hidden="true">
                            <i style={{ background: custom.base }} />
                            <i style={{ background: custom.accent }} />
                        </span>
                        Custom
                    </button>
                </div>
                {palette === 'custom' && (
                    <div className="palette-custom">
                        <label className="palette-color" htmlFor="custom-accent">
                            <input id="custom-accent" type="color" value={custom.accent} onChange={pick('accent')} />
                            <span>Accent<small>Buttons, links and focus</small></span>
                        </label>
                        <label className="palette-color" htmlFor="custom-base">
                            <input id="custom-base" type="color" value={custom.base} onChange={pick('base')} />
                            <span>Background tint<small>The color the grays lean toward</small></span>
                        </label>
                        <button type="button" className="btn-secondary" onClick={() => setCustom(DEFAULT_CUSTOM)}>Reset</button>
                        <p className="settings-hint">Text colors adjust on their own so everything stays readable in light and dark.</p>
                    </div>
                )}
            </div>
            <div className="settings-field">
                <span className="settings-label">Keyboard shortcuts</span>
                <dl className="shortcut-list">
                    {[...SHORTCUTS, ...(browserPaneApi() ? DESKTOP_SHORTCUTS : [])].map(([keys, what]) => (
                        <div key={keys} className="shortcut-row">
                            <dt><kbd>{formatShortcut(keys)}</kbd></dt>
                            <dd>{what}</dd>
                        </div>
                    ))}
                </dl>
            </div>
        </div>
    );
}
