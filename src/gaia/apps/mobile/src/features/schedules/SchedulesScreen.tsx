// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';

import type { Schedule } from '@/api/client.types';
import { useQueryFailure, useSchedules } from '@/data';
import { ScreenHeader } from '@/features/nav/ScreenHeader';
import { describeError, useT } from '@/i18n/useT';
import { ErrorState, List, ListSkeleton, Screen, Stack, Text, useTheme } from '@/ui';

import { ScheduleCard } from './ScheduleCard';
import { isCountdownRange } from './scheduleText';

/** The desktop's `.schedule-section h4`: a bold muted label above the list. */
function ListHeading({ title }: { title: string }) {
    return (
        <Text role="label" weight="bold" tone="muted" accessibilityRole="header">
            {title}
        </Text>
    );
}

const IDLE_TICK_MS = 30_000;

/** The current time; ticks every second while a run is under a minute away, else twice a minute. */
function useClock(schedules: Schedule[]): Date {
    const [now, setNow] = useState(() => new Date());
    const counting = schedules.some((s) => s.status === 'active' && isCountdownRange(s.next_run_at, now));
    useEffect(() => {
        const timer = setInterval(() => setNow(new Date()), counting ? 1000 : IDLE_TICK_MS);
        return () => clearInterval(timer);
    }, [counting]);
    return now;
}

/** Re-reads the list just after the soonest active run is due, so its counts and times update at once. */
function useRefreshWhenDue(schedules: Schedule[], now: Date, refetch: () => void): void {
    const soonest = schedules
        .filter((s) => s.status === 'active' && s.next_run_at)
        .map((s) => new Date(s.next_run_at!).getTime())
        .filter((ms) => ms > now.getTime())
        .sort((a, b) => a - b)[0];
    useEffect(() => {
        if (soonest === undefined) return;
        const timer = setTimeout(refetch, soonest - Date.now() + 1000);
        return () => clearTimeout(timer);
    }, [soonest, refetch]);
}

export interface SchedulesScreenProps {
    onOpenChat: (sessionId: string) => void;
}

export function SchedulesScreen({ onOpenChat }: SchedulesScreenProps) {
    const t = useT();
    const { space } = useTheme();
    const query = useSchedules();
    const failure = useQueryFailure(query);
    const schedules = query.data?.schedules ?? [];
    const now = useClock(schedules);
    useRefreshWhenDue(schedules, now, () => void query.refetch());

    return (
        <Screen header={<ScreenHeader title={t('schedules.title')} />}>
            <Stack paddingX="lg" paddingY="sm">
                <Text role="label" tone="secondary">
                    {t('mobileApp.schedules.readOnly')}
                </Text>
            </Stack>
            {query.isPending && !failure.failed ? <ListSkeleton rows={4} /> : null}
            {failure.failed ? (
                <ErrorState title={t('schedules.loadFailed')} message={describeError(failure.error, t)} onRetry={() => void query.refetch()} />
            ) : null}
            {query.data && schedules.length === 0 ? (
                <>
                    <Stack paddingX="lg" paddingY="sm">
                        <ListHeading title={t('schedules.list')} />
                    </Stack>
                    <Stack paddingX="lg" paddingY="xl" align="center">
                        <Text role="ui" tone="muted" align="center">
                            {t('schedules.empty')}
                        </Text>
                    </Stack>
                </>
            ) : null}
            {schedules.length > 0 ? (
                <List<Schedule>
                    data={schedules}
                    keyExtractor={(s) => s.id}
                    accessibilityLabel={t('schedules.list')}
                    contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: space.xl, gap: space.sm }}
                    ListHeaderComponent={<ListHeading title={t('schedules.list')} />}
                    renderItem={({ item }) => <ScheduleCard schedule={item} now={now} onOpenChat={onOpenChat} />}
                />
            ) : null}
        </Screen>
    );
}
