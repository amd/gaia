// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A failed Export used to only log, so clicking it looked like nothing happened. */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session } from '../../types';

const session: Session = {
    id: 'export-me', title: 'Notes', model: 'qwen', agent_type: 'gaia',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    system_prompt: null, message_count: 0, document_ids: [],
};

function stubFetch(exportResponse: () => Response) {
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://localhost');
        if (url.pathname === `/api/sessions/${session.id}/export`) return exportResponse();
        let body: unknown;
        if (url.pathname === `/api/sessions/${session.id}/messages`) {
            body = { messages: [], total: 0 };
        } else if (url.pathname.startsWith('/api/chat/permissions/')) {
            body = { session_id: session.id, mode: 'ask', grants: [] };
        } else if (url.pathname === '/api/chat/active') {
            body = { session_ids: [] };
        } else if (url.pathname === '/api/documents') {
            body = { documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 };
        } else {
            throw new Error(`Unexpected request: ${url}`);
        }
        return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
    }));
}

beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true, value: vi.fn(),
    });
    useChatStore.setState({
        agents: [], sessions: [session], currentSessionId: session.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('a failed export says so', () => {
    it('names the server error and can be dismissed', async () => {
        stubFetch(() => new Response('{"detail":"boom"}', { status: 500 }));
        render(<ChatView sessionId={session.id} />);

        fireEvent.click(screen.getByRole('button', { name: 'Export chat' }));

        const alert = await screen.findByRole('alert');
        expect(alert).toHaveTextContent("Couldn't export this chat");
        expect(alert).toHaveTextContent('HTTP 500');

        fireEvent.click(screen.getByRole('button', { name: 'Dismiss export error' }));
        await waitFor(() => expect(screen.queryByText(/Couldn't export this chat/)).not.toBeInTheDocument());
    });

    it('says the backend is unreachable when the request never lands', async () => {
        stubFetch(() => { throw new TypeError('Failed to fetch'); });
        render(<ChatView sessionId={session.id} />);

        fireEvent.click(screen.getByRole('button', { name: 'Export chat' }));

        expect(await screen.findByRole('alert')).toHaveTextContent("GAIA's backend isn't reachable");
    });
});
