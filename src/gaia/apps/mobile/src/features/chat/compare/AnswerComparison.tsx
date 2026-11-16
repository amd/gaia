// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isolate } from '@webui/i18n/registry';
import { shortModelName } from '@webui/utils/modelList.core';

import type { Message } from '@/api/client.types';
import { useT, type Translate } from '@/i18n/useT';
import { retryModel, type Comparison } from '@/state/answerComparison';
import { Button, Card, IconButton, Notice, Row, Spinner, Stack, Text } from '@/ui';
import { X } from '@/ui/icons';

import { Markdown } from '../markdown/Markdown';

export interface AnswerComparisonProps {
    original: Message;
    originalModel?: string | null;
    originalLatencyMs?: number;
    comparison: Comparison;
    onKeep: (which: 'original' | 'retry') => void;
    onClose: () => void;
}

const ltr = (text: string): string => isolate(text, 'ltr');
const seconds = (t: Translate, secs: number): string => ltr(t('answer.duration.sec', { n: secs.toFixed(1) }));
const rate = (t: Translate, tps?: number | null): string | null => (tps ? ltr(t('compare.rate', { n: tps })) : null);

function Meta({ parts }: { parts: (string | null | undefined)[] }) {
    const shown = parts.filter((p): p is string => !!p);
    return shown.length ? <Text role="caption" tone="muted">{shown.join(' · ')}</Text> : null;
}

/** The original answer with another model's stacked under it; keeping one continues the chat from it. */
export function AnswerComparison({ original, originalModel, originalLatencyMs, comparison, onKeep, onClose }: AnswerComparisonProps) {
    const t = useT();
    const { state, keeping } = comparison;
    const model = retryModel(state);
    const originalName = originalModel ? shortModelName(originalModel) : t('compare.original');
    return (
        <Stack paddingX="lg" paddingY="xs" gap="sm">
            <Row justify="between">
                <Text role="ui" weight="semibold" accessibilityRole="header">{t('compare.title')}</Text>
                <IconButton icon={X} label={t('compare.close')} size="sm" onPress={onClose} disabled={!!keeping} />
            </Row>
            <Card accessibilityLabel={t('compare.answerFrom', { model: originalModel ?? t.bare('compare.originalModel') })}>
                <Stack gap="sm">
                    <Text role="ui" weight="semibold">{originalName}</Text>
                    <Meta parts={[originalLatencyMs != null && originalLatencyMs > 0 ? seconds(t, originalLatencyMs / 1000) : null, rate(t, original.stats?.tokens_per_second)]} />
                    <Markdown text={original.content} />
                    <Button
                        label={t(keeping === 'original' ? 'compare.keeping' : 'compare.keep')}
                        variant="secondary"
                        full
                        disabled={!!keeping || state.status === 'running'}
                        onPress={() => onKeep('original')}
                    />
                </Stack>
            </Card>
            <Card accessibilityLabel={t('compare.answerFrom', { model: shortModelName(model) })}>
                <Stack gap="sm">
                    <Text role="ui" weight="semibold">{shortModelName(model)}</Text>
                    {state.status === 'running' ? (
                        <Row gap="sm">
                            <Spinner size="sm" />
                            <Text role="body" tone="muted">{t('compare.loading', { model: shortModelName(model) })}</Text>
                        </Row>
                    ) : null}
                    {state.status === 'failed' ? <Notice tone="danger" message={state.error} /> : null}
                    {state.status === 'ready' ? (
                        <>
                            <Meta
                                parts={[
                                    seconds(t, state.result.stats.seconds),
                                    state.result.stats.load_seconds >= 1
                                        ? t.compose((say) => say('compare.loadTime', { time: ltr(say('answer.duration.sec', { n: state.result.stats.load_seconds.toFixed(1) })) }))
                                        : null,
                                    rate(t, state.result.stats.tokens_per_second),
                                ]}
                            />
                            <Markdown text={state.result.content} />
                            <Button
                                label={t(keeping === 'retry' ? 'compare.keeping' : 'compare.keep')}
                                full
                                disabled={!!keeping}
                                onPress={() => onKeep('retry')}
                            />
                        </>
                    ) : null}
                </Stack>
            </Card>
        </Stack>
    );
}
