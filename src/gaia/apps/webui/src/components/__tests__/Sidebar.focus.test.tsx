// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from '../Sidebar';
import { useChatStore } from '../../stores/chatStore';
import { useModelStore } from '../../stores/modelStore';
import * as api from '../../services/api';
import type { Session } from '../../types';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);
const NOW = new Date().toISOString();

function chat(id: string, title: string): Session {
    return {
        id, title, created_at: NOW, updated_at: NOW,
        model: '', system_prompt: null, message_count: 2, document_ids: [], inference_remote: false,
    };
}

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.updateSession.mockResolvedValue(chat('a', 'x'));
    mockedApi.pinSession.mockResolvedValue(undefined as never);
    useModelStore.setState({
        active: { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true },
    });
    useChatStore.setState({
        sessions: [chat('a', 'Dinner ideas'), chat('b', 'Trip to Lisbon')],
        currentSessionId: null, runningSessionIds: [], pendingDeleteIds: [],
        sidebarOpen: true, sidebarCollapsed: false, messageQueues: {},
    });
});

describe('Sidebar keyboard focus', () => {
    it('returns focus to the chat after renaming it with Enter or Escape', async () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        const rename = screen.getByRole('button', { name: 'Rename Trip to Lisbon' });
        rename.focus();
        fireEvent.click(rename);
        const input = screen.getByRole('textbox', { name: 'Rename Trip to Lisbon' });
        fireEvent.change(input, { target: { value: 'Lisbon trip' } });
        // The Enter must not also press the chat button that takes focus.
        expect(fireEvent.keyDown(input, { key: 'Enter' })).toBe(false);
        await waitFor(() => expect(screen.getByRole('button', { name: 'Lisbon trip' })).toHaveFocus());

        fireEvent.click(screen.getByRole('button', { name: 'Rename Lisbon trip' }));
        fireEvent.keyDown(screen.getByRole('textbox', { name: 'Rename Lisbon trip' }), { key: 'Escape' });
        await waitFor(() => expect(screen.getByRole('button', { name: 'Lisbon trip' })).toHaveFocus());
    });

    it('keeps focus on the pin button when pinning moves the chat to another group', async () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: 'Pin Dinner ideas' }));
        await waitFor(() => expect(screen.getByRole('button', { name: 'Unpin Dinner ideas' })).toHaveFocus());
    });
});
