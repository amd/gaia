// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useConnectionsStore } from '../connectorsStore';
import * as api from '../../services/api';
import type { ConnectorInfo, ConnectorRow } from '../../types';

vi.mock('../../services/api');

const conn = (provider: string): ConnectorInfo => ({
    provider,
    account_email: `test@${provider}.com`,
    scopes: [],
    connected_at: null,
});

/** Build a ConnectorRow with sane defaults; override only what a test cares about. */
const row = (overrides: Partial<ConnectorRow> & { id: string }): ConnectorRow => ({
    display_name: overrides.id,
    icon: null,
    category: 'mail',
    tier: 'standard',
    type: 'oauth_pkce',
    description: '',
    product_url: null,
    docs_url: null,
    configured: true,
    configurable: true,
    config_error: null,
    account_id: `test@${overrides.id}.com`,
    scopes: ['mail.read'],
    enabled: true,
    activations: {},
    last_tested_at: null,
    mcp_env_keys: [],
    default_scopes: [],
    available_scopes: [],
    oauth_setup_fields: [],
    ...overrides,
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

describe('connectorsStore — refresh() migrated to /api/connectors (#1630)', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        useConnectionsStore.setState({
            connections: [],
            grants: {},
            loading: false,
            error: null,
            pendingMailProvider: undefined,
        });
    });

    it('maps connected OAuth connectors to ConnectorInfo and drops MCP / unconfigured tiles', async () => {
        vi.mocked(api.listConnectors).mockResolvedValue({
            connectors: [
                row({ id: 'google' }),
                row({ id: 'microsoft' }),
                row({ id: 'outlook', configured: false }), // not connected → dropped
                row({ id: 'mcp-git', type: 'mcp_server' }), // not OAuth → dropped
            ],
        });
        vi.mocked(api.listConnectorGrants).mockResolvedValue({ grants: {} });

        await useConnectionsStore.getState().refresh();

        const { connections, error, loading } = useConnectionsStore.getState();
        expect(error).toBeNull();
        expect(loading).toBe(false);
        expect(connections.map((c) => c.provider)).toEqual(['google', 'microsoft']);
        expect(connections[0]).toMatchObject({
            provider: 'google',
            account_email: 'test@google.com',
            scopes: ['mail.read'],
        });
        // Grants are fetched via the framework endpoint, not the dead /connections one.
        expect(api.listConnectorGrants).toHaveBeenCalledWith('google');
        expect(api.listConnectorGrants).toHaveBeenCalledWith('microsoft');
    });

    it('records grants per provider from the framework endpoint', async () => {
        vi.mocked(api.listConnectors).mockResolvedValue({
            connectors: [row({ id: 'google' })],
        });
        vi.mocked(api.listConnectorGrants).mockResolvedValue({
            grants: { 'builtin:email': ['mail.read'] },
        });

        await useConnectionsStore.getState().refresh();

        expect(useConnectionsStore.getState().grants.google).toEqual({
            'builtin:email': ['mail.read'],
        });
    });

    it('surfaces a list failure as error and leaves connections empty', async () => {
        vi.mocked(api.listConnectors).mockRejectedValue(new Error('boom'));

        await useConnectionsStore.getState().refresh();

        const { connections, error, loading } = useConnectionsStore.getState();
        expect(connections).toEqual([]);
        expect(error).toBe('boom');
        expect(loading).toBe(false);
    });
});
