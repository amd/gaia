// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useState } from 'react';

import type { Schedule } from '@/api/client.types';
import { GaiaApiError } from '@/api/errors';
import { useDeleteSchedule, useRunSchedule, useQueryFailure, useSchedules, useUpdateScheduleStatus } from '@/data';
import { ScreenHeader } from '@/features/nav/ScreenHeader';
import { describeError, useT } from '@/i18n/useT';
import { ErrorState, IconButton, List, ListSkeleton, Notice, Row, Screen, Stack, Text, useTheme } from '@/ui';
import { Plus, X } from '@/ui/icons';

import { ChipButton } from './ChipButton';
import { NewScheduleSheet } from './NewScheduleSheet';
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
    const update = useUpdateScheduleStatus();
    const run = useRunSchedule();
    const remove = useDeleteSchedule();
    const [actionError, setActionError] = useState<string | null>(null);
    const [creating, setCreating] = useState(false);
    const schedules = query.data?.schedules ?? [];
    const now = useClock(schedules);
    useRefreshWhenDue(schedules, now, () => void query.refetch());

    const callbacks = {
        onSuccess: () => setActionError(null),
        onError: (error: Error) => setActionError(error instanceof GaiaApiError ? error.detail : describeError(error, t)),
    };

    return (
        <Screen header={<ScreenHeader title={t('schedules.title')} />}>
            <NewScheduleSheet open={creating} onClose={() => setCreating(false)} />
            <Stack paddingX="lg" paddingY="sm" align="start">
                <ChipButton label={t.bare('schedules.new')} accessibilityLabel={t('schedules.new')} icon={Plus} variant="accent" onPress={() => setCreating(true)} />
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
                    ListHeaderComponent={
                        <Stack gap="sm">
                            {actionError ? (
                                <Row gap="sm" align="start">
                                    <Stack flex={1}>
                                        <Notice tone="danger" message={actionError} />
                                    </Stack>
                                    <IconButton icon={X} label={t('schedules.dismissError')} onPress={() => setActionError(null)} />
                                </Row>
                            ) : null}
                            <ListHeading title={t('schedules.list')} />
                        </Stack>
                    }
                    renderItem={({ item }) => (
                        <ScheduleCard
                            schedule={item}
                            now={now}
                            runPending={run.isPending && run.variables === item.name}
                            onOpenChat={onOpenChat}
                            onRunNow={() => run.mutate(item.name, callbacks)}
                            onToggleStatus={() =>
                                update.mutate({ name: item.name, status: item.status === 'active' ? 'paused' : 'active' }, callbacks)
                            }
                            onDelete={() => remove.mutate(item.name, callbacks)}
                        />
                    )}
                />
            ) : null}
        </Screen>
    );
}
