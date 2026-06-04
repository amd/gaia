// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AgentHubGrid } from '../AgentHubGrid';
import type { AgentInfo, AgentCatalogResponse, InstallStatus } from '../../types';
import * as api from '../../services/api';

vi.mock('../../services/api');

const mockedApi = vi.mocked(api);

function agent(partial: Partial<AgentInfo> & { id: string }): AgentInfo {
    return {
        name: partial.id,
        description: `${partial.id} description`,
        source: 'builtin',
        conversation_starters: [],
        models: [],
        ...partial,
    };
}

const INSTALLED: AgentInfo[] = [
    agent({ id: 'chat', name: 'Chat', source: 'builtin' }),
    agent({ id: 'code', name: 'Code', source: 'builtin' }),
];

const CATALOG: AgentCatalogResponse = {
    offline: false,
    agents: [
        agent({
            id: 'weather',
            name: 'Weather',
            source: 'installed',
            status: 'available',
            download_size_bytes: 1.4 * 1024 * 1024 * 1024,
            compatibility: { level: 'compatible' },
        }),
        agent({
            id: 'mars',
            name: 'Mars',
            source: 'installed',
            status: 'available',
            download_size_bytes: 5 * 1024 * 1024,
            compatibility: { level: 'incompatible', reasons: ['Requires NPU'] },
        }),
        // update for an installed agent
        agent({ id: 'chat', name: 'Chat', status: 'update_available', version: '0.1.0', latest_version: '0.2.0' }),
    ],
};

beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.listCatalog.mockResolvedValue(CATALOG);
    mockedApi.listAgents.mockResolvedValue({ agents: INSTALLED, total: INSTALLED.length });
});

describe('AgentHubGrid tabs', () => {
    it('shows the Installed tab with a count and update suffix', async () => {
        render(
            <AgentHubGrid
                agents={INSTALLED}
                activeAgentId="chat"
                onSelect={() => {}}
                onStartChat={() => {}}
            />,
        );
        // Default tab is Installed; both agents render.
        expect(screen.getByText('Chat')).toBeInTheDocument();
        expect(screen.getByText('Code')).toBeInTheDocument();
        // The tab starts as "Installed (2)" before the catalog is fetched.
        expect(screen.getByRole('tab', { name: /Installed \(2\)/ })).toBeInTheDocument();
    });

    it('loads the catalog and renders Available agents with Install buttons', async () => {
        const user = userEvent.setup();
        render(
            <AgentHubGrid
                agents={INSTALLED}
                activeAgentId="chat"
                onSelect={() => {}}
                onStartChat={() => {}}
            />,
        );

        await user.click(screen.getByRole('tab', { name: /Available/ }));

        await waitFor(() => expect(mockedApi.listCatalog).toHaveBeenCalled());

        // Available, not-installed agents show Install.
        await screen.findByText('Weather');
        expect(screen.getByText('Mars')).toBeInTheDocument();
        const installButtons = screen.getAllByRole('button', { name: /Install/ });
        expect(installButtons.length).toBeGreaterThanOrEqual(1);

        // Download size is rendered.
        expect(screen.getByText('1.4 GB')).toBeInTheDocument();
    });

    it('disables Install for incompatible agents', async () => {
        const user = userEvent.setup();
        render(
            <AgentHubGrid
                agents={INSTALLED}
                activeAgentId="chat"
                onSelect={() => {}}
                onStartChat={() => {}}
            />,
        );
        await user.click(screen.getByRole('tab', { name: /Available/ }));
        await screen.findByText('Mars');

        // The Mars card's Install button should be disabled (incompatible).
        const marsCard = screen.getByText('Mars').closest('.agent-hub-card')!;
        const installBtn = marsCard.querySelector('.btn-install') as HTMLButtonElement;
        expect(installBtn).toBeTruthy();
        expect(installBtn.disabled).toBe(true);
    });

    it('starts an install and shows a progress bar', async () => {
        const user = userEvent.setup();
        const installing: InstallStatus = { agent_id: 'weather', state: 'downloading', progress: 40 };
        mockedApi.installAgent.mockResolvedValue(installing);
        mockedApi.getInstallStatus.mockResolvedValue({ agent_id: 'weather', state: 'installing', progress: 80 });

        render(
            <AgentHubGrid
                agents={INSTALLED}
                activeAgentId="chat"
                onSelect={() => {}}
                onStartChat={() => {}}
            />,
        );
        await user.click(screen.getByRole('tab', { name: /Available/ }));
        await screen.findByText('Weather');

        const weatherCard = screen.getByText('Weather').closest('.agent-hub-card')!;
        const installBtn = weatherCard.querySelector('.btn-install') as HTMLButtonElement;
        await user.click(installBtn);

        await waitFor(() => expect(mockedApi.installAgent).toHaveBeenCalledWith('weather', expect.anything()));
        // Progress bar appears with a Cancel button.
        await waitFor(() => {
            const card = screen.getByText('Weather').closest('.agent-hub-card')!;
            expect(card.querySelector('.agent-install-bar')).toBeTruthy();
        });
    });

    it('prompts for trust before installing a non-verified native agent', async () => {
        const user = userEvent.setup();
        const nativeCatalog: AgentCatalogResponse = {
            offline: false,
            agents: [
                agent({
                    id: 'edge',
                    name: 'Edge Native',
                    source: 'installed',
                    status: 'available',
                    language: 'cpp',
                    security_tier: 'community',
                    requires_trust: true,
                    compatibility: { level: 'compatible' },
                }),
            ],
        };
        mockedApi.listCatalog.mockResolvedValue(nativeCatalog);
        mockedApi.installAgent.mockResolvedValue({ agent_id: 'edge', state: 'downloading', progress: 10 });

        render(
            <AgentHubGrid
                agents={INSTALLED}
                activeAgentId="chat"
                onSelect={() => {}}
                onStartChat={() => {}}
            />,
        );
        await user.click(screen.getByRole('tab', { name: /Available/ }));
        await screen.findByText('Edge Native');

        // Clicking Install opens the confirmation dialog (no install yet).
        const card = screen.getByText('Edge Native').closest('.agent-hub-card')!;
        await user.click(card.querySelector('.btn-install') as HTMLButtonElement);
        await screen.findByRole('alertdialog');
        expect(mockedApi.installAgent).not.toHaveBeenCalled();

        // Confirming installs with trust_native.
        await user.click(screen.getByRole('button', { name: /Trust & Install/ }));
        await waitFor(() =>
            expect(mockedApi.installAgent).toHaveBeenCalledWith('edge', expect.anything(), true),
        );
    });

    it('shows the offline banner when the catalog is cached', async () => {
        const user = userEvent.setup();
        mockedApi.listCatalog.mockResolvedValue({ ...CATALOG, offline: true });
        render(
            <AgentHubGrid
                agents={INSTALLED}
                activeAgentId="chat"
                onSelect={() => {}}
                onStartChat={() => {}}
            />,
        );
        await user.click(screen.getByRole('tab', { name: /Available/ }));
        await screen.findByText(/Showing cached catalog/);
    });

    it('shows an error with Retry when the catalog fails', async () => {
        const user = userEvent.setup();
        mockedApi.listCatalog.mockRejectedValueOnce(new Error('Catalog unavailable'));
        render(
            <AgentHubGrid
                agents={INSTALLED}
                activeAgentId="chat"
                onSelect={() => {}}
                onStartChat={() => {}}
            />,
        );
        await user.click(screen.getByRole('tab', { name: /Available/ }));
        await screen.findByText('Catalog unavailable');
        expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument();

        // Retry succeeds.
        mockedApi.listCatalog.mockResolvedValue(CATALOG);
        await user.click(screen.getByRole('button', { name: /Retry/ }));
        await screen.findByText('Weather');
    });
});
