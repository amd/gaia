// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useChatStore } from '../../stores/chatStore';
import type { ThemePreference } from '../../utils/theme';
import { DesktopAppSettings } from './DesktopAppSettings';
import { UpdatesSection } from './UpdatesSection';
import { DEFAULT_CUSTOM, PALETTES, type CustomColors, type PaletteId } from '../../utils/palette';
import { formatShortcut } from '../../utils/platform';
import { LOCALES, localeOptionLabel, type MessageKey } from '../../i18n/registry';
import { useT } from '../../i18n/useT';

const THEMES: Array<{ value: ThemePreference; label: MessageKey }> = [
    { value: 'light', label: 'settings.theme.light' },
    { value: 'dark', label: 'settings.theme.dark' },
    { value: 'system', label: 'settings.theme.system' },
];

const PALETTE_NAME: Record<PaletteId, MessageKey> = {
    default: 'palette.default',
    stone: 'palette.stone',
    marble: 'palette.marble',
    glacier: 'palette.glacier',
    forest: 'palette.forest',
    sage: 'palette.sage',
    basalt: 'palette.basalt',
    gold: 'palette.gold',
    dusk: 'palette.dusk',
    custom: 'settings.colors.custom',
};

const SHORTCUTS: Array<[string, MessageKey]> = [
    ['Enter', 'settings.shortcut.send'],
    ['Shift+Enter', 'settings.shortcut.newLine'],
    ['Esc', 'settings.shortcut.stop'],
    ['Mod+Shift+O', 'settings.shortcut.newChat'],
    ['Mod+K', 'settings.shortcut.search'],
    ['Mod+B', 'settings.shortcut.sidebar'],
    ['Mod+,', 'settings.shortcut.settings'],
];

export function GeneralSettings() {
    const t = useT();
    const locale = useChatStore((s) => s.locale);
    const setLocale = useChatStore((s) => s.setLocale);
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
            <h2 className="settings-pane-title">{t('settings.section.general')}</h2>
            <div className="settings-field">
                <label className="settings-label" htmlFor="ui-language">{t('settings.language')}</label>
                <select
                    id="ui-language"
                    className="settings-input"
                    value={locale}
                    onChange={(e) => setLocale(e.target.value)}
                >
                    {LOCALES.map(({ meta }) => (
                        <option key={meta.code} value={meta.code} lang={meta.code} dir={meta.dir}>
                            {localeOptionLabel(meta)}
                        </option>
                    ))}
                </select>
                <span className="settings-hint">{t('settings.languageHint')}</span>
            </div>
            <div className="settings-field">
                <span className="settings-label" id="appearance-label">{t('settings.appearance')}</span>
                <div className="segmented" role="radiogroup" aria-labelledby="appearance-label">
                    {THEMES.map((th) => (
                        <button
                            key={th.value}
                            type="button"
                            role="radio"
                            aria-checked={pref === th.value}
                            className={pref === th.value ? 'is-active' : ''}
                            onClick={() => setPref(th.value)}
                        >
                            {t(th.label)}
                        </button>
                    ))}
                </div>
            </div>
            <DesktopAppSettings />
            <UpdatesSection />
            <div className="settings-field">
                <span className="settings-label" id="palette-label">{t('settings.colors')}</span>
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
                            {t(PALETTE_NAME[p.id])}
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
                        {t('settings.colors.custom')}
                    </button>
                </div>
                {palette === 'custom' && (
                    <div className="palette-custom">
                        <label className="palette-color" htmlFor="custom-accent">
                            <input id="custom-accent" type="color" value={custom.accent} onChange={pick('accent')} />
                            <span>{t('settings.colors.accent')}<small>{t('settings.colors.accentHint')}</small></span>
                        </label>
                        <label className="palette-color" htmlFor="custom-base">
                            <input id="custom-base" type="color" value={custom.base} onChange={pick('base')} />
                            <span>{t('settings.colors.base')}<small>{t('settings.colors.baseHint')}</small></span>
                        </label>
                        <button type="button" className="btn-secondary" onClick={() => setCustom(DEFAULT_CUSTOM)}>{t('settings.colors.reset')}</button>
                        <p className="settings-hint">{t('settings.colors.hint')}</p>
                    </div>
                )}
            </div>
            <div className="settings-field">
                <span className="settings-label">{t('settings.shortcuts')}</span>
                <dl className="shortcut-list">
                    {SHORTCUTS.map(([keys, what]) => (
                        <div key={keys} className="shortcut-row">
                            <dt><kbd>{formatShortcut(keys)}</kbd></dt>
                            <dd>{t(what)}</dd>
                        </div>
                    ))}
                </dl>
            </div>
        </div>
    );
}
