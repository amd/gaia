// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * With Research on, a `/skill` message runs its skill even when it is sent
 * before the app has read the installed skills, and is kept with a reason
 * when they can't be read.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { forgetInstalledSkills } from '../../utils/research';
import type { Session, SystemStatus } from '../../types';

vi.mock('../../stores/modelNeededStore', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../stores/modelNeededStore')>()),
    ensureModel: vi.fn(async () => true),
}));

const session: Session = {
    id: 'chat-1', title: 'Notes', model: 'qwen', agent_type: 'gaia',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    system_prompt: null, message_count: 0, document_ids: [],
};

const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

/** Stubs the backend; returns the messages sent to it. */
function stubBackend(skills: () => Response | Promise<Response>): string[] {
    const sent: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
        const url = new URL(input, 'http://localhost');
        if (url.pathname === '/api/skills') return skills();
        if (url.pathname === '/api/chat/send') {
            sent.push(JSON.parse(String(init?.body)).message);
            return new Response('', { status: 200, headers: { 'content-type': 'text/event-stream' } });
        }
        if (url.pathname === `/api/sessions/${session.id}/messages`) return json({ messages: [], total: 0 });
        if (url.pathname.startsWith('/api/chat/permissions/')) return json({ session_id: session.id, mode: 'ask', grants: [] });
        if (url.pathname === '/api/chat/active') return json({ session_ids: [] });
        return json({});
    }));
    return sent;
}

function typeAndSend(text: string) {
    const box = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(box, { target: { value: text } });
    fireEvent.keyDown(box, { key: 'Enter' });
    return box;
}

beforeEach(() => {
    forgetInstalledSkills();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        agents: [], sessions: [session], currentSessionId: session.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, runningSessionIds: [],
        systemStatus: { init_state: 'ready', model_loaded: 'qwen' } as unknown as SystemStatus,
        research: { [session.id]: true }, messageQueues: {},
    });
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    useChatStore.setState({ research: {}, messageQueues: {} });
});

describe('a /skill message in a chat with Research on', () => {
    it('waits for the skill list, then runs the skill once, whatever case it was typed in', async () => {
        let loaded: (response: Response) => void = () => {};
        const sent = stubBackend(() => new Promise<Response>((resolve) => { loaded = resolve; }));
        render(<ChatView sessionId={session.id} />);

        const box = typeAndSend('/Docx draft a memo');
        fireEvent.keyDown(box, { key: 'Enter' });
        await act(async () => {});
        expect(sent).toEqual([]);

        loaded(json({ skills: [{ name: 'docx' }], invalid: {} }));
        await waitFor(() => expect(sent).toEqual(['/docx draft a memo']));
    });

    it('keeps text typed while the skill list loads and sends nothing over it', async () => {
        let loaded: (response: Response) => void = () => {};
        const sent = stubBackend(() => new Promise<Response>((resolve) => { loaded = resolve; }));
        render(<ChatView sessionId={session.id} />);

        const box = typeAndSend('/docx draft a memo');
        await act(async () => {});
        fireEvent.change(box, { target: { value: '/docx draft a memo to the team' } });

        loaded(json({ skills: [{ name: 'docx' }], invalid: {} }));
        await act(async () => {});
        expect(box).toHaveValue('/docx draft a memo to the team');
        expect(sent).toEqual([]);

        fireEvent.keyDown(box, { key: 'Enter' });
        await waitFor(() => expect(sent).toEqual(['/docx draft a memo to the team']));
    });

    it('queues the message in its chat when the user opens another chat during the wait', async () => {
        let loaded: (response: Response) => void = () => {};
        const sent = stubBackend(() => new Promise<Response>((resolve) => { loaded = resolve; }));
        render(<ChatView sessionId={session.id} />);

        typeAndSend('/Docx draft a memo');
        await act(async () => {});
        act(() => useChatStore.setState({ currentSessionId: 'chat-2' }));

        loaded(json({ skills: [{ name: 'docx' }], invalid: {} }));
        await waitFor(() => expect(useChatStore.getState().messageQueues[session.id]?.map((m) => m.text)).toEqual(['/docx draft a memo']));
        expect(sent).toEqual([]);
    });

    it('still researches a /name that is no installed skill', async () => {
        const sent = stubBackend(() => json({ skills: [{ name: 'docx' }], invalid: {} }));
        render(<ChatView sessionId={session.id} />);

        typeAndSend('/docs draft a memo');
        await waitFor(() => expect(sent).toEqual(['/research /docs draft a memo']));
    });

    it('keeps the message and says why when the skill list cannot be read', async () => {
        const sent = stubBackend(() => json({ detail: 'skills folder unreadable' }, 500));
        render(<ChatView sessionId={session.id} />);

        const box = typeAndSend('/docx draft a memo');
        expect(await screen.findByRole('alert')).toHaveTextContent(/Not sent.*could not read your skills \(skills folder unreadable\)/);
        expect(sent).toEqual([]);
        expect(box).toHaveValue('/docx draft a memo');
    });
});
