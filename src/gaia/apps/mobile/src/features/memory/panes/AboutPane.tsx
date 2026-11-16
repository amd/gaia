// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { ABOUT_GROUPS, ABOUT_PAGE_SIZE, oneLineQuote, type AboutYouItem } from '@webui/utils/memoryFormat.core';

import { useAddMemory, useForgetMemory, useMemoryKnowledge } from '@/data';
import { isPcRefusal, PcOnlyNotice } from '@/features/settings/PcOnlyNotice';
import { describeFailureWith, useT } from '@/i18n/useT';
import { Button, LoadingState, SectionHeader, Stack, Text } from '@/ui';

import { AddMemory } from '../about/AddMemory';
import { MemoryRow } from '../about/MemoryRow';
import type { MemoryPaneProps } from '../tabs';

export function AboutPane({ notify, memoryEnabled, onOpenAdvanced }: MemoryPaneProps) {
    const t = useT();
    const [want, setWant] = useState(ABOUT_PAGE_SIZE);
    const [forgetting, setForgetting] = useState<string | null>(null);
    const list = useMemoryKnowledge(want);
    const forget = useForgetMemory();
    const restore = useAddMemory();

    const heading = (
        <Text role="body" weight="semibold" accessibilityRole="header">
            {t('mem.about.title')}
        </Text>
    );

    if (memoryEnabled === undefined) return <LoadingState />;
    if (!memoryEnabled) {
        return (
            <Stack gap="md" paddingX="lg" paddingY="md" align="start" testID="memory-pane-about">
                {heading}
                <Text tone="secondary">{t('mem.about.off')}</Text>
                <Button label={t.bare('mem.about.turnOn')} accessibilityLabel={t('mem.about.turnOn')} variant="secondary" onPress={onOpenAdvanced} />
            </Stack>
        );
    }

    const undo = (item: AboutYouItem) =>
        restore.mutate(
            {
                content: item.content,
                category: item.category,
                domain: item.domain ?? undefined,
                context: item.context,
                entity: item.entity ?? undefined,
                sensitive: item.sensitive,
                due_at: item.due_at ?? undefined,
            },
            { onError: (error) => notify({ tone: 'danger', text: t.compose((say) => say('mem.about.restoreFailed', { error: describeFailureWith(error, say) })) }) },
        );

    const onForget = (item: AboutYouItem) => {
        setForgetting(item.id);
        forget.mutate(item.id, {
            onSuccess: () =>
                notify({
                    tone: 'info',
                    text: item.sensitive ? t('mem.about.forgottenSensitive') : t('mem.about.forgotten', { content: oneLineQuote(item.content) }),
                    sticky: true,
                    action: { label: t('mem.about.undo'), onPress: () => undo(item) },
                }),
            onError: (error) => notify({ tone: 'danger', text: t.compose((say) => say('mem.about.forgetFailed', { error: describeFailureWith(error, say) })) }),
            onSettled: () => setForgetting(null),
        });
    };

    const items = list.data?.items;
    const total = list.data?.total ?? 0;
    const groups = ABOUT_GROUPS.map((group) => ({ ...group, rows: (items ?? []).filter((item) => group.categories.includes(item.category)) })).filter(
        (group) => group.rows.length > 0,
    );

    return (
        <Stack gap="lg" paddingY="md" testID="memory-pane-about">
            <Stack gap="md" paddingX="lg">
                {heading}
                <Text tone="secondary">{t('mem.about.intro')}</Text>
                <AddMemory notify={notify} />
            </Stack>
            {list.isError && !items && isPcRefusal(list.error) ? (
                <Stack paddingX="lg">
                    <PcOnlyNotice testID="memory-about-pc-only" />
                </Stack>
            ) : list.isError && !items ? (
                <Stack gap="md" paddingX="lg" align="start" testID="memory-about-failed">
                    <Text tone="danger">{t.compose((say) => say('mem.about.loadFailed', { error: describeFailureWith(list.error, say) }))}</Text>
                    <Button label={t.bare('mem.about.retry')} accessibilityLabel={t('mem.about.retry')} variant="secondary" onPress={() => void list.refetch()} />
                </Stack>
            ) : !items ? (
                <LoadingState />
            ) : groups.length === 0 ? (
                <Stack gap="sm" paddingX="lg" testID="memory-about-empty">
                    <Text tone="secondary">{t('mem.about.empty')}</Text>
                    <Text role="caption" tone="muted">
                        {t('mem.about.emptyHint')}
                    </Text>
                </Stack>
            ) : (
                groups.map((group) => (
                    <Stack key={group.title} testID={`memory-about-group-${group.categories[0]}`}>
                        <SectionHeader title={t.bare(group.title)} />
                        {group.rows.map((item) => (
                            <MemoryRow key={item.id} item={item} forgetting={forgetting === item.id} onForget={onForget} notify={notify} />
                        ))}
                    </Stack>
                ))
            )}
            {items && items.length < total ? (
                <Stack gap="sm" paddingX="lg" align="start">
                    <Text role="caption" tone="muted">
                        {t('mem.about.showing', { shown: items.length, total })}
                    </Text>
                    <Button
                        label={t.bare('mem.about.showMore')}
                        accessibilityLabel={t('mem.about.showMore')}
                        variant="secondary"
                        busy={list.isFetching}
                        onPress={() => setWant(Math.max(want, items.length) + ABOUT_PAGE_SIZE)}
                    />
                </Stack>
            ) : null}
        </Stack>
    );
}
