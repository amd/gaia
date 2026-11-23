// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PrivacySettings } from '../PrivacySettings';
import { useChatStore } from '../../../stores/chatStore';
import { useModelStore } from '../../../stores/modelStore';
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
        useChatStore.setState({ systemStatus: { data_dir: 'D:\\gaia-home\\chat' } as SystemStatus });
        render(<PrivacySettings />);
        fireEvent.click(screen.getByRole('button', { name: 'Open folder' }));
        expect(api.openFileOrFolder).toHaveBeenCalledWith('D:\\gaia-home\\chat', false);
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

describe('PrivacySettings where your data goes', () => {
    afterEach(() => {
        useModelStore.setState({ active: null });
        useChatStore.setState({ locale: 'en' });
    });

    function useCloudModel() {
        useModelStore.setState({
            active: {
                provider: 'fireworks', model: 'fireworks.other', label: 'other',
                remote: true, is_default: false, provider_name: 'Fireworks AI',
            },
        } as never);
    }

    it('says what is stored here and what is sent, as two parallel lines', () => {
        useCloudModel();
        render(<PrivacySettings />);
        expect(screen.getByText('Stored on this PC: chats, documents, memory.')).toBeInTheDocument();
        expect(screen.getByText('Sent to Fireworks AI: each message and the chat history, to get an answer.')).toBeInTheDocument();
        expect(screen.queryByText(/stay on this PC/)).toBeNull();
    });

    it('says the same in Arabic', () => {
        useCloudModel();
        useChatStore.setState({ locale: 'ar' });
        const { container } = render(<PrivacySettings />);
        // Arabic text arrives wrapped in bidi isolates; compare the words only.
        const text = (container.textContent ?? '').replace(/[⁦-⁩]/g, '');
        expect(text).toContain('محفوظ على هذا الجهاز: المحادثات والمستندات والذاكرة.');
        expect(text).toContain('يُرسَل إلى Fireworks AI: كل رسالة وسجل المحادثة، للحصول على إجابة.');
    });
});
