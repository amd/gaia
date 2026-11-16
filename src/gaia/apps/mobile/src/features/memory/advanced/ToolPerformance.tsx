// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isolate } from '@webui/i18n/registry';
import { formatDuration, formatRelativeDate, successRateClass, type ToolStat } from '@webui/utils/memoryFormat.core';

import { useT } from '@/i18n/useT';
import { Divider, Row, Stack, Text, type Tone } from '@/ui';
import { Wrench } from '@/ui/icons';

import { SectionEmpty, SectionTitle } from './SectionTitle';

const RATE_TONE = { high: 'success', medium: 'warning', low: 'danger' } as const satisfies Record<string, Tone>;

/** The web's tool table as rows: the tool's name, then its numbers on the lines below. */
export function ToolPerformance({ tools }: { tools: ToolStat[] }) {
    const t = useT();
    const metric = (label: Parameters<typeof t>[0], value: string) => t('mobileApp.mem.metric', { label: t(label), value });
    const num = (n: number) => isolate(String(n), 'ltr');
    return (
        <Stack gap="md" testID="memory-tools">
            <SectionTitle icon={Wrench} title={t('mem.tools.title')} note={t('mem.tools.note')} />
            {tools.length === 0 ? (
                <SectionEmpty icon={Wrench} text={t('mem.tools.empty')} />
            ) : (
                <Stack>
                    {tools.map((tool, i) => (
                        <Stack key={tool.tool_name} gap="xs" paddingY="md" testID={`memory-tool-${tool.tool_name}`}>
                            {i > 0 ? <Divider /> : null}
                            <Text role="ui" weight="semibold" mono dir="ltr" numberOfLines={1}>
                                {tool.tool_name}
                            </Text>
                            <Row wrap gap="md">
                                <Text role="caption" tone="secondary">
                                    {metric('mem.tools.calls', num(tool.total_calls))}
                                </Text>
                                <Text role="caption" tone={RATE_TONE[successRateClass(tool.success_rate) as keyof typeof RATE_TONE]}>
                                    {metric('mem.tools.success', `${num(Math.round(tool.success_rate * 100))}%`)}
                                </Text>
                                <Text role="caption" tone="secondary">
                                    {metric('mem.tools.denied', num(tool.denied_count ?? 0))}
                                </Text>
                                <Text role="caption" tone="secondary">
                                    {metric('mem.tools.avgTime', isolate(formatDuration(tool.avg_duration_ms), 'ltr'))}
                                </Text>
                            </Row>
                            <Text role="caption" tone="muted">
                                {metric('mem.detail.lastUsed', formatRelativeDate(tool.last_used, t))}
                            </Text>
                            {tool.last_error ? (
                                <Text role="caption" tone="danger" dir="auto" numberOfLines={2}>
                                    {metric('mem.tools.lastError', tool.last_error)}
                                </Text>
                            ) : null}
                        </Stack>
                    ))}
                </Stack>
            )}
        </Stack>
    );
}
