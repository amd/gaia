// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useRouter } from 'expo-router';
import { Image, type ImageSourcePropType } from 'react-native';
import { useCallback, useMemo, useState } from 'react';

import { APP_NAME } from '@webui/utils/constants';
import { hiddenAutomatedCount } from '@webui/utils/sessionGrouping';

import { useDeleteSession, useMarkRead, useRenameSession, useSessions, useSessionSearch, useSetPinned } from '@/data';
import { ModelStatusPill } from '@/features/model';
import { NotificationsEntry } from '@/features/notifications';
import { describeError, describeErrorWith, useT } from '@/i18n/useT';
import {
    Chip,
    EmptyState,
    ErrorState,
    Icon,
    IconButton,
    ListRow,
    ListSkeleton,
    Notice,
    Row,
    Screen,
    SearchField,
    SectionHeader,
    SectionList,
    Stack,
    StatusDot,
    Text,
} from '@/ui';
import { Ellipsis, EyeOff, Monitor, Plus, X } from '@/ui/icons';

import { ChatRowSheets, type RowSheet } from './ChatRowSheets';
import { declaredNavEntries, type NavEntry } from './navEntries';
import { SearchResultRow } from './SearchResultRow';
import { useShowAutomated } from './showAutomated';
import { useMarkReadWhenSeen } from './useMarkReadWhenSeen';
import { useNow } from './useNow';
import {
    buildChatSections,
    chatListState,
    rowErrorMessage,
    rowText,
    searchedSessions,
    statusTone,
    type ChatRow,
    type ChatSection,
    type LiveRuns,
    type QueuedMessage,
    type RowErrorKind,
} from './chatList';

export interface ChatListError {
    kind: RowErrorKind;
    error: unknown;
}

export interface ChatDrawerContentProps {
    activeId?: string | null;
    /** Opens the chat; the drawer closes itself after the pick. */
    onSelect: (sessionId: string) => void;
    onClose: () => void;
    /** Shows a New chat button in the header when given. */
    onNewChat?: () => void;
    /** Route names the navigator declares; the section entries for the others stay hidden. */
    routeNames?: readonly string[];
    /** The section open now, so its entry is the only selected row. */
    activeRoute?: string | null;
    onOpenRoute?: (route: string) => void;
    /** Shows a Your PCs button in the header when given. */
    onPcs?: () => void;
    live?: LiveRuns;
    queues?: Readonly<Record<string, readonly QueuedMessage[]>>;
    /** The last failed row action, shown as an alert until the next one succeeds. */
    error?: ChatListError | null;
}

export function ChatDrawerContent({ activeId, onSelect, onClose, onNewChat, onPcs, routeNames = [], activeRoute, onOpenRoute, live, queues, error }: ChatDrawerContentProps) {
    const t = useT();
    const sessions = useSessions();
    const now = useNow();
    const [text, setText] = useState('');
    const [showAutomated, toggleAutomated] = useShowAutomated();
    const search = useSessionSearch(text, showAutomated);
    const entries = useMemo(() => declaredNavEntries(routeNames), [routeNames]);
    const q = text.trim();
    const open = useCallback((route: string) => {
        setText('');
        onOpenRoute?.(route);
    }, [onOpenRoute]);
    // Opening a chat or leaving the drawer ends the search.
    const select = useCallback((id: string) => {
        setText('');
        onSelect(id);
    }, [onSelect]);
    const close = useCallback(() => {
        setText('');
        onClose();
    }, [onClose]);
    const newChat = useCallback(() => {
        setText('');
        onNewChat?.();
    }, [onNewChat]);
    const hits = useMemo(
        () => new Map(search.query === q ? (search.data?.results ?? []).map((r) => [r.session_id, r]) : []),
        [search.query, search.data, q],
    );
    const shown = useMemo(
        () => (q ? searchedSessions(sessions.data?.sessions ?? [], q, hits) : (sessions.data?.sessions ?? [])),
        [sessions.data, q, hits],
    );
    const sections = useMemo(
        () => buildChatSections({ sessions: shown, activeId, live, queues, now, showAutomated }),
        [shown, activeId, live, queues, now, showAutomated],
    );
    const hiddenCount = hiddenAutomatedCount([...(sessions.data?.sessions ?? [])]);
    const state = chatListState(sessions, sections);
    const searchFailed = q !== '' && search.query === q && search.isError;
    const settled = search.settled || searchFailed;
    const rowCount = sections.reduce((n, s) => n + s.data.length, 0);

    const [failure, setFailure] = useState<ChatListError | null>(null);
    const [target, setTarget] = useState<RowSheet | null>(null);
    const outcome = (kind: RowErrorKind) => ({
        onSuccess: () => setFailure(null),
        onError: (e: unknown) => setFailure({ kind, error: e }),
    });
    const router = useRouter();
    const rename = useRenameSession();
    const setPinned = useSetPinned();
    const remove = useDeleteSession(activeId, () => router.replace('/'));
    const markRead = useMarkRead();
    const mutateRead = markRead.mutate;
    const markSeen = useCallback((id: string) => mutateRead(id, { onError: (e) => setFailure({ kind: 'markRead', error: e }) }), [mutateRead]);
    useMarkReadWhenSeen(activeId, sessions.data?.sessions.find((s) => s.id === activeId)?.unread, markSeen);
    const alert = failure ?? error;

    return (
        <Screen
            edges={['top', 'bottom', 'start']}
            footerBleed
            header={<Header onClose={close} onNewChat={onNewChat ? newChat : undefined} onPcs={onPcs} />}
            footer={
                <Stack>
                    {entries.filter((e) => e.footer).map((e) => (
                        <NavRow key={e.key} entry={e} active={activeRoute === e.route} onOpen={open} />
                    ))}
                    <NotificationsEntry active={activeRoute === 'notifications'} onOpen={() => open('notifications')} />
                    <ModelStatusPill />
                </Stack>
            }
        >
            <Stack paddingX="lg" paddingY="sm">
                <SearchField value={text} onChangeText={setText} placeholder={t.bare('sidebar.search')} accessibilityLabel={t('sidebar.search')} />
            </Stack>
            {entries.filter((e) => !e.footer).map((e) => (
                <NavRow key={e.key} entry={e} active={activeRoute === e.route} onOpen={open} />
            ))}
            {alert ? (
                <Notice tone="danger" message={rowErrorMessage(alert.kind, alert.error, t)} />
            ) : null}
            {state === 'loading' ? <ListSkeleton rows={8} /> : null}
            {state === 'error' ? <ErrorState title={describeError(sessions.error, t)} onRetry={() => void sessions.refetch()} /> : null}
            {state === 'empty' && q === '' ? <EmptyState title={t('sidebar.empty')} /> : null}
            {q !== '' && searchFailed ? (
                <Notice tone="danger" message={t.compose((say) => say('sidebar.searchFailed', { error: describeErrorWith(search.error, say) }))} />
            ) : null}
            {q !== '' && !searchFailed && settled && search.data?.truncated ? (
                <Notice tone="info" role="note" message={t('sidebar.searchCapped', { n: search.data.results.length })} />
            ) : null}
            {q !== '' && settled && rowCount > 0 ? (
                <Stack paddingX="lg" paddingY="xs">
                    <Text role="caption" tone="muted">{t.plural('sidebar.matchCount', rowCount)}</Text>
                </Stack>
            ) : null}
            {q !== '' && rowCount === 0 && state !== 'loading' && state !== 'error' ? (
                <EmptyState title={settled ? t('sidebar.noMatch') : t('sidebar.searching')} />
            ) : null}
            {state === 'list' ? (
                <SectionList<ChatRow, ChatSection>
                    sections={sections}
                    keyExtractor={(row) => row.id}
                    accessibilityLabel={t('sidebar.recent')}
                    renderSectionHeader={({ section }) => <SectionHeader title={t(section.titleKey)} />}
                    renderItem={({ item }) =>
                        q ? (
                            <SearchResultRow
                                row={item}
                                query={q}
                                snippet={hits.get(item.id)?.field === 'message' ? hits.get(item.id)!.snippet : null}
                                onSelect={select}
                            />
                        ) : (
                            <ChatListRow row={item} onSelect={select} onMore={(row) => setTarget({ row, mode: 'menu' })} />
                        )
                    }
                />
            ) : null}
            <ChatRowSheets
                target={target}
                onTarget={setTarget}
                onRename={(id, title) => rename.mutate({ sessionId: id, title }, outcome('rename'))}
                onSetPinned={(id, pinned) => setPinned.mutate({ id, pinned }, outcome(pinned ? 'pin' : 'unpin'))}
                onDelete={(id) => remove.mutate(id, outcome('delete'))}
            />
            {hiddenCount > 0 ? (
                <Stack paddingX="lg" paddingY="sm">
                    <Chip
                        label={showAutomated ? t('sidebar.hideBackground') : t.plural('sidebar.showBackground', hiddenCount)}
                        selected={showAutomated}
                        onPress={toggleAutomated}
                    />
                </Stack>
            ) : null}
        </Screen>
    );
}

// The same mark as the web sidebar's brand.
const gaiaRobot: ImageSourcePropType = require('@webui/assets/gaia-robot.png');

function Header({ onClose, onNewChat, onPcs }: { onClose: () => void; onNewChat?: () => void; onPcs?: () => void }) {
    const t = useT();
    return (
        <Stack paddingX="lg" paddingY="sm">
            <Row gap="sm" accessibilityRole="header">
                <Image source={gaiaRobot} accessible={false} style={{ width: 22, height: 22 }} />
                <Stack flex={1}>
                    <Text role="title" weight="semibold">{APP_NAME}</Text>
                </Stack>
                {onPcs ? <IconButton icon={Monitor} label={t('mobileApp.pairing.pcs.title')} onPress={onPcs} testID="pcs-drawer-button" /> : null}
                {onNewChat ? <IconButton icon={Plus} label={t('sidebar.newChat')} onPress={onNewChat} /> : null}
                <IconButton icon={X} label={t('sidebar.collapse')} onPress={onClose} />
            </Row>
        </Stack>
    );
}

function NavRow({ entry, active, onOpen }: { entry: NavEntry; active: boolean; onOpen: (route: string) => void }) {
    const t = useT();
    return (
        <ListRow
            title={t(entry.labelKey)}
            selected={active}
            leading={<Icon icon={entry.icon} size="sm" tone="secondary" />}
            onPress={() => onOpen(entry.route)}
        />
    );
}

function ChatListRow({ row, onSelect, onMore }: { row: ChatRow; onSelect: (sessionId: string) => void; onMore: (row: ChatRow) => void }) {
    const t = useT();
    const text = rowText(row, t);
    return (
        <ListRow
            title={row.title}
            emphasized={row.unread}
            selected={row.active}
            accessibilityLabel={text.aria}
            onPress={() => onSelect(row.id)}
            onLongPress={() => onMore(row)}
            trailing={<IconButton icon={Ellipsis} size="sm" label={t('row.moreActions', { title: row.title })} onPress={() => onMore(row)} />}
            leading={
                <Row gap="xs">
                    {row.dot && text.status ? <StatusDot tone={statusTone(row.dot)} label={text.status} /> : null}
                    {row.private ? <Icon icon={EyeOff} size="sm" label={t('row.private')} /> : null}
                </Row>
            }
            subtitle={text.note ?? undefined}
            subtitleTone={text.noteFailed ? 'danger' : undefined}
        />
    );
}
