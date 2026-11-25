// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Anyone holding the link's token can use GAIA, so it never reaches a log, and the copy says so. */

import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import type { TunnelStatus } from '../../types';
import { MobileAccessModal } from '../MobileAccessModal';

const logged = vi.hoisted(() => [] as unknown[][]);

vi.mock('../../utils/logger', () => {
    const record = (...args: unknown[]) => { logged.push(args); };
    const channel = { info: record, warn: record, error: record, debug: record, timed: record, time: () => 0 };
    return { log: new Proxy({}, { get: () => channel }) };
});
vi.mock('../../services/api');
const toCanvas = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));
vi.mock('qrcode', () => ({ default: { toCanvas } }));

const TOKEN = '6f17248d-secret-token-value';
const ACTIVE: TunnelStatus = {
    active: true,
    url: 'https://quiet-otter.ngrok.app',
    token: TOKEN,
    startedAt: null,
    error: null,
};

describe('MobileAccessModal privacy', () => {
    beforeEach(() => {
        logged.length = 0;
        toCanvas.mockClear();
        vi.mocked(api.getTunnelStatus).mockReset();
        vi.mocked(api.getTunnelStatus).mockResolvedValue(ACTIVE);
        document.documentElement.style.setProperty('--text-primary', '#111111');
        document.documentElement.style.setProperty('--bg-primary', '#FFFFFF');
    });

    afterEach(() => {
        useChatStore.setState({ locale: 'en' });
    });

    it('logs the tunnel host but never the token in the link', async () => {
        render(<MobileAccessModal isOpen onClose={vi.fn()} />);
        await waitFor(() => expect(toCanvas).toHaveBeenCalled());

        const text = logged.map((args) => args.map(String).join(' ')).join('\n');
        expect(text).toContain('quiet-otter.ngrok.app');
        expect(text).not.toContain(TOKEN);
    });

    it('names ngrok as a third party and says who can use the link', async () => {
        render(<MobileAccessModal isOpen onClose={vi.fn()} />);
        expect(await screen.findByText(
            'Your phone reaches this PC through ngrok, a third-party service. Anyone with this link can use GAIA until you turn this off.',
        )).toBeInTheDocument();
        expect(screen.queryByText(/stays secure/)).toBeNull();
    });

    it('asks for no password: ngrok never prompts for one, the link alone gets in', async () => {
        render(<MobileAccessModal isOpen onClose={vi.fn()} />);
        await screen.findByText(/a third-party service/);
        expect(screen.queryByText(/password/i)).toBeNull();
    });

    it('shows every line of the active dialog in Arabic', async () => {
        useChatStore.setState({ locale: 'ar' });
        render(<MobileAccessModal isOpen onClose={vi.fn()} onStop={vi.fn()} />);
        expect(await screen.findByText(/وهي خدمة تابعة لجهة خارجية/)).toBeInTheDocument();
        for (const ar of ['النفق يعمل', 'رابط الهاتف', 'امسح رمز QR بكاميرا هاتفك', 'إيقاف النفق']) {
            expect(screen.getAllByText(new RegExp(ar)).length).toBeGreaterThan(0);
        }
    });
});
