// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The phone link's Copy button says "Copied" only once the clipboard took the link. */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MobileAccessModal } from '../MobileAccessModal';

vi.mock('qrcode', () => ({ default: { toCanvas: vi.fn().mockResolvedValue(undefined) } }));

vi.mock('../../services/api', () => ({
    getTunnelStatus: vi.fn().mockResolvedValue({
        active: true,
        url: 'https://example.ngrok.app',
        token: 'tok',
        startedAt: null,
        error: null,
    }),
}));

function setClipboard(writeText: ((text: string) => Promise<void>) | null): void {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: writeText ? { writeText } : undefined });
}

function setExecCommand(result: boolean): void {
    Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn(() => result) });
}

afterEach(() => {
    setClipboard(null);
    Reflect.deleteProperty(document, 'execCommand');
});

describe('MobileAccessModal copy link', () => {
    it('copies the tokened link and says Copied', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        setClipboard(writeText);
        render(<MobileAccessModal isOpen onClose={() => {}} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Copy' }));

        expect(await screen.findByRole('button', { name: 'Copied' })).toHaveClass('copied');
        expect(writeText).toHaveBeenCalledWith('https://example.ngrok.app/?token=tok');
    });

    it("says it couldn't copy, never Copied, when the clipboard refuses", async () => {
        setClipboard(vi.fn().mockRejectedValue(new Error('denied')));
        setExecCommand(false);
        render(<MobileAccessModal isOpen onClose={() => {}} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Copy' }));

        const button = await screen.findByRole('button', { name: "Couldn't copy" });
        expect(button).toHaveClass('failed');
        expect(screen.queryByRole('button', { name: 'Copied' })).toBeNull();
    });
});
