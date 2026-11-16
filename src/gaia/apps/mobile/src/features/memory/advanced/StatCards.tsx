// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';

import { isolate, ref } from '@webui/i18n/registry';
import { dateLocale } from '@webui/i18n/translator';
import { CATEGORY_KEYS, formatDateRange, todayCount, type ActivityDay } from '@webui/utils/memoryFormat.core';

import type { MemoryStats } from '@/api/client.types';
import { useT } from '@/i18n/useT';
import { Row, Stack, Text, useTheme } from '@/ui';

/** Smaller than this many points and a tile wraps below its neighbor, so 320 wide gets one column. */
const TILE_MIN_WIDTH = 140;

function Tile({ value, label, sub, testID }: { value: string; label: string; sub: string; testID: string }) {
    const { colors, radius, space } = useTheme();
    return (
        <View
            testID={testID}
            style={{
                flexGrow: 1,
                flexBasis: TILE_MIN_WIDTH,
                padding: space.lg,
                gap: space.xs,
                backgroundColor: colors.bgSecondary,
                borderWidth: 1,
                borderColor: colors.borderLight,
                borderRadius: radius.lg,
            }}
        >
            <Text role="display" weight="bold">
                {isolate(value, 'ltr')}
            </Text>
            <Text role="label" weight="medium" tone="secondary">
                {label}
            </Text>
            <Text role="caption" tone="muted">
                {sub}
            </Text>
        </View>
    );
}

/** The web's five header cards. */
export function StatCards({ stats, activity }: { stats: MemoryStats; activity: ActivityDay[] }) {
    const t = useT();
    const { knowledge, conversations, tools } = stats;
    const addedToday = todayCount(activity);
    const stored =
        addedToday > 0
            ? t('mem.stat.addedToday', { n: isolate(String(addedToday), 'ltr') })
            : Object.keys(knowledge.by_category ?? {}).length > 0
              ? t.compose((say) =>
                    Object.entries(knowledge.by_category)
                        .slice(0, 3)
                        .map(([k, v]) => say('mem.stat.categoryCount', { category: CATEGORY_KEYS[k] ? ref(CATEGORY_KEYS[k]) : k, n: v }))
                        .join(say('mem.listSeparator')),
                )
              : t('mem.stat.noEntries');
    const sessionsSub =
        formatDateRange(conversations.first_session, conversations.last_session, dateLocale(t)) ||
        t('mem.stat.totalTurns', { n: isolate(String(conversations.total_turns), 'ltr') });
    const hasCalls = tools.total_calls > 0;

    return (
        <Stack gap="sm">
            <Row wrap gap="md" align="stretch" testID="memory-stat-cards">
                <Tile testID="memory-stat-stored" value={String(knowledge.total)} label={t('mem.stat.stored')} sub={stored} />
                <Tile
                    testID="memory-stat-retrieved"
                    value={String(knowledge.total_retrievals)}
                    label={t('mem.stat.retrieved')}
                    sub={
                        knowledge.total && knowledge.total_retrievals
                            ? t('mem.stat.avgRecall', { n: (knowledge.total_retrievals / knowledge.total).toFixed(1) })
                            : t('mem.stat.noRecalls')
                    }
                />
                <Tile testID="memory-stat-sessions" value={String(conversations.total_sessions)} label={t('mem.stat.sessions')} sub={sessionsSub} />
                <Tile
                    testID="memory-stat-tools"
                    value={String(tools.total_calls)}
                    label={t('mem.stat.toolCalls')}
                    sub={t('mem.stat.toolsExclMemory', { n: tools.unique_tools })}
                />
                <Tile
                    testID="memory-stat-success"
                    value={hasCalls ? `${Math.round(tools.overall_success_rate * 100)}%` : '—'}
                    label={t('mem.stat.successRate')}
                    sub={hasCalls ? t('mem.stat.errors', { n: tools.total_errors }) : t('mem.stat.noToolCalls')}
                />
            </Row>
            <Text role="caption" tone="muted">
                {t('mem.stat.toolsNotLogged')}
            </Text>
        </Stack>
    );
}
