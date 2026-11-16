// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { UseQueryResult } from '@tanstack/react-query';

import { formatDateFull, ROLE_KEYS } from '@webui/utils/memoryFormat.core';

import type { MemoryTurn } from '@/api/client.types';
import { dateLocale, useT } from '@/i18n/useT';
import { Badge, Button, Divider, Row, Spinner, Stack, Text } from '@/ui';

import { SectionFailed } from '../SectionFailed';

/** One conversation's turns, read-only and oldest first; long text wraps and each message follows its own script's direction. */
export function ConversationTurns({ turns, onOpenChat }: { turns: UseQueryResult<MemoryTurn[]>; onOpenChat?: () => void }) {
    const t = useT();
    const locale = dateLocale(t);

    return (
        <Stack gap="md" testID="memory-conv-detail">
            {onOpenChat ? (
                <Row>
                    <Button label={t.bare('mem.conv.openChat')} accessibilityLabel={t('mem.conv.openChat')} variant="secondary" onPress={onOpenChat} testID="memory-conv-open-chat" />
                </Row>
            ) : null}
            {turns.isError ? (
                <SectionFailed error={turns.error} onRetry={() => void turns.refetch()} />
            ) : !turns.data ? (
                <Spinner />
            ) : turns.data.length === 0 ? (
                <Text tone="muted">{t('mem.conv.noTurns')}</Text>
            ) : (
                turns.data.map((turn, index) => (
                    <Stack key={turn.id} gap="sm">
                        {index > 0 ? <Divider /> : null}
                        <Row gap="sm" wrap>
                            <Badge label={ROLE_KEYS[turn.role] ? t.bare(ROLE_KEYS[turn.role]!) : turn.role} accessibilityLabel={ROLE_KEYS[turn.role] ? t(ROLE_KEYS[turn.role]!) : turn.role} tone={turn.role === 'user' ? 'accent' : 'neutral'} />
                            <Text role="caption" tone="muted">
                                {formatDateFull(turn.timestamp, locale)}
                            </Text>
                        </Row>
                        <Text dir="auto" selectable>
                            {turn.content}
                        </Text>
                    </Stack>
                ))
            )}
        </Stack>
    );
}
