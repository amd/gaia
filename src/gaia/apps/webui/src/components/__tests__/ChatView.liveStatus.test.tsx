// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The streaming bubble's live line names the model's phase, like the TUI's. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
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
    agent_type: 'gaia',
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

let capturedCallbacks: api.StreamCallbacks | null = null;

beforeEach(() => {
    vi.clearAllMocks();
    capturedCallbacks = null;

    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
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

    useNotificationStore.setState({
        notifications: [],
        showPanel: false,
        typeFilter: null,
    });
});

afterEach(() => {
    vi.useRealTimers();
});

/** Type/render + click Send, and wait for sendMessageStream to be invoked. */
async function driveSend() {
    render(<ChatView sessionId={SESSION.id} />);

    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), {
            target: { value: 'send the drafted email' },
        });
        fireEvent.click(screen.getByLabelText('Send'));
    });

    expect(capturedCallbacks).not.toBeNull();
}

/** A running tool is named once, on its own row, with no live line repeating it. */
async function runningRow(text: string) {
    await waitFor(() => expect(document.querySelector('.flow-tool.active .flow-tool-label')?.textContent).toBe(text));
    expect(document.querySelector('.thinking-indicator')).toBeNull();
}

/** The live line at the end of the timeline, saying what the model is doing between steps. */
async function liveLine(text: string) {
    await waitFor(() => expect(document.querySelector('.thinking-indicator-text')?.textContent).toBe(text));
}

describe('ChatView live status', () => {
    it('says Thinking... until a phase event arrives, then shows the latest phase', async () => {
        await driveSend();
        await liveLine('Thinking...');

        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'working', phase: 'reading', message: 'Reading your request',
            } as unknown as StreamEvent);
        });
        await liveLine('Reading your request');

        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'working', phase: 'reasoning', message: 'Reasoning — 38 words so far', words: 38,
            } as unknown as StreamEvent);
        });
        await liveLine('Reasoning — 38 words so far');
        expect(useChatStore.getState().liveStatus).toBe('Reasoning — 38 words so far');
    });

    it('words the tool-list re-read itself, so it reaches the user in their language', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'working', phase: 'loading_tools', message: 'backend words',
            } as unknown as StreamEvent);
        });
        await liveLine('Loading more tools');
        expect(useChatStore.getState().livePhase).toBe('loading_tools');
    });

    it('ignores status messages that are not phases', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'warning', message: 'Full access is ON',
            } as unknown as StreamEvent);
        });
        expect(useChatStore.getState().liveStatus).toBeNull();
    });

    it('says a tool is running once it starts, not that the model is thinking', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'working', phase: 'tool_call', message: 'Preparing write_file',
            } as unknown as StreamEvent);
            capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'write_file' } as unknown as StreamEvent);
        });
        expect(useChatStore.getState().liveStatus).toBe('Writing file');
        await runningRow('Writing file');
    });
});

describe('one progress readout per turn', () => {
    it("keeps the backend's silent-wait tick out of the bubble", async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'working', message: 'Model is processing... (15s)',
            } as unknown as StreamEvent);
        });

        await liveLine('Thinking...');
        expect(useChatStore.getState().agentSteps).toEqual([]);
        expect(screen.queryByText(/Model is processing/)).toBeNull();
        expect(document.querySelector('.agent-timeline')).toBeNull();
    });

    it("does not relabel the model's reasoning with the tick", async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({ type: 'thinking', content: 'Let me look.' } as unknown as StreamEvent);
            capturedCallbacks!.onAgentEvent({
                type: 'status', status: 'working', message: 'Model is processing... (15s)',
            } as unknown as StreamEvent);
        });

        expect(useChatStore.getState().agentSteps.map((s) => s.label)).toEqual(['Thinking']);
    });

    it('names a running tool on its own row, once', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'tool_start', tool: 'read_file', narration: 'Reading file: notes.txt',
            } as unknown as StreamEvent);
        });

        await runningRow('Reading file: notes.txt');
    });

    it('stops naming a call once it finishes, even when no new phase follows', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'tool_start', tool: 'web_search', narration: 'Searching web: portland weather',
            } as unknown as StreamEvent);
            capturedCallbacks!.onAgentEvent({ type: 'tool_end', success: true } as unknown as StreamEvent);
        });

        await liveLine('Thinking...');
    });

    it('takes a narration that arrives with the arguments', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'read_file' } as unknown as StreamEvent);
            capturedCallbacks!.onAgentEvent({
                type: 'tool_args', tool: 'read_file', args: { file_path: 'notes.txt' }, narration: 'Reading file: notes.txt',
            } as unknown as StreamEvent);
        });

        await runningRow('Reading file: notes.txt');
    });
});

describe('a tool called with no arguments', () => {
    const lastToolStep = () => {
        const tools = useChatStore.getState().agentSteps.filter((s) => s.type === 'tool');
        return tools[tools.length - 1];
    };

    it('shows as running from its start, and empty arguments keep its label', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({
                type: 'tool_start', tool: 'list_skills', detail: 'Listing your skills',
            } as unknown as StreamEvent);
            capturedCallbacks!.onAgentEvent({
                type: 'tool_args', tool: 'list_skills', args: {}, detail: '',
            } as unknown as StreamEvent);
        });

        const step = lastToolStep();
        expect(step?.tool).toBe('list_skills');
        expect(step?.active).toBe(true);
        expect(step?.detail).toBe('Listing your skills');
        expect(useChatStore.getState().livePhase).toBe('tool');
    });

    it('never shows "{}" as the detail when no label was sent either', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'list_skills' } as unknown as StreamEvent);
            capturedCallbacks!.onAgentEvent({ type: 'tool_args', tool: 'list_skills', args: {} } as unknown as StreamEvent);
        });

        expect(lastToolStep()?.detail).not.toBe('{}');
    });

    it('still shows real arguments when no formatted detail came with them', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onAgentEvent({ type: 'tool_start', tool: 'read_file' } as unknown as StreamEvent);
            capturedCallbacks!.onAgentEvent({
                type: 'tool_args', tool: 'read_file', args: { file_path: 'a.txt' },
            } as unknown as StreamEvent);
        });

        expect(lastToolStep()?.detail).toBe('{"file_path":"a.txt"}');
    });
});

describe('opening a chat', () => {
    it("keeps a message sent before the chat's history arrives", async () => {
        let deliver!: (v: { messages: never[]; total: number }) => void;
        mockedApi.getMessages.mockReturnValueOnce(new Promise((r) => { deliver = r; }) as never);
        await driveSend();
        const userBubbles = () => useChatStore.getState().messages.filter((m) => m.role === 'user').map((m) => m.content);
        expect(userBubbles()).toEqual(['send the drafted email']);

        // The history load started before the send lands late, without that message.
        mockedApi.getMessages.mockReturnValue(new Promise(() => {}));
        await act(async () => { deliver({ messages: [], total: 0 }); });

        expect(userBubbles()).toEqual(['send the drafted email']);
    });
});

describe('mid-turn notes', () => {
    const emit = (e: object) => capturedCallbacks!.onAgentEvent(e as unknown as StreamEvent);

    it('moves text written before a tool call out of the answer and into the timeline', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onChunk({ type: 'chunk', content: "I'll check the forecast first." } as StreamEvent);
            emit({ type: 'commentary', content: "I'll check the forecast first." });
            emit({ type: 'tool_start', tool: 'fetch_page', narration: 'Reading web page' });
            emit({ type: 'tool_end', success: true });
        });
        act(() => {
            capturedCallbacks!.onChunk({ type: 'chunk', content: 'Clear Saturday.' } as StreamEvent);
        });

        await waitFor(() => expect(document.querySelector('.flow-note')).toHaveTextContent("I'll check the forecast first."));
        await waitFor(() => expect(useChatStore.getState().streamingContent).toBe('Clear Saturday.'));
        expect(screen.getAllByText("I'll check the forecast first.")).toHaveLength(1);
    });

    it('moves reasoning a lone </think> closed out of the answer', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onChunk({ type: 'chunk', content: 'Check the forecast first.' } as StreamEvent);
            emit({ type: 'thinking', content: 'Check the forecast first.', retracts_answer: true });
        });
        act(() => {
            capturedCallbacks!.onChunk({ type: 'chunk', content: 'Clear Saturday.' } as StreamEvent);
        });

        await waitFor(() => expect(useChatStore.getState().streamingContent).toBe('Clear Saturday.'));
        const thought = useChatStore.getState().agentSteps[0];
        expect(thought.type).toBe('thinking');
        expect(thought.detail).toBe('Check the forecast first.');
    });

    it('takes back a reply the model server cut off and says it restarts', async () => {
        await driveSend();
        act(() => {
            capturedCallbacks!.onChunk({ type: 'chunk', content: 'The ledger bal' } as StreamEvent);
        });
        await waitFor(() => expect(useChatStore.getState().streamingContent).toBe('The ledger bal'));
        act(() => {
            emit({ type: 'status', phase: 'answer_restart', message: 'cut off', retracts_answer: true });
            emit({ type: 'status', phase: 'model_restart', message: 'The model server stopped. GAIA is restarting it…' });
        });

        await waitFor(() => expect(useChatStore.getState().streamingContent).toBe(''));
        expect(document.querySelector('.flow-warning')).toHaveTextContent(
            'The answer so far was cut off, so GAIA is starting it again from the top.',
        );
        expect(useChatStore.getState().liveStatus).toBe('The model server stopped. GAIA is restarting it…');
        await waitFor(() => expect(screen.getByText(/The model server stopped\. GAIA is restarting it/)).toBeInTheDocument());

        act(() => {
            capturedCallbacks!.onChunk({ type: 'chunk', content: 'The ledger balances.' } as StreamEvent);
        });
        await waitFor(() => expect(useChatStore.getState().streamingContent).toBe('The ledger balances.'));
        expect(screen.queryByText('The ledger bal')).not.toBeInTheDocument();
    });

    it('ends the reasoning once the answer starts streaming', async () => {
        await driveSend();
        act(() => {
            emit({ type: 'thinking', content: 'Weighing it up.' });
        });
        act(() => {
            capturedCallbacks!.onChunk({ type: 'chunk', content: 'Clear Saturday.' } as StreamEvent);
        });

        const thought = useChatStore.getState().agentSteps[0];
        expect(thought.active).toBe(false);
        expect(thought.latencyMs).toBeGreaterThanOrEqual(0);
    });

    it('counts a load_skill call as a skill use, so the Skills panel re-reads mid-reply', async () => {
        useChatStore.setState({ skillUses: 0 });
        await driveSend();
        act(() => {
            emit({ type: 'tool_start', tool: 'load_skill', narration: 'Loading skill: file-ops' });
        });
        expect(useChatStore.getState().skillUses).toBe(1);

        act(() => {
            emit({ type: 'tool_start', tool: 'read_file', narration: 'Reading notes.md' });
        });
        expect(useChatStore.getState().skillUses).toBe(1);
    });
});
