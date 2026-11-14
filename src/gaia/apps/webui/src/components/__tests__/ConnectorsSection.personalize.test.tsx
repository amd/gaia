// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
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

const sse = vi.hoisted(() => ({ notify: null as null | ((event: { reason: string; connectorId: string; payload: Record<string, unknown> }) => void) }));
vi.mock('../../hooks/useConnectorsSSE', () => ({
    useConnectorsSSE: (onEvent: NonNullable<typeof sse.notify>) => { sse.notify = onEvent; },
}));

import * as api from '../../services/api';
import { ConnectorsSection } from '../ConnectorsSection';
import { PERSONALIZE_EVENT } from '../../utils/connectorPersonalize';
import { useChatStore } from '../../stores/chatStore';
import type { AgentInfo } from '../../types';

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
    mocked.activateConnectorAgent.mockResolvedValue({} as never);
    mocked.testConnector.mockResolvedValue({ ok: true, detail: 'started, 1 tool' });
    useChatStore.setState({ locale: 'en', agents: [{ id: 'gaia', namespaced_agent_id: 'installed:gaia' } as AgentInfo] });
});

function listenForPersonalize() {
    const handler = vi.fn();
    window.addEventListener(PERSONALIZE_EVENT, handler);
    return handler;
}

describe('Personalize GAIA offer', () => {
    it('is offered on a connected tile and requests the pass for that connector', async () => {
        await openTile(mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true }));
        const handler = listenForPersonalize();
        expect(screen.getByText(/your recent Slack channels/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Personalize GAIA' }));
        await waitFor(() => expect(handler).toHaveBeenCalledOnce());
        window.removeEventListener(PERSONALIZE_EVENT, handler);
        expect((handler.mock.calls[0][0] as CustomEvent).detail).toEqual({ connectorId: 'mcp-slack', displayName: 'Slack' });
    });

    it('activates an MCP connector for GAIA before starting, so the pass can read it', async () => {
        await openTile(mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true }));
        const handler = listenForPersonalize();
        fireEvent.click(screen.getByRole('button', { name: 'Personalize GAIA' }));
        await waitFor(() => expect(handler).toHaveBeenCalledOnce());
        window.removeEventListener(PERSONALIZE_EVENT, handler);
        expect(mocked.activateConnectorAgent).toHaveBeenCalledWith('mcp-slack', 'installed:gaia', ['use']);
    });

    it('skips activation when GAIA is already active for the connector', async () => {
        await openTile(mcpRow({
            id: 'mcp-slack', display_name: 'Slack', configured: true, activations: { 'installed:gaia': true },
        }));
        const handler = listenForPersonalize();
        fireEvent.click(screen.getByRole('button', { name: 'Personalize GAIA' }));
        await waitFor(() => expect(handler).toHaveBeenCalledOnce());
        window.removeEventListener(PERSONALIZE_EVENT, handler);
        expect(mocked.activateConnectorAgent).not.toHaveBeenCalled();
    });

    it('says why and starts nothing when activation fails', async () => {
        mocked.activateConnectorAgent.mockRejectedValue(new Error('keyring locked'));
        await openTile(mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true }));
        const handler = listenForPersonalize();
        fireEvent.click(screen.getByRole('button', { name: 'Personalize GAIA' }));
        expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't start personalization: keyring locked");
        window.removeEventListener(PERSONALIZE_EVENT, handler);
        expect(handler).not.toHaveBeenCalled();
    });

    it('withdraws the connect offer when that connector is disconnected', async () => {
        mocked.getConnector
            .mockResolvedValueOnce(mcpRow({ id: 'mcp-memory', display_name: 'Memory', configured: true }))
            .mockResolvedValueOnce(mcpRow({ id: 'mcp-memory', display_name: 'Memory', configured: false }));
        mocked.disconnectConnector.mockResolvedValue(undefined as never);
        await openTile(mcpRow({ id: 'mcp-memory', display_name: 'Memory' }));
        fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
        expect(await screen.findByText(/^Want GAIA to read/)).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Personalize GAIA' })).toBeNull());
    });

    it('says on the tile that GAIA keeps access only if you personalize, and where to turn it off', async () => {
        await openTile(mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true }));
        const tile = screen.getByText(/your recent Slack channels/);
        expect(tile).toHaveTextContent('If you do, GAIA keeps access to Slack afterwards; you can turn that off below.');
        // The pass is only told to read; the copy must not promise more than that.
        expect(tile).not.toHaveTextContent(/only reads/i);
    });

    it('says in the connect offer that GAIA keeps access only if you personalize, and where to turn it off', async () => {
        mocked.getConnector.mockResolvedValue(mcpRow({ id: 'mcp-memory', display_name: 'Memory', configured: true }));
        await openTile(mcpRow({ id: 'mcp-memory', display_name: 'Memory' }));
        fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
        // The card's own offer, the one place it is made (#7703).
        const banner = await waitFor(() => {
            const offer = document.querySelector('.connector-personalize--offer');
            expect(offer).not.toBeNull();
            return offer;
        });
        expect(banner).toHaveTextContent('If you do, GAIA keeps access to Memory afterwards; you can turn that off anytime by opening Memory here.');
        expect(banner).not.toHaveTextContent(/only reads/i);
    });

    it('is not offered before the connector is connected', async () => {
        await openTile(mcpRow({ id: 'mcp-slack', display_name: 'Slack' }));
        expect(screen.queryByRole('button', { name: 'Personalize GAIA' })).toBeNull();
    });

    it('is not offered on a disabled connector', async () => {
        await openTile(mcpRow({ id: 'mcp-slack', display_name: 'Slack', configured: true, enabled: false }));
        expect(screen.queryByRole('button', { name: 'Personalize GAIA' })).toBeNull();
    });

    it('offers personalization the moment a connector becomes connected', async () => {
        const row = mcpRow({ id: 'mcp-memory', display_name: 'Memory' });
        mocked.getConnector.mockResolvedValue(mcpRow({ id: 'mcp-memory', display_name: 'Memory', configured: true }));
        await openTile(row);
        fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
        // One offer, in the card the user just used, not a second one above the list (#7703).
        const offer = await screen.findByText(/^Want GAIA to read/);
        expect(offer.closest('.connector-detail')).not.toBeNull();
        expect(screen.getAllByRole('button', { name: 'Personalize GAIA' })).toHaveLength(1);
        expect(screen.queryByText(/^Optional: let GAIA read/)).toBeNull();
        expect(screen.queryByText('Memory is connected.')).toBeNull();
    });

    it('opens the card of a connector that connects in the background, when no card is open', async () => {
        const memory = mcpRow({ id: 'mcp-memory', display_name: 'Memory' });
        mocked.listConnectors.mockResolvedValue({ connectors: [memory, mcpRow({ id: 'mcp-slack', display_name: 'Slack' })] } as never);
        mocked.getConnector.mockResolvedValue({ ...memory, configured: true });
        render(<ConnectorsSection />);
        await screen.findByText('Memory');
        act(() => sse.notify?.({ reason: 'configured', connectorId: 'mcp-memory', payload: {} }));
        await screen.findByText(/^Want GAIA to read/);
        expect(screen.getByRole('button', { name: /^Memory/ })).toHaveAttribute('aria-expanded', 'true');
    });

    it('leaves the card the user is reading open when another connector connects', async () => {
        const memory = mcpRow({ id: 'mcp-memory', display_name: 'Memory' });
        mocked.listConnectors.mockResolvedValue({ connectors: [memory, mcpRow({ id: 'mcp-slack', display_name: 'Slack' })] } as never);
        mocked.getConnector.mockResolvedValue({ ...memory, configured: true });
        render(<ConnectorsSection />);
        fireEvent.click(await screen.findByText('Slack'));
        act(() => sse.notify?.({ reason: 'configured', connectorId: 'mcp-memory', payload: {} }));
        await waitFor(() => expect(mocked.getConnector).toHaveBeenCalledWith('mcp-memory'));
        expect(screen.getByRole('button', { name: /^Slack/ })).toHaveAttribute('aria-expanded', 'true');
        expect(screen.getByRole('button', { name: /^Memory/ })).toHaveAttribute('aria-expanded', 'false');
    });
});
