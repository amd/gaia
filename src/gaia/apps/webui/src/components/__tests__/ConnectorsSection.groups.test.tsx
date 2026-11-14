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
    });

    it('says a new connection once, on the card, which stays put', async () => {
        const git = row('Git', 'mcp_server');
        list([row('Microsoft', 'oauth_pkce'), git]);
        mocked.configureConnector.mockResolvedValue({} as never);
        mocked.getConnector.mockResolvedValue({ ...git, configured: true });
        render(<ConnectorsSection />);
        await screen.findByText('Git');
        fireEvent.click(screen.getByText('MCP servers', { selector: '.settings-disclosure-title' }));
        fireEvent.click(screen.getByRole('button', { name: /^Git/ }));
        fireEvent.click(document.querySelector('.configure-actions .btn-primary, .configure-actions button')!);

        const servers = group('MCP servers');
        await within(servers).findByText('Connected');
        // The status chip is the confirmation; no banner repeats it above the list (#7703).
        expect(screen.getAllByText('Connected')).toHaveLength(1);
        expect(screen.queryByText('Git is connected.')).not.toBeInTheDocument();
        expect(screen.getAllByRole('button', { name: 'Personalize GAIA' })).toHaveLength(1);
    });

    it('moves a connector that disconnects back to its group, open, counted there and marked (#7704, #7863)', async () => {
        const git = row('Git', 'mcp_server', { configured: true });
        list([git, row('GitHub', 'mcp_server', { configured: true }), row('Notion', 'mcp_server')]);
        mocked.disconnectConnector.mockResolvedValue(undefined as never);
        mocked.getConnector.mockResolvedValue({ ...git, configured: false });
        render(<ConnectorsSection />);
        await screen.findByText('Git');
        const connected = group('Connected');
        expect(connected.querySelector('.settings-disclosure-count')).toHaveTextContent('2');
        expect(group('MCP servers').querySelector('.settings-disclosure-count')).toHaveTextContent('1');
        fireEvent.click(within(connected).getByRole('button', { name: /^Git\b(?!Hub)/ }));
        fireEvent.click(within(connected).getByRole('button', { name: 'Disconnect' }));

        await waitFor(() => expect(within(group('MCP servers')).queryByText('Git')).toBeInTheDocument());
        const servers = group('MCP servers');
        expect(servers.open).toBe(true);
        expect(servers.querySelector('.settings-disclosure-count')).toHaveTextContent('2');
        expect(within(servers).getByText('Disconnected')).toHaveAttribute('role', 'status');
        expect(within(group('Connected')).queryByText('Git')).not.toBeInTheDocument();
        expect(group('Connected').querySelector('.settings-disclosure-count')).toHaveTextContent('1');
        // Focus went with the tile, not to the page.
        expect(within(servers).getByRole('button', { name: /^Git\b(?!Hub)/ })).toHaveFocus();
    });

    it('closes the Connected group when its last connector disconnects (#7863)', async () => {
        const git = row('Git', 'mcp_server', { configured: true });
        list([git, row('Notion', 'mcp_server')]);
        mocked.disconnectConnector.mockResolvedValue(undefined as never);
        mocked.getConnector.mockResolvedValue({ ...git, configured: false });
        render(<ConnectorsSection />);
        await screen.findByText('Git');
        fireEvent.click(within(group('Connected')).getByRole('button', { name: /^Git/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));

        await waitFor(() => expect(screen.queryByText('Connected', { selector: '.settings-disclosure-title' })).not.toBeInTheDocument());
        expect(within(group('MCP servers')).getByText('Git')).toBeInTheDocument();
    });

    it('offers Disconnect once: the menu steps aside while the card is open (#7704)', async () => {
        list([row('Git', 'mcp_server', { configured: true })]);
        render(<ConnectorsSection />);
        await screen.findByText('Git');
        expect(document.querySelector('.connector-tile-menu-trigger')).not.toBeNull();
        fireEvent.click(screen.getByRole('button', { name: /^Git/ }));
        expect(screen.getByRole('button', { name: 'Disconnect' })).toBeInTheDocument();
        expect(document.querySelector('.connector-tile-menu-trigger')).toBeNull();
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

    it('finds the mail accounts when searching for email', async () => {
        const servers = ['Notion', 'Postgres', 'Memory', 'Filesystem', 'Brave', 'Fetch'].map((n) => row(n, 'mcp_server'));
        list([
            row('google', 'oauth_pkce', { display_name: 'Google', description: 'Gmail and Google Calendar' }),
            row('microsoft_work', 'oauth_pkce', { display_name: 'Microsoft Work', description: 'Outlook mail' }),
            ...servers,
        ]);
        render(<ConnectorsSection />);
        await screen.findByText('Google');
        fireEvent.change(screen.getByRole('searchbox', { name: 'Search connectors' }), { target: { value: 'Email' } });
        expect(screen.getByText('Google')).toBeInTheDocument();
        expect(screen.getByText('Microsoft Work')).toBeInTheDocument();
        expect(screen.queryByText('Notion')).not.toBeInTheDocument();
        expect(screen.queryByText('No connectors match.')).not.toBeInTheDocument();
    });
});
