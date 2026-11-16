// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { ref, type MessageKey } from '@webui/i18n/registry';

import type { Goal } from '@/api/client.types';
import { useGoalAction, useGoals, useGoalStats, useGoalStoreError, type GoalAction } from '@/data';
import { isPcRefusal, PcOnlyNotice } from '@/features/settings/PcOnlyNotice';
import { describeFailureWith, useT } from '@/i18n/useT';
import { Badge, Button, LoadingState, Notice, Row, Stack, Text } from '@/ui';
import { Plus } from '@/ui/icons';

import { GoalCard } from '../goals/GoalCard';
import { STAT_STATUSES, STAT_TONES, STATUS_KEYS } from '../goals/goalText';
import { NewGoalSheet } from '../goals/NewGoalSheet';
import { SuggestedGoals } from '../goals/SuggestedGoals';
import type { MemoryPaneProps } from '../tabs';

const FAILURES: Record<GoalAction, MessageKey> = {
    approve: 'mem.goals.acceptFailed',
    reject: 'mem.goals.rejectFailed',
    cancel: 'mem.goals.cancelFailed',
    delete: 'mem.goals.deleteFailed',
};

export function GoalsPane({ notify }: MemoryPaneProps) {
    const t = useT();
    const [creating, setCreating] = useState(false);
    const list = useGoals();
    const stats = useGoalStats();
    const storeError = useGoalStoreError();
    const act = useGoalAction();
    const busyId = act.isPending ? act.variables.id : null;

    const run = (goal: Goal, action: GoalAction) =>
        act.mutate(
            { id: goal.id, action },
            { onError: (error) => notify({ tone: 'danger', text: t.compose((say) => say('mem.goals.failed', { what: ref(FAILURES[action]), error: describeFailureWith(error, say) })) }) },
        );

    const goals = list.data;
    const suggested = goals?.filter((goal) => goal.status === 'pending_approval') ?? [];
    const shown = goals?.filter((goal) => goal.status !== 'pending_approval' && goal.status !== 'rejected') ?? [];

    return (
        <Stack gap="lg" paddingX="lg" paddingY="md" testID="memory-pane-goals">
            <Text tone="secondary">{t('mem.goals.intro')}</Text>
            {storeError.data ? <Notice tone="warning" title={t('mem.goals.storeError')} message={storeError.data} testID="goals-store-error" /> : null}
            <Row gap="sm" wrap testID="goals-stats">
                {STAT_STATUSES.map((status) => (
                    <Badge
                        key={status}
                        label={`${stats.data?.goals[status] ?? 0} ${t.bare(STATUS_KEYS[status]!)}`}
                        tone={STAT_TONES[status]}
                        accessibilityLabel={`${t(STATUS_KEYS[status]!)}: ${stats.data?.goals[status] ?? 0}`}
                    />
                ))}
            </Row>
            <SuggestedGoals goals={suggested} busyId={busyId} onAccept={(goal) => run(goal, 'approve')} onReject={(goal) => run(goal, 'reject')} />
            <Stack align="start">
                <Button label={t.bare('mem.goals.new')} accessibilityLabel={t('mem.goals.new')} icon={Plus} variant="secondary" onPress={() => setCreating(true)} testID="goals-new" />
            </Stack>
            {list.isError && !goals && isPcRefusal(list.error) ? (
                <PcOnlyNotice testID="goals-pc-only" />
            ) : list.isError && !goals ? (
                <Stack gap="md" align="start" testID="goals-failed">
                    <Text tone="danger">{t('mobileApp.mem.sectionFailed')}</Text>
                    <Button label={t.bare('mem.about.retry')} accessibilityLabel={t('mem.about.retry')} variant="secondary" onPress={() => void list.refetch()} />
                </Stack>
            ) : !goals ? (
                <LoadingState title={t('mem.goals.loading')} />
            ) : shown.length === 0 ? (
                <Text tone="secondary" testID="goals-empty">
                    {t('mem.goals.empty')}
                </Text>
            ) : (
                <Stack gap="md">
                    {shown.map((goal) => (
                        <GoalCard key={goal.id} goal={goal} busy={busyId === goal.id} onCancel={(g) => run(g, 'cancel')} onDelete={(g) => run(g, 'delete')} />
                    ))}
                </Stack>
            )}
            <NewGoalSheet open={creating} onClose={() => setCreating(false)} />
        </Stack>
    );
}
