// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Pencil, Plus, Trash2, Undo2, Eye } from 'lucide-react';
import * as memoryApi from '../services/memoryApi';
import { ref, type MessageKey, type MessageRef } from '../i18n/registry';
import { dateLocale, tEn, useT, type Translate } from '../i18n/useT';
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

/** Rows per request; the knowledge endpoint serves at most 200. */
const PAGE_SIZE = 200;

/** Categories that describe the user, grouped under a plain heading. */
const GROUPS: { title: MessageKey; categories: string[] }[] = [
    { title: 'mem.about.group.preferences', categories: ['preference'] },
    { title: 'mem.about.group.about', categories: ['fact', 'profile', 'skill'] },
    { title: 'mem.about.group.reminders', categories: ['reminder'] },
    { title: 'mem.about.group.notes', categories: ['note'] },
];

const SOURCES: Record<string, MessageKey> = {
    user: 'mem.source.user',
    tool: 'mem.source.tool',
    llm_extract: 'mem.source.llm_extract',
    consolidation: 'mem.source.consolidation',
    inferred: 'mem.source.inferred',
    discovery: 'mem.source.discovery',
};

/** Where a memory came from, in the user's words; an unknown source reads "Saved". */
export function sourceKey(source: string): MessageKey {
    return SOURCES[source] ?? 'mem.source.other';
}

function relativeDay(iso: string, t: Translate): MessageRef | string | null {
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) return null;
    const days = Math.floor((Date.now() - then.getTime()) / 86_400_000);
    if (days <= 0) return ref('mem.day.today');
    if (days === 1) return ref('mem.day.yesterday');
    if (days < 30) return ref('mem.day.daysAgo', { n: days });
    return then.toLocaleDateString(dateLocale(t), { month: 'short', day: 'numeric', year: 'numeric' });
}

/** A stored due date; a bare "YYYY-MM-DD" is local midnight, not UTC. */
function parseDue(iso: string): Date | null {
    const bare = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    const due = bare ? new Date(+bare[1], +bare[2] - 1, +bare[3]) : new Date(iso);
    return Number.isNaN(due.getTime()) ? null : due;
}

/** A stored due date as a date-and-time field shows it, in local time. */
export function dueInputValue(iso: string | null | undefined): string {
    const due = iso ? parseDue(iso) : null;
    if (!due) return '';
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${due.getFullYear()}-${pad(due.getMonth() + 1)}-${pad(due.getDate())}T${pad(due.getHours())}:${pad(due.getMinutes())}`;
}

/** "Due Fri, Oct 30", or "Was due …" once past; a midnight due date counts as the whole day. */
export function dueLabel(iso: string, now: Date = new Date(), t: Translate = tEn): { text: string; past: boolean } | null {
    const due = parseDue(iso);
    if (!due) return null;
    const allDay = due.getHours() === 0 && due.getMinutes() === 0 && due.getSeconds() === 0;
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const past = allDay ? due.getTime() < startOfToday : due.getTime() < now.getTime();
    const when = due.toLocaleString(dateLocale(t), {
        weekday: 'short', month: 'short', day: 'numeric',
        ...(due.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}),
        ...(allDay ? {} : { hour: 'numeric', minute: '2-digit' }),
    });
    return { text: t(past ? 'mem.due.past' : 'mem.due.upcoming', { when }), past };
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
    const t = useT();
    const [items, setItems] = useState<AboutYouItem[] | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [editingId, setEditingId] = useState<string | null>(null);
    const [draft, setDraft] = useState('');
    const [draftDue, setDraftDue] = useState('');
    const [forgotten, setForgotten] = useState<AboutYouItem[]>([]);
    const [revealed, setRevealed] = useState<Set<string>>(new Set());
    const [newText, setNewText] = useState('');
    const [newCategory, setNewCategory] = useState('fact');
    const [newDue, setNewDue] = useState('');
    const [busyId, setBusyId] = useState<string | null>(null);
    const [total, setTotal] = useState(0);
    const [loadingMore, setLoadingMore] = useState(false);
    // Bumped by every load and every change, so an older load that lands late
    // cannot put back an item the user just forgot.
    const generation = useRef(0);
    // How many rows to show; a reload after an edit keeps the pages already open.
    const wanted = useRef(PAGE_SIZE);

    const load = useCallback(async (want?: number) => {
        const mine = ++generation.current;
        const count = want ?? wanted.current;
        try {
            const rows: AboutYouItem[] = [];
            const seen = new Set<string>();
            // Rows read from the server, duplicates included, so the next page starts after them.
            let offset = 0;
            let found = 0;
            for (;;) {
                const limit = Math.min(PAGE_SIZE, count - rows.length);
                // Filtered on the server, so paging never fills up with GAIA's own notes.
                const data = await memoryApi.getKnowledge({
                    about_user: true,
                    include_sensitive: true,
                    sort_by: 'updated_at',
                    order: 'desc',
                    offset,
                    limit,
                });
                if (mine !== generation.current) return;
                offset += data.items.length;
                // A memory saved between two pages shifts the rows, repeating one at the page edge.
                for (const item of data.items as AboutYouItem[]) {
                    if (seen.has(item.id)) continue;
                    seen.add(item.id);
                    rows.push(item);
                }
                found = typeof data.total === 'number' ? data.total : rows.length;
                // A short page is the end of what the store holds right now.
                if (data.items.length < limit || rows.length >= Math.min(count, found)) break;
            }
            wanted.current = count;
            setItems(rows);
            setTotal(found);
            setLoadError(null);
        } catch (err) {
            if (mine !== generation.current) return;
            log.system.warn('Failed to load what GAIA knows about the user', err);
            setLoadError(err instanceof Error ? err.message : String(err));
        }
    }, []);

    useEffect(() => { load(); }, [load, reloadKey]);

    const showMore = useCallback(async () => {
        setLoadingMore(true);
        // Set before loading, so a reload that lands mid-way fetches the new page too.
        wanted.current = Math.max(wanted.current, items?.length ?? 0) + PAGE_SIZE;
        try {
            await load();
        } finally {
            setLoadingMore(false);
        }
    }, [load, items]);

    const saveEdit = useCallback(async (item: AboutYouItem) => {
        const content = draft.trim();
        const dueChanged = item.category === 'reminder' && !!draftDue && draftDue !== dueInputValue(item.due_at);
        if (!content || (content === item.content && !dueChanged)) { setEditingId(null); return; }
        setBusyId(item.id);
        try {
            await memoryApi.editKnowledge(item.id, {
                ...(content !== item.content ? { content } : {}),
                ...(dueChanged ? { due_at: draftDue } : {}),
            });
            setEditingId(null);
            showToast(t('mem.about.updated'), 'success');
            await load();
            onChanged();
        } catch (err) {
            log.system.error('Failed to edit memory', err);
            showToast(t('mem.about.editFailed', { error: errorText(err) }), 'error');
        } finally {
            setBusyId(null);
        }
    }, [draft, draftDue, load, onChanged, showToast, t]);

    const forget = useCallback(async (item: AboutYouItem) => {
        setBusyId(item.id);
        try {
            await memoryApi.deleteKnowledge(item.id);
            generation.current++;
            setItems(prev => (prev ?? []).filter(i => i.id !== item.id));
            setTotal(prev => Math.max(prev - 1, 0));
            setForgotten(prev => [item, ...prev]);
            onChanged();
        } catch (err) {
            log.system.error('Failed to forget memory', err);
            showToast(t('mem.about.forgetFailed', { error: errorText(err) }), 'error');
        } finally {
            setBusyId(null);
        }
    }, [onChanged, showToast, t]);

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
            showToast(t('mem.about.restoreFailed', { error: errorText(err) }), 'error');
        } finally {
            setBusyId(null);
        }
    }, [load, onChanged, showToast, t]);

    const add = useCallback(async () => {
        const content = newText.trim();
        const reminder = newCategory === 'reminder';
        if (!content || (reminder && !newDue)) return;
        setBusyId('new');
        try {
            await memoryApi.createKnowledge({ content, category: newCategory, ...(reminder ? { due_at: newDue } : {}) });
            setNewText('');
            setNewDue('');
            showToast(t('mem.about.saved'), 'success');
            await load();
            onChanged();
        } catch (err) {
            log.system.error('Failed to add memory', err);
            showToast(t('mem.about.addFailed', { error: errorText(err) }), 'error');
        } finally {
            setBusyId(null);
        }
    }, [newText, newCategory, newDue, load, onChanged, showToast, t]);

    if (!memoryEnabled) {
        return (
            <div className="mem-about">
                <h2 className="mem-about-title">{t('mem.about.title')}</h2>
                <div className="mem-about-empty">
                    <p>{t('mem.about.off')}</p>
                    <button className="btn-secondary" onClick={onOpenAdvanced}>{t('mem.about.turnOn')}</button>
                </div>
            </div>
        );
    }

    const grouped = GROUPS
        .map(g => ({ ...g, rows: (items ?? []).filter(i => g.categories.includes(i.category)) }))
        .filter(g => g.rows.length > 0);

    return (
        <div className="mem-about">
            <h2 className="mem-about-title">{t('mem.about.title')}</h2>
            <p className="mem-about-intro">{t('mem.about.intro')}</p>

            <form
                className="mem-about-add"
                onSubmit={e => { e.preventDefault(); add(); }}
            >
                <input
                    className="mem-input"
                    placeholder={t('mem.about.addPlaceholder')}
                    aria-label={t('mem.about.addLabel')}
                    value={newText}
                    onChange={e => setNewText(e.target.value)}
                />
                <select
                    className="mem-select"
                    aria-label={t('mem.about.kind')}
                    value={newCategory}
                    onChange={e => setNewCategory(e.target.value)}
                >
                    <option value="fact">{t.bare('mem.about.group.about')}</option>
                    <option value="preference">{t.bare('mem.about.kind.preference')}</option>
                    <option value="reminder">{t.bare('mem.about.kind.reminder')}</option>
                    <option value="note">{t.bare('mem.about.kind.note')}</option>
                </select>
                {newCategory === 'reminder' && (
                    <input
                        type="datetime-local"
                        className="mem-input mem-about-when"
                        aria-label={t('mem.about.when')}
                        value={newDue}
                        onChange={e => setNewDue(e.target.value)}
                    />
                )}
                <button
                    className="btn-primary"
                    type="submit"
                    disabled={!newText.trim() || (newCategory === 'reminder' && !newDue) || busyId === 'new'}
                >
                    <Plus size={14} /> {t('mem.about.add')}
                </button>
            </form>

            {forgotten.map(item => (
                <div key={item.id} className="mem-about-forgotten" role="status">
                    <span title={item.sensitive ? undefined : item.content}>
                        {item.sensitive ? t('mem.about.forgottenSensitive') : t('mem.about.forgotten', { content: oneLineQuote(item.content) })}
                    </span>
                    <button className="mem-about-link" disabled={busyId === item.id} onClick={() => undoForget(item)}>
                        <Undo2 size={12} /> {t('mem.about.undo')}
                    </button>
                </div>
            ))}

            {loadError ? (
                <div className="mem-about-empty">
                    <p>{t('mem.about.loadFailed', { error: loadError })}</p>
                    <button className="btn-secondary" onClick={() => load()}>{t('mem.about.retry')}</button>
                </div>
            ) : items === null ? (
                <div className="mem-loading"><div className="mem-spinner" /> {t('mem.loading')}</div>
            ) : grouped.length === 0 ? (
                <div className="mem-about-empty">
                    <p>{t('mem.about.empty')}</p>
                    <p className="mem-about-hint">{t('mem.about.emptyHint')}</p>
                </div>
            ) : (
                grouped.map(group => (
                    <section key={group.title} className="mem-about-group" aria-label={t(group.title)}>
                        <h3 className="mem-about-group-title">{t(group.title)}</h3>
                        <ul className="mem-about-list">
                            {group.rows.map(item => {
                                const hidden = item.sensitive && !revealed.has(item.id);
                                const due = item.due_at ? dueLabel(item.due_at, new Date(), t) : null;
                                const when = relativeDay(item.updated_at, t);
                                const name = hidden ? ref('mem.about.sensitiveMemory') : item.content;
                                return (
                                    <li key={item.id} className="mem-about-item">
                                        {editingId === item.id ? (
                                            <form
                                                className="mem-about-edit"
                                                onSubmit={e => { e.preventDefault(); saveEdit(item); }}
                                            >
                                                <textarea
                                                    className="mem-input"
                                                    aria-label={t('mem.about.editLabel')}
                                                    rows={2}
                                                    autoFocus
                                                    value={draft}
                                                    onChange={e => setDraft(e.target.value)}
                                                    onKeyDown={e => {
                                                        if (e.key === 'Escape') { e.stopPropagation(); setEditingId(null); }
                                                        if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); saveEdit(item); }
                                                    }}
                                                />
                                                {item.category === 'reminder' && (
                                                    <input
                                                        type="datetime-local"
                                                        className="mem-input mem-about-when"
                                                        aria-label={t('mem.about.when')}
                                                        value={draftDue}
                                                        onChange={e => setDraftDue(e.target.value)}
                                                    />
                                                )}
                                                <div className="mem-about-edit-actions">
                                                    <button className="btn-primary" type="submit" disabled={busyId === item.id}>{t('mem.about.save')}</button>
                                                    <button className="btn-secondary" type="button" onClick={() => setEditingId(null)}>{t('mem.about.cancel')}</button>
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
                                                            <Eye size={12} /> {t('mem.about.showSensitive')}
                                                        </button>
                                                    ) : <bdi>{item.content}</bdi>}
                                                    {due && (
                                                        <span className={`mem-about-due${due.past ? ' is-overdue' : ''}`}>
                                                            {due.text}
                                                        </span>
                                                    )}
                                                    <span className="mem-about-meta">
                                                        {when === null
                                                            ? t(sourceKey(item.source))
                                                            : t('mem.about.meta', { source: ref(sourceKey(item.source)), when })}
                                                    </span>
                                                </div>
                                                <div className="mem-about-actions">
                                                    <button
                                                        className="mem-about-btn"
                                                        aria-label={t('mem.about.editAria', { name })}
                                                        title={t(hidden ? 'mem.about.editHidden' : 'mem.about.edit')}
                                                        disabled={hidden}
                                                        onClick={() => { setEditingId(item.id); setDraft(item.content); setDraftDue(dueInputValue(item.due_at)); }}
                                                    >
                                                        <Pencil size={14} />
                                                    </button>
                                                    <button
                                                        className="mem-about-btn mem-about-btn-remove"
                                                        aria-label={t('mem.about.forgetAria', { name })}
                                                        title={t('mem.about.forget')}
                                                        disabled={busyId === item.id}
                                                        onClick={() => forget(item)}
                                                    >
                                                        <Trash2 size={14} /> {t('mem.about.forget')}
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
            {!loadError && items !== null && items.length < total && (
                <div className="mem-about-more">
                    <span>{t('mem.about.showing', { shown: items.length, total })}</span>
                    <button className="btn-secondary" disabled={loadingMore} onClick={showMore}>
                        {t('mem.about.showMore')}
                    </button>
                </div>
            )}
        </div>
    );
}

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

/** How many characters of a forgotten memory the Undo bar quotes. */
export const FORGOTTEN_QUOTE_CHARS = 80;

/** A memory quoted on one line: its start, then an ellipsis when it is long. */
export function oneLineQuote(text: string): string {
    const flat = text.replace(/\s+/g, ' ').trim();
    if (flat.length <= FORGOTTEN_QUOTE_CHARS) return flat;
    return `${flat.slice(0, FORGOTTEN_QUOTE_CHARS - 1).trimEnd()}…`;
}
