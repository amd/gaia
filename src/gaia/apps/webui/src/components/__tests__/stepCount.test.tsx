// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * "N steps" is the agent's own step count, the figure the TUI and evals report.
 * Thinking, plan, and tool rows are not steps: verify-01 showed "23 steps" for a
 * 10-step turn, and kw-08's final answering step had no row at all.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentActivity } from '../AgentActivity';
import { ChatView } from '../ChatView';
import { MessageBubble } from '../MessageBubble';
import { useChatStore } from '../../stores/chatStore';
import type { AgentStep, Message, Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

let nextId = 0;
const row = (type: AgentStep['type'], tool?: string): AgentStep => ({
    id: ++nextId, type, label: type, tool, active: false, success: true, timestamp: nextId,
    ...(type === 'plan' ? { planSteps: ['a', 'b'] } : {}),
});

/** verify-01's shape: a thinking row and a plan per step, six tool calls, 24 rows. */
function verify01Rows(): AgentStep[] {
    const rows: AgentStep[] = [];
    for (let i = 0; i < 10; i++) {
        rows.push(row('thinking'));
        if (i < 8) rows.push(row('plan'));
        if (i < 6) rows.push(row('tool', 'read_file'));
    }
    return rows;
}

const summary = () => document.querySelector('.agent-summary-text')?.textContent;

describe('AgentActivity step count', () => {
    it('shows the agent step count, not the number of rows', () => {
        render(<AgentActivity steps={verify01Rows()} isActive={false} variant="summary" agentStepCount={10} />);
        expect(summary()).toBe('10 steps · 6 tools');
    });

    it('can report more steps than rows when a step produced none', () => {
        const rows = [row('thinking'), row('tool', 'run_python'), row('thinking')];
        render(<AgentActivity steps={rows} isActive={false} variant="summary" agentStepCount={19} />);
        expect(summary()).toBe('19 steps · 1 tool');
    });

    it('ignores a zero count rather than printing "0 steps"', () => {
        const rows = [row('thinking'), row('tool', 'read_file')];
        render(<AgentActivity steps={rows} isActive={false} variant="summary" agentStepCount={0} />);
        expect(summary()).toBe('2 steps · 1 tool');
    });
});

describe('MessageBubble step count', () => {
    it('reads a saved answer\'s count from its turn stats', () => {
        const message: Message = {
            id: 7, session_id: 's', role: 'assistant', content: 'Done.', created_at: '', rag_sources: null,
            stats: { tokens_per_second: 0, time_to_first_token: 0, input_tokens: 0, output_tokens: 0, steps: 9 },
        };
        render(<MessageBubble message={message} agentSteps={verify01Rows()} />);
        expect(summary()).toBe('9 steps · 6 tools');
    });
});

describe('ChatView live step count', () => {
    const SESSION: Session = {
        id: 'session-1', title: 'Existing chat', created_at: '', updated_at: '', model: 'qwen',
        system_prompt: null, message_count: 0, document_ids: [], agent_type: 'gaia',
    };
    let callbacks: api.StreamCallbacks | null = null;

    beforeEach(() => {
        vi.clearAllMocks();
        callbacks = null;
        mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
        mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
        mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
        mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
        mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
            callbacks = cbs as api.StreamCallbacks;
            return new AbortController();
        });
        Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
        useChatStore.setState({
            sessions: [SESSION], currentSessionId: SESSION.id, messages: [], documents: [],
            isStreaming: false, streamingContent: '', agentSteps: [], cards: [],
            isLoadingMessages: false, pendingPrompt: null, systemStatus: null,
        });
    });

    it('counts the agent\'s step events while the turn runs', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'check these' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        const emit = (e: object) => callbacks!.onAgentEvent(e as unknown as StreamEvent);

        act(() => {
            emit({ type: 'step', step: 1, total: 150, status: 'started' });
            emit({ type: 'thinking', content: 'Looking at the folder.' });
            emit({ type: 'plan', steps: ['list', 'read'] });
            emit({ type: 'tool_start', tool: 'list_files' });
            emit({ type: 'tool_end', success: true });
            emit({ type: 'step', step: 2, total: 150, status: 'started' });
            emit({ type: 'thinking', content: 'Reading one.' });
            emit({ type: 'plan', steps: ['read'] });
            emit({ type: 'tool_start', tool: 'read_file' });
        });

        await waitFor(() => expect(summary()).toBe('2 steps · 2 tools'));
    });
});
