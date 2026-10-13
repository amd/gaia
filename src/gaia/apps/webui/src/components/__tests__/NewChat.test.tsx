// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NewChat } from '../NewChat';
import { useChatStore } from '../../stores/chatStore';
import * as api from '../../services/api';
import type { SystemStatus } from '../../types';

vi.mock('../../services/api');

beforeEach(() => {
    useChatStore.setState({ systemStatus: null });
    vi.mocked(api.requestWarmUp).mockResolvedValue({ state: 'ready' });
});

describe('NewChat while GAIA is starting', () => {
    it('holds the first message instead of sending one the chat would drop', () => {
        useChatStore.setState({ systemStatus: { init_state: 'initializing' } as SystemStatus });
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);

        const box = screen.getByLabelText('Message');
        expect(box).toBeDisabled();
        expect(box).toHaveAttribute('placeholder', 'GAIA is starting…');
        fireEvent.keyDown(box, { key: 'Enter' });
        expect(onSend).not.toHaveBeenCalled();
    });

    it('sends once GAIA is ready', () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);

        const box = screen.getByLabelText('Message');
        fireEvent.change(box, { target: { value: 'hello' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        expect(onSend).toHaveBeenCalledWith('hello');
    });
});

describe('NewChat while the first prompt is being primed', () => {
    it('shows the wait without holding the message box', async () => {
        useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
        vi.mocked(api.requestWarmUp).mockResolvedValue({ state: 'warming', elapsed_seconds: 4.6 });
        vi.mocked(api.getWarmUp).mockResolvedValue({ state: 'warming', elapsed_seconds: 5.2 });
        const onSend = vi.fn().mockResolvedValue(undefined);
        render(<NewChat onSend={onSend} />);

        expect(await screen.findByRole('status')).toHaveTextContent('Getting GAIA ready… 4s');
        const box = screen.getByLabelText('Message');
        expect(box).toBeEnabled();
        fireEvent.change(box, { target: { value: 'hello' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        expect(onSend).toHaveBeenCalledWith('hello');
    });

    it('clears the wait once the prompt is ready', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        try {
            useChatStore.setState({ systemStatus: { init_state: 'ready' } as SystemStatus });
            vi.mocked(api.requestWarmUp).mockResolvedValue({ state: 'warming', elapsed_seconds: 1 });
            vi.mocked(api.getWarmUp).mockResolvedValue({ state: 'ready', elapsed_seconds: 3 });
            render(<NewChat onSend={vi.fn()} />);

            await screen.findByText(/Getting GAIA ready/);
            await vi.advanceTimersByTimeAsync(1_100);
            await waitFor(() => expect(screen.queryByText(/Getting GAIA ready/)).toBeNull());
        } finally {
            vi.useRealTimers();
        }
    });

    it('stays quiet when nothing needs priming', async () => {
        render(<NewChat onSend={vi.fn()} />);
        await waitFor(() => expect(api.requestWarmUp).toHaveBeenCalled());
        expect(screen.queryByText(/Getting GAIA ready/)).toBeNull();
    });
});
