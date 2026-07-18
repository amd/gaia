// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HubPage } from '../HubPage';
import type { AgentInfo, AgentCatalogResponse, InstallStatus } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function agent(partial: Partial<AgentInfo> & { id: string }): AgentInfo {
    return {
        name: partial.id,
        description: `${partial.id} description`,
        source: 'installed',
        conversation_starters: [],
        models: [],
        ...partial,
    };
}

const INSTALLED: AgentInfo[] = [agent({ id: 'chat', name: 'Chat', source: 'builtin' })];

const CATALOG: AgentCatalogResponse = {
    offline: false,
    agents: [
        agent({
            id: 'studio',
            name: 'Studio',
            type: 'app',
            status: 'available',
            security_tier: 'verified',
            compatibility: { level: 'compatible' },
        }),
        agent({
            id: 'rag-kit',
            name: 'Rag Kit',
            type: 'component',
            status: 'available',
            security_tier: 'community',
            compatibility: { level: 'compatible' },
        }),
        agent({
            id: 'weather',
            name: 'Weather',
            type: 'agent',
            status: 'available',
            security_tier: 'verified',
            compatibility: { level: 'compatible' },
        }),
    ],
};

const INSTALLED_STATUS: InstallStatus = { agent_id: 'studio', state: 'installed', progress: 100 };

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listCatalog.mockResolvedValue(CATALOG);
    mockedApi.listAgents.mockResolvedValue({ agents: INSTALLED, total: INSTALLED.length });
    mockedApi.installAgent.mockResolvedValue(INSTALLED_STATUS);
});

function renderHub() {
    return render(
        <HubPage
            agents={INSTALLED}
            activeAgentId="chat"
            onSelect={() => {}}
            onStartChat={() => {}}
        />,
    );
}

describe('HubPage lanes', () => {
    it('renders Apps · Components · Agents lanes from the catalog', async () => {
        renderHub();
        await waitFor(() => expect(mockedApi.listCatalog).toHaveBeenCalled());
        // Lane headings.
        expect(await screen.findByRole('heading', { name: /Apps/ })).toBeInTheDocument();
        expect(screen.getByRole('heading', { name: /Components/ })).toBeInTheDocument();
        // Two "Agents" — the lane heading; the Installed lane too. Cards present:
        expect(screen.getByText('Studio')).toBeInTheDocument();
        expect(screen.getByText('Rag Kit')).toBeInTheDocument();
        expect(screen.getByText('Weather')).toBeInTheDocument();
    });

    it('shows an actionable error with Retry when the catalog fetch fails', async () => {
        mockedApi.listCatalog.mockRejectedValueOnce(new Error('hub unreachable'));
        renderHub();
        expect(await screen.findByRole('alert')).toHaveTextContent('hub unreachable');
        expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument();
    });
});

describe('HubPage trust gate (issue #1722)', () => {
    it('installs a verified agent in one click after the gate', async () => {
        const user = userEvent.setup();
        renderHub();
        await screen.findByText('Studio');

        await user.click(screen.getByTitle('Install Studio'));

        // The trust gate opens; a verified agent's Install button is enabled.
        const dialog = await screen.findByRole('alertdialog');
        const proceed = within(dialog).getByRole('button', { name: /Install/ });
        expect(proceed).toBeEnabled();
        await user.click(proceed);

        await waitFor(() => expect(mockedApi.installAgent).toHaveBeenCalledTimes(1));
        const [id, , trustNative] = mockedApi.installAgent.mock.calls[0];
        expect(id).toBe('studio');
        expect(trustNative).toBe(false);
    });

    it('refuses a non-verified install until the override is acknowledged', async () => {
        const user = userEvent.setup();
        renderHub();
        await screen.findByText('Rag Kit');

        await user.click(screen.getByTitle('Install Rag Kit'));

        const dialog = await screen.findByRole('alertdialog');
        // Community tier → gated. Proceed disabled until the checkbox is ticked.
        const proceed = within(dialog).getByRole('button', { name: /Trust & Install/ });
        expect(proceed).toBeDisabled();
        expect(mockedApi.installAgent).not.toHaveBeenCalled();

        await user.click(within(dialog).getByRole('checkbox'));
        expect(proceed).toBeEnabled();
        await user.click(proceed);

        await waitFor(() => expect(mockedApi.installAgent).toHaveBeenCalledTimes(1));
        const [id, , trustNative] = mockedApi.installAgent.mock.calls[0];
        expect(id).toBe('rag-kit');
        // Community (non-native) → gated, but no native-trust flag is sent.
        expect(trustNative).toBe(false);
    });

    it('shows declared permissions in the trust gate', async () => {
        const user = userEvent.setup();
        mockedApi.listCatalog.mockResolvedValue({
            offline: false,
            agents: [
                agent({
                    id: 'perm-agent',
                    name: 'Perm Agent',
                    type: 'agent',
                    status: 'available',
                    security_tier: 'verified',
                    permissions: ['fs:read', 'net:fetch'],
                }),
            ],
        });
        renderHub();
        await screen.findByText('Perm Agent');
        await user.click(screen.getByTitle('Install Perm Agent'));

        const dialog = await screen.findByRole('alertdialog');
        expect(within(dialog).getByText('fs:read')).toBeInTheDocument();
        expect(within(dialog).getByText('net:fetch')).toBeInTheDocument();
    });
});
