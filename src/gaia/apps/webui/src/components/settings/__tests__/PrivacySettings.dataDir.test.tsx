// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PrivacySettings } from '../PrivacySettings';
import { useChatStore } from '../../../stores/chatStore';
import * as api from '../../../services/api';
import type { SystemStatus } from '../../../types';

vi.mock('../../../services/api', async (importActual) => ({
    ...(await importActual<typeof import('../../../services/api')>()),
    getSettings: vi.fn().mockResolvedValue({ network_policy: 'cloud_allowed' }),
    openFileOrFolder: vi.fn(),
}));
vi.mock('../UndoHistorySettings', () => ({ UndoHistorySettings: () => null }));

describe('PrivacySettings data folder', () => {
    it('shows the folder the backend reports, not a guessed default', () => {
        useChatStore.setState({ systemStatus: { data_dir: 'D:\\gaia-home\\chat' } as SystemStatus });
        render(<PrivacySettings />);
        expect(screen.getByText('D:\\gaia-home\\chat')).toBeInTheDocument();
        expect(screen.queryByText('~/.gaia/chat/')).toBeNull();
    });

    it('shows no folder until the backend has said which', () => {
        useChatStore.setState({ systemStatus: null });
        render(<PrivacySettings />);
        expect(screen.queryByText('Data folder')).toBeNull();
    });

    it('opens the data folder from a link and says when it cannot', async () => {
        useChatStore.setState({ systemStatus: { data_dir: 'D:\\gaia-home\\chat' } as SystemStatus });
        vi.mocked(api.openFileOrFolder).mockRejectedValueOnce(new Error('no file browser'));
        render(<PrivacySettings />);
        fireEvent.click(screen.getByRole('button', { name: 'Open folder' }));
        expect(api.openFileOrFolder).toHaveBeenCalledWith('D:\\gaia-home\\chat', false);
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't open the data folder: no file browser");
    });
});

describe('PrivacySettings cards', () => {
    it('shows each card title as a visible heading', () => {
        useChatStore.setState({ systemStatus: null });
        render(<PrivacySettings />);
        for (const title of ['Where your data is', 'Troubleshooting report', 'Delete chats']) {
            expect(screen.getByRole('heading', { level: 3, name: title })).toBeInTheDocument();
        }
    });
});
