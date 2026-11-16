// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, type ReactNode } from 'react';
import { View } from 'react-native';

import type { MessageKey, Say } from '@webui/i18n/registry';

import type { Schedule } from '@/api/client.types';
import { useScheduleResults } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { Disclosure, Icon, Notice, Row, Stack, Surface, Text, useTheme } from '@/ui';
import { Clock, MessageSquare } from '@/ui/icons';

import { ChipButton } from './ChipButton';
import { composeDateLocale, describeSchedule, formatNextRun, formatTime } from './scheduleText';

const STATUS: Record<Schedule['status'], { label: MessageKey; tone: 'success' | 'warning' | 'muted' }> = {
    active: { label: 'schedules.status.active', tone: 'success' },
    paused: { label: 'schedules.status.paused', tone: 'warning' },
    cancelled: { label: 'schedules.status.cancelled', tone: 'muted' },
};

/** The desktop's `.schedule-card-status`: a small tinted tag with square-ish corners, not a pill. */
function StatusTag({ label, tone }: { label: string; tone: 'success' | 'warning' | 'muted' }) {
    const { colors, radius } = useTheme();
    const bg = { success: colors.accentGreenDim, warning: colors.accentYellowDim, muted: colors.bgTertiary }[tone];
    return (
        <View style={{ backgroundColor: bg, borderRadius: radius.sm, paddingHorizontal: 8, paddingVertical: 2 }}>
            <Text role="caption" weight="semibold" tone={tone} numberOfLines={1} compact>
                {label}
            </Text>
        </View>
    );
}

/** The desktop's `.schedule-card-error`: a tinted strip with a danger hairline border. */
function ErrorStrip({ children }: { children: ReactNode }) {
    const { colors } = useTheme();
    return (
        <View style={{ borderWidth: 1, borderColor: colors.danger, borderRadius: 6, backgroundColor: colors.dangerDim, paddingHorizontal: 8, paddingVertical: 6 }}>
            {children}
        </View>
    );
}

export interface ScheduleCardProps {
    schedule: Schedule;
    now: Date;
    onOpenChat: (sessionId: string) => void;
}

export function ScheduleCard({ schedule, now, onOpenChat }: ScheduleCardProps) {
    const t = useT();
    const [historyOpen, setHistoryOpen] = useState(false);
    const history = useScheduleResults(schedule.name, schedule.last_run_at, historyOpen);
    const runs = history.data?.results ?? [];
    const historyShowsFailure = historyOpen && runs.some((r) => r.error);

    const described = describeSchedule(schedule.description_parts, t);
    const status = STATUS[schedule.status];
    const when = (fmt: (say: Say, locale: string | undefined) => string) =>
        t.compose((say, code) => fmt(say, composeDateLocale(code, t)));

    return (
        <Surface tone="secondary" radius="sm" border padding="md">
            <Stack gap="sm">
                <Row gap="sm" justify="between">
                    <Stack flex={1}>
                        <Text role="ui" weight="semibold" numberOfLines={1}>
                            {schedule.name}
                        </Text>
                    </Stack>
                    <StatusTag label={t(status.label)} tone={status.tone} />
                </Row>
                <Text role="label" tone="secondary" numberOfLines={3}>
                    {schedule.prompt}
                </Text>
                <Row gap="xs">
                    <Icon icon={Clock} size="sm" tone="muted" />
                    <Stack flex={1}>
                        <Text role="label" tone="muted">
                            {described ?? schedule.description}
                        </Text>
                    </Stack>
                </Row>
                <Row gap="md" wrap>
                    <Text role="label" tone="muted">
                        {t('schedules.card.runs', { n: schedule.run_count })}
                    </Text>
                    {schedule.error_count > 0 ? (
                        <Text role="label" tone="muted">
                            {t('schedules.card.errors', { n: schedule.error_count })}
                        </Text>
                    ) : null}
                    <Text role="label" tone="muted">
                        {when((say, locale) => say('schedules.card.last', { when: formatTime(schedule.last_run_at, say, now, locale) }))}
                    </Text>
                    <Text role="label" tone="muted">
                        {when((say, locale) =>
                            say('schedules.card.next', {
                                when: schedule.status === 'paused' ? say('schedules.status.paused') : formatNextRun(schedule.next_run_at, say, now, locale),
                            }),
                        )}
                    </Text>
                </Row>
                {schedule.last_error && !historyShowsFailure ? (
                    <ErrorStrip>
                        <Text role="label">{t('schedules.card.lastFailed', { error: schedule.last_error })}</Text>
                    </ErrorStrip>
                ) : null}
                {schedule.session_id ? (
                    <Row gap="xs" wrap>
                        <ChipButton
                            icon={MessageSquare}
                            label={t('schedules.card.openChat')}
                            onPress={() => onOpenChat(schedule.session_id!)}
                        />
                    </Row>
                ) : null}
                <Disclosure title={t('schedules.history')} open={historyOpen} onToggle={() => setHistoryOpen((open) => !open)}>
                    <Stack gap="sm">
                        {history.isPending ? <Text tone="muted">{t('schedules.historyLoading')}</Text> : null}
                        {history.isError ? <Notice tone="danger" message={describeError(history.error, t)} /> : null}
                        {history.data && runs.length === 0 ? <Text tone="muted">{t('schedules.historyEmpty')}</Text> : null}
                        {runs.map((run) => (
                            <Stack key={run.id} gap="xs">
                                <Text role="label" tone="muted">
                                    {when((say, locale) => formatTime(run.executed_at, say, now, locale))}
                                </Text>
                                {run.error ? (
                                    <Text role="label" tone="danger">
                                        {run.error}
                                    </Text>
                                ) : (
                                    <Text role="label" tone="secondary" numberOfLines={6}>
                                        {run.result || t('schedules.noOutput')}
                                    </Text>
                                )}
                            </Stack>
                        ))}
                    </Stack>
                </Disclosure>
            </Stack>
        </Surface>
    );
}
