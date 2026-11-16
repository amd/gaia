// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Settings › Connectors opened at one connector, as the chat's Connect card does.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import type { ConnectorRow } from '../../types';

vi.mock('../../services/api', () => ({
    listConnectors: vi.fn(),
    listAgentMcps: vi.fn(),
    getConnector: vi.fn(),
    listConnectorGrants: vi.fn(),
    listConnectorActivations: vi.fn(),
}));

vi.mock('../../hooks/useConnectorsSSE', () => ({
    useConnectorsSSE: () => {},
}));

import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { ConnectorsSection } from '../ConnectorsSection';

const mocked = vi.mocked(api);

const google = {
    id: 'google', display_name: 'Google', icon: null, category: 'email', tier: 'verified',
    type: 'oauth_pkce', description: '', product_url: null, docs_url: null,
    configured: false, configurable: false, config_error: 'not set up', enabled: true,
    account_id: null, scopes: [], activations: {}, last_tested_at: null, mcp_env_keys: [],
    default_scopes: ['openid'], available_scopes: ['openid'],
    oauth_setup_fields: [{ key: 'client_id', label: 'OAuth Client ID', kind: 'text', required: true }],
} as unknown as ConnectorRow;

beforeEach(() => {
    vi.clearAllMocks();
    mocked.listConnectors.mockResolvedValue({ connectors: [google] } as never);
    mocked.listAgentMcps.mockResolvedValue({ agent_mcps: [] } as never);
    mocked.listConnectorGrants.mockResolvedValue({ grants: {} } as never);
    mocked.listConnectorActivations.mockResolvedValue({ activations: {} } as never);
});

afterEach(() => {
    act(() => useChatStore.getState().closeSettings());
});

describe('ConnectorsSection opened at a connector', () => {
    it('opens that connector, where its one-time setup is', async () => {
        act(() => useChatStore.getState().openConnectorSettings('google'));
        expect(useChatStore.getState().settingsSection).toBe('connectors');
        render(<ConnectorsSection />);
        expect(await screen.findByText('OAuth Client ID')).toBeTruthy();
    });

    it('opens nothing when no connector was asked for', async () => {
        act(() => useChatStore.getState().openSettings('connectors'));
        render(<ConnectorsSection />);
        await screen.findByText('Google');
        expect(screen.queryByText('OAuth Client ID')).toBeNull();
        expect(useChatStore.getState().settingsConnector).toBeNull();
    });
});
