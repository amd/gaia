// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { LiveRuns } from '@/features/chats/chatList';
import type { ChatTurn } from '@/state/chatStreams';

/** From this width the chat list stays on screen beside the page, as the web sidebar does. */
export const PERMANENT_DRAWER_MIN_WIDTH = 768;

/** Where back lands from a chat opened cold; also the drawer navigator's first route. */
export const INITIAL_ROUTE = 'index';

export interface Navigator {
    replace(href: { pathname: '/chat/[id]'; params: { id: string } } | '/new' | '/'): void;
}

/** Replaces, so back from a chat never walks through every chat visited. */
export function openChat(router: Navigator, sessionId: string): void {
    router.replace({ pathname: '/chat/[id]', params: { id: sessionId } });
}

export function openNewChat(router: Navigator): void {
    router.replace('/new');
}

export function activeChatId(pathname: string): string | null {
    const match = /^\/chat\/([^/]+)\/?$/.exec(pathname);
    return match ? decodeURIComponent(match[1]) : null;
}

/** A chat with a turn that isn't idle is running; one blocked on a prompt is also awaiting. */
export function liveRunsFromTurns(turns: Readonly<Record<string, ChatTurn>>): LiveRuns {
    const running: string[] = [];
    const awaiting: Record<string, true> = {};
    for (const [id, turn] of Object.entries(turns)) {
        if (turn.status === 'idle') continue;
        running.push(id);
        if (turn.pendingPermission || turn.pendingQuestion) awaiting[id] = true;
    }
    return { running, queued: [], awaiting, prompting: [] };
}
