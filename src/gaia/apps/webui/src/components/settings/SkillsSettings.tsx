// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import type { MessageKey, Params } from '../../i18n/registry';
import { useT } from '../../i18n/useT';
import { AddSkillsFromLink } from './AddSkillsFromLink';
import { SettingsDisclosure } from './SettingsDisclosure';
import type { SkillInfo, SkillSource } from '../../types';

// Paths go in as params, so the translator keeps them left to right inside an Arabic sentence.
const SKILLS_DIR = { path: '~/.gaia/skills' };
const PROJECT_SKILLS_DIR = { path: '.claude/skills' };

const GROUPS: Array<{ source: SkillSource; title: MessageKey; note: MessageKey; empty?: MessageKey; params?: Params }> = [
    { source: 'agent-bundled', title: 'settings.skills.bundled', note: 'settings.skills.bundledNote' },
    { source: 'user', title: 'settings.skills.yours', note: 'settings.skills.yoursNote', empty: 'settings.skills.yoursEmpty', params: SKILLS_DIR },
    { source: 'claude-project', title: 'settings.skills.project', note: 'settings.skills.projectNote', params: PROJECT_SKILLS_DIR },
    { source: 'claude-import', title: 'settings.skills.import', note: 'settings.skills.importNote' },
];

/** The skills GAIA can load, with a way to start a chat that uses one. */
export function SkillsSettings({ onUseSkill }: { onUseSkill: (name: string) => void }) {
    const t = useT();
    const [skills, setSkills] = useState<SkillInfo[] | null>(null);
    const [invalid, setInvalid] = useState<Record<string, string>>({});
    const [error, setError] = useState<string | null>(null);
    const [query, setQuery] = useState('');

    const [reloads, setReloads] = useState(0);
    const reload = useCallback(() => setReloads((n) => n + 1), []);

    useEffect(() => {
        let cancelled = false;
        api.listSkills()
            .then((r) => { if (!cancelled) { setSkills(r.skills); setInvalid(r.invalid); setError(null); } })
            .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
        return () => { cancelled = true; };
    }, [reloads]);

    const q = query.trim().toLowerCase();
    const shown = (skills ?? []).filter((s) => !q || s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q));

    // A source this build has no group for (a newer agent's) shows the list flat, never hides it.
    const grouped = skills?.every((s) => GROUPS.some((g) => g.source === s.source)) ?? false;

    const renderRow = (s: SkillInfo) => (
        <li key={s.name} className="skill-row">
            <div className="skill-row-text">
                <span className="skill-row-name">
                    {s.name}
                    {s.version && <span className="skill-row-meta"> v{s.version}</span>}
                    {s.always_on && <span className="skill-row-meta"> · {t('settings.skills.alwaysOn')}</span>}
                </span>
                <span className="skill-row-desc"><bdi>{s.description}</bdi></span>
            </div>
            {!s.always_on && (
                <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => onUseSkill(s.name)}
                    aria-label={t('settings.skills.useAria', { name: s.name })}
                >
                    {t('settings.model.use')}
                </button>
            )}
        </li>
    );

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">{t('settings.section.skills')}</h2>
            <p className="settings-pane-lede">{t('settings.skills.lede')}</p>
            <details className="settings-more">
                <summary><ChevronDown size={13} className="settings-disclosure-chevron" aria-hidden="true" /> {t('settings.model.howItWorks')}</summary>
                <p className="settings-hint">{t('settings.skills.how', SKILLS_DIR)}</p>
            </details>
            <AddSkillsFromLink onAdded={reload} />
            {error && <p className="settings-error" role="alert">{error}</p>}
            {!skills && !error && <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>}
            {skills && skills.length > 6 && (
                <input
                    className="settings-input"
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={t('settings.skills.search')}
                    aria-label={t('settings.skills.search')}
                />
            )}
            {skills && skills.length === 0 && <p className="settings-hint">{t('settings.skills.none')}</p>}
            {grouped ? GROUPS.map((g) => {
                const rows = shown.filter((s) => s.source === g.source);
                const total = (skills ?? []).filter((s) => s.source === g.source).length;
                if (rows.length === 0 && (q || !g.empty)) return null;
                return (
                    <SettingsDisclosure key={g.source} title={t(g.title)} count={total} hint={t(g.note, g.params)} defaultOpen={g.source === 'user'} forceOpen={!!q}>
                        <section aria-label={t(g.title)}>
                            {rows.length === 0 ? <p className="settings-hint">{g.empty && t(g.empty, g.params)}</p> : <ul className="skill-list">{rows.map(renderRow)}</ul>}
                        </section>
                    </SettingsDisclosure>
                );
            }) : <ul className="skill-list">{shown.map(renderRow)}</ul>}
            {Object.keys(invalid).length > 0 && (
                <div className="settings-card" role="alert">
                    <span className="settings-label">{t('settings.skills.skipped')}</span>
                    <ul className="grant-list">
                        {Object.entries(invalid).map(([path, why]) => (
                            <li key={path}><bdi dir="ltr"><code>{path}</code></bdi> — <bdi>{why}</bdi></li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}
