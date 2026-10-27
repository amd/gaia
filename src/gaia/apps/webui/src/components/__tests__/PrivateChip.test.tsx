// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PrivateChip } from '../PrivateChip';
import { useChatStore } from '../../stores/chatStore';
import * as memoryApi from '../../services/memoryApi';

vi.mock('../../services/memoryApi');
const getSettings = vi.mocked(memoryApi.getMemorySettings);

function memory(enabled: boolean) {
    return { memory_enabled: enabled, mcp_memory_enabled: false, system_discovery_consent: false };
}

beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({ draftPrivate: false, settingsSection: null });
    getSettings.mockResolvedValue(memory(true));
});

describe('PrivateChip', () => {
    it('shows "Memory on" when off and "Private" when on, and says what a click does', async () => {
        render(<PrivateChip />);
        const chip = await screen.findByRole('button', { name: 'Turn on private chat' });
        expect(chip).toHaveTextContent('Memory on');
        expect(chip).not.toHaveClass('is-on');

        await userEvent.click(chip);
        expect(chip).toHaveAccessibleName('Turn off private chat');
        expect(chip).toHaveTextContent('Private');
        expect(chip).toHaveClass('is-on');
        expect(useChatStore.getState().draftPrivate).toBe(true);
    });

    it('shows "Memory off", disabled, when memory is turned off in Settings', async () => {
        getSettings.mockResolvedValue(memory(false));
        render(<PrivateChip />);
        const chip = await screen.findByRole('button', { name: 'Memory off' });
        expect(chip).toBeDisabled();
        expect(chip).toHaveAttribute('title', expect.stringMatching(/memory is off/i));
    });

    it('picks up memory being switched off once Settings closes', async () => {
        render(<PrivateChip />);
        await screen.findByRole('button', { name: 'Turn on private chat' });

        getSettings.mockResolvedValue(memory(false));
        act(() => useChatStore.setState({ settingsSection: 'memory' }));
        act(() => useChatStore.setState({ settingsSection: null }));
        expect(await screen.findByRole('button', { name: 'Memory off' })).toBeDisabled();
    });
});
