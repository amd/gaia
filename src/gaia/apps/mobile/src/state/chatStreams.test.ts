// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { GaiaClient } from '@/api/client.types';
import { answerQuestion } from '@/data/mutations';

import {
    applyEvent,
    beginTurn,
    confirmationDismissed,
    expirePendingPrompts,
    markPromptResolved,
    useChatStreamStore,
    writeTurn,
    IDLE_TURN,
    type QuestionRequest,
} from './chatStreams';

const resolved = (prompt: string) => useChatStreamStore.getState().resolved.chat?.[prompt]?.state;

describe('resolved prompts', () => {
    beforeEach(() => useChatStreamStore.setState({ turns: {}, resolved: {} }));

    it('survive a new turn', () => {
        markPromptResolved('chat', 'q1', { state: 'answered' });
        writeTurn('chat', beginTurn());
        expect(resolved('q1')).toBe('answered');
    });

    it('expire when the turn ends with the prompt open, but never undo an answer', () => {
        const question = { type: 'needs_input', request_id: 'q2' } as QuestionRequest;
        writeTurn('chat', { ...IDLE_TURN, status: 'streaming', pendingQuestion: question });
        expirePendingPrompts('chat');
        expect(resolved('q2')).toBe('expired');

        markPromptResolved('chat', 'q3', { state: 'answered' });
        markPromptResolved('chat', 'q3', { state: 'expired' });
        expect(resolved('q3')).toBe('answered');
    });

    it('keep the answer shown, except for a sensitive question', async () => {
        const client = { sendUserInput: jest.fn().mockResolvedValue({}) } as unknown as GaiaClient;
        const ask = (id: string, sensitive: boolean) =>
            writeTurn('chat', {
                ...IDLE_TURN,
                status: 'streaming',
                pendingQuestion: { type: 'needs_input', request_id: id, sensitive } as QuestionRequest,
            });
        ask('plain', false);
        await answerQuestion(client, { session_id: 'chat', request_id: 'plain', value: 'a', display: 'Option A' });
        ask('secret', true);
        await answerQuestion(client, { session_id: 'chat', request_id: 'secret', value: 'hunter2' });
        const { resolved } = useChatStreamStore.getState();
        expect(resolved.chat.plain).toEqual({ state: 'answered', answer: 'Option A' });
        expect(resolved.chat.secret).toEqual({ state: 'answered' });
    });
});

describe('approval-needed card', () => {
    it('outlives the turn, and goes on the next turn or a dismiss', () => {
        const asked = applyEvent(beginTurn(), { type: 'needs_confirmation', action: 'send_email', summary: 'to bob' });
        expect(asked.pendingConfirmation).toMatchObject({ action: 'send_email', summary: 'to bob' });
        const ended = applyEvent(asked, { type: 'done', content: 'ok' });
        expect(ended.pendingConfirmation).not.toBeNull();
        expect(beginTurn().pendingConfirmation).toBeNull();
        expect(confirmationDismissed(ended).pendingConfirmation).toBeNull();
    });
});
