// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The policy-alert flow in the live DOM (#986): a `policy_alert` stream event
 * shows a toast naming the tool, "View receipt" opens the notification panel
 * filtered to policy alerts, and the receipt's entry is scrolled into view.
 * Also covers a second alert replacing the first without a stale timer, the
 * toast going away on its own and on a chat switch, and a persisted block
 * rendering inline after reload.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { NotificationCenter } from '../NotificationCenter';
import { useChatStore } from '../../stores/chatStore';
import { useNotificationStore } from '../../stores/notificationStore';
import { POLICY_TOAST_DURATION_MS } from '../../hooks/usePolicyToast';
import type { Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1',
    title: 'Existing chat',
    created_at: '2026-06-10T00:00:00Z',
    updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
    agent_type: 'gaia',
};

function policyAlert(tool: string, receiptId?: string): StreamEvent {
    return {
        type: 'policy_alert',
        tool,
        decision: 'BLOCK',
        reason: `${tool} is not allowed here.`,
        rule_ids: ['no-shell'],
        policy_version: 'v7',
        receipt_id: receiptId,
    } as unknown as StreamEvent;
}

let callbacks: api.StreamCallbacks | null = null;
let scrollIntoView: ReturnType<typeof vi.fn>;

/** ChatView plus the notification panel, mounted the way App mounts it. */
function Harness({ sessionId = SESSION.id }: { sessionId?: string }) {
    const showPanel = useNotificationStore((s) => s.showPanel);
    const setShowPanel = useNotificationStore((s) => s.setShowPanel);
    return (
        <>
            <ChatView sessionId={sessionId} />
            {showPanel && <NotificationCenter onClose={() => setShowPanel(false)} />}
        </>
    );
}

async function send() {
    await act(async () => {
        fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'run it' } });
        fireEvent.click(screen.getByLabelText('Send'));
    });
    expect(callbacks).not.toBeNull();
}

function emit(event: StreamEvent) {
    act(() => {
        callbacks!.onAgentEvent(event);
    });
}

function toast() {
    return document.querySelector('.policy-alert-toast') as HTMLElement | null;
}

beforeEach(() => {
    vi.clearAllMocks();
    callbacks = null;

    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.cancelStream.mockResolvedValue(undefined as never);
    mockedApi.updateSession.mockResolvedValue(undefined as never);
    mockedApi.sendMessageStream.mockImplementation((_sid, _msg, cbs) => {
        callbacks = cbs as api.StreamCallbacks;
        return new AbortController();
    });

    scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: scrollIntoView });

    useChatStore.setState({
        sessions: [SESSION, { ...SESSION, id: 'session-2', title: 'Other chat' }],
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
    useNotificationStore.setState({ notifications: [], showPanel: false, typeFilter: null });
});

afterEach(() => {
    vi.useRealTimers();
});

describe('policy alert: toast → View receipt → scroll to the entry', () => {
    it('shows a toast naming the blocked tool and records a notification', async () => {
        render(<Harness />);
        await send();
        emit(policyAlert('run_shell_command', 'rcpt-1'));

        const shown = toast();
        expect(shown).not.toBeNull();
        expect(shown).toHaveAttribute('role', 'alert');
        expect(within(shown!).getByText('Blocked: run_shell_command is restricted by policy.')).toBeInTheDocument();
        expect(within(shown!).getByRole('button', { name: 'View receipt' })).toBeInTheDocument();

        const [n] = useNotificationStore.getState().notifications;
        expect(n).toMatchObject({
            id: 'rcpt-1',
            type: 'policy_alert',
            tool: 'run_shell_command',
            receiptId: 'rcpt-1',
            ruleIds: ['no-shell'],
            policyVersion: 'v7',
            agentId: SESSION.id,
        });
        // The panel stays shut until the user asks for the receipt.
        expect(document.querySelector('.notification-center')).toBeNull();
    });

    it('opens the panel on the policy filter and scrolls the receipt into view', async () => {
        render(<Harness />);
        await send();
        emit(policyAlert('run_shell_command', 'rcpt/1 a'));

        fireEvent.click(within(toast()!).getByRole('button', { name: 'View receipt' }));

        const state = useNotificationStore.getState();
        expect(state.showPanel).toBe(true);
        expect(state.typeFilter).toBe('policy_alert');

        // Receipt ids are URL-encoded into the anchor, so a slash or space can't break it.
        const entry = await waitFor(() => {
            const el = document.getElementById(`policy-receipt-${encodeURIComponent('rcpt/1 a')}`);
            expect(el).not.toBeNull();
            return el!;
        });
        expect(within(entry).getByText('rcpt/1 a')).toBeInTheDocument();
        expect(within(entry).getByText('Reason: run_shell_command is not allowed here.')).toBeInTheDocument();
        expect(within(entry).getByText('no-shell')).toBeInTheDocument();
        expect(within(entry).getByText('v7')).toBeInTheDocument();
        // A block is a record, not a prompt: no approve/deny on it.
        expect(within(entry).queryByRole('button', { name: /Approve|Deny/ })).toBeNull();
        await waitFor(() => {
            const calls = scrollIntoView.mock.contexts as HTMLElement[];
            expect(calls).toContain(entry);
        });
        const call = scrollIntoView.mock.calls[scrollIntoView.mock.contexts.indexOf(entry)];
        expect(call[0]).toMatchObject({ block: 'center' });
    });

    it('scrolls to the receipt of the toast clicked, not an older one', async () => {
        render(<Harness />);
        await send();
        emit(policyAlert('write_file', 'rcpt-old'));
        emit(policyAlert('run_shell_command', 'rcpt-new'));

        fireEvent.click(within(toast()!).getByRole('button', { name: 'View receipt' }));

        await waitFor(() => {
            const target = document.getElementById('policy-receipt-rcpt-new');
            expect(scrollIntoView.mock.contexts).toContain(target);
        });
        const old = document.getElementById('policy-receipt-rcpt-old');
        expect(scrollIntoView.mock.contexts).not.toContain(old);
    });

    it('offers no receipt link when the alert carried no receipt id', async () => {
        render(<Harness />);
        await send();
        emit(policyAlert('run_shell_command'));

        expect(within(toast()!).queryByRole('button', { name: 'View receipt' })).toBeNull();
        expect(within(toast()!).getByText('Receipt unavailable')).toBeInTheDocument();
    });
});

describe('policy alert toast lifetime', () => {
    it('a second alert replaces the first and the first timer cannot hide it early', async () => {
        render(<Harness />);
        await send();
        vi.useFakeTimers();

        emit(policyAlert('write_file', 'rcpt-a'));
        act(() => {
            vi.advanceTimersByTime(POLICY_TOAST_DURATION_MS - 1000);
        });
        emit(policyAlert('run_shell_command', 'rcpt-b'));

        expect(document.querySelectorAll('.policy-alert-toast')).toHaveLength(1);
        expect(toast()).toHaveTextContent('Blocked: run_shell_command is restricted by policy.');

        // Past the first toast's deadline: the second must still be up.
        act(() => {
            vi.advanceTimersByTime(1500);
        });
        expect(toast()).toHaveTextContent('run_shell_command');

        act(() => {
            vi.advanceTimersByTime(POLICY_TOAST_DURATION_MS);
        });
        expect(toast()).toBeNull();
        // Both blocks stay in the notification history.
        expect(useNotificationStore.getState().notifications.map((n) => n.receiptId).sort()).toEqual(['rcpt-a', 'rcpt-b']);
    });

    it('goes away on its own after the toast duration', async () => {
        render(<Harness />);
        await send();
        vi.useFakeTimers();
        emit(policyAlert('run_shell_command', 'rcpt-1'));

        act(() => {
            vi.advanceTimersByTime(POLICY_TOAST_DURATION_MS - 1);
        });
        expect(toast()).not.toBeNull();
        act(() => {
            vi.advanceTimersByTime(1);
        });
        expect(toast()).toBeNull();
    });

    it('does not follow the user to another chat', async () => {
        const { rerender } = render(<Harness />);
        await send();
        emit(policyAlert('run_shell_command', 'rcpt-1'));
        expect(toast()).not.toBeNull();

        await act(async () => {
            useChatStore.setState({ currentSessionId: 'session-2' });
            rerender(<Harness sessionId="session-2" />);
        });
        expect(toast()).toBeNull();
    });
});

describe('a persisted policy block after reload', () => {
    it('renders the Policy Shield step inline with its receipt', async () => {
        const question = {
            id: 1, session_id: SESSION.id, role: 'user', content: 'Delete the logs',
            created_at: '2026-06-10T00:00:01Z', rag_sources: null,
        };
        const reply = {
            id: 2, session_id: SESSION.id, role: 'assistant', content: 'That command is blocked here.',
            created_at: '2026-06-10T00:00:02Z', rag_sources: null,
            agent_steps: [{
                id: 1, type: 'policy_alert', label: 'Policy blocked run_shell_command',
                tool: 'run_shell_command', decision: 'BLOCK', reason: 'Shell is disabled by policy.',
                ruleIds: ['no-shell'], policyVersion: 'v7', receiptId: 'rcpt-saved',
                active: false, success: false, timestamp: 1,
            }],
        };
        mockedApi.getMessages.mockResolvedValue({ messages: [question, reply] as never, total: 2 });

        render(<Harness />);

        const card = await waitFor(() => {
            const el = document.querySelector('.flow-policy-alert') as HTMLElement | null;
            expect(el).not.toBeNull();
            return el!;
        });
        expect(within(card).getByText('Policy Shield')).toBeInTheDocument();
        expect(within(card).getByText('BLOCK')).toBeInTheDocument();
        expect(within(card).getByText('run_shell_command')).toBeInTheDocument();
        expect(within(card).getByText('Shell is disabled by policy.')).toBeInTheDocument();
        expect(within(card).getByText('no-shell')).toBeInTheDocument();
        expect(within(card).getByText('rcpt-saved')).toBeInTheDocument();
        // A reload replays history; it must not re-raise the toast.
        expect(toast()).toBeNull();
    });

    it('says the receipt is unavailable when the saved block has none', async () => {
        const reply = {
            id: 2, session_id: SESSION.id, role: 'assistant', content: 'Blocked.',
            created_at: '2026-06-10T00:00:02Z', rag_sources: null,
            agent_steps: [{
                id: 1, type: 'policy_alert', label: 'Policy blocked write_file',
                tool: 'write_file', decision: 'BLOCK', reason: 'Writes are disabled.',
                ruleIds: [], active: false, success: false, timestamp: 1,
            }],
        };
        mockedApi.getMessages.mockResolvedValue({ messages: [reply] as never, total: 1 });

        render(<Harness />);

        const card = await waitFor(() => {
            const el = document.querySelector('.flow-policy-alert') as HTMLElement | null;
            expect(el).not.toBeNull();
            return el!;
        });
        expect(within(card).getByText('Receipt unavailable')).toBeInTheDocument();
    });
});
