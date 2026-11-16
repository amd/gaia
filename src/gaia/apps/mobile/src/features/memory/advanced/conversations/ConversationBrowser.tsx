// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useMemo, useState } from 'react';

import { formatRelativeDate, ROLE_KEYS } from '@webui/utils/memoryFormat.core';

import { useConversationSearch, useMemoryConversation, useMemoryConversations, useSessions } from '@/data';
import { useT } from '@/i18n/useT';
import { Badge, Card, Row, SearchField, Sheet, Spinner, Stack, Text } from '@/ui';
import { MessageSquare } from '@/ui/icons';

import type { MemoryPaneProps } from '../../tabs';
import { SectionEmpty, SectionTitle } from '../SectionTitle';
import { SectionFailed } from '../SectionFailed';
import { ConversationTurns } from './ConversationTurns';

/** The web's counts: the sessions listed, and the turns a search returns. */
const SESSION_LIMIT = 20;
const SEARCH_LIMIT = 20;
export const CONVERSATION_SEARCH_DEBOUNCE_MS = 300;

/** The Advanced tab's conversation history: recent sessions, or the turns a search matches; tapping one reads it in a sheet. */
export function ConversationBrowser({ onOpenChat }: Pick<MemoryPaneProps, 'onOpenChat'>) {
    const t = useT();
    const [query, setQuery] = useState('');
    const [open, setOpen] = useState<string | null>(null);
    const sessions = useSessions();
    const recent = useMemoryConversations(SESSION_LIMIT);
    const turns = useMemoryConversation(open);
    const found = useConversationSearch(query, SEARCH_LIMIT);
    const searching = query.trim() !== '';

    // Chats deleted from the PC keep their memory turns but have no title.
    const titles = useMemo(() => new Map((sessions.data?.sessions ?? []).map((s) => [s.id, s.title])), [sessions.data]);
    const label = (sessionId: string) => titles.get(sessionId) || `${sessionId.slice(0, 8)}...`;
    const role = (value: string) => (ROLE_KEYS[value] ? t.bare(ROLE_KEYS[value]) : value);
    const roleFull = (value: string) => (ROLE_KEYS[value] ? t(ROLE_KEYS[value]) : value);

    let body;
    if (searching) {
        body = found.isError ? (
            <SectionFailed error={found.error} onRetry={() => void found.refetch()} />
        ) : !found.data ? (
            <Spinner />
        ) : found.data.length === 0 ? (
            <SectionEmpty icon={MessageSquare} text={t('mem.conv.noMatch', { query: query.trim() })} />
        ) : (
            <Stack gap="sm" testID="memory-conv-results">
                {found.data.map((turn) => (
                    <Card key={turn.id} padding="md" onPress={() => setOpen(turn.session_id)} accessibilityLabel={label(turn.session_id)} testID={`memory-conv-turn-${turn.id}`}>
                        <Stack gap="xs">
                            <Row gap="sm" wrap>
                                <Badge label={role(turn.role)} accessibilityLabel={roleFull(turn.role)} tone={turn.role === 'user' ? 'accent' : 'neutral'} />
                                <Text weight="medium" dir="auto">
                                    {label(turn.session_id)}
                                </Text>
                            </Row>
                            <Text dir="auto" numberOfLines={4}>
                                {turn.content}
                            </Text>
                            <Text role="caption" tone="muted">
                                {formatRelativeDate(turn.timestamp, t)}
                            </Text>
                        </Stack>
                    </Card>
                ))}
            </Stack>
        );
    } else {
        body = recent.isError ? (
            <SectionFailed error={recent.error} onRetry={() => void recent.refetch()} />
        ) : !recent.data ? (
            <Spinner />
        ) : recent.data.length === 0 ? (
            <SectionEmpty icon={MessageSquare} text={t('mem.conv.empty')} />
        ) : (
            <Stack gap="sm" testID="memory-conv-list">
                {recent.data.map((session) => (
                    <Card key={session.session_id} padding="md" onPress={() => setOpen(session.session_id)} accessibilityLabel={label(session.session_id)} testID={`memory-conv-${session.session_id}`}>
                        <Stack gap="xs">
                            <Row gap="sm" wrap>
                                <Text weight="medium" dir="auto">
                                    {label(session.session_id)}
                                </Text>
                                {session.consolidated ? <Badge label={t.bare('mem.conv.consolidated')} accessibilityLabel={t('mem.conv.consolidated')} /> : null}
                            </Row>
                            {session.first_message ? (
                                <Text dir="auto" tone="secondary" numberOfLines={3}>
                                    {session.first_message}
                                </Text>
                            ) : null}
                            <Row gap="xs" wrap>
                                <Text role="caption" tone="muted">
                                    {t.plural('mem.conv.turns', session.turn_count)}
                                </Text>
                                <Text role="caption" tone="muted">
                                    {`· ${formatRelativeDate(session.last_activity, t)}`}
                                </Text>
                            </Row>
                        </Stack>
                    </Card>
                ))}
            </Stack>
        );
    }

    return (
        <Stack gap="md" testID="memory-conversations">
            <SectionTitle icon={MessageSquare} title={t('mem.conv.title')} note={!searching && recent.data && recent.data.length > 0 ? String(recent.data.length) : undefined} />
            <SearchField
                value={query}
                onChangeText={setQuery}
                placeholder={t.bare('mem.conv.searchPlaceholder')}
                accessibilityLabel={t('mem.conv.search')}
                debounceMs={CONVERSATION_SEARCH_DEBOUNCE_MS}
                testID="memory-conv-search"
            />
            {body}
            <Sheet open={open !== null} onClose={() => setOpen(null)} title={open ? label(open) : undefined}>
                {open ? (
                    <ConversationTurns
                        key={open}
                        turns={turns}
                        onOpenChat={titles.has(open) && onOpenChat ? () => onOpenChat(open) : undefined}
                    />
                ) : null}
            </Sheet>
        </Stack>
    );
}
