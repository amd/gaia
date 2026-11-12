// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// GET /api/connectors sends { id, display_name, error } for a connector whose state it couldn't read (#6952).

import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../services/api';
import type { ConnectorListEntry, ConnectorRow } from '../../types';
import { ConnectorsSection } from '../ConnectorsSection';

vi.mock('../../services/api');
vi.mock('../../hooks/useConnectorsSSE', () => ({ useConnectorsSSE: () => {} }));

const mocked = vi.mocked(api);

function row(name: string, type: string, over: Partial<ConnectorRow> = {}): ConnectorRow {
    return {
        id: name.toLowerCase(), display_name: name, type, description: `${name} tools`, icon: null, category: 'general',
        tier: 'core', product_url: null, configured: false, configurable: true, enabled: true, mcp_env_keys: [],
        default_scopes: [], available_scopes: [], ...over,
    } as ConnectorRow;
}

const unavailable = { id: 'microsoft', display_name: 'Microsoft', error: 'unavailable' } as const;
const servers = ['Notion', 'Postgres', 'Memory', 'Filesystem', 'Brave', 'Fetch'].map((n) => row(n, 'mcp_server'));
const rows: ConnectorListEntry[] = [row('Google', 'oauth_pkce', { configured: true }), unavailable, ...servers];

const group = (title: string) => screen.getByText(title).closest('details') as HTMLDetailsElement;
const search = (value: string) =>
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search connectors' }), { target: { value } });

beforeEach(() => {
    vi.clearAllMocks();
    mocked.listAgentMcps.mockResolvedValue({ agent_mcps: [] });
    mocked.listConnectors.mockResolvedValue({ connectors: rows });
});

describe('ConnectorsSection with a connector the backend could not load', () => {
    it('shows it by name in its own open group and leaves the MCP count alone', async () => {
        render(<ConnectorsSection />);
        await screen.findByText('Google');
        const broken = group('Unavailable');
        expect(broken.open).toBe(true);
        expect(within(broken).getByText('Microsoft')).toBeInTheDocument();
        expect(within(group('MCP servers')).getByText(String(servers.length))).toBeInTheDocument();
    });

    it('keeps search working and finds it by name', async () => {
        render(<ConnectorsSection />);
        await screen.findByText('Google');
        search('g');
        expect(screen.getByText('Google')).toBeInTheDocument();
        expect(screen.queryByText('Unavailable')).not.toBeInTheDocument();
        search('micro');
        expect(within(group('Unavailable')).getByText('Microsoft')).toBeInTheDocument();
        expect(screen.queryByText('Google')).not.toBeInTheDocument();
    });

    it('names the unreadable activations file instead of saying restart', async () => {
        const path = 'C:\\Users\\me\\.gaia\\connectors\\activations.json';
        mocked.listConnectors.mockResolvedValue({
            connectors: [{ ...unavailable, reason: 'activations_unreadable', path }],
        });
        render(<ConnectorsSection />);
        await screen.findByText('Microsoft');
        expect(screen.getByText(/couldn't read its connector settings file at/)).toHaveTextContent(path);
        expect(screen.queryByText(/Restart GAIA/)).not.toBeInTheDocument();
    });

    it('keeps the generic hint when the cause is unknown', async () => {
        render(<ConnectorsSection />);
        await screen.findByText('Google');
        expect(screen.getByText(/Restart GAIA/)).toBeInTheDocument();
    });
});
