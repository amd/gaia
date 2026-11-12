// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

const group = (title: string) => screen.getByText(title, { selector: '.settings-disclosure-title' }).closest('details') as HTMLDetailsElement;

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

    it('keeps a connector in place when it connects, and says so', async () => {
        const git = row('Git', 'mcp_server');
        list([row('Microsoft', 'oauth_pkce'), git]);
        mocked.configureConnector.mockResolvedValue({} as never);
        mocked.getConnector.mockResolvedValue({ ...git, configured: true });
        render(<ConnectorsSection />);
        await screen.findByText('Git');
        fireEvent.click(screen.getByText('MCP servers', { selector: '.settings-disclosure-title' }));
        fireEvent.click(screen.getByRole('button', { name: /^Git/ }));
        fireEvent.click(document.querySelector('.configure-actions .btn-primary, .configure-actions button')!);

        await waitFor(() => expect(mocked.getConnector).toHaveBeenCalledWith('Git'));
        // Still under MCP servers, where the pointer was, not moved to a "Connected" group above.
        const servers = group('MCP servers');
        expect(within(servers).getByText('Git')).toBeInTheDocument();
        expect(screen.queryByText('Connected', { selector: '.settings-disclosure-title' })).not.toBeInTheDocument();
        expect(within(servers).getByText('Connected')).toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveTextContent('Git is connected.');
    });

    it('calls a turned-off connector Paused, and a working one Connected', async () => {
        list([row('Git', 'mcp_server', { configured: true }), row('GitHub', 'mcp_server', { configured: true, enabled: false })]);
        render(<ConnectorsSection />);
        await screen.findByText('Git');
        const connected = group('Connected');
        expect(within(connected).getByText('Paused')).toBeInTheDocument();
        expect(within(connected).queryByText('Configured')).not.toBeInTheDocument();
        expect(within(connected).queryByText('Disabled')).not.toBeInTheDocument();
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
