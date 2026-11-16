// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { AppState } from 'react-native';

import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';

import type { CreateScheduleRequest, DeleteScheduleResult, Schedule, ScheduleList, ScheduleResults } from '@/api/client.types';
import { APP_POLL_MS, SCHEDULE_RESULTS_LIMIT, SCHEDULES_POLL_MS } from '@/api/constants';

import { useGaiaClient } from './GaiaClientProvider';
import { failedWithoutData, requireClient } from './queries';

export const scheduleKeys = {
    list: ['schedules'] as const,
    results: (name: string) => ['schedules', name, 'results'] as const,
};

/** The PC's scheduled tasks, re-read while the screen is open so run counts and times follow the PC. */
export function useSchedules() {
    const client = useGaiaClient();
    return useQuery<ScheduleList>({
        queryKey: scheduleKeys.list,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).listSchedules({ signal }),
        // Each poll would put a failed query with no data back to pending, showing a skeleton for good.
        refetchInterval: (query) => (failedWithoutData(query) ? false : SCHEDULES_POLL_MS),
    });
}

/**
 * The app-wide read of the schedules, on the same query as the screen's. It re-reads every APP_POLL_MS while the app is
 * active, and skips a tick when the screen's faster poll has already refreshed the data, so the PC is asked once.
 */
export function useSchedulesInApp() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const query = useQuery<ScheduleList>({
        queryKey: scheduleKeys.list,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).listSchedules({ signal }),
    });
    useEffect(() => {
        if (client === null) return;
        const timer = setInterval(() => {
            if (AppState.currentState !== 'active') return;
            queryClient
                .fetchQuery<ScheduleList>({
                    queryKey: scheduleKeys.list,
                    queryFn: ({ signal }) => client.listSchedules({ signal }),
                    staleTime: APP_POLL_MS - 1000,
                })
                .catch((err: unknown) => console.warn('Could not re-read the scheduled tasks', err));
        }, APP_POLL_MS);
        return () => clearInterval(timer);
    }, [client, queryClient]);
    return query;
}

/** A schedule's recent runs; `lastRunAt` is in the key so a finished run re-reads the history. */
export function useScheduleResults(name: string, lastRunAt: string | null, enabled: boolean) {
    const client = useGaiaClient();
    return useQuery<ScheduleResults>({
        queryKey: [...scheduleKeys.results(name), lastRunAt],
        enabled: client !== null && enabled,
        queryFn: ({ signal }) => requireClient(client).listScheduleResults(name, SCHEDULE_RESULTS_LIMIT, { signal }),
    });
}

function replaceSchedule(queryClient: QueryClient, updated: Schedule): void {
    queryClient.setQueryData<ScheduleList>(scheduleKeys.list, (list) =>
        list && { ...list, schedules: list.schedules.map((s) => (s.name === updated.name ? updated : s)) },
    );
}

/** Creates a schedule: `mutate({ name, interval, prompt, timezone })`, then re-reads the list. A 409 `GaiaApiError` means the name is taken. */
export function useCreateSchedule() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<Schedule, Error, CreateScheduleRequest>({
        mutationFn: (request) => requireClient(client).createSchedule(request),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: scheduleKeys.list }),
    });
}

/** Pauses or resumes a schedule: `mutate({ name, status })`. */
export function useUpdateScheduleStatus() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<Schedule, Error, { name: string; status: 'active' | 'paused' }>({
        mutationFn: ({ name, status }) => requireClient(client).updateScheduleStatus(name, status),
        onSuccess: (updated) => replaceSchedule(queryClient, updated),
    });
}

/** Runs a schedule once now: `mutate(name)`. Rejects with a 409 `GaiaApiError` while a run is in flight. */
export function useRunSchedule() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<Schedule, Error, string>({
        mutationFn: (name) => requireClient(client).runSchedule(name),
        onSuccess: (updated) => replaceSchedule(queryClient, updated),
    });
}

/** Deletes a schedule: `mutate(name)`. */
export function useDeleteSchedule() {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useMutation<DeleteScheduleResult, Error, string>({
        mutationFn: (name) => requireClient(client).deleteSchedule(name),
        onSuccess: (_result, name) =>
            queryClient.setQueryData<ScheduleList>(scheduleKeys.list, (list) =>
                list && { ...list, schedules: list.schedules.filter((s) => s.name !== name), total: list.total - 1 },
            ),
    });
}
