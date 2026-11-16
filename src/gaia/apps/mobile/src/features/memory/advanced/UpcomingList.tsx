// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { View } from 'react-native';

import { formatRelativeDate, isOverdue, type TemporalItem } from '@webui/utils/memoryFormat.core';

import { useT } from '@/i18n/useT';
import { Icon, Row, Stack, Text, useTheme } from '@/ui';
import { CircleCheck, Clock, TriangleAlert } from '@/ui/icons';

import { SectionEmpty, SectionTitle } from './SectionTitle';

/** Items with a due time: overdue ones get a red edge and a warning icon, upcoming ones a yellow edge and a clock. */
export function UpcomingList({ items }: { items: TemporalItem[] }) {
    const t = useT();
    const { colors, radius, space } = useTheme();
    return (
        <Stack gap="md" testID="memory-upcoming">
            <SectionTitle icon={Clock} title={t('mem.upcoming.title')} />
            {items.length === 0 ? (
                <SectionEmpty icon={CircleCheck} text={t('mem.upcoming.empty')} />
            ) : (
                <Stack gap="sm">
                    {items.map((item) => {
                        const overdue = isOverdue(item.due_at);
                        const when = formatRelativeDate(item.due_at, t);
                        return (
                            <View
                                key={item.id}
                                testID={`memory-upcoming-${item.id}`}
                                style={{
                                    flexDirection: 'row',
                                    alignItems: 'flex-start',
                                    gap: space.md,
                                    padding: space.md,
                                    borderWidth: 1,
                                    borderColor: colors.borderLight,
                                    backgroundColor: overdue ? colors.dangerDim : colors.accentYellowDim,
                                    borderRadius: radius.md,
                                }}
                            >
                                <Icon icon={overdue ? TriangleAlert : Clock} size="md" tone={overdue ? 'danger' : 'warning'} />
                                <Stack flex={1} gap="xs">
                                    <Text dir="auto">{item.content}</Text>
                                    <Row wrap gap="xs">
                                        <Text role="caption" tone={overdue ? 'danger' : 'muted'}>
                                            {t(overdue ? 'mem.upcoming.overdue' : 'mem.upcoming.due', { when })}
                                        </Text>
                                        {item.context !== 'global' ? (
                                            <Text role="caption" tone="muted" dir="auto">
                                                {`· ${item.context}`}
                                            </Text>
                                        ) : null}
                                    </Row>
                                </Stack>
                            </View>
                        );
                    })}
                </Stack>
            )}
        </Stack>
    );
}
