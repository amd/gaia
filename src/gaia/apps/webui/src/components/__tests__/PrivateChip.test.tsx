// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { act, render, screen, waitFor } from '@testing-library/react';
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
    it('opens a menu that says what each choice does, so one click never turns a chat private', async () => {
        render(<PrivateChip />);
        const chip = await screen.findByRole('button', { name: 'Memory for this chat: Remember across chats: on' });
        expect(chip).toHaveTextContent('Remember across chats: on');
        expect(chip).not.toHaveClass('is-on');

        await userEvent.click(chip);
        expect(useChatStore.getState().draftPrivate).toBe(false);
        const menu = screen.getByRole('dialog', { name: 'Memory for this chat' });
        expect(menu).toHaveTextContent('Not saved to memory, not even its first message.');
        expect(screen.getByRole('button', { name: /Remember this chat/ })).toHaveAttribute('aria-pressed', 'true');

        await userEvent.click(screen.getByRole('button', { name: /Private chat/ }));
        expect(useChatStore.getState().draftPrivate).toBe(true);
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(chip).toHaveAccessibleName('Memory for this chat: Private');
        expect(chip).toHaveTextContent('Private');
        expect(chip).toHaveClass('is-on');
    });

    it('shows "Remember across chats: off", disabled, when memory is turned off in Settings', async () => {
        getSettings.mockResolvedValue(memory(false));
        render(<PrivateChip />);
        const chip = await screen.findByRole('button', { name: 'Remember across chats: off' });
        expect(chip).toBeDisabled();
        expect(chip).toHaveAttribute('title', expect.stringMatching(/is off in Settings/i));
    });

    it('picks up memory being switched off once Settings closes', async () => {
        render(<PrivateChip />);
        await screen.findByRole('button', { name: 'Memory for this chat: Remember across chats: on' });

        getSettings.mockResolvedValue(memory(false));
        act(() => useChatStore.setState({ settingsSection: 'memory' }));
        act(() => useChatStore.setState({ settingsSection: null }));
        expect(await screen.findByRole('button', { name: 'Remember across chats: off' })).toBeDisabled();
    });

    it('drops a Private choice once memory is switched off, so the next chat is not silently private', async () => {
        render(<PrivateChip />);
        await userEvent.click(await screen.findByRole('button', { name: 'Memory for this chat: Remember across chats: on' }));
        await userEvent.click(screen.getByRole('button', { name: /Private chat/ }));
        expect(useChatStore.getState().draftPrivate).toBe(true);

        getSettings.mockResolvedValue(memory(false));
        act(() => useChatStore.setState({ settingsSection: 'memory' }));
        act(() => useChatStore.setState({ settingsSection: null }));
        await screen.findByRole('button', { name: 'Remember across chats: off' });
        await waitFor(() => expect(useChatStore.getState().draftPrivate).toBe(false));

        // Memory back on: the chip and the draft agree the chat is not private.
        getSettings.mockResolvedValue(memory(true));
        act(() => useChatStore.setState({ settingsSection: 'memory' }));
        act(() => useChatStore.setState({ settingsSection: null }));
        expect(await screen.findByRole('button', { name: 'Memory for this chat: Remember across chats: on' })).toHaveTextContent('Remember across chats: on');
        expect(useChatStore.getState().draftPrivate).toBe(false);
    });

    it('drops a Private choice left over from before the screen opened with memory off', async () => {
        useChatStore.setState({ draftPrivate: true });
        getSettings.mockResolvedValue(memory(false));
        render(<PrivateChip />);
        await screen.findByRole('button', { name: 'Remember across chats: off' });
        await waitFor(() => expect(useChatStore.getState().draftPrivate).toBe(false));
    });

    it('keeps a Private choice while memory is on', async () => {
        useChatStore.setState({ draftPrivate: true });
        render(<PrivateChip />);
        const chip = await screen.findByRole('button', { name: 'Memory for this chat: Private' });
        await act(async () => { await Promise.resolve(); });
        expect(chip).toHaveTextContent('Private');
        expect(useChatStore.getState().draftPrivate).toBe(true);
    });

    it('says it does not know when the memory setting cannot be read, and logs through the app logger', async () => {
        const logged = vi.spyOn(log.ui, 'error').mockImplementation(() => {});
        const raw = vi.spyOn(console, 'error').mockImplementation(() => {});
        getSettings.mockRejectedValue(new Error('backend down'));
        render(<PrivateChip />);

        const chip = await screen.findByText('Remember across chats: unknown');
        expect(screen.queryByText('Remember across chats: on')).toBeNull();
        expect(chip.closest('button')).toHaveAttribute('title', expect.stringMatching(/couldn't read the memory setting/i));
        expect(logged).toHaveBeenCalledWith(expect.stringMatching(/memory setting/i), expect.any(Error));
        expect(raw).not.toHaveBeenCalledWith(expect.stringContaining('[PrivateChip]'), expect.anything());

        // Private still works, and reads as Private, when the setting is unknown.
        await userEvent.click(screen.getByRole('button', { name: 'Memory for this chat: Remember across chats: unknown' }));
        const remember = screen.getByRole('button', { name: /^Remember this chat/ });
        expect(remember).toHaveTextContent("Couldn't read the memory setting. Choose Private chat");
        expect(remember).not.toHaveTextContent('GAIA can learn from this chat');
        await userEvent.click(screen.getByRole('button', { name: /^Private chat/ }));
        expect(screen.getByRole('button', { name: 'Memory for this chat: Private' })).toHaveTextContent('Private');
        expect(useChatStore.getState().draftPrivate).toBe(true);
        logged.mockRestore();
        raw.mockRestore();
    });

    it('drops a stale "unknown" once a later read succeeds', async () => {
        vi.spyOn(log.ui, 'error').mockImplementation(() => {});
        getSettings.mockRejectedValueOnce(new Error('backend down'));
        render(<PrivateChip />);
        await screen.findByText('Remember across chats: unknown');

        act(() => useChatStore.setState({ settingsSection: 'memory' }));
        act(() => useChatStore.setState({ settingsSection: null }));
        expect(await screen.findByText('Remember across chats: on')).toBeInTheDocument();
    });
});
