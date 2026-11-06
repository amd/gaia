// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { ConnectorRow } from '../../types';

vi.mock('../../services/api', () => ({
    listConnectors: vi.fn(),
    listAgentMcps: vi.fn(),
    getConnector: vi.fn(),
    configureConnector: vi.fn(),
    disconnectConnector: vi.fn(),
    enableConnector: vi.fn(),
    disableConnector: vi.fn(),
    listConnectorGrants: vi.fn(),
    listConnectorActivations: vi.fn(),
    activateConnectorAgent: vi.fn(),
    deactivateConnectorAgent: vi.fn(),
}));

vi.mock('../../hooks/useConnectorsSSE', () => ({
    useConnectorsSSE: () => {},
}));

import * as api from '../../services/api';
import { useChatStore } from '../../stores/chatStore';
import { ConnectorsSection } from '../ConnectorsSection';

function mcpRow(overrides: Partial<ConnectorRow>): ConnectorRow {
    return {
        id: 'mcp-git',
        display_name: 'Git',
        icon: null,
        category: 'developer',
        tier: 'core',
        type: 'mcp_server',
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
        ...overrides,
    } as ConnectorRow;
}

const mocked = vi.mocked(api);

async function openTile(row: ConnectorRow) {
    mocked.listConnectors.mockResolvedValue({ connectors: [row] } as never);
    render(<ConnectorsSection />);
    fireEvent.click(await screen.findByText(row.display_name));
}

beforeEach(() => {
    vi.clearAllMocks();
    mocked.listAgentMcps.mockResolvedValue({ agent_mcps: [] } as never);
    mocked.listConnectorGrants.mockResolvedValue({ grants: [] } as never);
    mocked.listConnectorActivations.mockResolvedValue({ activations: {} } as never);
    mocked.configureConnector.mockResolvedValue({ configured: true } as never);
});

describe('MCP connector configure panel', () => {
    it('lets a keyless connector be connected with an empty config', async () => {
        const row = mcpRow({});
        mocked.getConnector.mockResolvedValue(mcpRow({ configured: true }));
        await openTile(row);

        const connect = screen.getByRole('button', { name: 'Connect' });
        expect(connect).toBeEnabled();
        expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();

        fireEvent.click(connect);

        await waitFor(() =>
            expect(mocked.configureConnector).toHaveBeenCalledWith('mcp-git', {}),
        );
        expect(await screen.findByRole('button', { name: 'Disconnect' })).toBeInTheDocument();
        expect(screen.getByRole('checkbox', { name: /this MCP server/ })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();
    });

    it('keeps Save disabled for a keyed connector until a value is typed', async () => {
        const row = mcpRow({
            id: 'mcp-github',
            display_name: 'GitHub MCP',
            mcp_env_keys: ['GITHUB_TOKEN'],
        });
        await openTile(row);

        const save = screen.getByRole('button', { name: 'Save' });
        expect(save).toBeDisabled();
        expect(screen.queryByRole('button', { name: 'Connect' })).toBeNull();

        fireEvent.change(screen.getByPlaceholderText('Enter value'), {
            target: { value: 'ghp_test' },
        });
        expect(save).toBeEnabled();

        fireEvent.click(save);
        await waitFor(() =>
            expect(mocked.configureConnector).toHaveBeenCalledWith('mcp-github', {
                GITHUB_TOKEN: 'ghp_test',
            }),
        );
    });

    it('refuses a token with the wrong prefix and trims a padded one', async () => {
        const row = mcpRow({
            id: 'mcp-slack',
            display_name: 'Slack',
            mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'],
            config_fields: [
                {
                    key: 'SLACK_MCP_XOXB_TOKEN',
                    label: 'Slack bot token',
                    kind: 'secret',
                    required: true,
                    placeholder: 'xoxb-…',
                    help_md: '',
                    prefix: 'xoxb-',
                },
            ],
        });
        await openTile(row);
        const input = screen.getByPlaceholderText('xoxb-…');
        const save = screen.getByRole('button', { name: 'Save' });

        fireEvent.change(input, { target: { value: 'xapp-1-A0-signing' } });
        fireEvent.click(save);
        expect(
            await screen.findByText(/Slack bot token should start with xoxb-/),
        ).toBeInTheDocument();
        expect(mocked.configureConnector).not.toHaveBeenCalled();

        fireEvent.change(input, { target: { value: '  xoxb-good  ' } });
        fireEvent.click(save);
        await waitFor(() =>
            expect(mocked.configureConnector).toHaveBeenCalledWith('mcp-slack', {
                SLACK_MCP_XOXB_TOKEN: 'xoxb-good',
            }),
        );
    });

    it('shows the field label, help and setup link from the catalog', async () => {
        const row = mcpRow({
            id: 'mcp-slack',
            display_name: 'Slack',
            mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'],
            setup_url: 'https://api.slack.com/apps?new_app=1&manifest_json=%7B%7D',
            config_fields: [
                {
                    key: 'SLACK_MCP_XOXB_TOKEN',
                    label: 'Slack bot token',
                    kind: 'secret',
                    required: true,
                    placeholder: 'xoxb-…',
                    help_md: 'Click **Create Slack app**. See the [guide](https://amd-gaia.ai/docs/connectors/slack).',
                },
            ],
        });
        await openTile(row);

        expect(screen.getByLabelText('Slack bot token')).toHaveAttribute('placeholder', 'xoxb-…');
        expect(screen.queryByText('SLACK_MCP_XOXB_TOKEN')).toBeNull();
        expect(screen.getByRole('link', { name: /Create Slack app/ })).toHaveAttribute(
            'href',
            'https://api.slack.com/apps?new_app=1&manifest_json=%7B%7D',
        );
        expect(screen.getByRole('link', { name: 'guide' })).toHaveAttribute(
            'href',
            'https://amd-gaia.ai/docs/connectors/slack',
        );
        expect(screen.queryByText(/\*\*/)).toBeNull();
    });

    it('drops the setup link and help once the connector is configured', async () => {
        const row = mcpRow({
            id: 'mcp-slack',
            display_name: 'Slack',
            configured: true,
            mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'],
            setup_url: 'https://api.slack.com/apps?new_app=1',
            config_fields: [
                {
                    key: 'SLACK_MCP_XOXB_TOKEN',
                    label: 'Slack bot token',
                    kind: 'secret',
                    required: true,
                    placeholder: 'xoxb-…',
                    help_md: 'Click **Create Slack app**.',
                },
            ],
        });
        await openTile(row);

        expect(screen.getByLabelText('Slack bot token')).toBeInTheDocument();
        expect(screen.queryByRole('link', { name: /Create Slack app/ })).toBeNull();
        expect(screen.queryByText(/Click/)).toBeNull();
    });

    const GAIA = { id: 'gaia', name: 'GAIA', namespaced_agent_id: 'installed:gaia', consumes_mcp_servers: true };

    it('shows a single switch when GAIA is the only assistant', async () => {
        useChatStore.setState({ agents: [GAIA] as never });
        mocked.listConnectorActivations.mockResolvedValue({
            activations: { 'installed:gaia': true },
        } as never);
        await openTile(mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true }));

        expect(await screen.findByRole('checkbox', { name: 'GAIA can use Slack in chat' })).toBeChecked();
        expect(screen.getAllByRole('checkbox')).toHaveLength(1);
        expect(screen.queryByText('Who can use it')).toBeNull();
        expect(screen.queryByText('No agents require access to this connector.')).toBeNull();
        useChatStore.setState({ agents: [] as never });
    });

    it('turning the single switch on also resumes a paused connector', async () => {
        useChatStore.setState({ agents: [GAIA] as never });
        mocked.enableConnector.mockResolvedValue({} as never);
        mocked.activateConnectorAgent.mockResolvedValue({} as never);
        await openTile(mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true, enabled: false }));

        const sw = await screen.findByRole('checkbox', { name: 'GAIA can use Slack in chat' });
        expect(sw).not.toBeChecked();
        fireEvent.click(sw);
        await waitFor(() => expect(mocked.activateConnectorAgent).toHaveBeenCalledWith('mcp-slack', 'installed:gaia', ['use']));
        expect(mocked.enableConnector).toHaveBeenCalledWith('mcp-slack');
        useChatStore.setState({ agents: [] as never });
    });

    it('keeps the per-assistant list when there is more than one', async () => {
        const other = { id: 'notes', name: 'Notes', namespaced_agent_id: 'custom:notes', consumes_mcp_servers: true };
        useChatStore.setState({ agents: [GAIA, other] as never });
        await openTile(mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true }));

        expect(await screen.findByText('Who can use it')).toBeInTheDocument();
        expect(screen.getAllByRole('checkbox', { name: "Can't use Slack in chat" })).toHaveLength(2);
        expect(screen.getByRole('checkbox', { name: /this MCP server/ })).toBeInTheDocument();
        useChatStore.setState({ agents: [] as never });
    });
});
