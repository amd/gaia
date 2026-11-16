// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { formatSize, ltrIn } from '@webui/components/setup/format';
import { cardText } from '@webui/components/startCardText';
import type { StartCard } from '@webui/types';
import { router } from 'expo-router';
import { useState } from 'react';

import { useGpuMemory, useSelectModel, useStartScreen } from '@/data';
import { shortModelName } from '@/features/new-chat/warmUp';
import { describeError, useLocaleCode, useT } from '@/i18n/useT';
import { Button, Card, Notice, Row, Stack, Text } from '@/ui';

import { SetupFooter } from '../SetupFooter';
import type { SetupStepContext } from '../SetupScreen';
import { readyView, starterCards, summaryRows, type SummaryRow } from './readyView';

/** One summary line's label and what it says. */
function summaryText(row: SummaryRow, t: ReturnType<typeof useT>, locale: string): { label: string; value: string; warn?: boolean } {
    const gb = (n: number) => ltrIn(locale, formatSize(n));
    switch (row.kind) {
        case 'chat':
            return { label: t('setup.ready.chat'), value: row.npu ? `${row.model} (${t('setup.models.npu')})` : row.model };
        case 'vision':
            return { label: t('setup.ready.vision'), value: row.downloaded ? row.model : t('setup.ready.visionOff', { name: row.model }) };
        case 'documents':
            return { label: t('setup.ready.documents'), value: row.model };
        case 'notInstalled':
            return { label: t('setup.ready.notInstalled'), value: row.models.join(', ') };
        case 'gpuRestart':
            return {
                label: t('setup.ready.gpu'),
                value: row.gb == null ? t('gpu.pending.restartAny') : t('gpu.pending.restart', { gb: gb(row.gb) }),
                warn: true,
            };
        case 'gpuNow':
            return { label: t('setup.ready.gpu'), value: row.gb == null ? t('setup.ready.gpuChanged') : t('setup.ready.gpuNow', { gb: gb(row.gb) }) };
    }
}

/** The last step: what answers, what to know about, and tasks to start with. Done opens the app. */
export function ReadyStep({ ctx }: { ctx: SetupStepContext }) {
    const t = useT();
    const locale = useLocaleCode();
    const select = useSelectModel();
    const gpu = useGpuMemory().data;
    const cards = starterCards(useStartScreen().data?.cards, ctx.again);
    const [error, setError] = useState<string | null>(null);
    const view = readyView(ctx.run, ctx.plan);
    const names = new Map((ctx.plan?.catalog ?? []).map((e) => [e.id, e.name]));
    const model = view.answering ? (view.cloud ? shortModelName(view.answering) : names.get(view.answering) ?? shortModelName(view.answering)) : null;

    // Chat opens on the model setup set up and tested, not an older pick; a cloud setup keeps its model.
    const finish = async (first?: StartCard) => {
        setError(null);
        try {
            if (!view.cloud && view.answering) await select.mutateAsync(view.answering);
        } catch (err) {
            setError(describeError(err, t));
            return;
        }
        ctx.finish();
        if (first) router.replace({ pathname: '/new', params: { prompt: first.prompt, promptId: `start:${first.skill}` } });
    };

    return (
        <Stack gap="md">
            <Text role="title" weight="semibold" accessibilityRole="header">{t('setup.ready.heading')}</Text>
            {model ? (
                <Text accessibilityRole="summary">
                    {t(view.cloud ? 'setup.ready.answersCloud' : 'setup.ready.answersLocal', { model })}
                </Text>
            ) : null}
            {!view.cloud ? (
                <Stack gap="sm">
                    {summaryRows(ctx.plan, view.answering, gpu).map((row) => {
                        const { label, value, warn } = summaryText(row, t, locale);
                        return (
                            <Row key={row.kind} gap="md" align="start" justify="between">
                                <Text role="label" tone="secondary">{label}</Text>
                                <Stack flex={1}>
                                    <Text tone={warn ? 'warning' : 'primary'} dir="auto">{value}</Text>
                                </Stack>
                            </Row>
                        );
                    })}
                </Stack>
            ) : null}
            {error ? <Notice tone="danger" message={error} /> : null}
            {view.failedJobs.map((job) => (
                <Notice key={job} tone="warning" message={t('setup.ready.extraFailed', { job: t(`setup.test.job.${job}`) })} />
            ))}
            {view.notDownloaded.length > 0 ? (
                <Notice tone="warning" message={t('setup.ready.extraNotFetched', { names: view.notDownloaded.join(', ') })} />
            ) : null}
            {cards.length > 0 ? (
                <Stack gap="sm">
                    <Text tone="secondary" accessibilityLabel={t('newChat.cardsLabel')}>{t('setup.ready.tasks')}</Text>
                    {cards.map((card) => {
                        const { title, note } = cardText(card, t);
                        return (
                            <Card key={card.skill} onPress={() => void finish(card)} accessibilityLabel={`${title}. ${note}`}>
                                <Stack gap="xs">
                                    <Text weight="semibold">{title}</Text>
                                    <Text role="caption" tone="secondary">{note}</Text>
                                </Stack>
                            </Card>
                        );
                    })}
                </Stack>
            ) : null}
            <SetupFooter>
                <Button label={t('setup.ready.start')} variant="primary" full busy={select.isPending} onPress={() => void finish()} />
            </SetupFooter>
        </Stack>
    );
}
