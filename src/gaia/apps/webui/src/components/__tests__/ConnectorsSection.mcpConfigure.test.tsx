// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
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
    testConnector: vi.fn(),
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

    it('keeps the token prefix left-to-right inside an Arabic wrong-prefix message', async () => {
        act(() => useChatStore.getState().setLocale('ar'));
        try {
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
            fireEvent.change(screen.getByPlaceholderText('xoxb-…'), { target: { value: 'xapp-1' } });
            fireEvent.click(document.querySelector('.configure-actions .btn-primary')!);
            const error = await waitFor(() => {
                const el = document.querySelector('.configure-error');
                expect(el).not.toBeNull();
                return el!.textContent ?? '';
            });
            expect(error).toContain('يجب أن يبدأ');
            // LRI ... PDI: the hyphen stays after "xoxb" instead of jumping to its start.
            expect(error).toContain('⁦xoxb-⁩');
        } finally {
            act(() => useChatStore.getState().setLocale('en'));
        }
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

    it('shows no per-agent grants block when no agent needs this connector', async () => {
        useChatStore.setState({ agents: [] as never });
        await openTile(mcpRow({ id: 'mcp-git', display_name: 'Git', configured: true }));
        await waitFor(() => expect(mocked.listConnectorActivations).toHaveBeenCalled());
        expect(screen.queryByText('Per-agent grants')).toBeNull();
        expect(document.querySelector('.connection-grants')).toBeNull();
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

    it('clears the check result when the single switch is turned off', async () => {
        useChatStore.setState({ agents: [GAIA] as never });
        mocked.listConnectorActivations.mockResolvedValue({
            activations: { 'installed:gaia': true },
        } as never);
        mocked.deactivateConnectorAgent.mockResolvedValue({} as never);
        const slack = { id: 'mcp-slack', display_name: 'Slack', configured: true, mcp_env_keys: ['SLACK_MCP_XOXB_TOKEN'] };
        mocked.getConnector.mockResolvedValue(mcpRow(slack));
        await openTile(mcpRow(slack));
        expect(screen.queryByRole('checkbox', { name: /this MCP server/ })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Check' }));
        expect(await screen.findByText('Slack started without errors.')).toBeInTheDocument();
        expect(mocked.testConnector).toHaveBeenCalledWith('mcp-slack');

        fireEvent.click(await screen.findByRole('checkbox', { name: 'GAIA can use Slack in chat' }));

        await waitFor(() =>
            expect(mocked.deactivateConnectorAgent).toHaveBeenCalledWith('mcp-slack', 'installed:gaia'),
        );
        await waitFor(() =>
            expect(screen.queryByText('Slack started without errors.')).not.toBeInTheDocument(),
        );
        useChatStore.setState({ agents: [] as never });
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
