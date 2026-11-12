// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The Connect button a mail or calendar tool sends when no account is usable.
// It arrives as a typed card, so it shows whatever the model wrote.

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { stripIsolates } from '../../i18n/registry';
import { useChatStore } from '../../stores/chatStore';
import type { AgentInfo, ConnectorRow } from '../../types';
import { referencedProviderConnected, connectedProviderLabel } from '../ConnectorRetryBanner';
import { RenderCard } from '../render/RenderCard';
import {
    ConnectAccountCard,
    connectCardProviders,
    grantIdsFor,
    hasConnectCard,
    isConnectAccountPayload,
} from '../render/ConnectAccountCard';

vi.mock('../../services/api');

const mocked = vi.mocked(api);

function connector(id: string, configured: boolean, over: Partial<ConnectorRow> = {}): ConnectorRow {
    return {
        id, display_name: id, icon: null, category: 'email', tier: 'verified', type: 'oauth_pkce',
        description: '', product_url: null, docs_url: null, configured, configurable: true,
        config_error: null, enabled: true, account_id: configured ? 'acct' : null, scopes: [],
        activations: {}, last_tested_at: null, mcp_env_keys: [],
        default_scopes: ['openid'], available_scopes: ['openid', 'mail.read'], oauth_setup_fields: [],
        ...over,
    } as ConnectorRow;
}

const FLAGSHIP = {
    id: 'gaia',
    namespaced_agent_id: 'installed:gaia',
    required_connections: [{ connector_id: 'google' }, { connector_id: 'microsoft' }],
} as unknown as AgentInfo;

const card = (providers: string[] = ['google', 'microsoft']) => ({
    kind: 'connect_account',
    providers,
    agent_id: 'installed:gaia',
    // What the stream adds for the tool row; the card ignores it.
    success: false,
    status: 'error',
    display_message: 'Needs a connected account',
});

const shown = () => stripIsolates(document.body.textContent ?? '');

beforeEach(() => {
    vi.clearAllMocks();
    mocked.listConnectors.mockResolvedValue({ connectors: [connector('google', false), connector('microsoft', false)] });
    mocked.getConnector.mockImplementation(async (id: string) => connector(id, false));
    mocked.authorizeConnector.mockResolvedValue({ flow_id: 'f', authorization_url: 'https://accounts.example/auth' });
    act(() => useChatStore.getState().setAgents([FLAGSHIP]));
    vi.spyOn(window, 'open').mockReturnValue(null);
});

afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    vi.restoreAllMocks();
});

describe('connect_account payload', () => {
    it('accepts the card a tool sends', () => {
        expect(isConnectAccountPayload(card())).toBe(true);
    });

    it.each([
        null,
        'google',
        { kind: 'connect_account' },
        { kind: 'connect_account', providers: [] },
        { kind: 'connect_account', providers: [1] },
        { kind: 'table', providers: ['google'] },
        { kind: 'connect_account', providers: ['google'], agent_id: 7 },
    ])('rejects %j', (value) => {
        expect(isConnectAccountPayload(value)).toBe(false);
    });

    it('is registered, so the stream draws it without a model marker', async () => {
        render(<RenderCard render="connect_account" data={card(['google'])} />);
        expect(await screen.findByRole('button', { name: 'Connect Google' })).toBeTruthy();
    });

    it('shows an invalid payload as unsupported instead of an empty button row', () => {
        render(<RenderCard render="connect_account" data={{ kind: 'connect_account', providers: [] }} />);
        expect(screen.queryByRole('button', { name: /Connect/ })).toBeNull();
    });
});

describe('grantIdsFor', () => {
    it('grants only the agent that asked, and only for a connector it declares', () => {
        const other = { namespaced_agent_id: 'installed:other', required_connections: [{ connector_id: 'google' }] };
        expect(grantIdsFor([FLAGSHIP, other], 'installed:gaia', 'google')).toEqual(['installed:gaia']);
        expect(grantIdsFor([FLAGSHIP, other], 'installed:gaia', 'microsoft_work')).toEqual([]);
        expect(grantIdsFor([other], 'installed:gaia', 'google')).toEqual([]);
        expect(grantIdsFor([FLAGSHIP], undefined, 'google')).toEqual([]);
    });
});

describe('ConnectAccountCard', () => {
    it('offers one button per account, by name', async () => {
        render(<ConnectAccountCard data={card()} />);
        expect(await screen.findByRole('button', { name: 'Connect Google' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Connect Microsoft' })).toBeTruthy();
        expect(shown()).toContain('GAIA needs you to connect an account for this.');
        // The whole point: no terminal command anywhere on the card.
        expect(shown()).not.toMatch(/gaia connectors|terminal/i);
    });

    it('starts sign-in with every scope and gives the asking agent access', async () => {
        render(<ConnectAccountCard data={card(['google'])} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Connect Google' }));
        await waitFor(() => expect(mocked.authorizeConnector).toHaveBeenCalledTimes(1));
        expect(mocked.authorizeConnector).toHaveBeenCalledWith('google', ['openid', 'mail.read'], ['installed:gaia']);
        expect(window.open).toHaveBeenCalledWith('https://accounts.example/auth', '_blank', 'noopener');
        expect(await screen.findByText(/A browser tab opened for sign-in/)).toBeTruthy();
    });

    it('says so when sign-in cannot start, and keeps the button', async () => {
        mocked.authorizeConnector.mockRejectedValue(new Error('Google sign-in is not set up on this PC.'));
        render(<ConnectAccountCard data={card(['google'])} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Connect Google' }));
        expect((await screen.findByRole('alert')).textContent).toContain('Google sign-in is not set up on this PC.');
        expect(screen.getByRole('button', { name: 'Connect Google' })).toBeTruthy();
        expect(window.open).not.toHaveBeenCalled();
    });

    it('turns to "ask again" once the account is connected', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [connector('google', true)] });
        render(<ConnectAccountCard data={card(['google'])} />);
        expect(await screen.findByText('Google is connected')).toBeTruthy();
        expect(shown()).toContain('Your account is connected. Ask again and GAIA will carry on.');
        // Still reachable, for a connection that lacks a permission.
        expect(screen.getByRole('button', { name: 'Reconnect Google' })).toBeTruthy();
    });

    it('does not call an expired account fixed just because it is still connected', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [connector('google', true)] });
        render(<ConnectAccountCard data={{ ...card(['google']), reconnect: ['google'] }} />);
        expect(await screen.findByRole('button', { name: 'Reconnect Google' })).toBeTruthy();
        expect(shown()).toContain('GAIA needs you to connect an account for this.');
        expect(shown()).not.toContain('Ask again');
    });

    it('opens Connectors in Settings from the card', async () => {
        render(<ConnectAccountCard data={card(['google'])} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Open Connectors in Settings' }));
        expect(useChatStore.getState().settingsSection).toBe('connectors');
        act(() => useChatStore.setState({ settingsSection: null }));
    });

    it('follows the interface language', async () => {
        act(() => useChatStore.getState().setLocale('ar'));
        render(<ConnectAccountCard data={card(['google'])} />);
        await screen.findAllByRole('button');
        expect(shown()).toContain('ربط Google');
        expect(shown()).toContain('يحتاج GAIA إلى أن تربط حسابًا لإتمام هذا.');
    });
});

describe('the retry cue after a Connect card', () => {
    it('reads the card off a message', () => {
        expect(connectCardProviders({ cards: [{ render: 'connect_account', data: card(['google']) }] })).toEqual(['google']);
        expect(connectCardProviders({ cards: [{ render: 'table', data: {} }] })).toEqual([]);
        expect(connectCardProviders(undefined)).toEqual([]);
    });

    it('waits only on accounts that were not connected when the tool ran', () => {
        const expired = { cards: [{ render: 'connect_account', data: { ...card(['google', 'microsoft']), reconnect: ['google'] } }] };
        expect(connectCardProviders(expired)).toEqual(['microsoft']);
        const onlyExpired = { cards: [{ render: 'connect_account', data: { ...card(['google']), reconnect: ['google'] } }] };
        expect(connectCardProviders(onlyExpired)).toEqual([]);
        // Still a card, so the reply's wording is not read for a retry cue.
        expect(hasConnectCard(onlyExpired)).toBe(true);
        expect(hasConnectCard({ cards: [{ render: 'table', data: {} }] })).toBe(false);
    });

    it('offers "ask again" when a named account connects, whatever the reply said', () => {
        const rows = [connector('google', false), connector('microsoft', true, { display_name: 'Microsoft' })];
        const reply = 'Your mailbox is not set up yet.';
        expect(referencedProviderConnected(reply, rows, ['google'])).toBe(false);
        expect(referencedProviderConnected(reply, rows, ['google', 'microsoft'])).toBe(true);
        expect(connectedProviderLabel(reply, rows, ['google', 'microsoft'])).toBe('Microsoft');
    });
});
