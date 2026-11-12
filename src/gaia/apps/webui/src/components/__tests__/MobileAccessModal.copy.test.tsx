// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A refused clipboard write must be logged and shown, never left as an
// unhandled rejection with a button that silently does nothing.

import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MobileAccessModal } from '../MobileAccessModal';
import { log } from '../../utils/logger';

vi.mock('qrcode', () => ({ default: { toCanvas: vi.fn().mockResolvedValue(undefined) } }));

vi.mock('../../services/api', () => ({
    getTunnelStatus: vi.fn().mockResolvedValue({
        active: true,
        url: 'https://example.ngrok.app',
        token: 'tok',
        startedAt: null,
        error: null,
        publicIp: null,
    }),
}));

afterEach(() => {
    vi.restoreAllMocks();
});

describe('MobileAccessModal copy URL', () => {
    it('shows Copied when the clipboard accepts the link', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        Object.assign(navigator, { clipboard: { writeText } });
        render(<MobileAccessModal isOpen onClose={() => {}} />);

        fireEvent.click(await screen.findByTitle('Copy URL'));

        expect(await screen.findByText('Copied')).toBeInTheDocument();
        expect(writeText).toHaveBeenCalledWith('https://example.ngrok.app/?token=tok');
        expect(screen.queryByRole('alert')).toBeNull();
    });

    it('logs and shows a failure when the clipboard refuses', async () => {
        const error = vi.spyOn(log.system, 'error').mockImplementation(() => {});
        Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } });
        render(<MobileAccessModal isOpen onClose={() => {}} />);

        fireEvent.click(await screen.findByTitle('Copy URL'));

        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't copy. Select the text and copy it instead.");
        expect(screen.queryByText('Copied')).toBeNull();
        expect(error).toHaveBeenCalledWith('Could not copy the mobile URL', expect.any(Error));
    });
});
