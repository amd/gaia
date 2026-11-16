// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useGlobalSearchParams, usePathname, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef } from 'react';

import { useSessionSync, useSessions } from '@/data';
import { useLastChatStore } from '@/state/lastChat';
import { useRouteNoticeStore } from '@/state/routeNotice';

import { chatFromLink, chatToRestore } from './launch';
import { activeChatId, openChat } from './navigation';

/**
 * The app's routing on top of the file routes: reopens the last chat once at launch, opens the
 * chat a `?session=` link names, remembers the open chat, and keeps the list fresh.
 */
export function RoutingEffects() {
    const router = useRouter();
    const pathname = usePathname();
    const { session: linked } = useGlobalSearchParams<{ session?: string }>();
    const sessions = useSessions().data?.sessions;
    const { loaded, lastChatId, choseNewChat, load, remember, markNewChatChosen } = useLastChatStore();
    const settled = useRef(false);

    useEffect(() => {
        void load();
    }, [load]);

    useSessionSync(useCallback((id: string) => openChat(router, id), [router]));

    useEffect(() => {
        const open = activeChatId(pathname);
        if (open) remember(open);
        else if (pathname === '/new') {
            remember(null);
            markNewChatChosen();
        }
    }, [pathname, remember, markNewChatChosen]);

    useEffect(() => {
        if (settled.current || !loaded || !sessions) return;
        settled.current = true;
        if (linked) {
            const id = chatFromLink(linked, sessions);
            if (id) openChat(router, id);
            else {
                useRouteNoticeStore.getState().show('mobileApp.chat.notFound');
                router.replace('/');
            }
            return;
        }
        const id = chatToRestore({ lastChatId, sessions, choseNewChat, pathname });
        if (id) openChat(router, id);
    }, [loaded, sessions, linked, lastChatId, choseNewChat, pathname, router]);

    return null;
}
