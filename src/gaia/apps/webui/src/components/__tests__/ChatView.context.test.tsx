// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The context meter tracks each turn, breaks the window down, and names turns before they leave view. */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { ContextUsage, Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-c',
    title: 'Context chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'gaia',
};

function usage(over: Partial<ContextUsage> = {}): ContextUsage {
    return {
        window: 32768, reply_reserve: 4096, system: 3000, tools: 2000, documents: 0, history: 1000, query: 0,
        used: 6000, history_budget: 12000, history_used: 1000, turns_in_view: 2, turns_hidden: 0,
        at_risk: [], at_risk_tokens: 0, ...over,
    };
}

let callbacks: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    callbacks = null;
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        callbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
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

const meter = () => screen.getByRole('button', { name: /^Context \d+% full/ });

describe('context meter', () => {
    it('moves when a turn pulls a long document into the context', async () => {
        mockedApi.getContextUsage
            .mockResolvedValueOnce({ available: true, ...usage() })
            .mockResolvedValueOnce({ available: true, ...usage({ documents: 14000, used: 20000 }) });
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {});
        expect(meter().textContent).toContain('18%');

        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'summarise report.pdf' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        await act(async () => {
            callbacks!.onDone({ type: 'done', content: 'summary', message_id: 2 } as StreamEvent);
        });
        expect(meter().textContent).toContain('61%');

        fireEvent.click(meter());
        expect(screen.getByText('Documents').closest('div')?.textContent).toContain('14K');
    });

    it('updates live from the turn\'s own measurement', async () => {
        mockedApi.getContextUsage.mockResolvedValue({ available: true, ...usage() });
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {});
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'q' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        act(() => {
            callbacks!.onAgentEvent({ type: 'context_usage', ...usage({ query: 3000, used: 9000 }) } as unknown as StreamEvent);
        });
        expect(meter().textContent).toContain('27%');
    });

    it('warns before older turns are dropped and names them', async () => {
        mockedApi.getContextUsage.mockResolvedValue({
            available: true,
            ...usage({ at_risk: ['what is in the Q3 plan?', 'list the owners'], at_risk_tokens: 9000 }),
        });
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {});
        const warning = screen.getByRole('status');
        expect(warning.textContent).toContain('oldest 2 turns');
        expect(warning.textContent).toContain('what is in the Q3 plan?');
        expect(warning.textContent).toContain('list the owners');
    });

    it('says when there is nothing to measure yet', async () => {
        mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'Measured once GAIA has answered in this chat.' });
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {});
        expect(screen.getByTitle('Measured once GAIA has answered in this chat.')).toBeTruthy();
    });
});
