// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Group sidebar chats: what needs you, what is pinned, then how recently they were used. */

import type { Session } from '../types';

export interface RecencyGroup {
    label: 'Today' | 'Yesterday' | 'Previous 7 days' | 'Previous 30 days' | 'Older';
    sessions: Session[];
}

function updatedAtMs(s: Session): number {
    const t = new Date(s.updated_at).getTime();
    return Number.isNaN(t) ? 0 : t;
}

/** Newest first, bucketed by calendar day relative to `now`. Empty buckets are dropped. */
export function groupSessionsByRecency(sessions: Session[], now: Date = new Date()): RecencyGroup[] {
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const day = 24 * 60 * 60 * 1000;
    const buckets: RecencyGroup[] = [
        { label: 'Today', sessions: [] },
        { label: 'Yesterday', sessions: [] },
        { label: 'Previous 7 days', sessions: [] },
        { label: 'Previous 30 days', sessions: [] },
        { label: 'Older', sessions: [] },
    ];
    const sorted = [...sessions].sort((a, b) => updatedAtMs(b) - updatedAtMs(a));
    for (const s of sorted) {
        const t = updatedAtMs(s);
        const i = t >= startOfToday ? 0
            : t >= startOfToday - day ? 1
            : t >= startOfToday - 7 * day ? 2
            : t >= startOfToday - 30 * day ? 3
            : 4;
        buckets[i].sessions.push(s);
    }
    return buckets.filter((b) => b.sessions.length > 0);
}

// ── The chat list as a queue (#5221) ─────────────────────────────────────

/** A row's status dot. `queued` waits for the GPU; `waiting` waits on you. */
export type RowStatus = 'running' | 'queued' | 'waiting' | 'done' | 'failed';

export interface LiveRuns {
    running: readonly string[];
    queued: readonly string[];
    /** Chats paused on a permission prompt or a question. */
    awaiting: Readonly<Record<string, unknown>>;
    /** Chats with an unanswered prompt in the notification store (the open chat's arrive first here). */
    prompting: readonly string[];
}

export function sessionRowStatus(s: Session, live: LiveRuns): RowStatus | null {
    if (live.awaiting[s.id] || live.prompting.includes(s.id)) return 'waiting';
    if (live.queued.includes(s.id)) return 'queued';
    if (live.running.includes(s.id)) return 'running';
    if (s.last_run_status === 'failed') return 'failed';
    if (s.last_run_status === 'done' || s.last_run_status === 'stopped') return 'done';
    return null;
}

export function isAutomated(s: Session): boolean {
    return s.kind === 'eval' || s.kind === 'scheduled';
}

export interface ListGroup {
    label: 'Needs you' | 'Pinned' | RecencyGroup['label'];
    sessions: Session[];
}

/**
 * Needs you first, then pinned, then by recency. Eval and scheduled runs are
 * left out unless `showAutomated`; a chat appears in one group only.
 */
export function groupSessionsForList(
    sessions: Session[],
    live: LiveRuns,
    { showAutomated = false, now = new Date() }: { showAutomated?: boolean; now?: Date } = {},
): ListGroup[] {
    const visible = showAutomated ? sessions : sessions.filter((s) => !isAutomated(s));
    const byRecency = (a: Session, b: Session) => updatedAtMs(b) - updatedAtMs(a);
    const needsYou = visible.filter((s) => sessionRowStatus(s, live) === 'waiting').sort(byRecency);
    const pinned = visible.filter((s) => s.pinned && !needsYou.includes(s)).sort(byRecency);
    const rest = visible.filter((s) => !needsYou.includes(s) && !pinned.includes(s));
    const groups: ListGroup[] = [];
    if (needsYou.length) groups.push({ label: 'Needs you', sessions: needsYou });
    if (pinned.length) groups.push({ label: 'Pinned', sessions: pinned });
    return [...groups, ...groupSessionsByRecency(rest, now)];
}

/** How many eval and scheduled runs the default list hides. */
export function hiddenAutomatedCount(sessions: Session[]): number {
    return sessions.filter(isAutomated).length;
}
