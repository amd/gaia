// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Zustand store for schedule management state. */

import { create } from 'zustand';
import type { Schedule, ScheduleResult } from '../types';
import * as api from '../services/api';
import { getT } from '../i18n/useT';
import { log } from '../utils/logger';
import { plainPreview } from '../utils/plainPreview';
import { useNotificationStore } from './notificationStore';

/** Extract a human-readable message from an API error. */
function parseApiError(err: unknown, fallback: string): string {
    if (!(err instanceof Error)) return fallback;
    const raw = err.message;
    // apiFetch throws "API 400: {\"detail\":\"...\"}" — extract the detail
    const jsonMatch = raw.match(/API \d+: (.+)/);
    if (jsonMatch) {
        try {
            const parsed = JSON.parse(jsonMatch[1]);
            if (parsed.detail) return parsed.detail;
        } catch { /* not JSON, use raw */ }
    }
    return raw;
}

/** Raise an error notification for each schedule that failed since *seen* was taken. */
function notifyNewFailures(seen: Record<string, number>, list: Schedule[]): void {
    const { addNotification } = useNotificationStore.getState();
    for (const s of list) {
        if (s.error_count <= (seen[s.id] ?? 0) || !s.last_error) continue;
        addNotification({
            id: `schedule-error-${s.id}-${s.error_count}`,
            type: 'error',
            agentId: 'scheduler',
            agentName: 'GAIA',
            title: getT()('schedules.failed', { name: s.name }),
            message: s.last_error,
            timestamp: Date.now(),
            read: false,
            dismissed: false,
            priority: 'high',
            ...(s.session_id ? { sessionId: s.session_id } : {}),
        });
    }
}

/** Runs that have ended; a run in flight is already counted in run_count. */
function finishedRuns(s: Schedule): number {
    return s.run_count - (s.running ? 1 : 0);
}

/** Raise an info notification for each schedule that finished a run cleanly since the snapshots were taken. */
function notifyNewFinishes(seenErrors: Record<string, number>, seenFinished: Record<string, number>, list: Schedule[]): void {
    const { addNotification } = useNotificationStore.getState();
    for (const s of list) {
        const finished = finishedRuns(s);
        if (finished <= (seenFinished[s.id] ?? 0) || s.error_count > (seenErrors[s.id] ?? 0)) continue;
        addNotification({
            id: `schedule-done-${s.id}-${finished}`,
            type: 'info',
            agentId: 'scheduler',
            agentName: 'GAIA',
            title: getT()('schedules.finished', { name: s.name }),
            message: plainPreview(s.last_result ?? ''),
            timestamp: Date.now(),
            read: false,
            dismissed: false,
            priority: 'medium',
            ...(s.session_id ? { sessionId: s.session_id } : {}),
        });
    }
}

export interface ScheduleDraft { when: string; name: string; prompt: string }

interface ScheduleState {
    schedules: Schedule[];
    loading: boolean;
    /** Why the last load failed. Owned by loadSchedules; replaces the list. */
    error: string | null;
    /** Why the last pause, resume, run now or delete failed. Shown above the list until dismissed, the next action succeeds, or the dialog closes. */
    actionError: string | null;

    /** Currently selected schedule (for viewing results). */
    selectedSchedule: string | null;
    results: ScheduleResult[];
    resultsLoading: boolean;
    /** error_count per schedule id at the last load; null before the first. */
    errorCounts: Record<string, number> | null;
    /** Finished runs per schedule id at the last load. */
    finishedCounts: Record<string, number>;
    /** What was typed in the New schedule form, kept so closing the dialog doesn't lose it. */
    draft: ScheduleDraft | null;
    setDraft: (draft: ScheduleDraft | null) => void;

    loadSchedules: () => Promise<void>;
    createSchedule: (name: string, interval: string, prompt: string) => Promise<void>;
    updateScheduleStatus: (name: string, status: string) => Promise<void>;
    runScheduleNow: (name: string) => Promise<void>;
    deleteSchedule: (name: string) => Promise<void>;
    selectSchedule: (name: string | null) => void;
    loadResults: (name: string) => Promise<void>;
    dismissActionError: () => void;
}

export const useScheduleStore = create<ScheduleState>((set, get) => ({
    schedules: [],
    loading: false,
    error: null,
    actionError: null,
    selectedSchedule: null,
    results: [],
    resultsLoading: false,
    errorCounts: null,
    finishedCounts: {},
    draft: null,
    setDraft: (draft) => set({ draft }),

    loadSchedules: async () => {
        // Only show loading spinner on initial load, not poll refreshes
        const isInitial = get().schedules.length === 0 && !get().error;
        if (isInitial) set({ loading: true });
        set({ error: null });
        try {
            const data = await api.listSchedules();
            const schedules: Schedule[] = data.schedules || [];
            const seen = get().errorCounts;
            const seenFinished = get().finishedCounts;
            // The first load is the state at launch, not news.
            if (seen) {
                notifyNewFailures(seen, schedules);
                notifyNewFinishes(seen, seenFinished, schedules);
            }
            set({
                schedules,
                loading: false,
                errorCounts: Object.fromEntries(schedules.map((s) => [s.id, s.error_count])),
                // Max, because Run now marks a run in flight just before counting it.
                finishedCounts: Object.fromEntries(
                    schedules.map((s) => [s.id, Math.max(finishedRuns(s), seenFinished[s.id] ?? 0)]),
                ),
            });
        } catch (err) {
            log.api.error('Failed to load schedules', err);
            set({ error: 'Failed to load schedules', loading: false });
        }
    },

    createSchedule: async (name, interval, prompt) => {
        try {
            const schedule = await api.createSchedule(name, interval, prompt);
            set((state) => ({ schedules: [...state.schedules, schedule], actionError: null }));
        } catch (err) {
            // The create form shows this error itself, so don't repeat it above the list.
            log.api.error('Failed to create schedule', err);
            throw err;
        }
    },

    updateScheduleStatus: async (name, status) => {
        try {
            const updated = await api.updateSchedule(name, status);
            set((state) => ({
                schedules: state.schedules.map((s) => (s.name === name ? updated : s)),
                actionError: null,
            }));
        } catch (err) {
            log.api.error(`Failed to update schedule "${name}"`, err);
            set({ actionError: parseApiError(err, 'Failed to update schedule') });
        }
    },

    runScheduleNow: async (name) => {
        try {
            const updated = await api.runScheduleNow(name);
            set((state) => ({
                schedules: state.schedules.map((s) => (s.name === name ? updated : s)),
                actionError: null,
            }));
        } catch (err) {
            log.api.error(`Failed to run schedule "${name}" now`, err);
            set({ actionError: parseApiError(err, 'Could not run the schedule now') });
        }
    },

    deleteSchedule: async (name) => {
        try {
            await api.deleteSchedule(name);
            set((state) => ({
                schedules: state.schedules.filter((s) => s.name !== name),
                selectedSchedule: state.selectedSchedule === name ? null : state.selectedSchedule,
                actionError: null,
            }));
        } catch (err) {
            log.api.error(`Failed to delete schedule "${name}"`, err);
            set({ actionError: parseApiError(err, 'Failed to delete schedule') });
        }
    },

    selectSchedule: (name) => {
        set({ selectedSchedule: name, results: [], resultsLoading: false });
        if (name) get().loadResults(name);
    },

    loadResults: async (name) => {
        set({ resultsLoading: true });
        try {
            const data = await api.getScheduleResults(name);
            // A late response for a schedule that is no longer selected must not land.
            if (get().selectedSchedule !== name) return;
            set({ results: data.results || [], resultsLoading: false });
        } catch (err) {
            log.api.error(`Failed to load results for "${name}"`, err);
            if (get().selectedSchedule !== name) return;
            set({ results: [], resultsLoading: false });
        }
    },

    dismissActionError: () => set({ actionError: null }),
}));
