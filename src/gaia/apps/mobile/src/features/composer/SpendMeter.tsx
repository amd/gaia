// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';

import { formatUsd, useActiveModel, useChatSpending } from '@/data';
import { openSettings } from '@/features/settings/openSettings';
import { dateLocale, useT } from '@/i18n/useT';
import { Button, Divider, Ltr, Pressable, Row, Sheet, Stack, Text } from '@/ui';

import { useChipCollapsed } from './ChipFitContext';

export interface SpendMeterProps {
    sessionId: string;
    /** A reply is running; the totals are read again once it ends. */
    busy: boolean;
    /** Starts with the sheet open (developer gallery). */
    initialOpen?: boolean;
}

/** This chat's and today's cloud spending; shown only while a priced cloud model is the chip's model. */
export function SpendMeter({ sessionId, busy, initialOpen = false }: SpendMeterProps) {
    const t = useT();
    const [open, setOpen] = useState(initialOpen);
    const model = useActiveModel().data?.model ?? null;
    const { data: spend, refetch } = useChatSpending(sessionId, model, { busy, enabled: model !== null });
    // The meter's bar goes first when the row is tight; this chip goes with it, before the model name is cut.
    const hidden = useChipCollapsed('meter');
    if (!spend?.paid || spend.model !== model || hidden) return null;

    const { chat, today } = spend;
    const floor = (s: { usd: number; unpriced_answers: number }) => `${s.unpriced_answers > 0 ? '≥ ' : ''}${formatUsd(s.usd)}`;
    const excluded = chat.unpriced_answers + today.unpriced_answers > 0;
    const answers = [...chat.items].reverse();
    const at = (iso: string) => new Date(iso).toLocaleTimeString(dateLocale(t), { hour: 'numeric', minute: '2-digit' });
    const row = (label: string, value: string) => (
        <Row gap="sm" justify="between">
            <Text role="body">{label}</Text>
            <Text role="body">
                <Ltr>{value}</Ltr>
            </Text>
        </Row>
    );

    return (
        <>
            <Pressable
                onPress={() => {
                    setOpen(true);
                    void refetch();
                }}
                accessibilityLabel={t('spend.aria', { chat: floor(chat), today: floor(today) })}
                accessibilityHint={t(excluded ? 'spend.titleExcluded' : 'spend.title')}
                accessibilityState={{ expanded: open }}
                testID="spend-meter"
            >
                <Row gap="xs">
                    <Text role="caption" tone="secondary">
                        <Ltr>{floor(chat)}</Ltr>
                    </Text>
                    <Text role="caption" tone="muted">
                        {`· ${t.bare('spend.today')} `}
                        <Ltr>{floor(today)}</Ltr>
                    </Text>
                </Row>
            </Pressable>
            <Sheet open={open} onClose={() => setOpen(false)} title={t('spend.window')}>
                <Stack gap="md">
                    <Stack gap="sm">
                        {row(t('spend.thisChat'), floor(chat))}
                        {row(t.plural('spend.todayChats', today.chats), floor(today))}
                    </Stack>
                    {excluded ? (
                        <Text role="caption" tone="secondary">
                            {t('spend.excluded', { chat: chat.unpriced_answers, today: today.unpriced_answers })}
                        </Text>
                    ) : null}
                    {answers.length > 0 ? (
                        <Stack gap="sm">
                            <Text role="label" weight="semibold" tone="secondary" accessibilityRole="header">
                                {t('spend.perAnswer')}
                            </Text>
                            {answers.map((a) => (
                                <Row key={a.message_id} gap="sm" justify="between">
                                    <Text role="caption" tone="secondary">
                                        <Ltr>{at(a.created_at)}</Ltr>
                                    </Text>
                                    {a.kind === 'estimate' && a.usd != null ? (
                                        <Text role="caption">
                                            <Ltr>{formatUsd(a.usd)}</Ltr>
                                        </Text>
                                    ) : (
                                        <Text role="caption" tone="muted">
                                            {t(a.kind === 'unreported' ? 'ctx.costUnreported' : 'ctx.unknown')}
                                        </Text>
                                    )}
                                </Row>
                            ))}
                        </Stack>
                    ) : null}
                    <Text role="caption" tone="secondary">
                        {t('spend.note')}
                    </Text>
                    <Divider />
                    <Button
                        variant="secondary"
                        label={t('spend.month')}
                        onPress={() => {
                            setOpen(false);
                            openSettings('model');
                        }}
                    />
                </Stack>
            </Sheet>
        </>
    );
}
