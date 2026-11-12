// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PrivacySettings } from '../PrivacySettings';
import { useChatStore } from '../../../stores/chatStore';
import type { SystemStatus } from '../../../types';
import * as api from '../../../services/api';

vi.mock('../../../services/api', async (importActual) => ({
    ...(await importActual<typeof import('../../../services/api')>()),
    getSettings: vi.fn().mockResolvedValue({ network_policy: 'cloud_allowed' }),
    openFileOrFolder: vi.fn().mockResolvedValue({ status: 'ok', path: '' }),
}));
vi.mock('../UndoHistorySettings', () => ({ UndoHistorySettings: () => null }));

describe('PrivacySettings data folder', () => {
    it('shows the folder the backend reports, not a guessed default', () => {
        useChatStore.setState({ systemStatus: { data_dir: 'D:\\gaia-home\\chat' } as SystemStatus });
        render(<PrivacySettings />);
        expect(screen.getByText('D:\\gaia-home\\chat')).toBeInTheDocument();
        expect(screen.queryByText('~/.gaia/chat/')).toBeNull();
    });

    it('opens the folder from its row', () => {
        useChatStore.setState({ systemStatus: { data_dir: 'D:\gaia-home\chat' } as SystemStatus });
        render(<PrivacySettings />);
        fireEvent.click(screen.getByRole('button', { name: 'Open folder' }));
        expect(api.openFileOrFolder).toHaveBeenCalledWith('D:\gaia-home\chat', false);
    });

    it('titles every card, not only for screen readers', () => {
        render(<PrivacySettings />);
        expect(screen.getByRole('heading', { name: 'Where your data is' })).toBeVisible();
        expect(screen.getByRole('heading', { name: 'Troubleshooting report' })).toBeVisible();
    });

    it('shows no folder until the backend has said which', () => {
        useChatStore.setState({ systemStatus: null });
        render(<PrivacySettings />);
        expect(screen.queryByText('Data folder')).toBeNull();
    });
});
