// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Several accounts of one OAuth connector: a tile that counts them, a row per
// account with its own state and actions, and nothing new for one account.

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import { stripIsolates } from '../../i18n/registry';
import { useChatStore } from '../../stores/chatStore';
import type { ConnectorAccount, ConnectorRow } from '../../types';
import { middleCut } from '../../utils/connectorAccounts';
import { ConnectorsSection } from '../ConnectorsSection';

vi.mock('../../services/api');
vi.mock('../../hooks/useConnectorsSSE', () => ({ useConnectorsSSE: () => {} }));

const mocked = vi.mocked(api);

const account = (email: string, over: Partial<ConnectorAccount> = {}): ConnectorAccount => ({
    account_id: `id-${email}`, email, status: 'ok', is_default: false, ...over,
});

function google(accounts: ConnectorAccount[]): ConnectorRow {
    const def = accounts.find((a) => a.is_default && a.status === 'ok');
    return {
        id: 'google', display_name: 'Google', type: 'oauth_pkce', description: '', icon: null, category: 'email',
        tier: 'core', product_url: null, docs_url: null, configured: accounts.some((a) => a.status === 'ok'),
        configurable: true, config_error: null, enabled: true, account_id: def?.email ?? null, accounts,
        scopes: ['openid', 'gmail.readonly'], activations: {}, last_tested_at: null, mcp_env_keys: [],
        default_scopes: [], available_scopes: [], oauth_setup_fields: [],
        oauth_client: { source: 'env', client_id: 'x', has_secret: false },
    } as ConnectorRow;
}

const JANE = account('jane@example.com', { is_default: true });
const WORK = account('jane.work@example.com');
const EXPIRED = account('jane@outlook-example.com', { status: 'reauth_required' });

const tile = () => screen.getByText('Google').closest('.connector-tile') as HTMLElement;
const open = async (row: ConnectorRow) => {
    mocked.listConnectors.mockResolvedValue({ connectors: [row] });
    mocked.getConnector.mockResolvedValue(row);
    render(<ConnectorsSection />);
    await screen.findByText('Google');
    fireEvent.click(within(tile()).getByRole('button', { name: /Google/ }));
};

beforeEach(() => {
    vi.clearAllMocks();
    mocked.listAgentMcps.mockResolvedValue({ agent_mcps: [] });
    mocked.listConnectorGrants.mockResolvedValue({ grants: {} } as never);
    mocked.authorizeConnector.mockResolvedValue({ flow_id: 'f', authorization_url: 'https://accounts.example/auth' });
    vi.spyOn(window, 'open').mockReturnValue(null);
});
afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    vi.restoreAllMocks();
});

describe('Settings with one account', () => {
    it('looks as before, plus a quiet link that adds another account', async () => {
        await open(google([JANE]));
        expect(within(tile()).getByText('jane@example.com')).toBeInTheDocument();
        expect(screen.queryByRole('list', { name: 'Google accounts' })).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Disconnect' })).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Add another Google account' }));
        await vi.waitFor(() => expect(mocked.authorizeConnector).toHaveBeenCalledWith(
            'google', ['openid', 'gmail.readonly'], [], { addAccount: true },
        ));
        expect(window.open).toHaveBeenCalledWith('https://accounts.example/auth', '_blank', 'noopener');
    });

    it('says the sign-in expired instead of Connected, and reconnects that account', async () => {
        await open(google([{ ...JANE, status: 'reauth_required' }]));
        expect(within(tile()).getAllByText('Sign-in expired').length).toBeGreaterThan(0);
        expect(screen.queryByRole('button', { name: /^Connect$/ })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Reconnect jane@example.com' }));
        await vi.waitFor(() => expect(mocked.authorizeConnector).toHaveBeenCalledWith(
            'google', expect.any(Array), [], { account: JANE.account_id },
        ));
    });
});

describe('Settings with several accounts', () => {
    it('counts them on the tile and flags the one that needs signing in', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, WORK, EXPIRED])] });
        render(<ConnectorsSection />);
        await screen.findByText('Google');
        expect(within(tile()).getByText('3 accounts')).toBeInTheDocument();
        expect(within(tile()).getByText('Sign-in expired')).toBeInTheDocument();
        // Still listed under "Connected": an expired sign-in is still the user's account.
        expect(screen.getByText('Connected').closest('details')).toContainElement(tile());
    });

    it('lists each account with its state, default and actions', async () => {
        await open(google([JANE, WORK, EXPIRED]));
        const rows = within(screen.getByRole('list', { name: 'Google accounts' })).getAllByRole('listitem');
        expect(rows).toHaveLength(3);
        expect(within(rows[0]).getByText('Default')).toBeInTheDocument();
        expect(within(rows[0]).getByText('Connected')).toBeInTheDocument();
        expect(within(rows[0]).queryByRole('button', { name: /^Make/ })).not.toBeInTheDocument();
        // Each button names its account, so a screen reader can tell the rows apart.
        expect(within(rows[1]).getByRole('button', { name: 'Make jane.work@example.com the default' })).toHaveTextContent('Make default');
        expect(within(rows[2]).getByText('Sign-in expired')).toBeInTheDocument();
        expect(within(rows[2]).getByRole('button', { name: 'Reconnect jane@outlook-example.com' })).toHaveTextContent('Reconnect');
        // The whole-connector Disconnect gives way to one per account.
        expect(rows.map((r) => within(r).getByRole('button', { name: /^Disconnect / }).getAttribute('aria-label')))
            .toEqual(['Disconnect jane@example.com', 'Disconnect jane.work@example.com', 'Disconnect jane@outlook-example.com']);
        expect(screen.getByRole('button', { name: 'Add another Google account' })).toBeInTheDocument();
    });

    it('makes another account the default', async () => {
        mocked.setDefaultConnectorAccount.mockResolvedValue({ accounts: [] });
        await open(google([JANE, WORK]));
        fireEvent.click(screen.getByRole('button', { name: 'Make jane.work@example.com the default' }));
        await vi.waitFor(() => expect(mocked.setDefaultConnectorAccount).toHaveBeenCalledWith('google', WORK.account_id));
    });

    it('asks before disconnecting one account, naming its address', async () => {
        mocked.disconnectConnectorAccount.mockResolvedValue();
        await open(google([JANE, WORK]));
        const rows = within(screen.getByRole('list', { name: 'Google accounts' })).getAllByRole('listitem');
        fireEvent.click(within(rows[1]).getByRole('button', { name: 'Disconnect jane.work@example.com' }));
        const ask = screen.getByRole('alertdialog');
        expect(stripIsolates(ask.textContent ?? '')).toContain('Disconnect jane.work@example.com?');
        expect(ask).toHaveTextContent('Your other Google accounts keep working.');
        expect(mocked.disconnectConnectorAccount).not.toHaveBeenCalled();
        fireEvent.click(within(ask).getByRole('button', { name: 'Disconnect' }));
        await vi.waitFor(() => expect(mocked.disconnectConnectorAccount).toHaveBeenCalledWith('google', WORK.account_id));
        expect(mocked.disconnectConnector).not.toHaveBeenCalled();
    });

    it('names an account stored before GAIA told accounts apart by its address', async () => {
        mocked.setDefaultConnectorAccount.mockResolvedValue({ accounts: [] });
        mocked.disconnectConnectorAccount.mockResolvedValue();
        const legacy = account('jane.old@example.com', { account_id: 'default' });
        await open(google([JANE, legacy]));
        const rows = within(screen.getByRole('list', { name: 'Google accounts' })).getAllByRole('listitem');
        fireEvent.click(within(rows[1]).getByRole('button', { name: 'Make jane.old@example.com the default' }));
        await vi.waitFor(() => expect(mocked.setDefaultConnectorAccount).toHaveBeenCalledWith('google', 'jane.old@example.com'));
        const disconnect = within(rows[1]).getByRole('button', { name: 'Disconnect jane.old@example.com' });
        await vi.waitFor(() => expect(disconnect).toBeEnabled());
        fireEvent.click(disconnect);
        fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Disconnect' }));
        await vi.waitFor(() => expect(mocked.disconnectConnectorAccount).toHaveBeenCalledWith('google', 'jane.old@example.com'));
    });

    it('names each account on its buttons in Arabic too', async () => {
        act(() => useChatStore.getState().setLocale('ar'));
        await open(google([JANE, WORK]));
        const rows = within(screen.getByRole('list', { name: /حسابات/ })).getAllByRole('listitem');
        const label = within(rows[1]).getAllByRole('button').map((b) => stripIsolates(b.getAttribute('aria-label') ?? ''));
        expect(label).toEqual([
            expect.stringMatching(/^جعل jane\.work@example\.com الحساب الافتراضي/),
            expect.stringMatching(/^قطع اتصال jane\.work@example\.com/),
        ]);
    });

    it('cuts a long address in its middle and keeps it whole on hover', async () => {
        const long = account('someone.with.a.very.long.address.indeed@example.com', { is_default: true });
        await open(google([long, WORK]));
        const shown = screen.getByTitle(long.email);
        expect(shown.textContent).toBe(middleCut(long.email));
        expect(shown.textContent).toMatch(/^someone.*…@example\.com$/);
        expect(shown).toHaveAttribute('dir', 'ltr');
    });

    it('counts accounts in Arabic', async () => {
        act(() => useChatStore.getState().setLocale('ar'));
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, WORK])] });
        render(<ConnectorsSection />);
        await screen.findByText('Google');
        expect(stripIsolates(tile().textContent ?? '')).toContain('الحسابات: 2');
    });
});

describe('Disconnect on a tile that shows one account', () => {
    const SAM = account('sam@example.com');
    // Another window or the CLI added an account since this tile last loaded.
    const severalNow = (choices: string[]) => Object.assign(new Error('Google has 2 connected accounts.'), {
        httpStatus: 409,
        body: { error: 'account_choice_required', connector_id: 'google', choices, message: 'Google has 2 connected accounts.' },
    });

    it('disconnects at once when the server finds one account, as before', async () => {
        mocked.disconnectConnector.mockResolvedValue();
        await open(google([JANE]));
        fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
        await vi.waitFor(() => expect(mocked.disconnectConnector).toHaveBeenCalledWith('google'));
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    });

    it('names every account the server found and removes them only once the user agrees', async () => {
        mocked.disconnectConnector.mockRejectedValueOnce(severalNow(['jane@example.com', 'sam@example.com']));
        mocked.disconnectConnector.mockResolvedValueOnce();
        await open(google([JANE]));
        fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
        const ask = await screen.findByRole('alertdialog');
        expect(stripIsolates(ask.textContent ?? '')).toContain('Disconnect all 2 Google accounts?');
        expect(within(ask).getByRole('list').textContent).toBe('jane@example.com' + 'sam@example.com');
        expect(mocked.disconnectConnector).toHaveBeenCalledTimes(1);
        fireEvent.click(within(ask).getByRole('button', { name: 'Disconnect all' }));
        await vi.waitFor(() => expect(mocked.disconnectConnector).toHaveBeenLastCalledWith('google', { all: true }));
        await vi.waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
        expect(mocked.disconnectConnectorAccount).not.toHaveBeenCalled();
    });

    it('keeps every account on Cancel and shows them all', async () => {
        mocked.disconnectConnector.mockRejectedValueOnce(severalNow(['jane@example.com', 'sam@example.com']));
        await open(google([JANE]));
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, SAM])] });
        mocked.getConnector.mockResolvedValue(google([JANE, SAM]));
        fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
        fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Cancel' }));
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
        await screen.findByRole('list', { name: 'Google accounts' });
        expect(mocked.disconnectConnector).toHaveBeenCalledTimes(1);
    });

    it('asks in Arabic too', async () => {
        act(() => useChatStore.getState().setLocale('ar'));
        mocked.disconnectConnector.mockRejectedValueOnce(severalNow(['jane@example.com', 'sam@example.com']));
        await open(google([JANE]));
        fireEvent.click(screen.getByRole('button', { name: /قطع الاتصال/ }));
        const ask = await screen.findByRole('alertdialog');
        const text = stripIsolates(ask.textContent ?? '');
        expect(text).toContain('هل تريد قطع اتصال جميع حسابات Google (عددها 2)؟');
        expect(text).toContain('jane@example.com');
        expect(text).toContain('sam@example.com');
    });
});
