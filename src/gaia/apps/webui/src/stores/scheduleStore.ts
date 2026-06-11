// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Zustand store for schedule management state. */

import { create } from 'zustand';
import type { Schedule, ScheduleResult } from '../types';
import * as api from '../services/api';
import { log } from '../utils/logger';

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

interface ScheduleState {
    schedules: Schedule[];
    loading: boolean;
    error: string | null;

    /** Currently selected schedule (for viewing results). */
    selectedSchedule: string | null;
    results: ScheduleResult[];
    resultsLoading: boolean;

    loadSchedules: () => Promise<void>;
    createSchedule: (name: string, interval: string, prompt: string) => Promise<void>;
    updateScheduleStatus: (name: string, status: string) => Promise<void>;
    deleteSchedule: (name: string) => Promise<void>;
    selectSchedule: (name: string | null) => void;
    loadResults: (name: string) => Promise<void>;
}

export const useScheduleStore = create<ScheduleState>((set, get) => ({
    schedules: [],
    loading: false,
    error: null,
    selectedSchedule: null,
    results: [],
    resultsLoading: false,

    loadSchedules: async () => {
        // Only show loading spinner on initial load, not poll refreshes
        const isInitial = get().schedules.length === 0 && !get().error;
        if (isInitial) set({ loading: true });
        set({ error: null });
        try {
            const data = await api.listSchedules();
            set({ schedules: data.schedules || [], loading: false });
        } catch (err) {
            log.api.error('Failed to load schedules', err);
            set({ error: 'Failed to load schedules', loading: false });
        }
    },

    createSchedule: async (name, interval, prompt) => {
        try {
            const schedule = await api.createSchedule(name, interval, prompt);
            set((state) => ({ schedules: [...state.schedules, schedule], error: null }));
        } catch (err) {
            const msg = parseApiError(err, 'Failed to create schedule');
            log.api.error('Failed to create schedule', err);
            set({ error: msg });
            throw err;
        }
    },

    updateScheduleStatus: async (name, status) => {
        try {
            const updated = await api.updateSchedule(name, status);
            set((state) => ({
                schedules: state.schedules.map((s) => (s.name === name ? updated : s)),
                error: null,
            }));
        } catch (err) {
            log.api.error(`Failed to update schedule "${name}"`, err);
            set({ error: parseApiError(err, 'Failed to update schedule') });
        }
    },

    deleteSchedule: async (name) => {
        try {
            await api.deleteSchedule(name);
            set((state) => ({
                schedules: state.schedules.filter((s) => s.name !== name),
                selectedSchedule: state.selectedSchedule === name ? null : state.selectedSchedule,
                error: null,
            }));
        } catch (err) {
            log.api.error(`Failed to delete schedule "${name}"`, err);
            set({ error: parseApiError(err, 'Failed to delete schedule') });
        }
    },

    selectSchedule: (name) => {
        set({ selectedSchedule: name, results: [] });
        if (name) get().loadResults(name);
    },

    loadResults: async (name) => {
        set({ resultsLoading: true });
        try {
            const data = await api.getScheduleResults(name);
            set({ results: data.results || [], resultsLoading: false });
        } catch (err) {
            log.api.error(`Failed to load results for "${name}"`, err);
            set({ results: [], resultsLoading: false });
        }
    },
}));
