// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryObserver } from '@tanstack/react-query';

import type { Message, MessageList, StreamEvent } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';
import { readTurn } from '@/state/chatStreams';

import { attachTurn, detachTurn, sendTurn } from './chatStream';
import { queryKeys } from './queries';

const ANSWER = 'Hello from the dev backend.';

function message(id: number, role: Message['role'], content: string): Message {
    return { id, session_id: 'new-chat', role, content, created_at: '2026-11-15T00:00:00Z', rag_sources: null };
}

const QUESTION = message(1, 'user', 'Say hello');
const REPLY = message(2, 'assistant', ANSWER);

async function settle(): Promise<void> {
    for (let i = 0; i < 10; i++) await jest.advanceTimersByTimeAsync(1);
}

/**
 * A new chat whose first turn ends while its screen is just opening: the screen's
 * first transcript read went out before GAIA saved the reply.
 */
function newChat(sessionId: string) {
    const saved: Message[] = [QUESTION];
    let releaseFirstRead!: () => void;
    const firstRead = new Promise<void>((resolve) => {
        releaseFirstRead = resolve;
    });
    let reads = 0;
    let endStream!: () => void;
    const streamEnded = new Promise<void>((resolve) => {
        endStream = resolve;
    });
    const client = {
        ...createFakeClient(),
        async getMessages(): Promise<MessageList> {
            const list = { messages: [...saved], total: saved.length };
            if (reads++ === 0) await firstRead;
            return list;
        },
        async *sendMessage(): AsyncGenerator<StreamEvent> {
            yield { type: 'chunk', content: ANSWER };
            await streamEnded;
            yield { type: 'done', message_id: REPLY.id, content: ANSWER };
        },
        // The turn is over on the PC, so a re-attach finds nothing running.
        async *attachToRun(): AsyncGenerator<StreamEvent> {},
    };
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const deps = { client, queryClient };
    const key = queryKeys.messages(sessionId);
    const mount = () => {
        const observer = new QueryObserver(queryClient, { queryKey: key, queryFn: () => client.getMessages() });
        const unsubscribe = observer.subscribe(() => {});
        attachTurn(deps, sessionId);
        return () => {
            unsubscribe();
            detachTurn(sessionId);
        };
    };
    const view = () => {
        const transcript = queryClient.getQueryData<MessageList>(key)?.messages.map((m) => m.content) ?? [];
        const { status, liveText } = readTurn(sessionId);
        // The answer is on screen exactly once: from the turn, or from the transcript.
        const shown = transcript.filter((text) => text === ANSWER).length + (liveText === ANSWER ? 1 : 0);
        return { status, liveText, transcript, shown };
    };
    return { deps, saved, mount, view, releaseFirstRead, endStream, queryClient };
}

describe('the first turn of a new chat', () => {
    beforeEach(() => jest.useFakeTimers({ doNotFake: ['nextTick', 'queueMicrotask'] }));
    afterEach(() => jest.useRealTimers());

    it('keeps the answer across a remount until a re-read brings the saved reply', async () => {
        const chat = newChat('first-turn-remount');
        try {
            expect(sendTurn(chat.deps, 'first-turn-remount', QUESTION.content)).toBe(true);
            await settle();
            const unmount = chat.mount();
            await settle();
            expect(chat.view()).toMatchObject({ status: 'streaming', liveText: ANSWER, shown: 1 });

            chat.saved.push(REPLY);
            chat.endStream();
            await settle();
            // The screen remounts as the turn ends.
            unmount();
            const unmountAgain = chat.mount();
            await settle();
            chat.releaseFirstRead();
            await settle();
            expect(chat.view()).toEqual({ status: 'idle', liveText: ANSWER, transcript: [QUESTION.content], shown: 1 });

            await jest.advanceTimersByTimeAsync(5000);
            expect(chat.view()).toEqual({ status: 'idle', liveText: '', transcript: [QUESTION.content, ANSWER], shown: 1 });
            unmountAgain();
        } finally {
            chat.queryClient.clear();
        }
    });

    it('keeps the answer when the re-reads never hold the reply', async () => {
        const chat = newChat('first-turn-unsaved');
        try {
            expect(sendTurn(chat.deps, 'first-turn-unsaved', QUESTION.content)).toBe(true);
            await settle();
            const unmount = chat.mount();
            chat.endStream();
            await settle();
            chat.releaseFirstRead();
            await jest.advanceTimersByTimeAsync(10_000);
            expect(chat.view()).toEqual({ status: 'idle', liveText: ANSWER, transcript: [QUESTION.content], shown: 1 });
            unmount();
            expect(readTurn('first-turn-unsaved').liveText).toBe(ANSWER);
        } finally {
            chat.queryClient.clear();
        }
    });
});
