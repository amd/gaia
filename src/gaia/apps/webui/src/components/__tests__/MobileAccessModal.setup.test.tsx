// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Opening Mobile access without ngrok is a one-time setup step, not a broken
 * feature (#1577): no "Couldn't start" / red error, what the feature is, where
 * to get ngrok, and Retry. Never a terminal command to run (#7415).
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MobileAccessModal } from '../MobileAccessModal';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';

vi.mock('../../services/api');

const HINT = 'ngrok is not installed. Install it from https://ngrok.com/download or run:\n    choco install ngrok';

describe('MobileAccessModal when ngrok is not installed', () => {
    beforeEach(() => {
        vi.mocked(api.getTunnelStatus).mockClear();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('reads as setup, not as a failure', () => {
        const { container } = render(
            <MobileAccessModal isOpen onClose={vi.fn()} setupCommands={['choco install ngrok']} onRetry={vi.fn()} />,
        );
        expect(screen.getByText('Needs ngrok')).toBeInTheDocument();
        expect(screen.queryByText("Couldn't start mobile access")).toBeNull();
        expect(screen.queryByText(/Failed to connect|Connection failed/)).toBeNull();
        expect(container.querySelector('.tunnel-error')).toBeNull();
        expect(container.querySelector('.status-dot.is-error')).toBeNull();
        expect(screen.queryByText(/Scan the QR code/)).toBeNull();
        expect(screen.queryByText('Connecting...')).toBeNull();
    });

    it('says what the feature is and where to get ngrok, never a command to run', () => {
        const onRetry = vi.fn();
        render(<MobileAccessModal isOpen onClose={vi.fn()} setupCommands={['choco install ngrok']} onRetry={onRetry} />);
        expect(screen.getByText(/Use GAIA from your phone while this PC is on/)).toBeInTheDocument();
        expect(screen.queryByText(/choco|brew|snap|apt|install ngrok/)).toBeNull();
        expect(screen.queryByRole('textbox')).toBeNull();
        expect(screen.queryByTitle('Copy command')).toBeNull();
        const link = screen.getByRole('link', { name: /Get ngrok/ });
        expect(link).toHaveAttribute('href', 'https://ngrok.com/download');
        expect(link).toHaveAttribute('rel', 'noopener noreferrer');
        fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
        expect(onRetry).toHaveBeenCalledTimes(1);
    });

    it('draws the status as a small dot, not a bar', () => {
        const { container } = render(<MobileAccessModal isOpen onClose={vi.fn()} setupCommands={[]} />);
        const dot = container.querySelector('.tunnel-status .status-dot') as HTMLElement;
        // The bare "setup" class picked up the setup screen's layout and stretched the dot.
        expect(dot.classList.contains('setup')).toBe(false);
        expect(dot).toHaveClass('is-setup');
    });

    it('does not poll the tunnel while it waits for setup', async () => {
        vi.useFakeTimers();
        render(<MobileAccessModal isOpen onClose={vi.fn()} setupCommands={['brew install ngrok']} />);
        await act(async () => {
            vi.advanceTimersByTime(6000);
        });
        expect(api.getTunnelStatus).not.toHaveBeenCalled();
    });

    it('a genuine failure is still an error, with no setup panel or Retry', () => {
        render(<MobileAccessModal isOpen onClose={vi.fn()} error="ngrok authtoken not configured." onRetry={vi.fn()} />);
        expect(screen.getByText("Couldn't start mobile access")).toBeInTheDocument();
        expect(screen.getByText('ngrok authtoken not configured.')).toBeInTheDocument();
        expect(screen.queryByRole('link', { name: /Get ngrok/ })).toBeNull();
        expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull();
    });

    it('is translated', () => {
        act(() => useChatStore.getState().setLocale('ar'));
        try {
            const { container } = render(
                <MobileAccessModal isOpen onClose={vi.fn()} setupCommands={['choco install ngrok']} onRetry={vi.fn()} />,
            );
            expect(container.querySelector('.tunnel-setup-title')?.textContent).toContain('استخدم GAIA من هاتفك');
        } finally {
            act(() => useChatStore.getState().setLocale('en'));
            localStorage.removeItem('gaia-chat-locale');
        }
    });

    it('setup wins over the raw hint text if both arrive', () => {
        const { container } = render(
            <MobileAccessModal isOpen onClose={vi.fn()} error={HINT} setupCommands={['choco install ngrok']} />,
        );
        expect(container.querySelector('.tunnel-error')).toBeNull();
        expect(screen.getByText('Needs ngrok')).toBeInTheDocument();
    });
});
