// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient } from '@tanstack/react-query';

import { createFakeClient } from '@/api/fake';
import { GaiaAuthError, GaiaStreamDroppedError } from '@/api/errors';
import { readTurn, useChatStreamStore } from '@/state/chatStreams';

import { detachTurn, sendTurn } from './chatStream';

describe('a stream that drops mid-turn', () => {
    it('reattaches and the turn continues', async () => {
        const client = createFakeClient({
            replies: async function* () {
                yield { type: 'chunk', content: 'Half ' };
                throw new GaiaStreamDroppedError(new Error('reset'));
            },
            running: { s1: [{ type: 'chunk', content: 'Half an answer.' }, { type: 'done', message_id: 2, content: 'Half an answer.' }] },
        });
        const queryClient = new QueryClient();
        try {
            const seen: string[] = [];
            const stop = useChatStreamStore.subscribe((state) => seen.push(state.turns.s1?.liveText ?? ''));
            expect(sendTurn({ client, queryClient }, 's1', 'Hi')).toBe(true);
            await new Promise((resolve) => setTimeout(resolve, 50));
            expect(readTurn('s1').error).toBeNull();
            stop();
            expect(seen).toContain('Half an answer.');
            expect(readTurn('s1').status).toBe('idle');
        } finally {
            detachTurn('s1');
            queryClient.clear();
        }
    });

    it('does not retry a refused reattach', async () => {
        const base = createFakeClient({
            replies: async function* () {
                yield { type: 'chunk', content: 'Half ' };
                throw new GaiaStreamDroppedError(new Error('reset'));
            },
        });
        const attach = jest.fn(async function* (): AsyncGenerator<never> {
            throw new GaiaAuthError('bad token');
        });
        const client = { ...base, attachToRun: attach } as typeof base;
        const queryClient = new QueryClient();
        try {
            sendTurn({ client, queryClient }, 's2', 'Hi');
            await new Promise((resolve) => setTimeout(resolve, 50));
            expect(attach).toHaveBeenCalledTimes(1);
            expect(readTurn('s2').error).toBeInstanceOf(GaiaAuthError);
        } finally {
            detachTurn('s2');
            queryClient.clear();
        }
    });
});
