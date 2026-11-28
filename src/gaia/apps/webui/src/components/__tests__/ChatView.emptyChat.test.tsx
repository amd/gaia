// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatView } from '../ChatView';
import { useChatStore } from '../../stores/chatStore';
import type { Session, SystemStatus } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

const SESSION: Session = {
    id: 'session-1', title: 'Weekend plans', created_at: '2026-06-10T00:00:00Z', updated_at: '2026-06-10T00:00:00Z',
    model: 'qwen', system_prompt: null, message_count: 0, document_ids: [], agent_type: 'gaia',
};
const READY = { lemonade_running: true, initialized: true, init_state: 'ready', init_tasks: [] } as unknown as SystemStatus;

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getMessages.mockResolvedValue({ messages: [], total: 0 });
    mockedApi.getMessageCount.mockResolvedValue(0);
    mockedApi.getActiveRuns.mockResolvedValue({ session_ids: [], queued: [], awaiting_user: {} });
    mockedApi.getContextUsage.mockResolvedValue({ available: false, reason: 'No answer yet.' });
    mockedApi.getPermissions.mockResolvedValue({ session_id: 'session', mode: 'ask', grants: [] });
    mockedApi.listDocuments.mockResolvedValue({ documents: [], total: 0, total_size_bytes: 0, total_chunks: 0 });
    mockedApi.getSystemStatus.mockResolvedValue(READY);
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() });
    useChatStore.setState({
        sessions: [SESSION], currentSessionId: SESSION.id, messages: [], documents: [],
        isStreaming: false, streamingContent: '', agentSteps: [], cards: [], isLoadingMessages: false,
        pendingPrompt: null, systemStatus: READY, messageQueues: {}, pendingDeleteIds: [],
    });
});

describe('a chat with no messages', () => {
    it('says so once its history has loaded, instead of a blank page', async () => {
        render(<ChatView sessionId={SESSION.id} />);

        expect(await screen.findByRole('heading', { name: 'This chat is empty' })).toBeInTheDocument();
        expect(screen.getByText('Send a message below to start it.')).toBeInTheDocument();
    });

    it('does not show the empty state while the history is still loading', () => {
        mockedApi.getMessages.mockImplementation(() => new Promise(() => {}));
        render(<ChatView sessionId={SESSION.id} />);

        expect(screen.queryByText('This chat is empty')).toBeNull();
    });
});
