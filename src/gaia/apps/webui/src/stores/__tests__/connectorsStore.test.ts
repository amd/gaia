// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useConnectionsStore } from '../connectorsStore';
import type { ConnectorInfo, ConnectorRow } from '../../types';

vi.mock('../../services/api');

const conn = (provider: string): ConnectorInfo => ({
    provider,
    account_email: `test@${provider}.com`,
    scopes: [],
    connected_at: null,
});

describe('connectorsStore — pendingMailProvider clearing', () => {
    beforeEach(() => {
        useConnectionsStore.setState({
            connections: [],
            grants: {},
            loading: false,
            error: null,
            pendingMailProvider: undefined,
        });
    });

    describe('removeConnection', () => {
        it('clears pendingMailProvider when the removed provider matches', () => {
            useConnectionsStore.setState({
                connections: [conn('google'), conn('microsoft')],
                pendingMailProvider: 'google',
            });
            useConnectionsStore.getState().removeConnection('google');
            expect(useConnectionsStore.getState().pendingMailProvider).toBeUndefined();
        });

        it('preserves pendingMailProvider when a different provider is removed', () => {
            useConnectionsStore.setState({
                connections: [conn('google'), conn('microsoft')],
                pendingMailProvider: 'google',
            });
            useConnectionsStore.getState().removeConnection('microsoft');
            expect(useConnectionsStore.getState().pendingMailProvider).toBe('google');
        });

        it('is a no-op on pendingMailProvider when it is already undefined', () => {
            useConnectionsStore.setState({
                connections: [conn('google')],
                pendingMailProvider: undefined,
            });
            useConnectionsStore.getState().removeConnection('google');
            expect(useConnectionsStore.getState().pendingMailProvider).toBeUndefined();
        });
    });

    describe('setConnections', () => {
        it('clears pendingMailProvider when that provider is no longer in the new connection list', () => {
            useConnectionsStore.setState({
                connections: [conn('google'), conn('microsoft')],
                pendingMailProvider: 'microsoft',
            });
            // Simulate a SSE bulk refresh that drops microsoft
            useConnectionsStore.getState().setConnections([conn('google')]);
            expect(useConnectionsStore.getState().pendingMailProvider).toBeUndefined();
        });

        it('preserves pendingMailProvider when the provider is still connected after refresh', () => {
            useConnectionsStore.setState({
                connections: [conn('google'), conn('microsoft')],
                pendingMailProvider: 'google',
            });
            useConnectionsStore.getState().setConnections([conn('google')]);
            expect(useConnectionsStore.getState().pendingMailProvider).toBe('google');
        });

        it('preserves undefined pendingMailProvider across any refresh', () => {
            useConnectionsStore.setState({
                connections: [conn('google')],
                pendingMailProvider: undefined,
            });
            useConnectionsStore.getState().setConnections([]);
            expect(useConnectionsStore.getState().pendingMailProvider).toBeUndefined();
        });
    });
});

// ---------------------------------------------------------------------------
// Helper: build a minimal ConnectorRow for test fixtures.
// ---------------------------------------------------------------------------
function row(overrides: Partial<ConnectorRow> & { id: string }): ConnectorRow {
    return {
        display_name: overrides.id,
        icon: null,
        category: 'oauth',
        tier: 'free',
        type: 'oauth_pkce',
        description: '',
        product_url: null,
        docs_url: null,
        configured: false,
        configurable: true,
        config_error: null,
        enabled: true,
        account_id: null,
        scopes: [],
        activations: {},
        last_tested_at: null,
        mcp_env_keys: [],
        default_scopes: [],
        available_scopes: [],
        ...overrides,
    } as ConnectorRow;
}

describe('connectorsStore — refresh()', () => {
    // Import after vi.mock hoisting resolves.
    // eslint-disable-next-line @typescript-eslint/consistent-type-imports
    let api: typeof import('../../services/api');

    beforeEach(async () => {
        api = await import('../../services/api');
        vi.resetAllMocks();
        useConnectionsStore.setState({
            connections: [],
            grants: {},
            loading: false,
            error: null,
            pendingMailProvider: undefined,
        });
    });

    it('populates connections with only configured oauth_pkce rows', async () => {
        vi.mocked(api.listConnectors).mockResolvedValue({
            connectors: [
                row({ id: 'google', type: 'oauth_pkce', configured: true, account_id: 'me@gmail.com', scopes: ['s'] }),
                row({ id: 'microsoft', type: 'oauth_pkce', configured: true, account_id: 'me@outlook.com', scopes: [] }),
                row({ id: 'foo', type: 'oauth_pkce', configured: false }),
                row({ id: 'mcp-git', type: 'mcp_server', configured: true }),
                { id: 'broken', error: 'unavailable' } as unknown as ConnectorRow,
            ],
        });
        vi.mocked(api.listConnectorGrants).mockResolvedValue({ grants: {} });

        await useConnectionsStore.getState().refresh();

        const { connections } = useConnectionsStore.getState();
        expect(connections).toHaveLength(2);
        expect(connections[0]).toEqual<ConnectorInfo>({
            provider: 'google',
            account_email: 'me@gmail.com',
            scopes: ['s'],
            connected_at: null,
        });
        expect(connections[1]).toEqual<ConnectorInfo>({
            provider: 'microsoft',
            account_email: 'me@outlook.com',
            scopes: [],
            connected_at: null,
        });
    });

    it('calls listConnectorGrants once per connected OAuth provider', async () => {
        vi.mocked(api.listConnectors).mockResolvedValue({
            connectors: [
                row({ id: 'google', type: 'oauth_pkce', configured: true, account_id: 'me@gmail.com', scopes: [] }),
                row({ id: 'microsoft', type: 'oauth_pkce', configured: true, account_id: 'me@outlook.com', scopes: [] }),
            ],
        });
        vi.mocked(api.listConnectorGrants)
            .mockResolvedValueOnce({ grants: { 'builtin:email': ['read'] } })
            .mockResolvedValueOnce({ grants: { 'builtin:email': ['read', 'send'] } });

        await useConnectionsStore.getState().refresh();

        expect(api.listConnectorGrants).toHaveBeenCalledTimes(2);
        expect(api.listConnectorGrants).toHaveBeenCalledWith('google');
        expect(api.listConnectorGrants).toHaveBeenCalledWith('microsoft');

        const { grants } = useConnectionsStore.getState();
        expect(grants['google']).toEqual({ 'builtin:email': ['read'] });
        expect(grants['microsoft']).toEqual({ 'builtin:email': ['read', 'send'] });
    });

    it('defaults grants to {} for a provider when listConnectorGrants rejects, but still populates the other provider', async () => {
        vi.mocked(api.listConnectors).mockResolvedValue({
            connectors: [
                row({ id: 'google', type: 'oauth_pkce', configured: true, account_id: 'me@gmail.com', scopes: [] }),
                row({ id: 'microsoft', type: 'oauth_pkce', configured: true, account_id: 'me@outlook.com', scopes: [] }),
            ],
        });
        vi.mocked(api.listConnectorGrants)
            .mockRejectedValueOnce(new Error('network error'))
            .mockResolvedValueOnce({ grants: { 'builtin:email': ['send'] } });

        await useConnectionsStore.getState().refresh();

        const { grants, error } = useConnectionsStore.getState();
        // The failing provider falls back to {} — no top-level error.
        expect(error).toBeNull();
        // One provider got empty grants, the other populated normally.
        const googleGrants = grants['google'];
        const msGrants = grants['microsoft'];
        expect(googleGrants === undefined || Object.keys(googleGrants).length === 0).toBe(true);
        expect(msGrants).toEqual({ 'builtin:email': ['send'] });
    });

    it('sets error and leaves connections empty when listConnectors rejects', async () => {
        vi.mocked(api.listConnectors).mockRejectedValue(new Error('backend down'));

        await useConnectionsStore.getState().refresh();

        const { error, connections } = useConnectionsStore.getState();
        expect(error).not.toBeNull();
        expect(connections).toEqual([]);
    });
});
