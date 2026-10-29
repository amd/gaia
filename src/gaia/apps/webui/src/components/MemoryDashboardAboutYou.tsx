// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Pencil, Plus, Trash2, Undo2, Eye } from 'lucide-react';
import * as memoryApi from '../services/memoryApi';
import { log } from '../utils/logger';

/** One stored memory, as the knowledge endpoint returns it. */
export interface AboutYouItem {
    id: string;
    category: string;
    content: string;
    domain: string | null;
    source: string;
    context: string;
    entity: string | null;
    sensitive: boolean;
    created_at: string;
    updated_at: string;
    due_at?: string | null;
}

/** Categories that describe the user, grouped under a plain heading. */
const GROUPS: { title: string; categories: string[] }[] = [
    { title: 'Preferences', categories: ['preference'] },
    { title: 'About you', categories: ['fact', 'profile', 'skill'] },
    { title: 'Reminders', categories: ['reminder'] },
    { title: 'Notes', categories: ['note'] },
];

export function sourceLabel(source: string): string {
    switch (source) {
        case 'user': return 'You added this';
        case 'tool': return 'You asked GAIA to remember this';
        case 'llm_extract': return 'Picked up in a chat';
        case 'consolidation': return 'From an older chat';
        case 'inferred': return 'From your profile setup';
        case 'discovery': return 'Found on this PC during profile setup';
        default: return 'Saved';
    }
}

function relativeDay(iso: string): string {
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) return '';
    const days = Math.floor((Date.now() - then.getTime()) / 86_400_000);
    if (days <= 0) return 'today';
    if (days === 1) return 'yesterday';
    if (days < 30) return `${days} days ago`;
    return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

/** "Due Fri, Oct 30", or "Was due …" once past; a midnight due date counts as the whole day. */
export function dueLabel(iso: string, now: Date = new Date()): { text: string; past: boolean } | null {
    const bare = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    const due = bare ? new Date(+bare[1], +bare[2] - 1, +bare[3]) : new Date(iso);
    if (Number.isNaN(due.getTime())) return null;
    const allDay = due.getHours() === 0 && due.getMinutes() === 0 && due.getSeconds() === 0;
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const past = allDay ? due.getTime() < startOfToday : due.getTime() < now.getTime();
    const when = due.toLocaleString(undefined, {
        weekday: 'short', month: 'short', day: 'numeric',
        ...(due.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}),
        ...(allDay ? {} : { hour: 'numeric', minute: '2-digit' }),
    });
    return { text: `${past ? 'Was due' : 'Due'} ${when}`, past };
}

interface Props {
    memoryEnabled: boolean;
    /** Bumped by the parent whenever another tab changes memory. */
    reloadKey: number;
    onChanged: () => void;
    onOpenAdvanced: () => void;
    showToast: (text: string, type?: 'success' | 'error' | 'info') => void;
}

export function MemoryDashboardAboutYou({ memoryEnabled, reloadKey, onChanged, onOpenAdvanced, showToast }: Props) {
    const [items, setItems] = useState<AboutYouItem[] | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [draft, setDraft] = useState('');
    const [forgotten, setForgotten] = useState<AboutYouItem[]>([]);
    const [revealed, setRevealed] = useState<Set<string>>(new Set());
    const [newText, setNewText] = useState('');
    const [newCategory, setNewCategory] = useState('fact');
    const [busyId, setBusyId] = useState<string | null>(null);
    // Bumped by every load and every change, so an older load that lands late
    // cannot put back an item the user just forgot.
    const generation = useRef(0);

    const load = useCallback(async () => {
        const mine = ++generation.current;
        try {
            // Filtered on the server, so paging never fills up with GAIA's own notes.
            const data = await memoryApi.getKnowledge({
                about_user: true,
                include_sensitive: true,
                sort_by: 'updated_at',
                order: 'desc',
                limit: 200,
            });
            if (mine !== generation.current) return;
            setItems(data.items as AboutYouItem[]);
            setLoadError(null);
        } catch (err) {
            if (mine !== generation.current) return;
            log.system.warn('Failed to load what GAIA knows about the user', err);
            setLoadError(err instanceof Error ? err.message : String(err));
        }
    }, []);

    useEffect(() => { load(); }, [load, reloadKey]);

    const saveEdit = useCallback(async (item: AboutYouItem) => {
        const content = draft.trim();
        if (!content || content === item.content) { setEditingId(null); return; }
        setBusyId(item.id);
        try {
            await memoryApi.editKnowledge(item.id, { content });
            setEditingId(null);
            showToast('Updated', 'success');
            await load();
            onChanged();
        } catch (err) {
            log.system.error('Failed to edit memory', err);
            showToast(`Couldn't save the change: ${err instanceof Error ? err.message : err}`, 'error');
        } finally {
            setBusyId(null);
        }
    }, [draft, load, onChanged, showToast]);

    const forget = useCallback(async (item: AboutYouItem) => {
        setBusyId(item.id);
        try {
            await memoryApi.deleteKnowledge(item.id);
            generation.current++;
            setItems(prev => (prev ?? []).filter(i => i.id !== item.id));
            setForgotten(prev => [item, ...prev]);
            onChanged();
        } catch (err) {
            log.system.error('Failed to forget memory', err);
            showToast(`Couldn't forget that: ${err instanceof Error ? err.message : err}`, 'error');
        } finally {
            setBusyId(null);
        }
    }, [onChanged, showToast]);

    const undoForget = useCallback(async (item: AboutYouItem) => {
        setBusyId(item.id);
        try {
            await memoryApi.createKnowledge({
                content: item.content,
                category: item.category,
                domain: item.domain ?? undefined,
                context: item.context,
                entity: item.entity ?? undefined,
                sensitive: item.sensitive,
                due_at: item.due_at ?? undefined,
            });
            setForgotten(prev => prev.filter(f => f.id !== item.id));
            await load();
            onChanged();
        } catch (err) {
            log.system.error('Failed to restore memory', err);
            showToast(`Couldn't restore it: ${err instanceof Error ? err.message : err}`, 'error');
        } finally {
            setBusyId(null);
        }
    }, [load, onChanged, showToast]);

    const add = useCallback(async () => {
        const content = newText.trim();
        if (!content) return;
        setBusyId('new');
        try {
            await memoryApi.createKnowledge({ content, category: newCategory });
            setNewText('');
            showToast('Saved', 'success');
            await load();
            onChanged();
        } catch (err) {
            log.system.error('Failed to add memory', err);
            showToast(`Couldn't save it: ${err instanceof Error ? err.message : err}`, 'error');
        } finally {
            setBusyId(null);
        }
    }, [newText, newCategory, load, onChanged, showToast]);

    if (!memoryEnabled) {
        return (
            <div className="mem-about">
                <h2 className="mem-about-title">What GAIA knows about you</h2>
                <div className="mem-about-empty">
                    <p>Memory is off, so GAIA isn't saving anything about you.</p>
                    <button className="btn-secondary" onClick={onOpenAdvanced}>Turn it on in Advanced</button>
                </div>
            </div>
        );
    }

    const grouped = GROUPS
        .map(g => ({ ...g, rows: (items ?? []).filter(i => g.categories.includes(i.category)) }))
        .filter(g => g.rows.length > 0);

    return (
        <div className="mem-about">
            <h2 className="mem-about-title">What GAIA knows about you</h2>
            <p className="mem-about-intro">
                GAIA uses these in every chat. Edit anything that's wrong, or forget it so GAIA stops using it.
            </p>

            <form
                className="mem-about-add"
                onSubmit={e => { e.preventDefault(); add(); }}
            >
                <input
                    className="mem-input"
                    placeholder="Tell GAIA something about you, e.g. “I prefer short answers”"
                    aria-label="Something GAIA should know about you"
                    value={newText}
                    onChange={e => setNewText(e.target.value)}
                />
                <select
                    className="mem-select"
                    aria-label="Kind"
                    value={newCategory}
                    onChange={e => setNewCategory(e.target.value)}
                >
                    <option value="fact">About you</option>
                    <option value="preference">Preference</option>
                    <option value="reminder">Reminder</option>
                    <option value="note">Note</option>
                </select>
                <button className="btn-primary" type="submit" disabled={!newText.trim() || busyId === 'new'}>
                    <Plus size={14} /> Add
                </button>
            </form>

            {forgotten.map(item => (
                <div key={item.id} className="mem-about-forgotten" role="status">
                    <span>
                        Forgotten: {item.sensitive ? 'a sensitive memory' : `“${item.content}”`} — GAIA
                        won't learn it again from old chats.
                    </span>
                    <button className="mem-about-link" disabled={busyId === item.id} onClick={() => undoForget(item)}>
                        <Undo2 size={12} /> Undo
                    </button>
                </div>
            ))}

            {loadError ? (
                <div className="mem-about-empty">
                    <p>Couldn't load your memories: {loadError}</p>
                    <button className="btn-secondary" onClick={load}>Try again</button>
                </div>
            ) : items === null ? (
                <div className="mem-loading"><div className="mem-spinner" /> Loading…</div>
            ) : grouped.length === 0 ? (
                <div className="mem-about-empty">
                    <p>GAIA hasn't saved anything about you yet.</p>
                    <p className="mem-about-hint">
                        Tell it in a chat (“remember that I live in Calgary”) or add something above.
                    </p>
                </div>
            ) : (
                grouped.map(group => (
                    <section key={group.title} className="mem-about-group" aria-label={group.title}>
                        <h3 className="mem-about-group-title">{group.title}</h3>
                        <ul className="mem-about-list">
                            {group.rows.map(item => {
                                const hidden = item.sensitive && !revealed.has(item.id);
                                const due = item.due_at ? dueLabel(item.due_at) : null;
                                return (
                                    <li key={item.id} className="mem-about-item">
                                        {editingId === item.id ? (
                                            <form
                                                className="mem-about-edit"
                                                onSubmit={e => { e.preventDefault(); saveEdit(item); }}
                                            >
                                                <textarea
                                                    className="mem-input"
                                                    aria-label="Edit memory"
                                                    rows={2}
                                                    autoFocus
                                                    value={draft}
                                                    onChange={e => setDraft(e.target.value)}
                                                    onKeyDown={e => {
                                                        if (e.key === 'Escape') { e.stopPropagation(); setEditingId(null); }
                                                        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); saveEdit(item); }
                                                    }}
                                                />
                                                <div className="mem-about-edit-actions">
                                                    <button className="btn-primary" type="submit" disabled={busyId === item.id}>Save</button>
                                                    <button className="btn-secondary" type="button" onClick={() => setEditingId(null)}>Cancel</button>
                                                </div>
                                            </form>
                                        ) : (
                                            <>
                                                <div className="mem-about-text">
                                                    {hidden ? (
                                                        <button
                                                            className="mem-about-link"
                                                            onClick={() => setRevealed(prev => new Set(prev).add(item.id))}
                                                        >
                                                            <Eye size={12} /> Sensitive — show
                                                        </button>
                                                    ) : item.content}
                                                    {due && (
                                                        <span className={`mem-about-due${due.past ? ' is-overdue' : ''}`}>
                                                            {due.text}
                                                        </span>
                                                    )}
                                                    <span className="mem-about-meta">
                                                        {sourceLabel(item.source)} · {relativeDay(item.updated_at)}
                                                    </span>
                                                </div>
                                                <div className="mem-about-actions">
                                                    <button
                                                        className="mem-about-btn"
                                                        aria-label={`Edit: ${hidden ? 'sensitive memory' : item.content}`}
                                                        title="Edit"
                                                        onClick={() => { setEditingId(item.id); setDraft(item.content); }}
                                                    >
                                                        <Pencil size={14} />
                                                    </button>
                                                    <button
                                                        className="mem-about-btn mem-about-btn-remove"
                                                        aria-label={`Forget: ${hidden ? 'sensitive memory' : item.content}`}
                                                        title="Forget"
                                                        disabled={busyId === item.id}
                                                        onClick={() => forget(item)}
                                                    >
                                                        <Trash2 size={14} /> Forget
                                                    </button>
                                                </div>
                                            </>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                    </section>
                ))
            )}
        </div>
    );
}
