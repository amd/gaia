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

// The switch is drawn disabled until the saved policy loads; a click before then is dropped.
async function findReadySwitch(): Promise<HTMLElement> {
    const toggle = await screen.findByRole('switch', { name: 'Local only' });
    await waitFor(() => expect(toggle).toBeEnabled());
    return toggle;
}

function slowPolicyLoad(): () => void {
    let finish: () => void = () => {};
    mockedApi.getSettings.mockReturnValueOnce(new Promise((r) => {
        finish = () => r({ network_policy: 'cloud_allowed' });
    }) as never);
    return () => finish();
}

describe('PrivacySettings local only', () => {
    it('shows the policy and switches it on from Settings', async () => {
        render(<PrivacySettings />);
        const toggle = await findReadySwitch();
        expect(toggle).not.toBeChecked();

        fireEvent.click(toggle);

        await waitFor(() => expect(mockedApi.updateSettings).toHaveBeenCalledWith({ network_policy: 'local_ai_only' }));
        const notice = await screen.findByRole('status');
        expect(notice).toHaveTextContent('Cloud models are unavailable');
        // Next to the switch, not at the bottom of a long pane.
        expect(screen.getByRole('region', { name: 'Local only' })).toContainElement(notice);
    });

    it('draws Local only as the same slider switch as the rest of Settings', async () => {
        render(<PrivacySettings />);
        const toggle = await findReadySwitch();
        expect(toggle.closest('.toggle-switch')).not.toBeNull();
        expect(toggle.closest('.toggle-switch')?.querySelector('.toggle-track')).not.toBeNull();
        expect(screen.queryByText('Off', { exact: true })).not.toBeInTheDocument();
    });

    it('keeps keyboard focus on the switch while the change saves', async () => {
        let finish: (v: unknown) => void = () => {};
        mockedApi.updateSettings.mockReturnValueOnce(new Promise((r) => { finish = r; }) as never);
        render(<PrivacySettings />);
        const toggle = await findReadySwitch();
        toggle.focus();
        fireEvent.click(toggle);
        expect(toggle).not.toBeDisabled();
        expect(toggle).toHaveAttribute('aria-busy', 'true');
        expect(document.activeElement).toBe(toggle);
        await act(async () => { finish({ network_policy: 'local_ai_only' }); });
        expect(toggle).toBeChecked();
    });

    it('returns to the server policy when switching on fails', async () => {
        mockedApi.updateSettings.mockRejectedValueOnce(new Error('policy file is read-only'));
        render(<PrivacySettings />);
        const toggle = await findReadySwitch();

        fireEvent.click(toggle);

        expect(await screen.findByRole('alert')).toHaveTextContent('policy file is read-only');
        expect(toggle).not.toBeChecked();
    });

    it('ignores a click before the saved policy loads, then takes the next one', async () => {
        const loaded = slowPolicyLoad();
        render(<PrivacySettings />);
        const toggle = await screen.findByRole('switch', { name: 'Local only' });
        expect(toggle).toBeDisabled();

        fireEvent.click(toggle);
        expect(mockedApi.updateSettings).not.toHaveBeenCalled();

        await act(async () => { loaded(); });
        expect(toggle).toBeEnabled();
        fireEvent.click(toggle);
        await waitFor(() => expect(mockedApi.updateSettings).toHaveBeenCalledTimes(1));
        expect(await screen.findByRole('status')).toHaveTextContent('Cloud models are unavailable');
    });

    it('still reports a failed switch-on when the saved policy loads late', async () => {
        const loaded = slowPolicyLoad();
        mockedApi.updateSettings.mockRejectedValueOnce(new Error('policy file is read-only'));
        render(<PrivacySettings />);
        setTimeout(loaded, 50);
        const toggle = await findReadySwitch();

        fireEvent.click(toggle);

        expect(await screen.findByRole('alert')).toHaveTextContent('policy file is read-only');
        expect(toggle).not.toBeChecked();
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

    it('keeps the count it asked about while the chats are being deleted', async () => {
        let finishFirst: () => void = () => {};
        mockedApi.deleteSession.mockReturnValueOnce(new Promise<void>((r) => { finishFirst = r; }));
        let finishSecond: () => void = () => {};
        mockedApi.deleteSession.mockReturnValueOnce(new Promise<void>((r) => { finishSecond = r; }));
        render(<PrivacySettings />);
        askToDelete();
        confirmDelete();
        await act(async () => { finishFirst(); });
        expect(useChatStore.getState().sessions).toEqual([TAXES]);
        expect(screen.getByRole('alertdialog', { name: 'Delete all 2 chats?' })).toBeInTheDocument();
        expect(screen.queryByRole('alertdialog', { name: 'Delete your 1 chat?' })).not.toBeInTheDocument();
        await act(async () => { finishSecond(); });
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
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
