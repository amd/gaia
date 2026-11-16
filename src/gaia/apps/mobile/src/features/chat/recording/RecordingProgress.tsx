// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMemo } from 'react';
import { View } from 'react-native';

import type { MessageKey } from '@webui/i18n/registry';

import type { StreamEvent } from '@/api/client.types';
import { useT, type Translate } from '@/i18n/useT';
import { Icon, Ltr, ProgressLine, Row, Stack, Surface, Text, useTheme } from '@/ui';
import { Check, Minus } from '@/ui/icons';

import { recordingFromEvents, type FoundSection, type StageRow } from './recordingStages';

/** "About 12 min left", "Under a minute left"; empty before there is an estimate. */
function timeLeft(seconds: number | undefined, t: Translate): string {
    if (seconds == null) return '';
    if (seconds < 60) return t('recording.leftUnderMinute');
    return t('recording.leftMinutes', { minutes: Math.ceil(seconds / 60) });
}

function Mark({ state }: { state: StageRow['state'] }) {
    const { colors } = useTheme();
    if (state === 'done') return <Icon icon={Check} size="sm" tone="success" />;
    if (state === 'skipped') return <Icon icon={Minus} size="sm" tone="muted" />;
    const running = state === 'running';
    return (
        <View style={{ width: 16, height: 16, alignItems: 'center', justifyContent: 'center' }}>
            <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: running ? colors.accentSolid : colors.textMuted, opacity: running ? 1 : 0.45 }} />
        </View>
    );
}

function Stage({ row }: { row: StageRow }) {
    const t = useT();
    const label = t(`recording.stage.${row.name}` as MessageKey);
    const running = row.state === 'running';
    const left = running ? timeLeft(row.etaSeconds, t) : '';
    const status = row.state === 'skipped' ? t('recording.skipped') : left;
    return (
        <Stack gap="xs" testID={`recording-stage-${row.name}`}>
            <Row gap="sm">
                <Mark state={row.state} />
                <View style={{ flex: 1, minWidth: 0 }}>
                    <Text role="ui" weight={running ? 'semibold' : undefined} tone={running ? 'primary' : 'muted'} numberOfLines={1}>
                        {label}
                    </Text>
                </View>
                {running && row.percent != null ? <Ltr>{`${row.percent}%`}</Ltr> : null}
                {status ? (
                    <Text role="caption" tone="muted">
                        {status}
                    </Text>
                ) : null}
            </Row>
            {running ? <ProgressLine value={row.percent == null ? undefined : row.percent / 100} /> : null}
        </Stack>
    );
}

function Found({ sections, total }: { sections: FoundSection[]; total: number }) {
    const t = useT();
    return (
        <Stack gap="sm" testID="recording-found">
            <Row gap="sm">
                <View style={{ flex: 1 }}>
                    <Text role="ui" weight="semibold">
                        {t('recording.found.title')}
                    </Text>
                </View>
                <Text role="caption" tone="muted">
                    {t('recording.found.count', { done: sections.length, total })}
                </Text>
            </Row>
            {sections.map((s) => (
                <Stack key={s.section} gap="xs">
                    <Row gap="sm" align="start">
                        <Ltr>{`${s.start}–${s.end}`}</Ltr>
                        {s.summary ? (
                            <View style={{ flex: 1 }}>
                                <Text role="caption" tone="secondary">
                                    {s.summary}
                                </Text>
                            </View>
                        ) : null}
                    </Row>
                    {s.items.map((item) => (
                        <Row key={`${item.seconds}-${item.title}`} gap="sm" align="start">
                            <Ltr>{item.start}</Ltr>
                            <View style={{ flex: 1 }}>
                                <Text role="label">{item.title}</Text>
                            </View>
                        </Row>
                    ))}
                </Stack>
            ))}
        </Stack>
    );
}

/** Where the recording the running turn is transcribing stands; nothing when the turn has none. */
export function RecordingProgress({ events }: { events: readonly StreamEvent[] }) {
    const t = useT();
    const view = useMemo(() => recordingFromEvents(events), [events]);
    if (!view) return null;
    return (
        <Surface tone="card" radius="md" padding="md" border accessibilityLabel={t('recording.title')} testID="recording-progress">
            <Stack gap="md">
                <Text role="ui" weight="semibold">
                    {t('recording.title')}
                </Text>
                {view.stages.map((row) => (
                    <Stage key={row.name} row={row} />
                ))}
                {view.found.length > 0 ? <Found sections={view.found} total={view.total} /> : null}
            </Stack>
        </Surface>
    );
}
