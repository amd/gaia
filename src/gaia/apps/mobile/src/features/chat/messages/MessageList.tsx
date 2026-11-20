// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, type ReactNode } from 'react';

import { latencyByMessage } from '@webui/utils/answerStats.core';

import type { Message } from '@/api/client.types';
import { useMessages } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { ErrorState, List, ListSkeleton, Stack } from '@/ui';

import { AssistantBubble } from './AssistantBubble';
import { JumpButton } from './JumpButton';
import { registerJumpToMessage } from './jumpToMessage';
import { UserBubble } from './UserBubble';
import { useFollowScroll } from './useFollowScroll';

export interface MessageListViewProps {
    /** Oldest first, as GAIA stores them. */
    messages: readonly Message[];
    loading?: boolean;
    error?: unknown;
    onRetry?: () => void;
    /** The answer being written, shown under the last message. */
    live?: ReactNode;
    /** The reply whose failure the model-server fix in `live` explains. */
    fixShownBelowId?: number | null;
}

export function MessageListView({ messages, loading = false, error, onRetry, live, fixShownBelowId = null }: MessageListViewProps) {
    const t = useT();
    const { listRef, following, showJump, onScroll, jumpToBottom } = useFollowScroll<Message>();
    const latency = useMemo(() => latencyByMessage(messages), [messages]);
    const shown = useMemo(() => messages.filter((m) => m.role !== 'system').reverse(), [messages]);

    const bringIntoView = useCallback(
        (index: number) => listRef.current?.scrollToIndex({ index, viewPosition: 0.5, animated: true }),
        [listRef],
    );

    useEffect(
        () =>
            registerJumpToMessage((messageId) => {
                const index = shown.findIndex((m) => m.id === messageId);
                if (index < 0) return false;
                bringIntoView(index);
                return true;
            }),
        [shown, bringIntoView],
    );

    if (loading) return <ListSkeleton rows={6} label={t('chat.loadingMessages')} />;
    if (error) return <ErrorState title={describeError(error, t)} onRetry={onRetry} />;

    return (
        <Stack flex={1}>
            <List<Message>
                listRef={listRef}
                inverted
                data={shown}
                keyExtractor={(m) => String(m.id)}
                renderItem={({ item, index }) =>
                    item.role === 'user' ? (
                        <UserBubble message={item} onCollapse={() => bringIntoView(index)} />
                    ) : (
                        <AssistantBubble
                            message={item}
                            fixShownBelow={item.id === fixShownBelowId}
                            latencyMs={item.stats?.seconds != null ? item.stats.seconds * 1000 : latency.get(item.id)}
                        />
                    )
                }
                ListHeaderComponent={live ? <>{live}</> : undefined}
                onScroll={onScroll}
                scrollEventThrottle={64}
                maintainVisibleContentPosition={following ? undefined : { minIndexForVisible: 0 }}
                onScrollToIndexFailed={({ averageItemLength, index }) =>
                    listRef.current?.scrollToOffset({ offset: averageItemLength * index, animated: true })
                }
            />
            <JumpButton visible={showJump} onPress={jumpToBottom} />
        </Stack>
    );
}

/** A chat's transcript, from the PC. `live` is the answer still being written. */
export function MessageList({
    sessionId,
    live,
    fixShownBelowId = null,
}: {
    sessionId: string;
    live?: ReactNode;
    fixShownBelowId?: number | null;
}) {
    const query = useMessages(sessionId);
    return (
        <MessageListView
            messages={query.data?.messages ?? []}
            loading={query.isPending}
            error={query.error}
            onRetry={() => void query.refetch()}
            live={live}
            fixShownBelowId={fixShownBelowId}
        />
    );
}
