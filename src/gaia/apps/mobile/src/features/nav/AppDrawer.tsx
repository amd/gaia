// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQueryClient } from '@tanstack/react-query';
import { useDrawerStatus, type DrawerContentComponentProps } from 'expo-router/drawer';
import { usePathname, useRouter } from 'expo-router';
import { useEffect, useMemo } from 'react';

import { queryKeys } from '@/data';
import { ChatDrawerContent } from '@/features/chats/ChatDrawerContent';
import { useChatStreamStore } from '@/state/chatStreams';

import { activeChatId, liveRunsFromTurns, openChat, openNewChat } from './navigation';

// Queues are empty until the composer queue is wired to the drawer.
const NO_QUEUES = {};

export function AppDrawer({ navigation, state }: DrawerContentComponentProps) {
    const router = useRouter();
    const pathname = usePathname();
    const queryClient = useQueryClient();
    const drawerStatus = useDrawerStatus();
    useEffect(() => {
        if (drawerStatus === 'open') void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
    }, [drawerStatus, queryClient]);
    const turns = useChatStreamStore((s) => s.turns);
    const live = useMemo(() => liveRunsFromTurns(turns), [turns]);
    return (
        <ChatDrawerContent
            activeId={activeChatId(pathname)}
            live={live}
            queues={NO_QUEUES}
            routeNames={state.routeNames}
            activeRoute={pathname.split('/')[1] || null}
            onOpenRoute={(route) => {
                router.push(`/${route}`);
                navigation.closeDrawer();
            }}
            onSelect={(id) => {
                openChat(router, id);
                navigation.closeDrawer();
            }}
            onNewChat={() => {
                openNewChat(router);
                navigation.closeDrawer();
            }}
            onPcs={() => {
                router.push('/pair/pcs');
                navigation.closeDrawer();
            }}
            onClose={() => navigation.closeDrawer()}
        />
    );
}
