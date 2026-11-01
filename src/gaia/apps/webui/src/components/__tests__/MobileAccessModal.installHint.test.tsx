// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import type { TunnelStatus } from '../../types';
import { MobileAccessModal } from '../MobileAccessModal';

vi.mock('../../services/api');
vi.mock('qrcode', () => ({ default: { toCanvas: vi.fn().mockResolvedValue(undefined) } }));

const ACTIVE: TunnelStatus = {
    active: true,
    url: 'https://example.ngrok-free.app',
    token: 'tok',
    startedAt: null,
    error: null,
    publicIp: null,
};

const STARTING: TunnelStatus = { ...ACTIVE, active: false, url: null, token: null };

function setTokens() {
    document.documentElement.style.setProperty('--text-primary', '#111111');
    document.documentElement.style.setProperty('--bg-primary', '#FFFFFF');
}

describe('MobileAccessModal home-screen hint', () => {
    beforeEach(() => {
        vi.mocked(api.getTunnelStatus).mockReset();
        setTokens();
    });

    afterEach(() => {
        useChatStore.setState({ locale: 'en' });
    });

    it('tells the user how to add GAIA to the home screen on iPhone and Android', async () => {
        vi.mocked(api.getTunnelStatus).mockResolvedValue(ACTIVE);
        render(<MobileAccessModal isOpen onClose={vi.fn()} />);
        expect(await screen.findByText('Keep GAIA on your phone')).toBeInTheDocument();
        const steps = screen.getByText(/add it to your home screen/).textContent;
        expect(steps).toMatch(/in Safari, or Chrome on iPhone, tap Share, then Add to Home Screen/);
        expect(steps).toMatch(/In Chrome on Android, open the ⋮ menu and tap Add to Home screen/);
    });

    it('shows the hint in the chosen language', async () => {
        useChatStore.setState({ locale: 'ar' });
        vi.mocked(api.getTunnelStatus).mockResolvedValue(ACTIVE);
        render(<MobileAccessModal isOpen onClose={vi.fn()} />);
        expect(await screen.findByText(/أبقِ GAIA على هاتفك/)).toBeInTheDocument();
        expect(screen.queryByText('Keep GAIA on your phone')).toBeNull();
    });

    it('says the link does not outlive mobile access, so a restart needs the new code', async () => {
        vi.mocked(api.getTunnelStatus).mockResolvedValue(ACTIVE);
        render(<MobileAccessModal isOpen onClose={vi.fn()} />);
        const note = await screen.findByText(/This link stops working when mobile access stops/);
        expect(note.textContent).toMatch(/scan the new code/);
        expect(note.textContent).toMatch(/open the new link and add it again/);
    });

    it('stays hidden while the tunnel is still starting, with no link to install yet', async () => {
        vi.mocked(api.getTunnelStatus).mockResolvedValue(STARTING);
        render(<MobileAccessModal isOpen onClose={vi.fn()} />);
        expect(await screen.findByText('Starting tunnel...')).toBeInTheDocument();
        expect(screen.queryByText('Keep GAIA on your phone')).toBeNull();
    });

    it('stays hidden when the tunnel failed to start', () => {
        render(<MobileAccessModal isOpen onClose={vi.fn()} error="ngrok is not installed." />);
        expect(screen.queryByText('Keep GAIA on your phone')).toBeNull();
        expect(screen.queryByText(/Add to Home Screen/)).toBeNull();
    });

    it('stays hidden when the status call fails, rather than offering steps for a link it never got', async () => {
        vi.mocked(api.getTunnelStatus).mockRejectedValue(new Error('offline'));
        render(<MobileAccessModal isOpen onClose={vi.fn()} />);
        await waitFor(() => expect(api.getTunnelStatus).toHaveBeenCalled());
        expect(screen.getByText('Starting tunnel...')).toBeInTheDocument();
        expect(screen.queryByText('Keep GAIA on your phone')).toBeNull();
    });
});
