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
    mockedApi.getSettings.mockResolvedValue({ network_policy: 'cloud_allowed' } as never);
    mockedApi.updateSettings.mockResolvedValue({ network_policy: 'local_ai_only', policy_warnings: [] } as never);
    useChatStore.setState({ sessions: [TRIP, TAXES] });
});

afterEach(() => {
    vi.useRealTimers();
});

describe('PrivacySettings local only', () => {
    it('shows the policy and switches it on from Settings', async () => {
        render(<PrivacySettings />);
        const toggle = await screen.findByRole('switch', { name: 'Local only' });
        expect(toggle).toHaveAttribute('aria-checked', 'false');

        fireEvent.click(toggle);

        await waitFor(() => expect(mockedApi.updateSettings).toHaveBeenCalledWith({ network_policy: 'local_ai_only' }));
        expect(await screen.findByRole('status')).toHaveTextContent('Cloud models are unavailable');
    });

    it('returns to the server policy when switching on fails', async () => {
        mockedApi.updateSettings.mockRejectedValueOnce(new Error('policy file is read-only'));
        render(<PrivacySettings />);
        const toggle = await screen.findByRole('switch', { name: 'Local only' });

        fireEvent.click(toggle);

        expect(await screen.findByRole('alert')).toHaveTextContent('policy file is read-only');
        expect(toggle).toHaveAttribute('aria-checked', 'false');
    });
});

describe('PrivacySettings delete all chats', () => {
    const askToDelete = () => fireEvent.click(screen.getByRole('button', { name: 'Delete all chats' }));
    const confirmDelete = () => fireEvent.click(screen.getByRole('button', { name: 'Delete 2 chats' }));

    it('asks in a dialog that names what goes, with Cancel focused, and deletes nothing yet', () => {
        render(<PrivacySettings />);
        askToDelete();
        const dialog = screen.getByRole('alertdialog', { name: 'Delete all 2 chats?' });
        expect(dialog).toHaveTextContent("This can't be undone.");
        expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
        expect(screen.getByRole('button', { name: 'Delete 2 chats' })).toHaveClass('btn-danger');
        expect(mockedApi.deleteSession).not.toHaveBeenCalled();
        expect(useChatStore.getState().sessions).toEqual([TRIP, TAXES]);
    });

    it('titles the card where everyone can see it', () => {
        render(<PrivacySettings />);
        expect(screen.getByRole('heading', { name: 'Delete chats' })).toBeVisible();
        expect(screen.getByRole('region', { name: 'Delete chats' })).toContainElement(screen.getByRole('button', { name: 'Delete all chats' }));
    });

    it('a second click on the same button does not delete', () => {
        render(<PrivacySettings />);
        askToDelete();
        expect(screen.queryByRole('button', { name: /Click again/ })).not.toBeInTheDocument();
        expect(mockedApi.deleteSession).not.toHaveBeenCalled();
    });

    it('Cancel closes the dialog and keeps every chat', () => {
        render(<PrivacySettings />);
        askToDelete();
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
        expect(mockedApi.deleteSession).not.toHaveBeenCalled();
    });

    it('Escape cancels too', () => {
        render(<PrivacySettings />);
        askToDelete();
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
        expect(mockedApi.deleteSession).not.toHaveBeenCalled();
    });

    it('says one chat, not 1 chats', () => {
        useChatStore.setState({ sessions: [TRIP] });
        render(<PrivacySettings />);
        askToDelete();
        expect(screen.getByRole('alertdialog', { name: 'Delete your 1 chat?' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Delete 1 chat' })).toBeInTheDocument();
    });

    it('deletes every chat once confirmed and says so', async () => {
        render(<PrivacySettings />);
        askToDelete();
        confirmDelete();
        await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('All chats deleted.'));
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
        expect(screen.queryByRole('alert')).not.toBeInTheDocument();
        expect(mockedApi.deleteSession).toHaveBeenNthCalledWith(1, 'trip');
        expect(mockedApi.deleteSession).toHaveBeenNthCalledWith(2, 'taxes');
        expect(useChatStore.getState().sessions).toEqual([]);
    });

    it('names the chats that stayed, and does not claim they are all gone', async () => {
        mockedApi.deleteSession.mockImplementation(async (id: string) => {
            if (id === 'trip') throw new Error('disk full');
        });
        render(<PrivacySettings />);
        askToDelete();
        confirmDelete();
        expect(await screen.findByRole('alert')).toHaveTextContent('Could not delete 1 chat(s): Trip plan: disk full');
        expect(screen.queryByText('All chats deleted.')).not.toBeInTheDocument();
        expect(mockedApi.deleteSession).toHaveBeenCalledTimes(2);
        expect(useChatStore.getState().sessions).toEqual([TRIP]);
    });

    it('counts every failure when none of the deletes succeed', async () => {
        mockedApi.deleteSession.mockRejectedValue(new Error('offline'));
        render(<PrivacySettings />);
        askToDelete();
        confirmDelete();
        expect(await screen.findByRole('alert')).toHaveTextContent(
            'Could not delete 2 chat(s): Trip plan: offline; Taxes: offline',
        );
        expect(screen.queryByRole('status')).not.toBeInTheDocument();
        expect(useChatStore.getState().sessions).toEqual([TRIP, TAXES]);
    });
});

describe('PrivacySettings troubleshooting report', () => {
    it('says nothing is sent and offers to save a report', async () => {
        mockedApi.createSetupReport.mockResolvedValue({ name: 'diagnostics-20261104-192300.tgz', path: 'C:\Users\you\.gaia\diagnostics-20261104-192300.tgz', size_bytes: 900 });
        mockedApi.fetchSetupReport.mockResolvedValue(new Blob(['x']));
        URL.createObjectURL = vi.fn(() => 'blob:report');
        URL.revokeObjectURL = vi.fn();
        vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
        render(<PrivacySettings />);
        expect(screen.getByRole('region', { name: 'Troubleshooting report' })).toHaveTextContent('Nothing is sent anywhere.');
        await act(async () => {
            fireEvent.click(screen.getByRole('button', { name: 'Save a troubleshooting report' }));
        });
        expect(mockedApi.createSetupReport).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('diagnostics-20261104-192300.tgz'));
    });
});
