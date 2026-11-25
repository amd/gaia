// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';

vi.mock('../../services/api');
const sse = vi.hoisted(() => ({
    notify: null as null | ((event: {
        reason: string;
        connectorId: string | null;
        payload: Record<string, unknown>;
    }) => void),
}));
vi.mock('../../hooks/useConnectorsSSE', () => ({
    useConnectorsSSE: (onEvent: NonNullable<typeof sse.notify>) => { sse.notify = onEvent; },
}));

import * as api from '../../services/api';
import { ar } from '../../i18n/locales/ar';
import { enMessages } from '../../i18n/locales/en';
import { useChatStore } from '../../stores/chatStore';
import { ConnectorsSection } from '../ConnectorsSection';

const mocked = vi.mocked(api);

async function disconnected(payload: Record<string, unknown>) {
    act(() => sse.notify?.({ reason: 'disconnected', connectorId: 'google', payload }));
    await waitFor(() => expect(mocked.getConnector).toHaveBeenCalledWith('google'));
}

async function renderSection() {
    render(<ConnectorsSection />);
    await waitFor(() => expect(mocked.listConnectors).toHaveBeenCalledOnce());
}

beforeEach(() => {
    vi.clearAllMocks();
    sse.notify = null;
    mocked.listConnectors.mockResolvedValue({ connectors: [] } as never);
    mocked.listAgentMcps.mockResolvedValue({ agent_mcps: [] } as never);
    mocked.getConnector.mockResolvedValue({ id: 'google', configured: false, enabled: true } as never);
    act(() => useChatStore.getState().setLocale('en'));
});

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
});

describe('Disconnect outcome notice', () => {
    it('says nothing was stored instead of claiming a revoke', async () => {
        await renderSection();
        await disconnected({
            connector_id: 'google',
            revoke_supported: true,
            revoked_remotely: false,
            nothing_stored: true,
            revoke_error: null,
        });
        const notice = await screen.findByRole('status');
        expect(notice).toHaveTextContent(enMessages['settings.connectors.nothingStored']);
        expect(notice).not.toHaveTextContent(/provider access revoked/i);
        expect(notice).not.toHaveTextContent(enMessages['settings.connectors.noRevokeApi']);
    });

    it('uses the nothing-stored wording for a provider with no revoke endpoint', async () => {
        await renderSection();
        await disconnected({
            connector_id: 'google',
            revoke_supported: false,
            revoked_remotely: false,
            nothing_stored: true,
            revoke_error: null,
        });
        const notice = await screen.findByRole('status');
        expect(notice).toHaveTextContent(enMessages['settings.connectors.nothingStored']);
        expect(notice).not.toHaveTextContent(enMessages['settings.connectors.noRevokeApi']);
    });

    it('still names the missing revoke endpoint when a token was stored', async () => {
        await renderSection();
        await disconnected({
            connector_id: 'google',
            revoke_supported: false,
            revoked_remotely: false,
            nothing_stored: false,
            revoke_attempted: false,
            revoke_error: null,
        });
        const notice = await screen.findByRole('status');
        expect(notice).toHaveTextContent(enMessages['settings.connectors.noRevokeApi']);
        expect(notice).not.toHaveTextContent(enMessages['settings.connectors.nothingStored']);
    });

    it('shows the nothing-stored notice in Arabic', async () => {
        act(() => useChatStore.getState().setLocale('ar'));
        await renderSection();
        await disconnected({
            connector_id: 'google',
            revoke_supported: true,
            revoked_remotely: false,
            nothing_stored: true,
            revoke_error: null,
        });
        const notice = await screen.findByRole('status');
        expect(notice).toHaveTextContent(ar.messages['settings.connectors.nothingStored']);
    });

    it('says an unreadable sign-in could not be read instead of nothing stored', async () => {
        await renderSection();
        const reason =
            "GAIA could not read the stored sign-in, so it removed the local copy without revoking GAIA's access at Google. To revoke it, remove GAIA at https://myaccount.google.com/permissions.";
        await disconnected({
            connector_id: 'google',
            revoke_supported: true,
            revoked_remotely: false,
            nothing_stored: false,
            revoke_attempted: false,
            revoke_error: reason,
        });
        const notice = await screen.findByRole('status');
        expect(notice).toHaveTextContent(/could not read/i);
        expect(notice).toHaveTextContent(
            'https://myaccount.google.com/permissions',
        );
        expect(notice).not.toHaveTextContent(
            enMessages['settings.connectors.nothingStored'],
        );
    });

    it('says the revoke was attempted and failed', async () => {
        await renderSection();
        const reason = 'GAIA revoke endpoint rejected the request (status 500)';
        await disconnected({
            connector_id: 'google',
            revoke_supported: true,
            revoked_remotely: false,
            revoke_attempted: true,
            nothing_stored: false,
            revoke_error: reason,
        });
        const notice = await screen.findByRole('status');
        expect(notice).toHaveTextContent(
            'Disconnected locally, but the provider did not confirm the revoke',
        );
        expect(notice).toHaveTextContent(reason);
    });

    it('stays quiet after a confirmed revoke', async () => {
        await renderSection();
        await disconnected({
            connector_id: 'google',
            revoke_supported: true,
            revoked_remotely: true,
            nothing_stored: false,
            revoke_error: null,
        });
        await waitFor(() => expect(mocked.getConnector).toHaveBeenCalledWith('google'));
        expect(screen.queryByRole('status')).toBeNull();
    });
});
