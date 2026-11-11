// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Renaming a chat, toggling private mode, dropping files and removing a
 * document from the chat used to fail with only a console line (#4817). A
 * failed rename also left the title field open on an unhandled rejection.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Document, Session } from '../../types';

vi.mock('../../stores/modelNeededStore', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../stores/modelNeededStore')>()),
    ensureModel: vi.fn(async () => true),
}));

const doc: Document = {
    id: 'd1', filename: 'notes.txt', filepath: '/home/me/notes.txt', file_size: 10,
    chunk_count: 1, indexed_at: '2026-10-01T00:00:00Z', last_accessed_at: null,
    sessions_using: 1, indexing_status: 'complete',
} as Document;

const session: Session = {
    id: 'chat-1', title: 'Notes', model: 'qwen', agent_type: 'gaia',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    system_prompt: null, message_count: 0, document_ids: [doc.id],
};

type Route = (init: RequestInit | undefined) => Response;

const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const refused = () => json({ detail: 'database is locked' }, 500);

function stubFetch(routes: Record<string, Route>) {
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
        const url = new URL(input, 'http://localhost');
        const route = routes[`${init?.method ?? 'GET'} ${url.pathname}`];
        if (route) return route(init);
        if (url.pathname === `/api/sessions/${session.id}/messages`) return json({ messages: [], total: 0 });
        if (url.pathname.startsWith('/api/chat/permissions/')) return json({ session_id: session.id, mode: 'ask', grants: [] });
        if (url.pathname === '/api/chat/active') return json({ session_ids: [] });
        if (url.pathname === '/api/documents') return json({ documents: [doc], total: 1, total_size_bytes: 10, total_chunks: 1 });
        throw new Error(`Unexpected request: ${init?.method ?? 'GET'} ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
}

beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        agents: [], sessions: [session], currentSessionId: session.id,
        messages: [], documents: [doc], isStreaming: false, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('chat actions that fail say so', () => {
    it('a rename the backend refuses shows why, closes the field and keeps the old title', async () => {
        stubFetch({ [`PUT /api/sessions/${session.id}`]: refused });
        render(<ChatView sessionId={session.id} />);

        fireEvent.click(screen.getByRole('button', { name: /Notes/ }));
        const field = screen.getByRole('textbox', { name: 'Chat title' });
        fireEvent.change(field, { target: { value: 'Trip plan' } });
        fireEvent.keyDown(field, { key: 'Enter' });

        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent("Couldn't rename this chat: database is locked");
        expect(screen.queryByRole('textbox', { name: 'Chat title' })).not.toBeInTheDocument();
        expect(useChatStore.getState().sessions[0].title).toBe('Notes');

        fireEvent.click(screen.getByRole('button', { name: 'Dismiss error' }));
        await waitFor(() => expect(screen.queryByText(/Couldn't rename/)).not.toBeInTheDocument());
    });

    it('a private-mode toggle that fails says so and leaves the chat as it was', async () => {
        stubFetch({ [`PATCH /api/sessions/${session.id}/private`]: refused });
        render(<ChatView sessionId={session.id} />);

        fireEvent.click(screen.getByRole('button', { name: 'Turn on private chat' }));

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't change private mode for this chat: database is locked");
        expect(useChatStore.getState().sessions[0].private).toBeFalsy();
    });

    it('removing a document from the chat that fails names the document', async () => {
        stubFetch({ [`DELETE /api/sessions/${session.id}/documents/${doc.id}`]: refused });
        render(<ChatView sessionId={session.id} />);

        // The pill's remove button is display:none until hover, which leaves it no accessible name.
        fireEvent.click(screen.getByLabelText('Remove notes.txt from this chat'));

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't remove notes.txt from this chat: database is locked");
        expect(useChatStore.getState().sessions[0].document_ids).toEqual([doc.id]);
    });

    it('a dropped file that fails to upload is named, and one that uploads but is not attached says so', async () => {
        stubFetch({
            'POST /api/documents/upload': (init) => {
                const name = (init?.body as FormData).get('file') as File;
                return name.name === 'broken.pdf'
                    ? json({ detail: 'File is damaged' }, 422)
                    : json({ ...doc, id: 'd2', filename: 'report.pdf' });
            },
            [`POST /api/sessions/${session.id}/documents`]: refused,
        });
        const { container } = render(<ChatView sessionId={session.id} />);

        const files = [new File(['x'], 'broken.pdf'), new File(['y'], 'report.pdf')];
        await act(async () => {
            fireEvent.drop(container.querySelector('.task-view')!, { dataTransfer: { files } });
        });

        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent("Couldn't add broken.pdf to your documents: File is damaged");
        expect(alert).toHaveTextContent("report.pdf is in your documents but isn't attached to this chat: database is locked");
    });

    it('a rename that works shows no error', async () => {
        stubFetch({ [`PUT /api/sessions/${session.id}`]: () => json({ ...session, title: 'Trip plan' }) });
        render(<ChatView sessionId={session.id} />);

        fireEvent.click(screen.getByRole('button', { name: /Notes/ }));
        const field = screen.getByRole('textbox', { name: 'Chat title' });
        fireEvent.change(field, { target: { value: 'Trip plan' } });
        fireEvent.keyDown(field, { key: 'Enter' });

        await waitFor(() => expect(useChatStore.getState().sessions[0].title).toBe('Trip plan'));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    });
});
