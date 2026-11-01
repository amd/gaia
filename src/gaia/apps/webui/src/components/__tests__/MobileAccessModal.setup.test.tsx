// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Opening Mobile access without ngrok is a one-time setup step, not a broken
 * feature (#1577): no "Couldn't start" / red error, this OS's command with a
 * Copy button, the download link, and Retry in place.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MobileAccessModal } from '../MobileAccessModal';
import * as api from '../../services/api';

vi.mock('../../services/api');

const HINT = 'ngrok is not installed. Install it from https://ngrok.com/download or run:\n    choco install ngrok';

describe('MobileAccessModal when ngrok is not installed', () => {
    let writeText: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        writeText = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator, { clipboard: { writeText } });
        vi.mocked(api.getTunnelStatus).mockClear();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('reads as setup, not as a failure', () => {
        const { container } = render(
            <MobileAccessModal isOpen onClose={vi.fn()} setupCommands={['choco install ngrok']} onRetry={vi.fn()} />,
        );
        expect(screen.getByText('ngrok required')).toBeInTheDocument();
        expect(screen.getByText('Set up ngrok (one-time)')).toBeInTheDocument();
        expect(screen.queryByText("Couldn't start mobile access")).toBeNull();
        expect(screen.queryByText(/Failed to connect|Connection failed/)).toBeNull();
        expect(container.querySelector('.tunnel-error')).toBeNull();
        expect(container.querySelector('.status-dot.error')).toBeNull();
        expect(screen.queryByText(/Scan the QR code/)).toBeNull();
        expect(screen.queryByText('Connecting...')).toBeNull();
    });

    it('shows only the command it was given, copyable in one click', async () => {
        render(<MobileAccessModal isOpen onClose={vi.fn()} setupCommands={['choco install ngrok']} />);
        const inputs = screen.getAllByLabelText('ngrok install command') as HTMLInputElement[];
        expect(inputs.map((i) => i.value)).toEqual(['choco install ngrok']);
        expect(screen.queryByText(/brew|snap|apt/)).toBeNull();

        fireEvent.click(screen.getByTitle('Copy command'));
        expect(writeText).toHaveBeenCalledWith('choco install ngrok');
        await waitFor(() => expect(screen.getByText('Copied')).toBeInTheDocument());
    });

    it('copies the exact command of the row clicked when there are two routes', async () => {
        const apt = 'curl -sSL https://example | sudo tee x && sudo apt install ngrok';
        render(<MobileAccessModal isOpen onClose={vi.fn()} setupCommands={['sudo snap install ngrok', apt]} />);
        const buttons = screen.getAllByTitle('Copy command');
        expect(buttons).toHaveLength(2);
        fireEvent.click(buttons[1]);
        expect(writeText).toHaveBeenCalledWith(apt);
        await waitFor(() => expect(screen.getAllByText('Copied')).toHaveLength(1));
    });

    it('links the download page and still offers Retry when no command is known', () => {
        const onRetry = vi.fn();
        render(<MobileAccessModal isOpen onClose={vi.fn()} setupCommands={[]} onRetry={onRetry} />);
        expect(screen.queryByLabelText('ngrok install command')).toBeNull();
        const link = screen.getByRole('link', { name: /ngrok download page/ });
        expect(link).toHaveAttribute('href', 'https://ngrok.com/download');
        expect(link).toHaveAttribute('rel', 'noopener noreferrer');
        fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
        expect(onRetry).toHaveBeenCalledTimes(1);
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
        expect(screen.queryByText('Set up ngrok (one-time)')).toBeNull();
        expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull();
    });

    it('setup wins over the raw hint text if both arrive', () => {
        const { container } = render(
            <MobileAccessModal isOpen onClose={vi.fn()} error={HINT} setupCommands={['choco install ngrok']} />,
        );
        expect(container.querySelector('.tunnel-error')).toBeNull();
        expect(screen.getByText('ngrok required')).toBeInTheDocument();
    });
});
