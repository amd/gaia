// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { QueuedMessage } from '@webui/utils/messageQueue';

import { useT, type Translate } from '@/i18n/useT';
import { Button, Icon, IconButton, Pressable, Row, Stack, Surface, Text } from '@/ui';
import { RotateCcw, X } from '@/ui/icons';

export interface QueuedMessagesProps {
    items: readonly QueuedMessage[];
    onRemove(id: number): void;
    onRetry(id: number): void;
    /** Absent when no running reply can take a message. */
    onSteer?(id: number): void;
    /** A reply is running in this chat, so queued messages wait for it. */
    replying: boolean;
}

function stateLine(item: QueuedMessage, replying: boolean, t: Translate): string {
    switch (item.state) {
        case 'failed':
            return item.error ? t('queue.notSentWhy', { error: item.error }) : t('queue.notSent');
        case 'queued':
            if (item.note) return t('queue.steerRefused');
            return t(replying ? 'queue.waiting' : 'queue.sendingNext');
        case 'sending':
            return t('queue.sending');
        default:
            return t('queue.delivered');
    }
}

/** Messages typed while GAIA answers, oldest first. */
export function QueuedMessages({ items, onRemove, onRetry, onSteer, replying }: QueuedMessagesProps) {
    const t = useT();
    // A message going out right now already shows in the chat.
    const shown = items.filter((m) => m.state !== 'starting');
    if (shown.length === 0) return null;
    return (
        <Stack gap="sm" paddingX="lg" testID="queued-messages" accessibilityRole="list">
            {shown.map((item) => (
                <Surface key={item.id} tone="secondary" radius="md" padding="sm" border testID={`queued-${item.id}`}>
                    <Stack gap="xs">
                        <Row gap="sm" align="center">
                            <Stack flex={1}>
                                <Text role="ui" numberOfLines={2}>
                                    {item.text}
                                </Text>
                            </Stack>
                            {item.state === 'queued' || item.state === 'failed' ? (
                                <IconButton
                                    icon={X}
                                    size="sm"
                                    label={t('queue.removeAria', { text: item.text })}
                                    onPress={() => onRemove(item.id)}
                                    testID={`queued-remove-${item.id}`}
                                />
                            ) : null}
                        </Row>
                        <Text role="caption" tone={item.state === 'failed' ? 'danger' : 'secondary'}>
                            {stateLine(item, replying, t)}
                        </Text>
                        {item.state === 'failed' ? (
                            <Pressable
                                accessibilityLabel={t('queue.sendAgainAria', { text: item.text })}
                                onPress={() => onRetry(item.id)}
                                testID={`queued-retry-${item.id}`}
                            >
                                <Row gap="sm">
                                    <Icon icon={RotateCcw} size="sm" tone="accent" />
                                    <Text role="ui" weight="medium" tone="accent">
                                        {t('queue.sendAgain')}
                                    </Text>
                                </Row>
                            </Pressable>
                        ) : null}
                        {item.state === 'queued' && onSteer ? (
                            <Button
                                label={t('queue.steer')}
                                variant="ghost"
                                size="sm"
                                onPress={() => onSteer(item.id)}
                                testID={`queued-steer-${item.id}`}
                            />
                        ) : null}
                    </Stack>
                </Surface>
            ))}
        </Stack>
    );
}
