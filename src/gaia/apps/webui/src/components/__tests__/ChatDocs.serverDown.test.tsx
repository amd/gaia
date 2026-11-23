// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The chat's document bar and the message after a drop word a file that failed
 * only because the model server was down the way the document library does (#8246).
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { SessionDocsBar } from '../SessionDocsBar';
import { useChatStore } from '../../stores/chatStore';
import type { Document, Session } from '../../types';

vi.mock('../../stores/modelNeededStore', async (importOriginal) => ({
    ...(await importOriginal<typeof import('../../stores/modelNeededStore')>()),
    ensureModel: vi.fn(async () => true),
}));

const BROKEN: Document = {
    id: 'doc-broken', filename: 'broken.pdf', filepath: '/home/me/broken.pdf', file_size: 1024,
    chunk_count: 0, indexed_at: '', last_accessed_at: null, sessions_using: 1,
    indexing_status: 'failed', error_kind: 'corrupt', last_error: 'Could not read PDF: broken.pdf',
};

const DOWN: Document = {
    ...BROKEN, id: 'doc-down', filename: 'notes.md', filepath: '/home/me/notes.md',
    error_kind: 'model_server_down', last_error: 'HTTPConnectionPool: connection refused',
};

const BUSY: Document = {
    ...BROKEN, id: 'doc-busy', filename: 'busy.md', filepath: '/home/me/busy.md',
    error_kind: 'model_server_busy', last_error: 'Lemonade Server timed out',
};

const session: Session = {
    id: 'chat-1', title: 'Notes', model: 'qwen', agent_type: 'gaia',
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    system_prompt: null, message_count: 0, document_ids: [],
};

const AR_UNREADABLE = 'تعذّرت قراءتها';
const AR_WAITING = 'بانتظار خادم النماذج';

const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

/** Answer the chat view's requests; an upload returns the document named by the dropped file. */
function stubFetch() {
    vi.stubGlobal('fetch', vi.fn(async (input: string, init?: RequestInit) => {
        const url = new URL(input, 'http://localhost');
        const method = init?.method ?? 'GET';
        if (method === 'POST' && url.pathname === '/api/documents/upload') {
            const file = (init?.body as FormData).get('file') as File;
            return json(file.name === DOWN.filename ? DOWN : BROKEN);
        }
        if (url.pathname === `/api/sessions/${session.id}/messages`) return json({ messages: [], total: 0 });
        if (url.pathname.startsWith('/api/chat/permissions/')) return json({ session_id: session.id, mode: 'ask', grants: [] });
        if (url.pathname === '/api/chat/active') return json({ session_ids: [] });
        if (url.pathname === '/api/documents') return json({ documents: [DOWN, BROKEN], total: 2, total_size_bytes: 2048, total_chunks: 0 });
        throw new Error(`Unexpected request: ${method} ${url}`);
    }));
}

function showBar(docs: Document[]) {
    useChatStore.setState({ sessions: [{ ...session, document_ids: docs.map((d) => d.id) }], documents: docs });
    const { container } = render(<SessionDocsBar sessionId={session.id} onDetachFailed={() => {}} />);
    return container.querySelector('.doc-context-bar') as HTMLElement;
}

async function drop(name: string) {
    const { container } = render(<ChatView sessionId={session.id} />);
    await act(async () => {
        fireEvent.drop(container.querySelector('.task-view')!, { dataTransfer: { files: [new File(['x'], name)] } });
    });
    return screen.findByRole('alert');
}

beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        locale: 'en', agents: [], sessions: [session], currentSessionId: session.id,
        messages: [], documents: [], isStreaming: false, streamingContent: '', agentSteps: [],
        isLoadingMessages: false, pendingPrompt: null, systemStatus: null, runningSessionIds: [],
    });
});

afterEach(() => {
    useChatStore.setState({ locale: 'en' });
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe("the chat's document bar", () => {
    it('counts a server-down file as waiting for the model server, not unreadable', () => {
        const bar = showBar([DOWN, BROKEN]);

        expect(bar).toHaveAttribute('aria-label', "1 couldn't be read · 1 waiting for the model server");
        expect(screen.getByTitle(/^notes\.md is waiting for GAIA's model server/)).toBeInTheDocument();
        expect(screen.getByTitle(/^broken\.pdf couldn't be read/)).toBeInTheDocument();
    });

    it('counts a file the server was too busy for as one to retry, not as waiting', () => {
        const bar = showBar([BUSY]);

        expect(bar).toHaveAttribute('aria-label', '1 to retry');
        expect(bar).not.toHaveTextContent('waiting for the model server');
        expect(screen.getByTitle(/^busy\.md wasn't added because GAIA's model server was busy/)).toBeInTheDocument();
    });

    it('still calls a file GAIA could not read unreadable', () => {
        const bar = showBar([BROKEN]);

        expect(bar).toHaveAttribute('aria-label', "1 couldn't be read");
        expect(bar).not.toHaveTextContent('waiting for the model server');
    });

    it('says the same in Arabic', () => {
        useChatStore.setState({ locale: 'ar' });
        const bar = showBar([DOWN]);

        expect(bar).toHaveTextContent(`${AR_WAITING}: 1`);
        expect(bar).not.toHaveTextContent(AR_UNREADABLE);
        expect(screen.getByTitle(new RegExp(`notes\\.md\\S* ${AR_WAITING}`))).toBeInTheDocument();
    });

    it('still calls an unreadable file unreadable in Arabic', () => {
        useChatStore.setState({ locale: 'ar' });
        const bar = showBar([BROKEN]);

        expect(bar).toHaveTextContent(`${AR_UNREADABLE}: 1`);
        expect(bar).not.toHaveTextContent(AR_WAITING);
    });
});

describe('a file dropped on the chat', () => {
    beforeEach(stubFetch);

    it('says it is waiting for the model server when the server was down', async () => {
        const alert = await drop(DOWN.filename);

        expect(alert).toHaveTextContent("notes.md is waiting for GAIA's model server. Open the document library to retry once it's back.");
        expect(alert).not.toHaveTextContent("couldn't be read");
    });

    it("still says it couldn't be read when GAIA could not read it", async () => {
        const alert = await drop(BROKEN.filename);

        expect(alert).toHaveTextContent("broken.pdf couldn't be read. Open the document library to see why and retry.");
    });

    it('says so in Arabic', async () => {
        useChatStore.setState({ locale: 'ar' });
        const alert = await drop(DOWN.filename);

        expect(alert).toHaveTextContent(`${AR_WAITING} في GAIA. افتح مكتبة المستندات لإعادة المحاولة عندما يعود.`);
        expect(alert).not.toHaveTextContent('تعذّرت قراءة');
    });

    it('still says an unreadable file could not be read in Arabic', async () => {
        useChatStore.setState({ locale: 'ar' });
        const alert = await drop(BROKEN.filename);

        expect(alert).toHaveTextContent('افتح مكتبة المستندات لمعرفة السبب وإعادة المحاولة.');
    });
});
