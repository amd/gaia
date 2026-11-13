// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
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

function skipReason(why: string): MessageKey {
    if (/description is \d+ characters/.test(why)) return 'settings.skills.skipped.tooLong';
    if (/'description' is missing/.test(why)) return 'settings.skills.skipped.noDescription';
    return 'settings.skills.skipped.other';
}

/** A skill's description, cut at two lines until the reader asks for the rest. */
function SkillDescription({ text }: { text: string }) {
    const t = useT();
    const ref = useRef<HTMLSpanElement>(null);
    const [open, setOpen] = useState(false);
    const [cut, setCut] = useState(false);

    useLayoutEffect(() => {
        const el = ref.current;
        if (!el || open) return;
        const measure = () => setCut(el.scrollHeight > el.clientHeight + 1);
        measure();
        if (typeof ResizeObserver === 'undefined') return;
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [text, open]);

    return (
        <>
            <span ref={ref} className={`skill-row-desc${open ? ' is-open' : ''}`}><bdi>{text}</bdi></span>
            {(cut || open) && (
                <button type="button" className="skill-row-more" aria-expanded={open} onClick={() => setOpen(!open)}>
                    {t(open ? 'settings.skills.less' : 'settings.skills.more')}
                </button>
            )}
        </>
    );
}

/** The skills GAIA can load, with a way to start a chat that uses one. */
export function SkillsSettings({ onUseSkill }: { onUseSkill: (name: string) => void }) {
    const t = useT();
    const [skills, setSkills] = useState<SkillInfo[] | null>(null);
    const [invalid, setInvalid] = useState<Record<string, string>>({});
    const [error, setError] = useState<string | null>(null);
    const [query, setQuery] = useState('');
    const search = useRef<HTMLInputElement>(null);
    const active = useRef(true);

    const load = useCallback(() => {
        api.listSkills()
            .then((r) => { if (active.current) { setSkills(r.skills); setInvalid(r.invalid); setError(null); } })
            .catch((err) => { if (active.current) setError(err instanceof Error ? err.message : String(err)); });
    }, []);

    // Read again when the window comes back, so a skill folder added meanwhile shows up.
    useEffect(() => {
        active.current = true;
        load();
        window.addEventListener('focus', load);
        return () => { active.current = false; window.removeEventListener('focus', load); };
    }, [load]);

    const q = query.trim().toLowerCase();
    const shown = (skills ?? []).filter((s) => !q || s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q));

    // A source this build has no group for (a newer agent's) shows the list flat, never hides it.
    const grouped = skills?.every((s) => GROUPS.some((g) => g.source === s.source)) ?? false;

    const renderRow = (s: SkillInfo) => (
        <li key={s.name} className="skill-row">
            <div className="skill-row-text">
                <span className="skill-row-name">
                    <span title={s.version ? `v${s.version}` : undefined}>{s.name}</span>
                    {s.always_on && <span className="skill-row-meta"> · {t('settings.skills.alwaysOn')}</span>}
                </span>
                <SkillDescription text={s.description} />
            </div>
            {!s.always_on && (
                <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => onUseSkill(s.name)}
                    aria-label={t('settings.skills.useAria', { name: s.name })}
                >
                    {t('settings.skills.try')}
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
            <AddSkillsFromLink onAdded={load} />
            {error && <p className="settings-error" role="alert">{error}</p>}
            {!skills && !error && <p className="settings-hint"><Loader2 size={13} className="spin" /> {t('settings.loading')}</p>}
            {skills && skills.length > 6 && (
                <input
                    ref={search}
                    className="settings-input"
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={t('settings.skills.search')}
                    aria-label={t('settings.skills.search')}
                />
            )}
            {skills && skills.length === 0 && <p className="settings-hint">{t('settings.skills.none')}</p>}
            {q && skills && skills.length > 0 && shown.length === 0 && (
                <p className="settings-hint" role="status">
                    {t('settings.skills.noMatch', { query: query.trim() })}{' '}
                    <button type="button" className="link-btn" onClick={() => { setQuery(''); search.current?.focus(); }}>
                        {t('settings.skills.clearSearch')}
                    </button>
                </p>
            )}
            {grouped ? GROUPS.map((g) => {
                const rows = shown.filter((s) => s.source === g.source);
                if (rows.length === 0 && (q || !g.empty)) return null;
                return (
                    <SettingsDisclosure key={g.source} title={t(g.title)} count={rows.length} hint={t(g.note, g.params)} defaultOpen={g.source === 'agent-bundled' || (g.source === 'user' && rows.length > 0)} forceOpen={!!q}>
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
                            <li key={path}>{t(skipReason(why), { name: path.split(/[\\/]/).filter(Boolean).pop() ?? path })}</li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}
