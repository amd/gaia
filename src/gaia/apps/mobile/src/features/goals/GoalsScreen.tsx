// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { Goal } from '@/api/client.types';
import { useGoals, useQueryFailure } from '@/data';
import { ScreenHeader } from '@/features/nav/ScreenHeader';
import { describeError, useT } from '@/i18n/useT';
import { Badge, ErrorState, List, ListSkeleton, Row, Screen, Stack, Text, useTheme } from '@/ui';

import { GoalCard } from './GoalCard';
import { STAT_STATUSES, STAT_TONES, STATUS_KEYS } from './goalText';

/** The PC's goals and their tasks, read-only: they are created, accepted and cancelled on the PC. */
export function GoalsScreen() {
    const t = useT();
    const { space } = useTheme();
    const query = useGoals();
    const failure = useQueryFailure(query);
    const goals = query.data ?? [];
    const shown = goals.filter((goal) => goal.status !== 'rejected');

    return (
        <Screen header={<ScreenHeader title={t('mem.tab.goals')} />}>
            {query.isPending && !failure.failed ? <ListSkeleton rows={4} /> : null}
            {failure.failed ? (
                <ErrorState title={t('mem.goals.loadFailed')} message={describeError(failure.error, t)} onRetry={() => void query.refetch()} />
            ) : null}
            {query.data ? (
                <List<Goal>
                    data={shown}
                    keyExtractor={(goal) => goal.id}
                    accessibilityLabel={t('mem.tab.goals')}
                    contentContainerStyle={{ paddingHorizontal: space.gutter, paddingBottom: space.xl, gap: space.sm }}
                    ListHeaderComponent={
                        <Stack gap="md" paddingY="sm">
                            <Text tone="secondary">{t('mobileApp.goals.intro')}</Text>
                            <Row gap="sm" wrap testID="goals-stats">
                                {STAT_STATUSES.map((status) => {
                                    const n = goals.filter((goal) => goal.status === status).length;
                                    return (
                                        <Badge
                                            key={status}
                                            label={`${n} ${t.bare(STATUS_KEYS[status]!)}`}
                                            tone={STAT_TONES[status]}
                                            accessibilityLabel={`${t(STATUS_KEYS[status]!)}: ${n}`}
                                        />
                                    );
                                })}
                            </Row>
                        </Stack>
                    }
                    ListEmptyComponent={
                        <Text tone="secondary" testID="goals-empty">
                            {t('mobileApp.goals.empty')}
                        </Text>
                    }
                    renderItem={({ item }) => <GoalCard goal={item} />}
                />
            ) : null}
        </Screen>
    );
}
