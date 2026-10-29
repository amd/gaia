// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The one poll of GET /api/chat/active (#5256). Every consumer reads the
 * result from chatStore; nothing else should call `api.getActiveRuns`.
 *
 * Every second while a chat runs, so a permission prompt reaches "Needs you"
 * within one (#5221). Idle, it backs off to IDLE_MAX_MS, and wakes at once
 * when this window starts a turn or regains focus.
 */

import * as api from './api';
import { useChatStore } from '../stores/chatStore';
import { log } from '../utils/logger';

export const BUSY_INTERVAL_MS = 1_000;
export const IDLE_MAX_MS = 15_000;

let inFlight: Promise<void> | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;
let idleDelay = BUSY_INTERVAL_MS;
let generation = 0;
let stopCurrent: (() => void) | null = null;

/** Fetch once now; concurrent callers share the request in flight. */
export function refreshActiveRuns(): Promise<void> {
    if (inFlight) return inFlight;
    const requestedAt = Date.now();
    inFlight = api.getActiveRuns()
        .then((runs) => useChatStore.getState().setActiveRuns(runs, requestedAt))
        .catch((err) => log.api.warn('Could not read which chats are running', err))
        .finally(() => { inFlight = null; });
    return inFlight;
}

function isBusy(): boolean {
    const s = useChatStore.getState();
    return s.isStreaming || s.runningSessionIds.length > 0;
}

function nextDelay(): number {
    if (isBusy()) {
        idleDelay = BUSY_INTERVAL_MS;
        return BUSY_INTERVAL_MS;
    }
    idleDelay = Math.min(idleDelay * 2, IDLE_MAX_MS);
    return idleDelay;
}

function schedule(gen: number): void {
    void refreshActiveRuns().then(() => {
        if (gen !== generation) return;
        timer = setTimeout(() => schedule(gen), nextDelay());
    });
}

/** Poll now and restart the busy cadence. */
export function wakeActiveRunsPoll(): void {
    if (!stopCurrent) return;
    if (timer) clearTimeout(timer);
    timer = null;
    idleDelay = BUSY_INTERVAL_MS;
    schedule(++generation);
}

/** Start the app's single poll; returns its stop. Starting again replaces the old one. */
export function startActiveRunsPoll(): () => void {
    stopCurrent?.();
    const unsubscribe = useChatStore.subscribe((s, prev) => {
        if (s.isStreaming && !prev.isStreaming) wakeActiveRunsPoll();
    });
    const onFocus = () => wakeActiveRunsPoll();
    window.addEventListener('focus', onFocus);
    const stop = () => {
        generation++;
        if (timer) clearTimeout(timer);
        timer = null;
        unsubscribe();
        window.removeEventListener('focus', onFocus);
        if (stopCurrent === stop) stopCurrent = null;
    };
    stopCurrent = stop;
    idleDelay = BUSY_INTERVAL_MS;
    schedule(++generation);
    return stop;
}
