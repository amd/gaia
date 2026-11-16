// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { Pressable as BarPress, View } from 'react-native';

import { isolate } from '@webui/i18n/registry';
import { dateLocale, type Translate } from '@webui/i18n/translator';
import { ACTIVITY_RANGE_LABELS, ACTIVITY_RANGES, type ActivityDay } from '@webui/utils/memoryFormat.core';

import { useT } from '@/i18n/useT';
import { Row, SegmentedControl, Stack, Text, useTheme } from '@/ui';
import { TrendingUp } from '@/ui/icons';

import { SectionFailed } from './SectionFailed';
import { SectionEmpty, SectionTitle } from './SectionTitle';

const CHART_HEIGHT = 120;

const dayTotal = (d: ActivityDay) => d.conversations + d.tool_calls + d.knowledge_added + d.errors;

function dayLabel(date: string, t: Translate): string {
    return new Date(`${date}T00:00:00`).toLocaleDateString(dateLocale(t), { month: 'short', day: 'numeric' });
}

export interface ActivityChartProps {
    days: number;
    onDaysChange: (days: number) => void;
    activity: ActivityDay[] | undefined;
    /** Why the read failed, with a retry; the range control stays so another range can be tried. */
    failure?: { error: unknown; onRetry: () => void };
}

/** The web's activity bars, drawn with Views. A tap picks a bar and shows its day below, where the web shows a hover tooltip. */
export function ActivityChart({ days, onDaysChange, activity, failure }: ActivityChartProps) {
    const t = useT();
    const { colors, radius, space } = useTheme();
    const [pick, setPick] = useState<{ days: number; index: number } | null>(null);
    const picked = pick?.days === days ? pick.index : null;
    const setPicked = (index: number | null) => setPick(index === null ? null : { days, index });

    const rows = activity ?? [];
    const max = Math.max(1, ...rows.map(dayTotal));
    const total = rows.reduce((sum, d) => sum + dayTotal(d), 0);
    const peak = rows.reduce<ActivityDay | null>((best, d) => (!best || dayTotal(d) > dayTotal(best) ? d : best), null);
    const shown = picked !== null ? rows[picked] : undefined;
    const summary = peak
        ? t('mobileApp.mem.activitySummary', {
              total: isolate(String(total), 'ltr'),
              peak: isolate(String(dayTotal(peak)), 'ltr'),
              date: dayLabel(peak.date, t),
          })
        : t('mem.activity.empty');
    const step = (by: number) => setPicked(Math.min(rows.length - 1, Math.max(0, (picked ?? (by > 0 ? -1 : rows.length)) + by)));
    const colorOf = {
        errors: colors.danger,
        knowledge_added: colors.accentPurple,
        tool_calls: colors.accentGreen,
        conversations: colors.accentBlue,
    } as const;
    const legend = [
        { color: colors.accentBlue, label: t('mem.legend.conversations') },
        { color: colors.accentGreen, label: t('mem.stat.toolCalls') },
        { color: colors.accentPurple, label: t('mem.legend.knowledge') },
        { color: colors.danger, label: t('mem.legend.errors') },
    ];

    return (
        <Stack gap="md" testID="memory-activity">
            <SectionTitle icon={TrendingUp} title={t('mem.activity.title')} />
            <SegmentedControl
                options={ACTIVITY_RANGES.map((d) => ({ value: String(d), label: t.bare(ACTIVITY_RANGE_LABELS[d]), accessibilityLabel: t(ACTIVITY_RANGE_LABELS[d]) }))}
                value={String(days)}
                onChange={(v) => onDaysChange(Number(v))}
                accessibilityLabel={t('mem.activity.range')}
                testID="memory-activity-range"
            />
            {failure ? (
                <SectionFailed error={failure.error} onRetry={failure.onRetry} />
            ) : rows.length > 0 ? (
                <>
                    <View
                        accessible
                        accessibilityRole="adjustable"
                        accessibilityLabel={`${t('mem.activity.title')}. ${summary}`}
                        accessibilityValue={shown ? { text: dayLabel(shown.date, t) } : undefined}
                        accessibilityActions={[{ name: 'increment' }, { name: 'decrement' }]}
                        onAccessibilityAction={(e) => step(e.nativeEvent.actionName === 'increment' ? 1 : -1)}
                        testID="memory-activity-chart"
                        style={{
                            flexDirection: 'row',
                            alignItems: 'flex-end',
                            gap: 2,
                            height: CHART_HEIGHT + 2 * space.md,
                            paddingHorizontal: space.sm,
                            paddingVertical: space.md,
                            backgroundColor: colors.bgSecondary,
                            borderWidth: 1,
                            borderColor: colors.borderLight,
                            borderRadius: radius.lg,
                        }}
                    >
                        {rows.map((day, i) => (
                            <BarPress
                                key={day.date}
                                accessible={false}
                                testID={`memory-activity-bar-${i}`}
                                onPress={() => setPicked(i === picked ? null : i)}
                                style={{ flex: 1, height: '100%', justifyContent: 'flex-end', alignItems: 'center', gap: 1 }}
                            >
                                {(['errors', 'knowledge_added', 'tool_calls', 'conversations'] as const).map((key) =>
                                    day[key] > 0 ? (
                                        <View
                                            key={key}
                                            style={{
                                                width: '100%',
                                                maxWidth: 28,
                                                height: `${(day[key] / max) * 100}%`,
                                                backgroundColor: colorOf[key],
                                                opacity: picked === null || picked === i ? 1 : 0.4,
                                                borderTopLeftRadius: 2,
                                                borderTopRightRadius: 2,
                                            }}
                                        />
                                    ) : null,
                                )}
                            </BarPress>
                        ))}
                    </View>
                    <Row justify="between">
                        <Text role="caption" tone="muted">
                            {dayLabel(rows[0].date, t)}
                        </Text>
                        {rows.length > 1 ? (
                            <Text role="caption" tone="muted">
                                {dayLabel(rows[rows.length - 1].date, t)}
                            </Text>
                        ) : null}
                    </Row>
                    {shown ? (
                        <Stack gap="xs" testID="memory-activity-detail">
                            <Text role="label" weight="semibold">
                                {dayLabel(shown.date, t)}
                            </Text>
                            {dayTotal(shown) === 0 ? <Text tone="muted">0</Text> : null}
                            {shown.conversations > 0 ? <Text tone="secondary">{t('mem.activity.conversations', { n: shown.conversations })}</Text> : null}
                            {shown.tool_calls > 0 ? <Text tone="secondary">{t('mem.activity.toolCalls', { n: shown.tool_calls })}</Text> : null}
                            {shown.knowledge_added > 0 ? <Text tone="secondary">{t('mem.activity.learned', { n: shown.knowledge_added })}</Text> : null}
                            {shown.errors > 0 ? <Text tone="danger">{t('mem.stat.errors', { n: shown.errors })}</Text> : null}
                        </Stack>
                    ) : (
                        <Text role="caption" tone="muted">
                            {t('mobileApp.mem.activityTapHint')}
                        </Text>
                    )}
                    <Row wrap gap="lg">
                        {legend.map((item) => (
                            <Row key={item.label} gap="xs">
                                <View style={{ width: 10, height: 10, borderRadius: radius.full, backgroundColor: item.color }} />
                                <Text role="caption" tone="secondary">
                                    {item.label}
                                </Text>
                            </Row>
                        ))}
                    </Row>
                </>
            ) : activity ? (
                <SectionEmpty icon={TrendingUp} text={t('mem.activity.empty')} />
            ) : null}
        </Stack>
    );
}

