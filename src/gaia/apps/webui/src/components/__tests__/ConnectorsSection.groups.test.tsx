// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import type { ConnectorRow } from '../../types';
import { ConnectorsSection } from '../ConnectorsSection';

vi.mock('../../services/api');
vi.mock('../../hooks/useConnectorsSSE', () => ({ useConnectorsSSE: () => {} }));

const mocked = vi.mocked(api);

function row(id: string, type: string, over: Partial<ConnectorRow> = {}): ConnectorRow {
    return {
        id, display_name: id, type, description: `${id} tools`, icon: null, category: 'general', tier: 'core',
        product_url: null, configured: false, configurable: true, enabled: true, mcp_env_keys: [],
        default_scopes: [], available_scopes: [], ...over,
    } as ConnectorRow;
}

const group = (title: string) => screen.getByText(title).closest('details') as HTMLDetailsElement;

function list(rows: ConnectorRow[]) {
    mocked.listConnectors.mockResolvedValue({ connectors: rows });
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked.listAgentMcps.mockResolvedValue({ agent_mcps: [] });
});

describe('ConnectorsSection groups', () => {
    it('opens what is connected and keeps the rest a click away', async () => {
        list([row('Google', 'oauth_pkce', { configured: true }), row('Microsoft', 'oauth_pkce'), row('Notion', 'mcp_server')]);
        render(<ConnectorsSection />);
        await screen.findByText('Google');
        expect(group('Connected').open).toBe(true);
        expect(group('Accounts').open).toBe(false);
        expect(group('MCP servers').open).toBe(false);
    });

    it('opens accounts when nothing is connected yet', async () => {
        list([row('Microsoft', 'oauth_pkce'), row('Notion', 'mcp_server')]);
        render(<ConnectorsSection />);
        await screen.findByText('Microsoft');
        expect(screen.queryByText('Connected')).not.toBeInTheDocument();
        expect(group('Accounts').open).toBe(true);
    });

    it('flags a connector that needs setup, not every unconnected one', async () => {
        list([row('Microsoft', 'oauth_pkce'), row('Slack', 'oauth_pkce', { configurable: false })]);
        render(<ConnectorsSection />);
        await screen.findByText('Slack');
        expect(screen.getAllByText('Setup required')).toHaveLength(1);
        expect(screen.queryByText('Not configured')).not.toBeInTheDocument();
    });

    it('searches every group and opens what it finds', async () => {
        const servers = ['Notion', 'Postgres', 'Memory', 'Filesystem', 'Brave', 'Fetch'].map((n) => row(n, 'mcp_server'));
        list([row('Microsoft', 'oauth_pkce'), ...servers]);
        render(<ConnectorsSection />);
        await screen.findByText('Microsoft');
        fireEvent.change(screen.getByRole('searchbox', { name: 'Search connectors' }), { target: { value: 'postgres' } });
        expect(group('MCP servers').open).toBe(true);
        expect(screen.getByText('Postgres')).toBeInTheDocument();
        expect(screen.queryByText('Notion')).not.toBeInTheDocument();
        fireEvent.change(screen.getByRole('searchbox', { name: 'Search connectors' }), { target: { value: 'nothing here' } });
        expect(screen.getByText('No connectors match.')).toBeInTheDocument();
    });
});
