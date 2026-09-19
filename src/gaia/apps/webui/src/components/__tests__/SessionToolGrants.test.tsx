// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { SessionToolGrants } from '../SessionToolGrants';
import { useNotificationStore } from '../../stores/notificationStore';
import { useChatStore } from '../../stores/chatStore';
import type { Session } from '../../types';

const chat = (id: string, title: string) =>
    ({ id, title, created_at: '', updated_at: '', model: 'qwen', system_prompt: null,
       message_count: 0, document_ids: [], agent_type: 'doc' }) as Session;

beforeEach(() => {
    useChatStore.setState({ sessions: [chat('chat-A', 'Quarterly report'), chat('chat-B', 'Scratch')] });
    useNotificationStore.setState({ alwaysAllowGrants: [] });
});

describe('SessionToolGrants', () => {
    it('says nothing is auto-approved when there are no grants', () => {
        render(<SessionToolGrants />);
        expect(screen.getByText(/No tools are auto-approved/)).toBeTruthy();
        expect(screen.queryByText('Revoke All')).toBeNull();
    });

    it('lists each grant with the chat it belongs to', () => {
        useNotificationStore.setState({
            alwaysAllowGrants: [
                { sessionId: 'chat-A', tool: 'run_shell_command', grantedAt: 1 },
                { sessionId: 'chat-B', tool: 'web_search', grantedAt: 2 },
            ],
        });
        render(<SessionToolGrants />);
        expect(screen.getByLabelText('Revoke run_shell_command in Quarterly report')).toBeTruthy();
        expect(screen.getByLabelText('Revoke web_search in Scratch')).toBeTruthy();
    });

    it('Revoke removes that grant from the store and the list', () => {
        useNotificationStore.setState({
            alwaysAllowGrants: [
                { sessionId: 'chat-A', tool: 'run_shell_command', grantedAt: 1 },
                { sessionId: 'chat-B', tool: 'web_search', grantedAt: 2 },
            ],
        });
        render(<SessionToolGrants />);

        fireEvent.click(screen.getByLabelText('Revoke run_shell_command in Quarterly report'));

        expect(useNotificationStore.getState().isAlwaysAllowed('chat-A', 'run_shell_command')).toBe(false);
        expect(useNotificationStore.getState().isAlwaysAllowed('chat-B', 'web_search')).toBe(true);
        expect(screen.queryByLabelText('Revoke run_shell_command in Quarterly report')).toBeNull();
    });

    it('Revoke All clears every grant', () => {
        useNotificationStore.setState({
            alwaysAllowGrants: [{ sessionId: 'chat-A', tool: 'run_shell_command', grantedAt: 1 }],
        });
        render(<SessionToolGrants />);
        fireEvent.click(screen.getByText('Revoke All'));
        expect(useNotificationStore.getState().alwaysAllowGrants).toEqual([]);
        expect(screen.getByText(/No tools are auto-approved/)).toBeTruthy();
    });
});
