// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect } from 'react';
import { AppState } from 'react-native';

import { useQuery, useQueryClient } from '@tanstack/react-query';

import type { ScheduleList, ScheduleResults } from '@/api/client.types';
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
