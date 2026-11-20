// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Memory dashboard types and formatters with no DOM or store, shared by the web and the phone app. */

import { ref, type MessageKey, type MessageRef } from '../i18n/registry';
import { dateLocale, tEn, type Translate } from '../i18n/translator';

/** The label of each memory category. */
export const CATEGORY_KEYS: Record<string, MessageKey> = {
    fact: 'mem.cat.fact', preference: 'mem.cat.preference', error: 'mem.cat.error',
    skill: 'mem.cat.skill', note: 'mem.cat.note', reminder: 'mem.cat.reminder',
};

/** The Activity chart's ranges in days, and the one it opens on. */
export const ACTIVITY_RANGES = [1, 7, 30] as const;
export const DEFAULT_ACTIVITY_DAYS = 30;
/** How far ahead Upcoming & Overdue looks. */
export const UPCOMING_DAYS = 7;

export const ACTIVITY_RANGE_LABELS: Record<(typeof ACTIVITY_RANGES)[number], MessageKey> = {
    1: 'mem.activity.day1',
    7: 'mem.activity.days7',
    30: 'mem.activity.days30',
};

export interface ActivityDay {
    date: string;
    conversations: number;
    tool_calls: number;
    knowledge_added: number;
    errors: number;
}


export interface ToolStat {
    tool_name: string;
    total_calls: number;
    success_count: number;
    failure_count: number;
    denied_count?: number;
    success_rate: number;
    avg_duration_ms: number;
    last_used: string | null;
    last_error: string | null;
}


export interface TemporalItem {
    id: string;
    content: string;
    category: string;
    due_at: string;
    reminded_at: string | null;
    context: string;
    created_at: string;
}

export function formatDate(iso: string | null, locale: string | undefined): string {
    if (!iso) return '\u2014';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '\u2014';
        const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' };
        if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
        return d.toLocaleDateString(locale, opts);
    } catch { return '\u2014'; }
}

export function formatDateFull(iso: string | null, locale: string | undefined): string {
    if (!iso) return '';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleString(locale, {
            year: 'numeric', month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
        });
    } catch { return ''; }
}

export function formatRelativeDate(iso: string | null, t: Translate): string {
    if (!iso) return '\u2014';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '\u2014';
        const now = Date.now();
        const diff = d.getTime() - now;
        const absDiff = Math.abs(diff);
        const secs = Math.floor(absDiff / 1000);
        const mins = Math.floor(absDiff / 60000);
        const hours = Math.floor(absDiff / 3600000);
        const days = Math.floor(absDiff / 86400000);

        if (diff < 0) {
            // Past
            if (secs < 5) return t('time.justNow');
            if (secs < 60) return t('mem.rel.secondsAgo', { n: secs });
            if (hours === 0) return t('time.minutesAgo', { n: mins });
            if (days === 0) return t('time.hoursAgo', { n: hours });
            if (days === 1) return t('time.yesterday');
            if (days < 30) return t('time.daysAgo', { n: days });
            return formatDateFull(iso, dateLocale(t));
        } else {
            // Future
            if (secs < 5) return t('mem.rel.now');
            if (secs < 60) return t('mem.rel.inSeconds', { n: secs });
            if (hours === 0) return t('mem.rel.inMinutes', { n: mins });
            if (days === 0) return t('mem.rel.inHours', { n: hours });
            if (days === 1) return t('mem.rel.tomorrow');
            if (days < 30) return t('mem.rel.inDays', { n: days });
            return formatDateFull(iso, dateLocale(t));
        }
    } catch { return '\u2014'; }
}

export function formatDuration(ms: number): string {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

export function isOverdue(dueAt: string): boolean {
    try {
        return new Date(dueAt).getTime() < Date.now();
    } catch { return false; }
}

export function successRateClass(rate: number): string {
    if (rate >= 0.9) return 'high';
    if (rate >= 0.7) return 'medium';
    return 'low';
}

export function formatDateRange(start: string | null, end: string | null, locale: string | undefined): string {
    const s = formatDate(start, locale);
    const e = formatDate(end, locale);
    if (s === '\u2014' && e === '\u2014') return '';
    if (s === e) return s;
    return `${s} \u2013 ${e}`;
}

/** Count today's activity from the timeline data. */
export function todayCount(activity: ActivityDay[]): number {
    if (activity.length === 0) return 0;
    const today = new Date().toISOString().slice(0, 10);
    const todayData = activity.find(d => d.date === today);
    if (!todayData) return 0;
    return todayData.knowledge_added;
}

// ── About you ───────────────────────────────────────────────────────────────

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
export const ABOUT_PAGE_SIZE = 200;

/** Categories that describe the user, grouped under a plain heading. */
export const ABOUT_GROUPS: { title: MessageKey; categories: string[] }[] = [
    { title: 'mem.about.group.preferences', categories: ['preference'] },
    { title: 'mem.about.group.about', categories: ['fact', 'profile', 'skill'] },
    { title: 'mem.about.group.reminders', categories: ['reminder'] },
    { title: 'mem.about.group.notes', categories: ['note'] },
];

export const MEMORY_SOURCE_KEYS: Record<string, MessageKey> = {
    user: 'mem.source.user',
    tool: 'mem.source.tool',
    llm_extract: 'mem.source.llm_extract',
    consolidation: 'mem.source.consolidation',
    inferred: 'mem.source.inferred',
    discovery: 'mem.source.discovery',
    error_auto: 'mem.source.error_auto',
};

/** Where a memory came from, in the user's words; an unknown source reads "Saved". */
export function sourceKey(source: string): MessageKey {
    return MEMORY_SOURCE_KEYS[source] ?? 'mem.source.other';
}

export function relativeDay(iso: string, t: Translate): MessageRef | string | null {
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

/** How many characters of a forgotten memory the Undo bar quotes. */
export const FORGOTTEN_QUOTE_CHARS = 80;

/** A memory quoted on one line: its start, then an ellipsis when it is long. */
export function oneLineQuote(text: string): string {
    const flat = text.replace(/\s+/g, ' ').trim();
    if (flat.length <= FORGOTTEN_QUOTE_CHARS) return flat;
    return `${flat.slice(0, FORGOTTEN_QUOTE_CHARS - 1).trimEnd()}…`;
}

// ── Knowledge browser and conversation history ──────────────────────────────

/** Every category the knowledge browser filters by and the form saves, in the web's order. */
export const KNOWLEDGE_CATEGORIES = ['fact', 'preference', 'error', 'skill', 'note', 'reminder'] as const;

/** Rows per page of the knowledge browser. */
export const KNOWLEDGE_PAGE_SIZE = 15;

/** One memory with everything the browser's detail shows. */
export interface KnowledgeEntry extends AboutYouItem {
    confidence: number;
    metadata: Record<string, unknown> | string | null;
    use_count: number;
    last_used: string | null;
    reminded_at: string | null;
    superseded_by: string | null;
    has_embedding?: boolean;
}

export interface EntityInfo {
    entity: string;
    count: number;
    last_updated: string;
}

export interface ConvSession {
    session_id: string;
    turn_count: number;
    started_at: string | null;
    last_activity: string | null;
    first_message: string | null;
    consolidated?: boolean;
}

export interface ConvTurn {
    id: number;
    session_id: string;
    role: string;
    content: string;
    context: string;
    timestamp: string;
}

export const ROLE_KEYS: Record<string, MessageKey> = { user: 'mem.role.user', assistant: 'mem.role.assistant' };

export function confidenceLevel(c: number): 'high' | 'medium' | 'low' {
    if (c >= 0.7) return 'high';
    if (c >= 0.4) return 'medium';
    return 'low';
}
