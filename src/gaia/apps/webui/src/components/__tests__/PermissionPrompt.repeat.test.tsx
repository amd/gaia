// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * One task asked "Allow GAIA to use run_python?" nine times in one turn while
 * "Always allow" sat unnoticed beside "Allow once". From the second identical
 * prompt the card points at it. It never answers for the user, and a different
 * tool, scope, or risk gets no nudge.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionPrompt } from '../PermissionPrompt';
import { ChatView } from '../ChatView';
import { repeatedAllowCount, useNotificationStore } from '../../stores/notificationStore';
import { useChatStore } from '../../stores/chatStore';
import type { GaiaNotification } from '../../types/agent';
import type { Session, StreamEvent } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');
const mockedApi = vi.mocked(api);

const originalRespond = useNotificationStore.getState().respondToPermission;
const respond = vi.fn();

const PY_SCOPE = 'Python snippets (run_python)';

function prompt(over: Partial<GaiaNotification> = {}): GaiaNotification {
    return {
        id: `perm-${Math.random()}`, type: 'permission_request', agentId: 'chat-1', sessionId: 'chat-1',
        agentName: 'GAIA', title: 'Allow run_python?', message: '', timestamp: 1,
        read: false, dismissed: false, priority: 'high', tool: 'run_python',
        toolArgs: { code: 'print(1)' }, alwaysScope: PY_SCOPE, risk: 'execute', ...over,
    };
}

/** Earlier prompts first, the live one last (the store keeps newest first). */
function seed(earlier: GaiaNotification[], live: GaiaNotification) {
    useNotificationStore.setState({ notifications: [live, ...earlier] });
    render(<PermissionPrompt sessionId="chat-1" />);
}

const hint = () => document.querySelector('.perm-card-hint');
const alwaysButton = () => screen.queryByRole('button', { name: /Always allow/ });

describe('repeated identical permission prompts', () => {
    beforeEach(() => {
        respond.mockReset().mockResolvedValue(undefined);
        useNotificationStore.setState({ notifications: [], respondToPermission: respond });
    });
    afterEach(() => {
        useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond });
    });

    it('does not nudge on the first prompt', () => {
        seed([], prompt());
        expect(hint()).toBeNull();
        expect(alwaysButton()).not.toHaveClass('is-suggested');
    });

    it('points at always-allow from the second identical prompt', () => {
        seed([prompt({ response: 'allow' })], prompt());
        expect(hint()).toHaveTextContent("You've allowed this once already.");
        expect(hint()).toHaveTextContent(PY_SCOPE);
        expect(alwaysButton()).toHaveClass('is-suggested');
        // Allow once stays the primary choice; nothing is decided for the user.
        expect(screen.getByRole('button', { name: 'Allow once' })).toHaveClass('is-primary');
        expect(respond).not.toHaveBeenCalled();
    });

    it('counts every earlier allow', () => {
        const earlier = Array.from({ length: 8 }, () => prompt({ response: 'allow' }));
        seed(earlier, prompt());
        expect(hint()).toHaveTextContent("You've allowed this 8 times already.");
    });

    it('answers always only when the user clicks it', async () => {
        const live = prompt();
        seed([prompt({ response: 'allow' })], live);
        fireEvent.click(alwaysButton()!);
        await waitFor(() => expect(respond).toHaveBeenCalledExactlyOnceWith(live.id, 'always'));
    });

    it.each([
        ['a different tool', prompt({ tool: 'write_file', alwaysScope: 'write_file on a.md', risk: 'write', response: 'allow' })],
        ['a different scope of the same tool', prompt({ tool: 'run_shell_command', alwaysScope: 'git status', response: 'allow' })],
        ['a lower-risk call of the same scope', prompt({ risk: 'read', response: 'allow' })],
        ['a denied prompt', prompt({ response: 'deny' })],
        ['an unanswered prompt', prompt()],
        ['another chat', prompt({ sessionId: 'chat-2', response: 'allow' })],
    ])('does not nudge after %s', (_label, earlier) => {
        seed([earlier], prompt());
        expect(hint()).toBeNull();
        expect(alwaysButton()).not.toHaveClass('is-suggested');
        expect(screen.getByRole('button', { name: 'Allow once' })).toBeInTheDocument();
    });

    it('still asks a different tool even after many allows of another', () => {
        const earlier = Array.from({ length: 5 }, () => prompt({ response: 'allow' }));
        seed(earlier, prompt({ tool: 'run_shell_command', alwaysScope: 'git status', risk: 'read', toolArgs: { command: 'git status' } }));
        expect(screen.getByRole('alertdialog')).toHaveTextContent('Allow GAIA to use run_shell_command?');
        expect(hint()).toBeNull();
        expect(respond).not.toHaveBeenCalled();
    });

    it('never nudges toward a call that offers no always (e.g. a delete)', () => {
        const earlier = Array.from({ length: 3 }, () => prompt({ tool: 'run_shell_command', alwaysScope: undefined, risk: 'destructive', response: 'allow' }));
        seed(earlier, prompt({ tool: 'run_shell_command', alwaysScope: undefined, risk: 'destructive', toolArgs: { command: 'rm -rf build' } }));
        expect(alwaysButton()).toBeNull();
        expect(hint()).toBeNull();
    });

    it('never counts a destructive call even if it came with a scope', () => {
        const live = prompt({ risk: 'destructive' });
        expect(repeatedAllowCount([prompt({ risk: 'destructive', response: 'allow' }), live], live)).toBe(0);
    });

    it('never nudges a fresh-consent tool', () => {
        const live = prompt({ tool: 'allow_path_access', alwaysScope: 'x' });
        expect(repeatedAllowCount([prompt({ tool: 'allow_path_access', alwaysScope: 'x', response: 'allow' }), live], live)).toBe(0);
    });
});

describe('ChatView carries the call risk into the prompt', () => {
    const SESSION: Session = {
        id: 'chat-1', title: 'Existing chat', created_at: '', updated_at: '', model: 'qwen',
        system_prompt: null, message_count: 0, document_ids: [], agent_type: 'gaia',
    };
    let callbacks: api.StreamCallbacks | null = null;

    beforeEach(() => {
        vi.clearAllMocks();
        useNotificationStore.setState({ notifications: [], respondToPermission: originalRespond });
        mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
        mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
        mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
        mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
        mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
        mockedApi.confirmTool.mockResolvedValue(undefined as never);
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

    it('nudges the second run_python prompt of a turn, not a write_file one after it', async () => {
        render(<ChatView sessionId={SESSION.id} />);
        await act(async () => {
            fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'make the invoices' } });
            fireEvent.click(screen.getByLabelText('Send'));
        });
        const ask = (id: string, tool: string, scope: string, risk: string) => act(() => {
            callbacks!.onAgentEvent({
                type: 'permission_request', tool, args: { code: 'x' }, confirm_id: id, always_scope: scope, risk,
            } as unknown as StreamEvent);
        });

        ask('c1', 'run_python', PY_SCOPE, 'execute');
        expect(useNotificationStore.getState().notifications[0].risk).toBe('execute');
        await act(async () => { fireEvent.click(await screen.findByRole('button', { name: 'Allow once' })); });
        await waitFor(() => expect(mockedApi.confirmTool).toHaveBeenCalledTimes(1));

        ask('c2', 'run_python', PY_SCOPE, 'execute');
        await waitFor(() => expect(hint()).toHaveTextContent("You've allowed this once already."));
        await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Allow once' })); });
        await waitFor(() => expect(mockedApi.confirmTool).toHaveBeenCalledTimes(2));

        ask('c3', 'write_file', 'write_file on a.md', 'write');
        await waitFor(() => expect(screen.getByRole('alertdialog')).toHaveTextContent('write_file'));
        expect(hint()).toBeNull();
    });
});
