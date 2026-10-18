// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PrivacySettings } from '../PrivacySettings';
import { useChatStore } from '../../../stores/chatStore';
import type { SystemStatus } from '../../../types';

vi.mock('../../../services/api');

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
});
