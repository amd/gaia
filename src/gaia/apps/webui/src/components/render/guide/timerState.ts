// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The guide's timer state machine, free of React and the DOM so the phone shares it with the web. */

export type TimerStatus = 'idle' | 'running' | 'paused' | 'done';

export interface TimerView {
    status: TimerStatus;
    remainingSeconds: number;
}

export interface TimerEntry {
    status: TimerStatus;
    totalMs: number;
    /** Wall-clock end while running, so a hidden tab or a re-render never drifts the count. */
    endsAt: number | null;
    /** Left on the clock while paused. */
    leftMs: number;
}

export type TimerEntries = Record<string, TimerEntry>;

export interface Timers {
    read(id: string, seconds: number): TimerView;
    /** Start, pause, or resume; a finished timer starts over. */
    toggle(id: string, seconds: number): void;
    reset(id: string): void;
}

export const TICK_MS = 250;

export function toggleTimer(prev: TimerEntries, id: string, seconds: number, now: number): TimerEntries {
    const e = prev[id];
    const totalMs = seconds * 1000;
    if (!e || e.status === 'idle' || e.status === 'done') {
        return { ...prev, [id]: { status: 'running', totalMs, endsAt: now + totalMs, leftMs: totalMs } };
    }
    if (e.status === 'running') {
        return { ...prev, [id]: { ...e, status: 'paused', endsAt: null, leftMs: Math.max(0, e.endsAt! - now) } };
    }
    return { ...prev, [id]: { ...e, status: 'running', endsAt: now + e.leftMs } };
}

export function resetTimer(prev: TimerEntries, id: string): TimerEntries {
    if (!prev[id]) return prev;
    const next = { ...prev };
    delete next[id];
    return next;
}

/** Ids of the running timers whose time is up. */
export function finishedTimers(entries: TimerEntries, now: number): string[] {
    return Object.entries(entries)
        .filter(([, e]) => e.status === 'running' && e.endsAt !== null && e.endsAt <= now)
        .map(([id]) => id);
}

export function markDone(prev: TimerEntries, ids: string[]): TimerEntries {
    const next = { ...prev };
    for (const id of ids) next[id] = { ...next[id], status: 'done', endsAt: null, leftMs: 0 };
    return next;
}

export function hasRunning(entries: TimerEntries): boolean {
    return Object.values(entries).some((e) => e.status === 'running');
}

export function readTimer(entries: TimerEntries, id: string, seconds: number, now: number): TimerView {
    const e = entries[id];
    if (!e) return { status: 'idle', remainingSeconds: seconds };
    const leftMs = e.status === 'running' && e.endsAt !== null ? Math.max(0, e.endsAt - now) : e.leftMs;
    return { status: e.status, remainingSeconds: leftMs / 1000 };
}
