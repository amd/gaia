// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A reply being read aloud stops when the user leaves its chat. */

import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useReadAloudStore } from '../../stores/readAloudStore';
import type { Session } from '../../types';

vi.mock('../../stores/modelNeededStore', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../stores/modelNeededStore')>()),
    ensureModel: vi.fn(async () => true),
}));

const session: Session = {
    id: 'chat-1', title: 'Notes', model: 'qwen', agent_type: 'gaia',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    system_prompt: null, message_count: 0, document_ids: [],
};

const json = (body: unknown) =>
    new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://localhost');
        if (url.pathname === `/api/sessions/${session.id}/messages`) return json({ messages: [], total: 0 });
        if (url.pathname.startsWith('/api/chat/permissions/')) return json({ session_id: session.id, mode: 'ask', grants: [] });
        if (url.pathname === '/api/chat/active') return json({ session_ids: [] });
        if (url.pathname === '/api/documents') return json({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
        if (url.pathname === '/api/speech/settings') return json({ voice: 'af_heart', speed: 100, auto: false, min_speed: 50, max_speed: 200, voices: [] });
        throw new Error(`Unexpected request: ${url}`);
    }));
    useChatStore.setState({
        agents: [], sessions: [session], currentSessionId: session.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
    useReadAloudStore.setState({ messageId: `${session.id}:m1`, status: 'playing', error: null });
});

afterEach(() => {
    useReadAloudStore.getState().stop();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('read aloud and leaving the chat', () => {
    it('keeps reading while the chat is open and stops once it closes', () => {
        const { unmount } = render(<ChatView sessionId={session.id} />);
        expect(useReadAloudStore.getState().messageId).toBe(`${session.id}:m1`);

        unmount();
        expect(useReadAloudStore.getState().messageId).toBeNull();
        expect(useReadAloudStore.getState().status).toBeNull();
    });
});
