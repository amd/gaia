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

function chat(id: string, title: string): Session {
    const now = new Date().toISOString();
    return {
        id, title, created_at: now, updated_at: now,
        model: '', system_prompt: null, message_count: 2, document_ids: [], inference_remote: false,
    };
}

const titles = () => useChatStore.getState().sessions.map((s) => s.title);
const renameButtons = () => screen.getAllByRole('button', { name: 'Rename How rainbows form' });

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.updateSession.mockResolvedValue(chat('a', 'x'));
    useModelStore.setState({
        active: { provider: 'local', model: 'Gemma-4-E4B-it-GGUF', label: 'Gemma', remote: false, is_default: true },
    });
    useChatStore.setState({
        sessions: [chat('a', 'How rainbows form'), chat('b', 'How rainbows form')],
        currentSessionId: null, runningSessionIds: [], pendingDeleteIds: [],
        sidebarOpen: true, sidebarCollapsed: false, messageQueues: {},
    });
});

function startRename() {
    render(<Sidebar onNewChat={vi.fn()} />);
    fireEvent.click(renameButtons()[1]);
    return screen.getByRole('textbox', { name: 'Rename How rainbows form' });
}

describe('Sidebar rename', () => {
    it('renames from the row button and pins the title against the auto-retitler', async () => {
        const input = startRename();
        fireEvent.change(input, { target: { value: '  Rainbow physics  ' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        await waitFor(() => expect(mockedApi.updateSession).toHaveBeenCalledExactlyOnceWith(
            'b', { title: 'Rainbow physics', title_is_custom: true },
        ));
        expect(titles()).toEqual(['How rainbows form', 'Rainbow physics']);
        expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    });

    it('renames on double-click of the title', async () => {
        render(<Sidebar onNewChat={vi.fn()} />);
        fireEvent.doubleClick(screen.getAllByText('How rainbows form')[0]);
        const input = screen.getByRole('textbox');
        fireEvent.change(input, { target: { value: 'First' } });
        fireEvent.blur(input);
        await waitFor(() => expect(mockedApi.updateSession).toHaveBeenCalledExactlyOnceWith(
            'a', { title: 'First', title_is_custom: true },
        ));
    });

    it('saves nothing on Escape, an unchanged title, or an empty one', () => {
        let input = startRename();
        fireEvent.change(input, { target: { value: 'Changed' } });
        fireEvent.keyDown(input, { key: 'Escape' });
        expect(titles()).toEqual(['How rainbows form', 'How rainbows form']);

        fireEvent.click(renameButtons()[1]);
        fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });

        fireEvent.click(renameButtons()[1]);
        input = screen.getByRole('textbox');
        fireEvent.change(input, { target: { value: '   ' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        expect(mockedApi.updateSession).not.toHaveBeenCalled();
    });

    it('puts the old title back and says so when the save fails', async () => {
        mockedApi.updateSession.mockRejectedValue(new Error('Database is locked'));
        const input = startRename();
        fireEvent.change(input, { target: { value: 'Nope' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        expect(await screen.findByRole('alert')).toHaveTextContent('Database is locked');
        expect(titles()).toEqual(['How rainbows form', 'How rainbows form']);
    });
});
