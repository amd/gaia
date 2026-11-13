// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** What the in-chat Connect button asks the backend for. */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import type { AgentInfo } from '../../types';
import { EmailConnectCta } from '../email/EmailConnectCta';

vi.mock('../../services/api');

const mocked = vi.mocked(api);

const FLAGSHIP = {
    id: 'gaia',
    namespaced_agent_id: 'installed:gaia',
    required_connections: [{ connector_id: 'google' }, { connector_id: 'microsoft' }],
} as unknown as AgentInfo;

beforeEach(() => {
    vi.clearAllMocks();
    mocked.authorizeConnector.mockResolvedValue({ flow_id: 'f', authorization_url: 'https://accounts.example/auth' });
    vi.spyOn(window, 'open').mockReturnValue(null);
});

afterEach(() => {
    act(() => useChatStore.getState().setAgents([]));
    vi.restoreAllMocks();
});

describe('EmailConnectCta connect request', () => {
    it('names GAIA and sends no scopes, so the backend asks only for what GAIA declares', async () => {
        act(() => useChatStore.getState().setAgents([FLAGSHIP]));
        render(<EmailConnectCta content="NOT_CONNECTED: google is not currently connected." />);

        fireEvent.click(screen.getByRole('button', { name: 'Connect Google' }));

        await waitFor(() => expect(mocked.authorizeConnector).toHaveBeenCalledTimes(1));
        expect(mocked.authorizeConnector).toHaveBeenCalledWith('google', [], ['installed:gaia']);
        // The connector's whole scope catalog (gmail.modify, Drive) is never read to build the request.
        expect(mocked.getConnector).not.toHaveBeenCalled();
        expect(window.open).toHaveBeenCalledWith('https://accounts.example/auth', '_blank', 'noopener');
    });

    it('grants no one when GAIA is not in the agent list', async () => {
        render(<EmailConnectCta content="NOT_CONNECTED: microsoft is not currently connected." />);

        fireEvent.click(screen.getByRole('button', { name: 'Connect Microsoft' }));

        await waitFor(() => expect(mocked.authorizeConnector).toHaveBeenCalledWith('microsoft', [], []));
    });

    it('shows the backend error and keeps the button when sign-in cannot start', async () => {
        mocked.authorizeConnector.mockRejectedValue(new Error('Google sign-in is not set up on this PC.'));
        render(<EmailConnectCta content="NOT_CONNECTED: google is not currently connected." />);

        fireEvent.click(screen.getByRole('button', { name: 'Connect Google' }));

        expect((await screen.findByRole('alert')).textContent).toContain('Google sign-in is not set up on this PC.');
        expect(screen.getByRole('button', { name: 'Connect Google' })).toBeTruthy();
    });

    it('offers Google and Microsoft only', () => {
        render(<EmailConnectCta content="AUTH_REQUIRED: connect an email account to continue." />);

        expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Connect Google', 'Connect Microsoft']);
    });
});
