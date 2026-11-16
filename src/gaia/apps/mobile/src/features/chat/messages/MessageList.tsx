// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useMemo, type ReactNode } from 'react';

import { latencyByMessage } from '@webui/utils/answerStats.core';

import type { Message } from '@/api/client.types';
import { useChatStream, useMessages } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { ErrorState, List, ListSkeleton, Stack } from '@/ui';

import { useAnswerComparison } from '../compare/useAnswerComparison';
import { AssistantBubble } from './AssistantBubble';
import { JumpButton } from './JumpButton';
import { registerJumpToMessage } from './jumpToMessage';
import { MessageActionsContext } from './MessageActionsContext';
import { UserBubble } from './UserBubble';
import { useFollowScroll } from './useFollowScroll';
import type { MessageContext } from './useMessageActions';

export interface MessageListViewProps {
    /** Oldest first, as GAIA stores them. */
    messages: readonly Message[];
    loading?: boolean;
    error?: unknown;
    onRetry?: () => void;
    /** The answer being written, shown under the last message. */
    live?: ReactNode;
    /** The chat, so each answer can show its file changes. */
    sessionId?: string;
    /** The reply whose failure the model-server fix in `live` explains. */
    fixShownBelowId?: number | null;
}

/** `shown` is newest first, so the messages after `index` are the ones before it. */
function contextOf(shown: readonly Message[], index: number): MessageContext {
    for (let i = index - 1; i >= 0; i--) {
        if (shown[i].role === 'user') return { laterTurns: i + 1, latest: index === 0 };
    }
    return { laterTurns: 0, latest: index === 0 };
}

export function MessageListView({ messages, loading = false, error, onRetry, live, sessionId, fixShownBelowId = null }: MessageListViewProps) {
    const t = useT();
    const { listRef, following, showJump, onScroll, jumpToBottom } = useFollowScroll<Message>();
    const latency = useMemo(() => latencyByMessage(messages), [messages]);
    const shown = useMemo(() => messages.filter((m) => m.role !== 'system').reverse(), [messages]);
    const newestReply = shown.find((m) => m.role === 'assistant')?.id;

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
                renderItem={({ item, index }) => {
                    const context = contextOf(shown, index);
                    return item.role === 'user' ? (
                        <UserBubble message={item} onCollapse={() => bringIntoView(index)} context={context} />
                    ) : (
                        <AssistantBubble
                            message={item}
                            context={context}
                            sessionId={sessionId}
                            newest={item.id === newestReply}
                            fixShownBelow={item.id === fixShownBelowId}
                            latencyMs={item.stats?.seconds != null ? item.stats.seconds * 1000 : latency.get(item.id)}
                        />
                    );
                }}
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
    readOnly = false,
    fixShownBelowId = null,
}: {
    sessionId: string;
    live?: ReactNode;
    readOnly?: boolean;
    fixShownBelowId?: number | null;
}) {
    const query = useMessages(sessionId);
    const { send, status } = useChatStream(sessionId);
    const { comparing } = useAnswerComparison(sessionId);
    const actions = useMemo(() => ({ send, busy: status !== 'idle' || comparing, readOnly }), [send, status, comparing, readOnly]);
    return (
        <MessageActionsContext.Provider value={actions}>
            <MessageListView
                messages={query.data?.messages ?? []}
                loading={query.isPending}
                error={query.error}
                onRetry={() => void query.refetch()}
                live={live}
                sessionId={sessionId}
                fixShownBelowId={fixShownBelowId}
            />
        </MessageActionsContext.Provider>
    );
}
