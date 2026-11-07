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
    testConnector: vi.fn(),
}));

vi.mock('../../hooks/useConnectorsSSE', () => ({
    useConnectorsSSE: () => {},
}));

import * as api from '../../services/api';
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
    mocked.testConnector.mockResolvedValue({ ok: true, detail: 'started, 1 tool' });
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

    it('checks that the server starts right after a save', async () => {
        const github = { id: 'mcp-github', display_name: 'GitHub', mcp_env_keys: ['GITHUB_TOKEN'] };
        mocked.getConnector.mockResolvedValue(mcpRow({ ...github, configured: true }));
        await openTile(mcpRow(github));
        fireEvent.change(screen.getByPlaceholderText('Enter value'), {
            target: { value: 'ghp_test' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));

        expect(await screen.findByText('GitHub started without errors.')).toBeInTheDocument();
        expect(mocked.testConnector).toHaveBeenCalledWith('mcp-github');
    });

    it('says a connected server that will not start is broken, with the reason', async () => {
        mocked.testConnector.mockResolvedValue({
            ok: false,
            detail: 'Authentication failed - check your Slack tokens',
        });
        await openTile(
            mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true, mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'] }),
        );

        fireEvent.click(screen.getByRole('button', { name: 'Check' }));

        expect(
            await screen.findByText("Slack didn't start. Check the token, then save it again."),
        ).toBeInTheDocument();
        expect(screen.getByText('Authentication failed - check your Slack tokens')).toBeInTheDocument();
    });

    it('does not blame the token when the server was only slow to start', async () => {
        mocked.testConnector.mockResolvedValue({
            ok: false,
            reason: 'timeout',
            detail: 'did not start within 10 seconds',
        });
        await openTile(
            mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true, mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'] }),
        );

        fireEvent.click(screen.getByRole('button', { name: 'Check' }));

        expect(await screen.findByText(/Slack took too long to start/)).toBeInTheDocument();
        expect(screen.queryByText(/Check the token/)).not.toBeInTheDocument();
    });

    it('clears the check result when the server is turned off', async () => {
        const slack = { id: 'mcp-slack', display_name: 'Slack', configured: true, mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'] };
        mocked.getConnector.mockResolvedValue(mcpRow({ ...slack, enabled: false }));
        await openTile(mcpRow(slack));
        fireEvent.click(screen.getByRole('button', { name: 'Check' }));
        expect(await screen.findByText('Slack started without errors.')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('checkbox', { name: /this MCP server/ }));

        await waitFor(() => expect(mocked.disableConnector).toHaveBeenCalledWith('mcp-slack'));
        await waitFor(() =>
            expect(screen.queryByText('Slack started without errors.')).not.toBeInTheDocument(),
        );
    });

    it('shows the newest check when an older one finishes last', async () => {
        let finishFirst: (r: { ok: boolean; detail: string }) => void = () => {};
        mocked.testConnector
            .mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve; }))
            .mockResolvedValueOnce({ ok: true, detail: 'started, 1 tool' });
        const slack = { id: 'mcp-slack', display_name: 'Slack', configured: true, mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'] };
        mocked.getConnector.mockResolvedValue(mcpRow(slack));
        await openTile(mcpRow(slack));

        const save = async (token: string) => {
            fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: token } });
            // The button reads "Saved" for a moment after the first save.
            fireEvent.click(screen.getByRole('button', { name: /^Saved?$/ }));
            await waitFor(() =>
                expect(mocked.configureConnector).toHaveBeenLastCalledWith('mcp-slack', { SLACK_MCP_XOXB_TOKEN: token }),
            );
        };
        await save('xoxb-old');
        await save('xoxb-new');
        expect(await screen.findByText('Slack started without errors.')).toBeInTheDocument();

        finishFirst({ ok: false, detail: 'Authentication failed - check your Slack tokens' });

        await new Promise((r) => setTimeout(r, 0));
        expect(screen.getByText('Slack started without errors.')).toBeInTheDocument();
        expect(screen.queryByText(/Authentication failed/)).not.toBeInTheDocument();
    });

    it('offers no Check on a server that is turned off', async () => {
        await openTile(
            mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true, enabled: false, mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'] }),
        );

        expect(screen.queryByRole('button', { name: 'Check' })).not.toBeInTheDocument();
    });
});
