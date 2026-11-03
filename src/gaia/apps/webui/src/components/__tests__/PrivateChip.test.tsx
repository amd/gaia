// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PrivateChip } from '../PrivateChip';
import { useChatStore } from '../../stores/chatStore';
import * as memoryApi from '../../services/memoryApi';
import { log } from '../../utils/logger';

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

    it('drops a Private choice once memory is switched off, so the next chat is not silently private', async () => {
        render(<PrivateChip />);
        await userEvent.click(await screen.findByRole('button', { name: 'Turn on private chat' }));
        expect(useChatStore.getState().draftPrivate).toBe(true);

        getSettings.mockResolvedValue(memory(false));
        act(() => useChatStore.setState({ settingsSection: 'memory' }));
        act(() => useChatStore.setState({ settingsSection: null }));
        await screen.findByRole('button', { name: 'Memory off' });
        expect(useChatStore.getState().draftPrivate).toBe(false);

        // Memory back on: the chip and the draft agree the chat is not private.
        getSettings.mockResolvedValue(memory(true));
        act(() => useChatStore.setState({ settingsSection: 'memory' }));
        act(() => useChatStore.setState({ settingsSection: null }));
        expect(await screen.findByRole('button', { name: 'Turn on private chat' })).toHaveTextContent('Memory on');
        expect(useChatStore.getState().draftPrivate).toBe(false);
    });

    it('drops a Private choice left over from before the screen opened with memory off', async () => {
        useChatStore.setState({ draftPrivate: true });
        getSettings.mockResolvedValue(memory(false));
        render(<PrivateChip />);
        await screen.findByRole('button', { name: 'Memory off' });
        expect(useChatStore.getState().draftPrivate).toBe(false);
    });

    it('keeps a Private choice while memory is on', async () => {
        useChatStore.setState({ draftPrivate: true });
        render(<PrivateChip />);
        const chip = await screen.findByRole('button', { name: 'Turn off private chat' });
        await act(async () => { await Promise.resolve(); });
        expect(chip).toHaveTextContent('Private');
        expect(useChatStore.getState().draftPrivate).toBe(true);
    });

    it('says it does not know when the memory setting cannot be read, and logs through the app logger', async () => {
        const logged = vi.spyOn(log.ui, 'error').mockImplementation(() => {});
        const raw = vi.spyOn(console, 'error').mockImplementation(() => {});
        getSettings.mockRejectedValue(new Error('backend down'));
        render(<PrivateChip />);

        const chip = await screen.findByText('Memory unknown');
        expect(screen.queryByText('Memory on')).toBeNull();
        expect(chip.closest('button')).toHaveAttribute('title', expect.stringMatching(/couldn't read the memory setting/i));
        expect(logged).toHaveBeenCalledWith(expect.stringMatching(/memory setting/i), expect.any(Error));
        expect(raw).not.toHaveBeenCalledWith(expect.stringContaining('[PrivateChip]'), expect.anything());

        // Private still works, and reads as Private, when the setting is unknown.
        await userEvent.click(screen.getByRole('button', { name: 'Turn on private chat' }));
        expect(screen.getByRole('button', { name: 'Turn off private chat' })).toHaveTextContent('Private');
        expect(useChatStore.getState().draftPrivate).toBe(true);
        logged.mockRestore();
        raw.mockRestore();
    });

    it('drops a stale "unknown" once a later read succeeds', async () => {
        vi.spyOn(log.ui, 'error').mockImplementation(() => {});
        getSettings.mockRejectedValueOnce(new Error('backend down'));
        render(<PrivateChip />);
        await screen.findByText('Memory unknown');

        act(() => useChatStore.setState({ settingsSection: 'memory' }));
        act(() => useChatStore.setState({ settingsSection: null }));
        expect(await screen.findByText('Memory on')).toBeInTheDocument();
    });
});
