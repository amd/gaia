// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Regression tests for issue #4286: the Memory Dashboard's beta-confirm box was
 * a plain <div> (no dialog role, no focus management) and the page title was an
 * <h3>, so heading navigation started mid-hierarchy.
 *
 * Pins:
 *   - the page title is an <h1> and the conversation-detail title an <h2>
 *   - the beta-confirm box exposes role="dialog" + aria-modal + a name
 *   - opening it moves focus in, Tab stays inside, closing restores focus
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryDashboard } from '../MemoryDashboard';
import * as memoryApi from '../../services/memoryApi';

vi.mock('../../services/memoryApi');

const mockedApi = vi.mocked(memoryApi);

beforeEach(() => {
    vi.resetAllMocks();
    mockedApi.getMemoryStats.mockResolvedValue(null);
    mockedApi.getMemoryActivity.mockResolvedValue([]);
    mockedApi.getKnowledge.mockResolvedValue({ items: [], total: 0 });
    mockedApi.getToolSummary.mockResolvedValue([]);
    mockedApi.getMemoryConversations.mockResolvedValue([]);
    mockedApi.getUpcomingItems.mockResolvedValue([]);
    mockedApi.getEmbeddingCoverage.mockResolvedValue({
        total_items: 0, with_embedding: 0, without_embedding: 0, coverage_pct: 0,
    });
    mockedApi.getEntities.mockResolvedValue([]);
    mockedApi.listGoals.mockResolvedValue({ goals: [], total: 0 });
    mockedApi.getGoalStats.mockResolvedValue({ goals: {}, tasks: {} });
    // Memory disabled keeps the dashboard body minimal — the Settings section
    // (and its beta-confirm toggle) renders either way.
    mockedApi.getMemorySettings.mockResolvedValue({
        memory_enabled: false,
        mcp_memory_enabled: false,
        system_discovery_consent: false,
    });
    mockedApi.updateMemorySettings.mockResolvedValue({
        memory_enabled: true,
        mcp_memory_enabled: false,
        system_discovery_consent: false,
    });
});

async function renderDashboard() {
    render(<MemoryDashboard />);
    return await screen.findByRole('button', { name: /Memory is disabled/ });
}

describe('MemoryDashboard accessibility', () => {
    it('titles the page with an h1 so heading navigation starts at the top', async () => {
        await renderDashboard();

        expect(
            screen.getByRole('heading', { level: 1, name: /Memory Dashboard/ })
        ).toBeInTheDocument();
        expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument();
    });

    it('exposes the beta confirmation as a named modal dialog', async () => {
        const user = userEvent.setup();
        const toggle = await renderDashboard();

        await user.click(toggle);

        const dialog = await screen.findByRole('dialog', { name: 'Enable Agent Memory?' });
        expect(dialog).toHaveAttribute('aria-modal', 'true');
        expect(dialog).toHaveAccessibleDescription(/Agent memory is a work-in-progress/);
    });

    it('moves focus into the dialog and keeps Tab inside it', async () => {
        const user = userEvent.setup();
        const toggle = await renderDashboard();

        await user.click(toggle);
        const dialog = await screen.findByRole('dialog');
        expect(dialog).toHaveFocus();

        // Tab through every focusable and past the end — focus must not escape.
        for (let i = 0; i < 4; i++) {
            await user.tab();
            expect(dialog.contains(document.activeElement)).toBe(true);
        }
        await user.tab({ shift: true });
        expect(dialog.contains(document.activeElement)).toBe(true);
    });

    it('closes on Escape and hands focus back to the toggle that opened it', async () => {
        const user = userEvent.setup();
        const toggle = await renderDashboard();

        await user.click(toggle);
        await screen.findByRole('dialog');

        await user.keyboard('{Escape}');

        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(toggle).toHaveFocus();
    });
});
