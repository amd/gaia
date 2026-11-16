// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { IDLE_TURN, beginTurn, type ChatTurn } from '@/state/chatStreams';

import { activeChatId, liveRunsFromTurns, openChat, openNewChat } from './navigation';

describe('drawer navigation', () => {
    it('opens a chat by replacing the current screen, and New chat goes to /new', () => {
        const router = { replace: jest.fn() };
        openChat(router, 'abc');
        expect(router.replace).toHaveBeenCalledWith({ pathname: '/chat/[id]', params: { id: 'abc' } });
        openNewChat(router);
        expect(router.replace).toHaveBeenLastCalledWith('/new');
    });

    it('reads the open chat from the path', () => {
        expect(activeChatId('/chat/a%20b')).toBe('a b');
        expect(activeChatId('/new')).toBeNull();
        expect(activeChatId('/')).toBeNull();
    });

    it('marks chats live when their turn is not idle, and awaiting when blocked on a prompt', () => {
        const blocked: ChatTurn = { ...beginTurn(), pendingQuestion: { type: 'needs_input', request_id: 'q' } };
        const live = liveRunsFromTurns({ a: beginTurn(), b: IDLE_TURN, c: blocked });
        expect(live.running).toEqual(['a', 'c']);
        expect(Object.keys(live.awaiting)).toEqual(['c']);
    });
});
