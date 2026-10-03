// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import type { SkillInfo } from '../../types';

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

    return (
        <div className="settings-pane">
            <h2 className="settings-pane-title">Skills</h2>
            <p className="settings-pane-lede">
                Skills teach GAIA a job — a workflow, a CLI, a document type. GAIA loads one when a request
                needs it, and keeps the lessons it learns while using it. Add your own under <code>~/.gaia/skills</code>.
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
            <ul className="skill-list">
                {shown.map((s) => (
                    <li key={s.name} className="skill-row">
                        <div className="skill-row-text">
                            <span className="skill-row-name">
                                {s.name}
                                {s.version && <span className="skill-row-meta"> v{s.version}</span>}
                            </span>
                            <span className="skill-row-desc">{s.description}</span>
                        </div>
                        <button
                            type="button"
                            className="btn-secondary"
                            onClick={() => onUseSkill(s.name)}
                            aria-label={`Use ${s.name} in a new chat`}
                        >
                            Use
                        </button>
                    </li>
                ))}
            </ul>
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
