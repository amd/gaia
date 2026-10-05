// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function session(agent_type: string): Session {
    return {
        id: 'session-1',
        title: 'Old chat',
        created_at: '2026-06-10T00:00:00Z',
        updated_at: '2026-06-10T00:00:00Z',
        model: 'qwen',
        system_prompt: null,
        message_count: 0,
        document_ids: [],
        agent_type,
    };
}

function show(s: Session) {
    useChatStore.setState({
        sessions: [s],
        currentSessionId: s.id,
        messages: [],
        documents: [],
        isStreaming: false,
        streamingContent: '',
        agentSteps: [],
        isLoadingMessages: false,
        pendingPrompt: null,
        systemStatus: null,
    });
    render(<ChatView sessionId={s.id} />);
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [] });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session-1', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({
        documents: [],
        total: 0,
        total_size_bytes: 0,
        total_chunks: 0,
    });
});

describe('ChatView and the flagship agent', () => {
    it('lets a flagship chat be continued', async () => {
        show(session('gaia'));
        await waitFor(() => expect(mockedApi.getPermissions).toHaveBeenCalledWith('session-1'));
        expect(screen.getByLabelText('Message')).toBeEnabled();
        expect(screen.getByRole('button', { name: /Permission mode/ })).toBeEnabled();
    });

    it.each(['chat', 'doc', 'email'])('makes a chat from the retired "%s" agent read-only', async (agent) => {
        show(session(agent));
        const box = screen.getByLabelText('Message');
        expect(box).toBeDisabled();
        expect(box).toHaveAttribute('placeholder', expect.stringContaining(`retired "${agent}" agent`));
        expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
        expect(screen.getByRole('button', { name: /Permission mode/ })).toBeDisabled();
    });
});
