// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';
import * as api from '../../services/api';
import type { FeaturedLibrary, LinkAddResult, LinkPreview, LinkSkill } from '../../types';

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Ready to add: readable, not blocked, new or an update, and code only when allowed. */
const addable = (s: LinkSkill, allowCode: boolean) =>
    !s.error && (!s.installed || s.update) && (allowCode || !s.has_code);

function summarize(result: LinkAddResult): string {
    const updated = result.added.filter((a) => a.updated).map((a) => a.name);
    const added = result.added.filter((a) => !a.updated).map((a) => a.name);
    const part = (verb: string, names: string[]) =>
        names.length > 3 ? `${verb} ${names.length} skills.` : `${verb} ${names.join(', ')}.`;
    return [added.length ? part('Added', added) : '', updated.length ? part('Updated', updated) : ''].filter(Boolean).join(' ');
}

/** Paste a link to a skill or a skills library, review what it holds, then add it. */
export function AddSkillsFromLink({ onAdded }: { onAdded: () => void }) {
    const [url, setUrl] = useState('');
    const [busy, setBusy] = useState<'preview' | 'add' | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [preview, setPreview] = useState<LinkPreview | null>(null);
    const [picked, setPicked] = useState<Set<string>>(new Set());
    const [allowCode, setAllowCode] = useState(false);
    const [result, setResult] = useState<LinkAddResult | null>(null);
    const [featured, setFeatured] = useState<FeaturedLibrary[]>([]);

    useEffect(() => {
        let cancelled = false;
        api.listFeaturedSkillLibraries()
            .then((r) => { if (!cancelled) setFeatured(r.libraries); })
            .catch((err) => { if (!cancelled) setError(`Could not load suggested libraries: ${errorText(err)}`); });
        return () => { cancelled = true; };
    }, []);

    const read = async (link: string) => {
        if (!link.trim()) return;
        setBusy('preview');
        setError(null);
        setPreview(null);
        setResult(null);
        try {
            const p = await api.previewSkillLink(link.trim());
            setPreview(p);
            setPicked(new Set(p.skills.filter((s) => addable(s, false)).map((s) => s.name)));
        } catch (err) {
            setError(errorText(err));
        } finally {
            setBusy(null);
        }
    };

    const add = async () => {
        if (!preview || picked.size === 0) return;
        setBusy('add');
        setError(null);
        try {
            const r = await api.addSkillsFromLink(preview.link, [...picked], allowCode, preview.pin);
            setResult(r);
            setPreview(null);
            setUrl('');
            if (r.added.length) onAdded();
        } catch (err) {
            setError(errorText(err));
        } finally {
            setBusy(null);
        }
    };

    const toggle = (name: string) => setPicked((prev) => {
        const next = new Set(prev);
        if (next.has(name)) next.delete(name); else next.add(name);
        return next;
    });

    const toggleCode = (on: boolean) => {
        setAllowCode(on);
        if (!on && preview) {
            const code = new Set(preview.skills.filter((s) => s.has_code).map((s) => s.name));
            setPicked((prev) => new Set([...prev].filter((n) => !code.has(n))));
        }
    };

    const selectable = preview?.skills.filter((s) => addable(s, allowCode)) ?? [];
    const allPicked = selectable.length > 0 && selectable.every((s) => picked.has(s.name));
    const hasCode = preview?.skills.some((s) => s.has_code && !s.error) ?? false;

    return (
        <div className="settings-card skill-link">
            <div className="skill-link-top">
                <span className="settings-label">Add skills</span>
                {featured.map((f) => (
                    <button
                        key={f.id}
                        type="button"
                        className="btn-secondary skill-link-chip"
                        onClick={() => { setUrl(f.url); void read(f.url); }}
                        disabled={busy !== null}
                        title={f.description}
                        aria-label={`Get ${f.name}: ${f.description}`}
                    >
                        Get {f.name}
                    </button>
                ))}
            </div>
            <form className="skill-link-form" onSubmit={(e) => { e.preventDefault(); void read(url); }}>
                <input
                    className="settings-input"
                    type="text"
                    inputMode="url"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    onPaste={(e) => {
                        const pasted = e.clipboardData.getData('text').trim();
                        if (pasted) { e.preventDefault(); setUrl(pasted); void read(pasted); }
                    }}
                    placeholder="Paste a GitHub, skills.sh or .zip link"
                    title="A GitHub repo or folder, owner/repo, a skills.sh page, or an https .zip. A library adds every skill in it."
                    aria-label="Link to a skill or skills library"
                    disabled={busy !== null}
                />
                <button type="submit" className="btn-secondary" disabled={!url.trim() || busy !== null}>
                    {busy === 'preview' ? <><Loader2 size={13} className="spin" /> Reading…</> : 'Read'}
                </button>
            </form>
            {error && <p className="settings-error" role="alert">{error}</p>}

            {preview && (
                <div className="skill-link-preview">
                    <details className="skill-link-warning" role="note">
                        <summary>
                            <AlertTriangle size={14} aria-hidden="true" />
                            Only add skills from people you trust.
                        </summary>
                        <p>{preview.warning}</p>
                    </details>
                    <div className="skill-link-head">
                        <span>{preview.skills.length} in <code>{preview.source}</code></span>
                        {selectable.length > 1 && (
                            <button
                                type="button"
                                className="btn-secondary"
                                onClick={() => setPicked(allPicked ? new Set() : new Set(selectable.map((s) => s.name)))}
                            >
                                {allPicked ? 'None' : 'All'}
                            </button>
                        )}
                    </div>
                    <ul className="skill-list skill-link-list">
                        {preview.skills.map((s) => {
                            const can = addable(s, allowCode);
                            const tag = s.error ? (s.verdict === 'BLOCK' ? 'blocked' : 'unreadable')
                                : s.installed && !s.update ? 'installed elsewhere'
                                : s.update ? 'update'
                                : null;
                            const why = s.error
                                ?? (s.installed && !s.update ? 'A skill with this name is already installed from somewhere else.'
                                    : s.update ? 'Installed from this link before. Adding it again updates it to the latest version.'
                                    : null);
                            const hasDetails = !!why || s.findings.length > 0 || (s.has_code && !allowCode);
                            return (
                                <li key={s.path} className="skill-row">
                                    <label className="skill-link-pick">
                                        <input
                                            type="checkbox"
                                            checked={can && picked.has(s.name)}
                                            disabled={!can}
                                            onChange={() => toggle(s.name)}
                                            aria-label={`Add ${s.name}`}
                                        />
                                        <span className="skill-row-text">
                                            <span className="skill-row-name">
                                                {s.name}
                                                {tag && <span className={`skill-link-tag${s.error ? ' is-bad' : ''}`}>{tag}</span>}
                                                {s.has_code && <span className="skill-link-tag">code</span>}
                                                {s.verdict === 'REVIEW' && <span className="skill-link-tag">review</span>}
                                            </span>
                                            {s.description && <span className="skill-row-desc skill-link-desc" title={s.description}>{s.description}</span>}
                                            {hasDetails && (
                                                <details className="skill-link-findings">
                                                    <summary>Details</summary>
                                                    {why && <p>{why}</p>}
                                                    {s.has_code && !allowCode && <p>Ships code. Tick Include skills with code to add it.</p>}
                                                    {s.findings.length > 0 && <ul>{s.findings.map((f) => <li key={f}>{f}</li>)}</ul>}
                                                </details>
                                            )}
                                        </span>
                                    </label>
                                </li>
                            );
                        })}
                    </ul>
                    <div className="skill-link-actions">
                        {hasCode && (
                            <label className="skill-link-code" title="Their scripts can run on this PC with your permissions.">
                                <input type="checkbox" checked={allowCode} onChange={(e) => toggleCode(e.target.checked)} />
                                <span>Include skills with code</span>
                            </label>
                        )}
                        <button type="button" className="btn-secondary" onClick={() => setPreview(null)} disabled={busy !== null}>
                            Cancel
                        </button>
                        <button type="button" className="btn-primary" onClick={() => void add()} disabled={picked.size === 0 || busy !== null}>
                            {busy === 'add' ? <><Loader2 size={13} className="spin" /> Adding…</> : `Add ${picked.size}`}
                        </button>
                    </div>
                </div>
            )}

            {result && (
                <div className="skill-link-result" role="status">
                    {result.added.length > 0 && <p>{summarize(result)}</p>}
                    {Object.keys(result.failed).length > 0 && (
                        <details>
                            <summary>{Object.keys(result.failed).length} not added</summary>
                            <ul className="grant-list">
                                {Object.entries(result.failed).map(([name, why]) => <li key={name}><code>{name}</code> — {why}</li>)}
                            </ul>
                        </details>
                    )}
                </div>
            )}
        </div>
    );
}
