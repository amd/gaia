// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import type { SkillInfo, SkillSource } from '../../types';

const GROUPS: Array<{ source: SkillSource; title: string; note: string; empty?: string }> = [
    { source: 'agent-bundled', title: 'Built into GAIA', note: 'Ship with GAIA.' },
    {
        source: 'user',
        title: 'Yours',
        note: 'From your ~/.gaia/skills folder.',
        empty: 'None yet. Add a folder with a SKILL.md file to ~/.gaia/skills and reopen this page.',
    },
    {
        source: 'claude-import',
        title: 'From Claude Code',
        note: 'Found in your Claude Code skill folders. GAIA reads them in place and does not change them.',
    },
];

/** The skills GAIA can load, with a way to start a chat that uses one. */
export function SkillsSettings({ onUseSkill }: { onUseSkill: (name: string) => void }) {
    const [skills, setSkills] = useState<SkillInfo[] | null>(null);
    const [invalid, setInvalid] = useState<Record<string, string>>({});
    const [error, setError] = useState<string | null>(null);
    const [query, setQuery] = useState('');

    useEffect(() => {
        let cancelled = false;
        api.listSkills()
            .then((r) => { if (!cancelled) { setSkills(r.skills); setInvalid(r.invalid); } })
            .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
        return () => { cancelled = true; };
    }, []);

    const q = query.trim().toLowerCase();
    const shown = (skills ?? []).filter((s) => !q || s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q));

    const grouped = skills?.every((s) => s.source) ?? false;

    const renderRow = (s: SkillInfo) => (
        <li key={s.name} className="skill-row">
            <div className="skill-row-text">
                <span className="skill-row-name">
                    {s.name}
                    {s.version && <span className="skill-row-meta"> v{s.version}</span>}
                    {s.always_on && <span className="skill-row-meta"> · Always on</span>}
                </span>
                <span className="skill-row-desc">{s.description}</span>
            </div>
            {!s.always_on && (
                <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => onUseSkill(s.name)}
                    aria-label={`Use ${s.name} in a new chat`}
                >
                    Use
                </button>
            )}
        </li>
    );

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">Skills</h2>
            <p className="settings-pane-lede">
                Skills teach GAIA a job — a workflow, a CLI, a document type. GAIA loads one when a request
                needs it, and keeps the lessons it learns while using it. Your own skills go in <code>~/.gaia/skills</code>, one folder each with a <code>SKILL.md</code> file.
            </p>
            {error && <p className="settings-error" role="alert">{error}</p>}
            {!skills && !error && <p className="settings-hint"><Loader2 size={13} className="spin" /> Loading…</p>}
            {skills && skills.length > 6 && (
                <input
                    className="settings-input"
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search skills"
                    aria-label="Search skills"
                />
            )}
            {skills && skills.length === 0 && <p className="settings-hint">No skills are installed.</p>}
            {grouped ? GROUPS.map((g) => {
                const rows = shown.filter((s) => s.source === g.source);
                const total = (skills ?? []).filter((s) => s.source === g.source).length;
                if (rows.length === 0 && (q || !g.empty)) return null;
                return (
                    <section key={g.source} className="skill-group" aria-label={g.title}>
                        <h3 className="skill-group-title">
                            {g.title} <span className="skill-row-meta">{total}</span>
                        </h3>
                        <p className="settings-hint">{g.note}</p>
                        {rows.length === 0 ? <p className="settings-hint">{g.empty}</p> : <ul className="skill-list">{rows.map(renderRow)}</ul>}
                    </section>
                );
            }) : <ul className="skill-list">{shown.map(renderRow)}</ul>}
            {Object.keys(invalid).length > 0 && (
                <div className="settings-card" role="alert">
                    <span className="settings-label">Skipped, could not be read</span>
                    <ul className="grant-list">
                        {Object.entries(invalid).map(([path, why]) => (
                            <li key={path}><code>{path}</code> — {why}</li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}
