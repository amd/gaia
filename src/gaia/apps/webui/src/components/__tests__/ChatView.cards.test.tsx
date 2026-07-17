// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Contract tests for the streaming-cards wiring in ChatView (issue #2108):
 * `tool_result` events with a non-empty `render` field append a card to the
 * store, the live streaming bubble shows cards mid-stream, and cards survive
 * onDone / handleStop / resetStreaming / onError in the ways #1580's
 * session-switch guard already established for agent steps.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { AgentInfo, Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1',
    // Deliberately NOT 'New Task' — skips onDone's auto-title branch entirely.
    title: 'Existing chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'doc',
};

const DOC_AGENT: AgentInfo = {
    id: 'doc',
    name: 'Doc Agent',
    description: 'Search and answer questions from indexed documents.',
    source: 'builtin',
    conversation_starters: ['Find contract clauses'],
    models: [],
    tags: ['rag', 'files'],
    icon: 'file-text',
    tools_count: 3,
};

const preScanPayload = {
    kind: 'email_pre_scan',
    urgent: [{ message_id: 'm1', sender: 'Alice <alice@example.com>', subject: 'Server down' }],
    actionable: [],
    informational_count: 2,
    suggested_archives: [],
    suggested_drafts: [],
};

const toolResultWithRender = {
    type: 'tool_result',
    tool: 'pre_scan_inbox',
    summary: 'Scanned',
    render: 'email_pre_scan',
    data: preScanPayload,
} as unknown as StreamEvent;

const toolResultWithoutRender = {
    type: 'tool_result',
    tool: 'search_documents',
    summary: 'ok',
} as unknown as StreamEvent;

/**
 * A persisted AgentStep carrying render/data (issue #2109). The backend now
 * populates these fields on tool_result-type steps whenever a card was
 * involved, so `getMessages()` returning a message with this step is enough
 * for `cardsFromSteps(agentSteps)` to derive the card — no in-memory merge
 * needed.
 */
const agentStepWithRenderCard = {
    id: 1,
    type: 'tool',
    label: 'Using tool',
    tool: 'pre_scan_inbox',
    result: 'Scanned',
    success: true,
    active: false,
    timestamp: 1234567890,
    render: 'email_pre_scan',
    data: preScanPayload,
};

let capturedCallbacks: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    capturedCallbacks = null;

    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [],
        total: 0,
        total_size_bytes: 0,
        total_chunks: 0,
    });
    mockedApi.cancelStream.mockResolvedValue(undefined as never);
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        capturedCallbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });

    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: vi.fn(),
    });

    useChatStore.setState({
        agents: [DOC_AGENT],
        activeAgentId: 'doc',
        sessions: [SESSION],
        currentSessionId: SESSION.id,
        messages: [],
        documents: [],
        isStreaming: false,
        streamingContent: '',
        agentSteps: [],
        cards: [],
        isLoadingMessages: false,
        pendingPrompt: null,
        systemStatus: null,
    });
});

afterEach(() => {
    vi.useRealTimers();
});

/** Type/render + click Send, and wait for sendMessageStream to be invoked. */
async function driveSend() {
    render(<ChatView sessionId={SESSION.id} />);

    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message input'), {
            target: { value: 'scan my inbox' },
        });
        fireEvent.click(screen.getByLabelText('Send message'));
    });

    expect(capturedCallbacks).not.toBeNull();
}

describe('ChatView streaming cards (#2108)', () => {
    it('appends a card on tool_result.render and shows it in the live streaming bubble', async () => {
        await driveSend();

        act(() => {
            capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'pre_scan_inbox' } as unknown as StreamEvent);
        });
        act(() => {
            capturedCallbacks!.onAgentEvent(toolResultWithRender);
        });

        const cards = useChatStore.getState().cards;
        expect(cards).toHaveLength(1);
        expect(cards[0].render).toBe('email_pre_scan');

        // Live bubble renders the card mid-stream, before any onDone.
        expect(screen.getByText('Server down')).toBeInTheDocument();
    });

    it('does not append a card for a tool_result without a render field', async () => {
        await driveSend();

        act(() => {
            capturedCallbacks!.onAgentEvent(toolResultWithoutRender);
        });

        expect(useChatStore.getState().cards).toEqual([]);
        expect(document.querySelector('.render-card')).toBeNull();
    });

    it('onDone transfers cards onto the finalized message and clears the store', async () => {
        vi.useFakeTimers();
        try {
            await driveSend();

            act(() => {
                capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'pre_scan_inbox' } as unknown as StreamEvent);
            });
            act(() => {
                capturedCallbacks!.onAgentEvent(toolResultWithRender);
            });

            // #2109: agent_steps now carries render/data — cardsFromSteps
            // derives the card from this hydrated step, replacing the old
            // in-memory prevWithCards merge.
            mockedApi.getMessages.mockResolvedValue({
                messages: [
                    {
                        id: 10,
                        session_id: SESSION.id,
                        role: 'user',
                        content: 'scan my inbox',
                        created_at: '2026-07-16T00:00:00.000Z',
                        rag_sources: null,
                    },
                    {
                        id: 11,
                        session_id: SESSION.id,
                        role: 'assistant',
                        content: 'Here is your inbox summary.',
                        created_at: '2026-07-16T00:00:01.000Z',
                        rag_sources: null,
                        agent_steps: [agentStepWithRenderCard],
                    },
                ] as any,
                total: 2,
            });

            act(() => {
                capturedCallbacks!.onDone({ type: 'done', content: 'Here is your inbox summary.' } as unknown as StreamEvent);
            });

            // Advance past the 350ms streamEnding window (the 300ms refetch fires
            // inside this same window), then flush the resolved-promise microtasks.
            act(() => {
                vi.advanceTimersByTime(400);
            });
            await act(async () => {});

            expect(screen.getByText('Server down')).toBeInTheDocument();
            expect(useChatStore.getState().cards).toEqual([]);

            const assistantMsg = useChatStore.getState().messages.find((m) => m.role === 'assistant');
            expect(assistantMsg?.cards).toHaveLength(1);
        } finally {
            vi.useRealTimers();
        }
    });

    it('handleStop transfers in-flight cards onto the partial message', async () => {
        vi.useFakeTimers();
        try {
            await driveSend();

            // handleStop only saves a partial message when content is non-empty.
            act(() => {
                capturedCallbacks!.onChunk({ type: 'chunk', content: 'partial answer text' } as unknown as StreamEvent);
            });
            act(() => {
                capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'pre_scan_inbox' } as unknown as StreamEvent);
            });
            act(() => {
                capturedCallbacks!.onAgentEvent(toolResultWithRender);
            });

            act(() => {
                fireEvent.click(screen.getByLabelText('Stop generating'));
            });

            // Stay under the 3s message-poll interval so a stray poll doesn't
            // clobber the partial message while still clearing the 350ms
            // streamEnding window.
            act(() => {
                vi.advanceTimersByTime(400);
            });
            await act(async () => {});

            const assistantMsg = useChatStore.getState().messages.find((m) => m.role === 'assistant');
            expect(assistantMsg?.cards).toHaveLength(1);
            expect(screen.getByText('Server down')).toBeInTheDocument();
            expect(useChatStore.getState().cards).toEqual([]);
        } finally {
            vi.useRealTimers();
        }
    });

    it('resetStreaming (session switch) wipes in-flight cards', async () => {
        await driveSend();

        act(() => {
            capturedCallbacks!.onAgentEvent(toolResultWithRender);
        });
        expect(useChatStore.getState().cards).toHaveLength(1);

        act(() => {
            useChatStore.getState().resetStreaming();
        });

        expect(useChatStore.getState().cards).toEqual([]);
    });

    it('onError clears cards', async () => {
        await driveSend();

        act(() => {
            capturedCallbacks!.onAgentEvent(toolResultWithRender);
        });
        expect(useChatStore.getState().cards).toHaveLength(1);

        act(() => {
            capturedCallbacks!.onError(new Error('boom'));
        });

        expect(useChatStore.getState().cards).toEqual([]);
    });
});

describe('ChatView cards hydration from persisted agent_steps, no live streaming (#2109)', () => {
    it('renders a card from a persisted agent_steps entry on initial session load', async () => {
        mockedApi.getMessages.mockResolvedValue({
            messages: [
                {
                    id: 20,
                    session_id: SESSION.id,
                    role: 'assistant',
                    content: 'Here is your inbox summary.',
                    created_at: '2026-07-16T00:00:01.000Z',
                    rag_sources: null,
                    agent_steps: [agentStepWithRenderCard],
                },
            ] as any,
            total: 1,
        });

        render(<ChatView sessionId={SESSION.id} />);

        // No sendMessageStream call and no onAgentEvent/onDone callback fired —
        // the card must come purely from the initial getMessages() response
        // (ChatView's loadMessages mapper), never from the streaming path.
        expect(await screen.findByText('Server down')).toBeInTheDocument();
        expect(capturedCallbacks).toBeNull();
    });
});
