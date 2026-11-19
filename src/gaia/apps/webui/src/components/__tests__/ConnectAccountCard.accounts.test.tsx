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
import { ConnectorRetryBanner, referencedProviderConnected } from '../ConnectorRetryBanner';
import { EmailConnectCta } from '../email/EmailConnectCta';
import { ConnectAccountCard, isConnectAccountPayload } from '../render/ConnectAccountCard';

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
const OTHER = { id: 'my-agent', namespaced_agent_id: 'installed:my-agent', required_connections: [{ connector_id: 'google' }] } as unknown as AgentInfo;

const card = { kind: 'connect_account', providers: ['google'], agent_id: 'installed:gaia' };
const shown = () => stripIsolates(document.body.textContent ?? '');

beforeEach(() => {
    vi.clearAllMocks();
    mocked.getConnector.mockResolvedValue(google([JANE]));
    mocked.authorizeConnector.mockResolvedValue({ flow_id: 'f', authorization_url: 'https://accounts.example/auth' });
    act(() => useChatStore.getState().setAgents([FLAGSHIP, OTHER]));
    vi.spyOn(window, 'open').mockReturnValue(null);
});
afterEach(() => {
    act(() => useChatStore.getState().setLocale('en'));
    vi.restoreAllMocks();
});

describe('Connect card with several accounts', () => {
    it('reconnects the account that failed, or adds a different one, and never offers a fresh Connect', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, EXPIRED])] });
        render(<ConnectAccountCard data={{ ...card, account: { google: EXPIRED.account_id } }} />);
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

    it('never guesses the default when the card names no account', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([WORK, JANE])] });
        render(<ConnectAccountCard data={{ ...card, reconnect: ['google'] }} />);
        expect(await screen.findByRole('button', { name: 'Reconnect Google' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Use a different account' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /Reconnect jane/ })).not.toBeInTheDocument();
    });

    it('offers the account the card names, even when it still reads as working', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, WORK])] });
        render(<ConnectAccountCard data={{ ...card, reconnect: ['google'], account: { google: WORK.account_id } }} />);
        const reconnect = await screen.findByRole('button', { name: /Reconnect jane/ });
        expect(stripIsolates(reconnect.textContent ?? '')).toBe('Reconnect jane.work@example.com');
        fireEvent.click(reconnect);
        await waitFor(() => expect(mocked.authorizeConnector).toHaveBeenCalledWith(
            'google', [], ['installed:gaia'], { account: WORK.account_id },
        ));
    });

    it('finds the named account by its address too', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, WORK, EXPIRED])] });
        render(<ConnectAccountCard data={{ ...card, account: { google: 'Jane.Work@example.com' } }} />);
        const reconnect = await screen.findByRole('button', { name: /Reconnect jane/ });
        expect(stripIsolates(reconnect.textContent ?? '')).toBe('Reconnect jane.work@example.com');
        // Only the named one: the unrelated expired account is not offered.
        expect(shown()).not.toContain('jane.old@example.com');
    });

    it('does not offer an unrelated expired account beside a working one', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, EXPIRED])] });
        render(<ConnectAccountCard data={card} />);
        expect(await screen.findByRole('button', { name: 'Use a different account' })).toBeInTheDocument();
        expect(shown()).not.toContain('jane.old@example.com');
    });

    it('names an account stored without a stable id by its address', async () => {
        const legacy = account('jane@example.com', { account_id: 'default', status: 'reauth_required', is_default: true });
        mocked.listConnectors.mockResolvedValue({ connectors: [google([legacy])] });
        render(<ConnectAccountCard data={card} />);
        fireEvent.click(await screen.findByRole('button', { name: /Reconnect jane/ }));
        await waitFor(() => expect(mocked.authorizeConnector).toHaveBeenCalledWith(
            'google', [], ['installed:gaia'], { account: 'jane@example.com' },
        ));
    });

    it('drops a card whose account names a provider it does not offer', () => {
        expect(isConnectAccountPayload({ ...card, account: { google: 'a' } })).toBe(true);
        expect(isConnectAccountPayload({ ...card, account: { microsoft: 'a' } })).toBe(false);
        expect(isConnectAccountPayload({ ...card, account: { google: '' } })).toBe(false);
        expect(isConnectAccountPayload({ ...card, account: 'a' })).toBe(false);
    });

    it('asks again once the offered account is back, and stops watching', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        const stale = account('jane.stale@example.com', { status: 'reauth_required' });
        const failed = account('jane.work@example.com', { status: 'reauth_required' });
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, stale, failed])] });
        render(<ConnectAccountCard data={{ ...card, account: { google: failed.account_id } }} />);
        fireEvent.click(await screen.findByRole('button', { name: /Reconnect jane.work/ }));
        await waitFor(() => expect(shown()).toContain('A browser tab opened'));
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, stale, { ...failed, status: 'ok' }])] });
        await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
        await waitFor(() => expect(shown()).toContain('Your account is connected'));
        const calls = mocked.listConnectors.mock.calls.length;
        await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
        expect(mocked.listConnectors.mock.calls.length).toBe(calls);
        vi.useRealTimers();
    });
    it('says a different account was added beside the default, not that Google is connected', async () => {
        const sam = account('sam@example.com');
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, WORK])] });
        render(<ConnectAccountCard data={card} />);
        fireEvent.click(await screen.findByRole('button', { name: 'Use a different account' }));
        await waitFor(() => expect(shown()).toContain('A browser tab opened'));
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE, WORK, sam])] });
        act(() => { window.dispatchEvent(new Event('focus')); });
        await waitFor(() => expect(shown()).toContain('Added sam@example.com as another Google account'));
        expect(shown()).not.toContain('Google is connected');
    });

    it('says Google is connected when the new account is the only one', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([])] });
        render(<ConnectAccountCard data={card} />);
        await screen.findByRole('button', { name: 'Connect Google' });
        mocked.listConnectors.mockResolvedValue({ connectors: [google([JANE])] });
        act(() => { window.dispatchEvent(new Event('focus')); });
        await waitFor(() => expect(shown()).toContain('Google is connected'));
        expect(shown()).not.toContain('Added');
    });
});

describe('email Connect button with several accounts', () => {
    it('offers the only account, expired, and a different one, in Arabic too', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([EXPIRED])] });
        act(() => useChatStore.getState().setLocale('ar'));
        render(<EmailConnectCta content="AUTH_REQUIRED: Authentication required for google" />);
        await waitFor(() => expect(shown()).toContain('jane.old@example.com'));
        expect(shown()).toContain('استخدام حساب آخر');
        expect(shown()).not.toMatch(/Connect Google/);
        fireEvent.click(screen.getByTitle('jane.old@example.com'));
        await waitFor(() => expect(mocked.authorizeConnector).toHaveBeenCalledWith(
            'google', [], ['installed:gaia'], { account: EXPIRED.account_id },
        ));
    });

    it('keeps Connect Google for a user with no account', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([])] });
        render(<EmailConnectCta content="NOT_CONNECTED: google is not currently connected." />);
        expect(await screen.findByRole('button', { name: 'Connect Google' })).toBeInTheDocument();
    });

    it('holds the button until it knows the accounts, so a fast click never signs in blind', async () => {
        let answer: (v: { connectors: ConnectorRow[] }) => void = () => {};
        mocked.listConnectors.mockReturnValue(new Promise((resolve) => { answer = resolve; }));
        render(<EmailConnectCta content="AUTH_REQUIRED: Authentication required for google" />);
        const early = screen.getByRole('button', { name: 'Connect Google' });
        expect(early).toBeDisabled();
        fireEvent.click(early);
        await act(async () => answer({ connectors: [google([{ ...JANE, status: 'reauth_required' }])] }));
        const reconnect = await screen.findByRole('button', { name: /Reconnect jane/ });
        expect(reconnect).toBeEnabled();
        expect(mocked.authorizeConnector).not.toHaveBeenCalled();
    });

    it('reads the accounts again when the window comes back', async () => {
        mocked.listConnectors.mockResolvedValue({ connectors: [google([])] });
        render(<EmailConnectCta content="AUTH_REQUIRED: Authentication required for google" />);
        await screen.findByRole('button', { name: 'Connect Google' });
        mocked.listConnectors.mockResolvedValue({ connectors: [google([{ ...JANE, status: 'reauth_required' }])] });
        act(() => { window.dispatchEvent(new Event('focus')); });
        expect(await screen.findByRole('button', { name: /Reconnect jane/ })).toBeInTheDocument();
    });
});

describe('the retry cue with several accounts', () => {
    const reply = 'AUTH_REQUIRED: Authentication required for google';

    it('is not held back by an unrelated expired account', () => {
        const before = [google([EXPIRED])];
        // A new account signed in; the stale one stays expired.
        expect(referencedProviderConnected(reply, [google([JANE, EXPIRED])], ['google'], before)).toBe(true);
        expect(referencedProviderConnected(reply, before, ['google'], before)).toBe(false);
    });

    it('waits for the account the card named', () => {
        const failed = account('jane.work@example.com', { status: 'reauth_required' });
        const before = [google([JANE, EXPIRED, failed])];
        const named = { google: failed.account_id };
        expect(referencedProviderConnected(reply, before, ['google'], before, named)).toBe(false);
        expect(referencedProviderConnected(reply, [google([JANE, { ...EXPIRED, status: 'ok' }, failed])], ['google'], before, named)).toBe(false);
        expect(referencedProviderConnected(reply, [google([JANE, EXPIRED, { ...failed, status: 'ok' }])], ['google'], before, named)).toBe(true);
    });

    it('with several accounts and none named, waits for one to start working', () => {
        const before = [google([JANE, EXPIRED])];
        expect(referencedProviderConnected(reply, before, ['google'], before)).toBe(false);
        expect(referencedProviderConnected(reply, [google([JANE, { ...EXPIRED, status: 'ok' }])], ['google'], before)).toBe(true);
    });

    it('offers Ask again once the expired account is back, and stops polling', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        const stale = account('jane.stale@example.com', { status: 'reauth_required' });
        mocked.listConnectors.mockResolvedValue({ connectors: [google([stale])] });
        render(<ConnectorRetryBanner content={reply} providers={['google']} onRetry={() => {}} />);
        await act(async () => { await vi.advanceTimersByTimeAsync(0); });
        expect(screen.queryByRole('button', { name: 'Ask again' })).not.toBeInTheDocument();
        mocked.listConnectors.mockResolvedValue({ connectors: [google([stale, JANE])] });
        await act(async () => { await vi.advanceTimersByTimeAsync(4000); });
        expect(await screen.findByRole('button', { name: 'Ask again' })).toBeInTheDocument();
        const calls = mocked.listConnectors.mock.calls.length;
        await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
        expect(mocked.listConnectors.mock.calls.length).toBe(calls);
        vi.useRealTimers();
    });

    it('stops polling after a while when nothing changes', async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        mocked.listConnectors.mockResolvedValue({ connectors: [google([EXPIRED])] });
        render(<ConnectorRetryBanner content={reply} providers={['google']} onRetry={() => {}} />);
        await act(async () => { await vi.advanceTimersByTimeAsync(15 * 60_000); });
        const calls = mocked.listConnectors.mock.calls.length;
        await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
        expect(mocked.listConnectors.mock.calls.length).toBe(calls);
        vi.useRealTimers();
    });
});
