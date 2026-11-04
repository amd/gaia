// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PrivacySettings } from '../PrivacySettings';
import { useChatStore } from '../../../stores/chatStore';
import * as api from '../../../services/api';
import type { Session } from '../../../types';

vi.mock('../../../services/api');
vi.mock('../UndoHistorySettings', () => ({ UndoHistorySettings: () => null }));

const mockedApi = vi.mocked(api);

function chat(id: string, title: string): Session {
    return {
        id, title, created_at: '', updated_at: '', model: '',
        system_prompt: null, message_count: 1, document_ids: [],
    };
}

const TRIP = chat('trip', 'Trip plan');
const TAXES = chat('taxes', 'Taxes');

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.deleteSession.mockResolvedValue(undefined);
    useChatStore.setState({ sessions: [TRIP, TAXES] });
});

afterEach(() => {
    vi.useRealTimers();
});

describe('PrivacySettings delete all chats', () => {
    it('deletes nothing on the first click and asks for a second', () => {
        render(<PrivacySettings />);
        fireEvent.click(screen.getByRole('button', { name: 'Delete all chats' }));
        expect(mockedApi.deleteSession).not.toHaveBeenCalled();
        expect(screen.getByRole('button', { name: 'Click again to delete all chats' })).toBeInTheDocument();
        expect(useChatStore.getState().sessions).toEqual([TRIP, TAXES]);
    });

    it('deletes every chat on the second click and says so', async () => {
        render(<PrivacySettings />);
        fireEvent.click(screen.getByRole('button', { name: 'Delete all chats' }));
        fireEvent.click(screen.getByRole('button', { name: 'Click again to delete all chats' }));
        await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('All chats deleted.'));
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(mockedApi.deleteSession).toHaveBeenNthCalledWith(1, 'trip');
        expect(mockedApi.deleteSession).toHaveBeenNthCalledWith(2, 'taxes');
        expect(useChatStore.getState().sessions).toEqual([]);
    });

    it('asks again once the confirm expires, and that click still deletes nothing', () => {
        vi.useFakeTimers();
        render(<PrivacySettings />);
        fireEvent.click(screen.getByRole('button', { name: 'Delete all chats' }));
        act(() => { vi.advanceTimersByTime(3999); });
        expect(screen.getByRole('button', { name: 'Click again to delete all chats' })).toBeInTheDocument();
        act(() => { vi.advanceTimersByTime(1); });
        fireEvent.click(screen.getByRole('button', { name: 'Delete all chats' }));
        expect(mockedApi.deleteSession).not.toHaveBeenCalled();
        expect(screen.getByRole('button', { name: 'Click again to delete all chats' })).toBeInTheDocument();
    });

    it('names the chats that stayed, and does not claim they are all gone', async () => {
        mockedApi.deleteSession.mockImplementation(async (id: string) => {
            if (id === 'trip') throw new Error('disk full');
        });
        render(<PrivacySettings />);
        fireEvent.click(screen.getByRole('button', { name: 'Delete all chats' }));
        fireEvent.click(screen.getByRole('button', { name: 'Click again to delete all chats' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not delete 1 chat(s): Trip plan: disk full');
        expect(screen.queryByText('All chats deleted.')).not.toBeInTheDocument();
        expect(mockedApi.deleteSession).toHaveBeenCalledTimes(2);
        expect(useChatStore.getState().sessions).toEqual([TRIP]);
    });

    it('counts every failure when none of the deletes succeed', async () => {
        mockedApi.deleteSession.mockRejectedValue(new Error('offline'));
        render(<PrivacySettings />);
        fireEvent.click(screen.getByRole('button', { name: 'Delete all chats' }));
        fireEvent.click(screen.getByRole('button', { name: 'Click again to delete all chats' }));
        expect(await screen.findByRole('alert')).toHaveTextContent(
            'Could not delete 2 chat(s): Trip plan: offline; Taxes: offline',
        );
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
        expect(useChatStore.getState().sessions).toEqual([TRIP, TAXES]);
    });
});
