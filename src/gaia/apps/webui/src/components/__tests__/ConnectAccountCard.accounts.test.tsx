// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A chat card for a provider that already has accounts: sign in to the one that
// stopped working, or use a different one; never connect the provider afresh.

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { stripIsolates } from '../../i18n/registry';
import { useChatStore } from '../../stores/chatStore';
import type { AgentInfo, ConnectorAccount, ConnectorRow } from '../../types';
import { referencedProviderConnected } from '../ConnectorRetryBanner';
import { EmailConnectCta } from '../email/EmailConnectCta';
import { ConnectAccountCard } from '../render/ConnectAccountCard';

vi.mock('../../services/api');
vi.mock('../../hooks/useConnectorsSSE', () => ({ useConnectorsSSE: () => {} }));

const mocked = vi.mocked(api);

const account = (email: string, over: Partial<ConnectorAccount> = {}): ConnectorAccount => ({
    account_id: `id-${email}`, email, status: 'ok', is_default: false, ...over,
});

function google(accounts: ConnectorAccount[]): ConnectorRow {
    return {
        id: 'google', display_name: 'Google', icon: null, category: 'email', tier: 'verified', type: 'oauth_pkce',
        description: '', product_url: null, docs_url: null, configured: accounts.some((a) => a.status === 'ok'),
        configurable: true, config_error: null, enabled: true, account_id: accounts[0]?.email ?? null, accounts,
        scopes: [], activations: {}, last_tested_at: null, mcp_env_keys: [],
        default_scopes: ['openid'], available_scopes: ['openid'], oauth_setup_fields: [],
    } as ConnectorRow;
}

const JANE = account('jane@example.com', { is_default: true });
const WORK = account('jane.work@example.com');
const EXPIRED = account('jane.old@example.com', { status: 'reauth_required' });

const FLAGSHIP = {
    id: 'gaia',
    namespaced_agent_id: 'installed:gaia',
    required_connections: [{ connector_id: 'google' }],
} as unknown as AgentInfo;
const EMAIL = { id: 'email', namespaced_agent_id: 'installed:email', required_connections: [{ connector_id: 'google' }] } as unknown as AgentInfo;

const card = { kind: 'connect_account', providers: ['google'], agent_id: 'installed:gaia' };
const shown = () => stripIsolates(document.body.textContent ?? '');

beforeEach(() => {
    vi.clearAllMocks();
    mocked.getConnector.mockResolvedValue(google([JANE]));
    mocked.authorizeConnector.mockResolvedValue({ flow_id: 'f', authorization_url: 'https://accounts.example/auth' });
    act(() => useChatStore.getState().setAgents([FLAGSHIP, EMAIL]));
    vi.spyOn(window, 'open').mockReturnValue(null);
});
afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    vi.restoreAllMocks();
});

describe('Connect card with several accounts', () => {
    it('reconnects the account that failed, or adds a different one, and never offers a fresh Connect', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, EXPIRED])] });
        render(<ConnectAccountCard data={card} />);
        const reconnect = await screen.findByRole('button', { name: /Reconnect/ });
        expect(stripIsolates(reconnect.textContent ?? '')).toBe('Reconnect jane.old@example.com');
        expect(screen.queryByRole('button', { name: 'Connect Google' })).not.toBeInTheDocument();
        // One account still waits on a sign-in, so the card doesn't call the provider connected.
        expect(shown()).not.toContain('Google is connected');

        fireEvent.click(reconnect);
        await waitFor(() => expect(mocked.authorizeConnector).toHaveBeenCalledWith(
            'google', [], ['installed:gaia'], { account: EXPIRED.account_id },
        ));
        fireEvent.click(screen.getByRole('button', { name: 'Use a different account' }));
        await waitFor(() => expect(mocked.authorizeConnector).toHaveBeenLastCalledWith(
            'google', [], ['installed:gaia'], { addAccount: true },
        ));
    });

    it('keeps the plain button for one working account', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE])] });
        render(<ConnectAccountCard data={{ ...card, reconnect: ['google'] }} />);
        expect(await screen.findByRole('button', { name: 'Reconnect Google' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Use a different account' })).not.toBeInTheDocument();
    });

    it('signs in to the default account again when every account works', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([WORK, JANE])] });
        render(<ConnectAccountCard data={{ ...card, reconnect: ['google'] }} />);
        const reconnect = await screen.findByRole('button', { name: /Reconnect/ });
        expect(stripIsolates(reconnect.textContent ?? '')).toBe('Reconnect jane@example.com');
    });
});

describe('email Connect button with several accounts', () => {
    it('offers the failed account and a different one, in Arabic too', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, EXPIRED])] });
        act(() => useChatStore.getState().setLocale('ar'));
        render(<EmailConnectCta content="AUTH_REQUIRED: Authentication required for google" />);
        await waitFor(() => expect(shown()).toContain('jane.old@example.com'));
        expect(shown()).toContain('استخدام حساب آخر');
        expect(shown()).not.toMatch(/Connect Google/);
        fireEvent.click(screen.getByTitle('jane.old@example.com'));
        await waitFor(() => expect(mocked.authorizeConnector).toHaveBeenCalledWith(
            'google', ['openid'], ['installed:email'], { account: EXPIRED.account_id },
        ));
    });

    it('keeps Connect Google for a user with no account', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([])] });
        render(<EmailConnectCta content="NOT_CONNECTED: google is not currently connected." />);
        expect(await screen.findByRole('button', { name: 'Connect Google' })).toBeInTheDocument();
    });
});

describe('the retry cue with several accounts', () => {
    it('waits until the account that failed is signed in again', () => {
        const reply = 'AUTH_REQUIRED: Authentication required for google';
        expect(referencedProviderConnected(reply, [google([JANE, EXPIRED])])).toBe(false);
        expect(referencedProviderConnected(reply, [google([JANE, WORK])])).toBe(true);
    });
});
