// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { fireEvent, render, screen, within } from '@testing-library/react';
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

const group = (title: string) => screen
    .getAllByText(title)
    .map((el) => el.closest('details'))
    .find(Boolean) as HTMLDetailsElement;

function list(rows: ConnectorRow[]) {
    mocked.listConnectors.mockResolvedValue({ connectors: rows });
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked.listAgentMcps.mockResolvedValue({ agent_mcps: [] });
});

describe('ConnectorsSection groups', () => {
    it('lists what is connected first, then the rest by category', async () => {
        list([
            row('Google', 'oauth_pkce', { configured: true, category: 'workspace' }),
            row('Microsoft', 'oauth_pkce', { category: 'workspace' }),
            row('Notion', 'mcp_server', { category: 'productivity' }),
        ]);
        render(<ConnectorsSection />);
        await screen.findByText('Google');
        expect(within(group('Connected')).getByText('Google')).toBeInTheDocument();
        expect(within(group('Mail, calendar & files')).getByText('Microsoft')).toBeInTheDocument();
        expect(within(group('Productivity')).getByText('Notion')).toBeInTheDocument();
        expect(group('Productivity').open).toBe(true);
    });

    it('puts a category GAIA has no name for under Other', async () => {
        list([row('Microsoft', 'oauth_pkce', { category: 'workspace' }), row('Mystery', 'mcp_server', { category: 'quantum' })]);
        render(<ConnectorsSection />);
        await screen.findByText('Mystery');
        expect(screen.queryByText('Connected')).not.toBeInTheDocument();
        expect(within(group('Other')).getByText('Mystery')).toBeInTheDocument();
    });

    it('filters to one category from its chip, and back with All', async () => {
        list([
            row('Microsoft', 'oauth_pkce', { category: 'workspace' }),
            row('Notion', 'mcp_server', { category: 'productivity' }),
            row('Linear', 'mcp_server', { category: 'productivity' }),
        ]);
        render(<ConnectorsSection />);
        await screen.findByText('Notion');
        const chips = within(screen.getByRole('group', { name: 'Categories' }));
        const productivity = chips.getByRole('button', { name: /Productivity/ });
        expect(within(productivity).getByText('2')).toBeInTheDocument();
        fireEvent.click(productivity);
        expect(productivity).toHaveAttribute('aria-pressed', 'true');
        expect(screen.queryByText('Microsoft')).not.toBeInTheDocument();
        expect(screen.getByText('Linear')).toBeInTheDocument();
        fireEvent.click(chips.getByRole('button', { name: 'All' }));
        expect(screen.getByText('Microsoft')).toBeInTheDocument();
    });

    it('hides the chips when there is only one category', async () => {
        list([row('Notion', 'mcp_server', { category: 'productivity' })]);
        render(<ConnectorsSection />);
        await screen.findByText('Notion');
        expect(screen.queryByRole('group', { name: 'Categories' })).not.toBeInTheDocument();
        expect(screen.getByRole('searchbox', { name: 'Search connectors' })).toBeInTheDocument();
    });

    it('shows each connector with a badge and its description', async () => {
        list([row('Notion', 'mcp_server', { category: 'productivity', description: 'Pages and databases' })]);
        const { container } = render(<ConnectorsSection />);
        await screen.findByText('Notion');
        expect(screen.getByText('Pages and databases')).toBeInTheDocument();
        expect(container.querySelector('.connector-logo')).toHaveTextContent('N');
    });

    it("shows the service's own mark when GAIA ships one", async () => {
        list([row('mcp-notion', 'mcp_server', { category: 'productivity' })]);
        const { container } = render(<ConnectorsSection />);
        await screen.findByText('mcp-notion');
        expect(container.querySelector('.connector-logo svg path')).toBeInTheDocument();
        expect(container.querySelector('.connector-logo--letter')).toBeNull();
    });

    it('flags a connector that needs setup, not every unconnected one', async () => {
        list([row('Microsoft', 'oauth_pkce'), row('Slack', 'oauth_pkce', { configurable: false })]);
        render(<ConnectorsSection />);
        await screen.findByText('Slack');
        expect(screen.getAllByText('Setup required')).toHaveLength(1);
        expect(screen.queryByText('Not configured')).not.toBeInTheDocument();
    });

    it('searches every category, and a search that empties the chosen one shows all matches', async () => {
        const servers = ['Notion', 'Postgres', 'Memory', 'Filesystem', 'Brave', 'Fetch'].map((n) => row(n, 'mcp_server', { category: 'data' }));
        list([row('Microsoft', 'oauth_pkce', { category: 'workspace' }), ...servers]);
        render(<ConnectorsSection />);
        await screen.findByText('Microsoft');
        fireEvent.click(within(screen.getByRole('group', { name: 'Categories' })).getByRole('button', { name: /Mail, calendar/ }));
        fireEvent.change(screen.getByRole('searchbox', { name: 'Search connectors' }), { target: { value: 'postgres' } });
        expect(screen.getByText('Postgres')).toBeInTheDocument();
        expect(screen.queryByText('Notion')).not.toBeInTheDocument();
        fireEvent.change(screen.getByRole('searchbox', { name: 'Search connectors' }), { target: { value: 'nothing here' } });
        expect(screen.getByText('No connectors match.')).toBeInTheDocument();
    });
});
