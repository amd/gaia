// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The QR raster is the one place in the app that needs colour *literals* --
 * `toCanvas()` paints pixels and cannot take `var()`. It used to carry its own
 * copy of the two hexes, which is how a raster drifts away from the theme.
 *
 * Pins the contract with the QR library rather than just "we called it":
 *   - the two tones come from --text-primary/--bg-primary as they resolve now
 *   - a theme swap moves them, with no literal in the component
 *   - unresolved tokens skip the render loudly instead of painting a blank code
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { MobileAccessModal } from '../MobileAccessModal';

const toCanvas = vi.fn().mockResolvedValue(undefined);
vi.mock('qrcode', () => ({ default: { toCanvas: (...a: unknown[]) => toCanvas(...a) } }));

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

/** Stand in for styles/index.css, which jsdom does not load. */
function setTokens(ink: string | null, paper: string | null) {
    const s = document.documentElement.style;
    s.removeProperty('--text-primary');
    s.removeProperty('--bg-primary');
    if (ink) s.setProperty('--text-primary', ink);
    if (paper) s.setProperty('--bg-primary', paper);
}

describe('MobileAccessModal QR colours', () => {
    beforeEach(() => {
        toCanvas.mockClear();
    });

    afterEach(() => {
        setTokens(null, null);
    });

    it('paints the raster in the resolved theme tokens, not its own copies', async () => {
        setTokens('#F0EDE7', '#17161C');
        render(<MobileAccessModal isOpen onClose={() => {}} />);

        await waitFor(() => expect(toCanvas).toHaveBeenCalled());
        const [, url, opts] = toCanvas.mock.calls[0] as [unknown, string, { color: Record<string, string> }];
        expect(url).toBe('https://example.ngrok.app/?token=tok');
        expect(opts.color).toEqual({ dark: '#F0EDE7', light: '#17161C' });
    });

    it('follows the tokens when the theme changes', async () => {
        setTokens('#242129', '#F5F2EC');
        render(<MobileAccessModal isOpen onClose={() => {}} />);

        await waitFor(() => expect(toCanvas).toHaveBeenCalled());
        const opts = toCanvas.mock.calls[0][2] as { color: Record<string, string> };
        expect(opts.color).toEqual({ dark: '#242129', light: '#F5F2EC' });
    });

    it('skips the render loudly when the tokens do not resolve', async () => {
        setTokens(null, null);
        const err = vi.spyOn(console, 'error').mockImplementation(() => {});
        render(<MobileAccessModal isOpen onClose={() => {}} />);

        await waitFor(() =>
            expect(err.mock.calls.some((c) => String(c.join(' ')).includes('QR code skipped'))).toBe(true),
        );
        expect(toCanvas).not.toHaveBeenCalled();
        err.mockRestore();
    });
});
