// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Image, type ImageSourcePropType } from 'react-native';
import { useCallback, useMemo } from 'react';

import { APP_NAME } from '@webui/utils/constants';
import { hiddenAutomatedCount } from '@webui/utils/sessionGrouping';

import { useSessions } from '@/data';
import { NotificationsEntry } from '@/features/notifications';
import { describeError, useT } from '@/i18n/useT';
import {
    Chip,
    EmptyState,
    ErrorState,
    Icon,
    IconButton,
    ListRow,
    ListSkeleton,
    Row,
    Screen,
    SectionHeader,
    SectionList,
    Stack,
    StatusDot,
    Text,
} from '@/ui';
import { EyeOff, Monitor, Plus, X } from '@/ui/icons';

import { declaredNavEntries, type NavEntry } from './navEntries';
import { useShowAutomated } from './showAutomated';
import { useNow } from './useNow';
import {
    buildChatSections,
    chatListState,
    rowText,
    statusTone,
    type ChatRow,
    type ChatSection,
    type LiveRuns,
    type QueuedMessage,
} from './chatList';

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
}

export function ChatDrawerContent({ activeId, onSelect, onClose, onNewChat, onPcs, routeNames = [], activeRoute, onOpenRoute, live, queues }: ChatDrawerContentProps) {
    const t = useT();
    const sessions = useSessions();
    const now = useNow();
    const [showAutomated, toggleAutomated] = useShowAutomated();
    const entries = useMemo(() => declaredNavEntries(routeNames), [routeNames]);
    const open = useCallback((route: string) => onOpenRoute?.(route), [onOpenRoute]);
    const sections = useMemo(
        () => buildChatSections({ sessions: sessions.data?.sessions ?? [], activeId, live, queues, now, showAutomated }),
        [sessions.data, activeId, live, queues, now, showAutomated],
    );
    const hiddenCount = hiddenAutomatedCount([...(sessions.data?.sessions ?? [])]);
    const state = chatListState(sessions, sections);

    return (
        <Screen
            edges={['top', 'bottom', 'start']}
            footerBleed
            header={<Header onClose={onClose} onNewChat={onNewChat} onPcs={onPcs} />}
            footer={
                <Stack>
                    {entries.filter((e) => e.footer).map((e) => (
                        <NavRow key={e.key} entry={e} active={activeRoute === e.route} onOpen={open} />
                    ))}
                    <NotificationsEntry active={activeRoute === 'notifications'} onOpen={() => open('notifications')} />
                </Stack>
            }
        >
            {entries.filter((e) => !e.footer).map((e) => (
                <NavRow key={e.key} entry={e} active={activeRoute === e.route} onOpen={open} />
            ))}
            {state === 'loading' ? <ListSkeleton rows={8} /> : null}
            {state === 'error' ? <ErrorState title={describeError(sessions.error, t)} onRetry={() => void sessions.refetch()} /> : null}
            {state === 'empty' ? <EmptyState title={t('sidebar.empty')} /> : null}
            {state === 'list' ? (
                <SectionList<ChatRow, ChatSection>
                    sections={sections}
                    keyExtractor={(row) => row.id}
                    accessibilityLabel={t('sidebar.recent')}
                    renderSectionHeader={({ section }) => <SectionHeader title={t(section.titleKey)} />}
                    renderItem={({ item }) => <ChatListRow row={item} onSelect={onSelect} />}
                />
            ) : null}
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

function ChatListRow({ row, onSelect }: { row: ChatRow; onSelect: (sessionId: string) => void }) {
    const t = useT();
    const text = rowText(row, t);
    return (
        <ListRow
            title={row.title}
            emphasized={row.unread}
            selected={row.active}
            accessibilityLabel={text.aria}
            onPress={() => onSelect(row.id)}
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
